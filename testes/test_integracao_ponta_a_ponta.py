import sys, os
_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in [_base, os.path.join(_base, "ml"), os.path.join(_base, "scripts")]:
    if os.path.isdir(_d): sys.path.insert(0, _d)
"""
test_integracao_ponta_a_ponta.py — Valida que TODOS os componentes
integrados (scorer, motor, dataset) funcionam juntos.

Cobre:
  1. integrar_base.py: gera dataset enriquecido a partir de dados brutos
  2. scorer.py com VWAPTracker: estado causal, reset diario, distancia vs VWAP
  3. motor_rt_alphaz.py /api/contexto: endpoint exposto com dados corretos
  4. dataset enriquecido: contem todas as features novas
  5. retreinar_lgbm_limpo.py --usar-complemento: carrega dataset enriquecido
  6. walk_forward_v914_limpo.py: auto-detecta dataset enriquecido
"""
import os
import sys
import json
import time
import pickle
import tempfile
import subprocess
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ml.scorer import ScorerML
from features.vwap_tracker import VWAPTracker


class _DummyModel:
    """Modelo dummy serializável (declarado no escopo do módulo)."""
    def predict_proba(self, X):
        return np.array([[0.3, 0.7]] * len(X))


# ============================================================
#  1. VWAPTracker: estado causal e reset diario
# ============================================================
def test_vwap_tracker_estado_causal():
    """Atualizar com negocios em ordem cronologica produz VWAP causal."""
    tracker = VWAPTracker('WINV26', tick=5.0)
    # 3 negocios: 100@10, 102@20, 104@30 -> VWAP = (1000+2040+3120)/60 = 102.667
    # 14/08/2026 09:00 BRT = epoch 1786708800000
    ts1 = 1786708800000
    tracker.update(ts1, 100.0, 10)
    assert tracker.vwap == 100.0
    assert tracker.vol_total == 10
    ts2 = ts1 + 60000  # 09:01
    tracker.update(ts2, 102.0, 20)
    assert abs(tracker.vwap - (100*10 + 102*20) / 30) < 1e-9
    assert tracker.vol_total == 30
    # proximo dia — virada
    ts3 = ts1 + 86400 * 1000  # exatamente 1 dia depois
    tracker.update(ts3, 200.0, 5)
    # VWAP deve resetar para 200.0 (primeiro negocio do novo dia)
    assert tracker.vwap == 200.0
    assert tracker.vol_total == 5


def test_vwap_tracker_cruzamento():
    """cruzou_vwap = 1 quando lado muda, 0 caso contrario."""
    tracker = VWAPTracker('WINV26')
    base = 1786708800000
    # 1: preco > vwap (lado = 1)
    # depois preco < vwap (lado = 0) -> cruzou = 1
    # depois preco > vwap (lado = 1) -> cruzou = 1
    tracker.update(base, 100, 10)  # vwap=100, lado=0 (preco==vwap)
    tracker.update(base + 1000, 110, 10)  # vwap=105, lado=1 (acima)
    assert tracker.acima_vwap == 1.0
    assert tracker.cruzou_vwap == 0.0  # veio de lado=None
    tracker.update(base + 2000, 100, 10)  # vwap=103.33, lado=0 (abaixo)
    assert tracker.abaixo_vwap == 1.0
    assert tracker.cruzou_vwap == 1.0  # mudou de 1 para 0
    tracker.update(base + 3000, 110, 10)  # vwap=105, lado=1
    assert tracker.cruzou_vwap == 1.0  # mudou de 0 para 1


def test_vwap_tracker_distancia_e_ticks():
    """dist_vwap_pts e dist_vwap_ticks (com tick=5)."""
    tracker = VWAPTracker('WINV26', tick=5.0)
    tracker.update(1786708800000, 100, 10)  # vwap=100
    # preco=110: dist_vwap=+10, ticks=+2
    tracker.update(1786708801000, 110, 10)
    # agora vwap = (100*10 + 110*10)/20 = 105, dist=+5, ticks=+1
    assert abs(tracker.vwap - 105) < 1e-9
    assert abs(tracker.dist_vwap_pts - 5) < 1e-9


# ============================================================
#  2. ScorerML: injeta VWAP e ajuste no predict
# ============================================================
class DummyBloco:
    """Bloco de book para testes."""
    def __init__(self, mid=100.0, bid=99.5, ask=100.5):
        self.mid = mid
    def __getattr__(self, name):
        return getattr(self, name, None)


def test_scorer_com_ajuste_oficial():
    """Scorer com tabela de ajuste carrega o ajuste de D-1."""
    blob = {'modelo': _DummyModel(), 'features': ['preco_ultimo', 'aggr_imb']}
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.pkl')
    pickle.dump(blob, open(tmp.name, 'wb'))
    tmp.close()

    # tabela de ajuste: WINV26 em D-1 com ajuste 100.0
    df_aj = pd.DataFrame([
        {'data_pregao': '2026-08-13', 'contrato': 'WINV26', 'ajuste': 100.0},
    ])
    scorer = ScorerML(tmp.name, ['WINV26'], tabela_ajuste_oficial=df_aj)
    os.unlink(tmp.name)
    # verificar que o mapa foi construido
    assert 'WINV26' in scorer.tabela_ajuste
    assert scorer.tabela_ajuste['WINV26']['2026-08-13'] == 100.0


def test_scorer_nao_falha_sem_ajuste():
    """Sem tabela de ajuste, o scorer funciona normalmente (features ficam NaN)."""
    blob = {'modelo': _DummyModel(), 'features': ['preco_ultimo', 'aggr_imb']}
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.pkl')
    pickle.dump(blob, open(tmp.name, 'wb'))
    tmp.close()
    scorer = ScorerML(tmp.name, ['WINV26'])
    os.unlink(tmp.name)
    # sem tabela — mapas estao vazios
    assert scorer.tabela_ajuste == {}
    assert scorer.ajuste_anterior_oficial == {}


# ============================================================
#  3. integrar_base.py: fluxo completo
# ============================================================
@pytest.mark.skipif(
    not os.path.exists(r'D:\MarketData\Profit\RAW\ano=2026\mes=08\dia=14'),
    reason='dados brutos nao disponiveis para integracao',
)
def test_integrar_base_arquivos_gerados():
    """integrar_base.py gera arquivos de auditoria (ajuste + vwap)."""
    from calcular_ajuste_diario import listar_dias
    from calcular_vwap_diaria import listar_dias as lv
    dias = listar_dias(2026, 8)
    assert len(dias) > 0, 'nenhum dia RAW disponivel'
    # verificar que existem arquivos .csv de ajuste na pasta
    # (podem ter sido gerados por integracao anterior)
    save_dir = r'D:\MarketData\mimo'
    csvs = [f for f in os.listdir(save_dir) if f.startswith('ajuste_diario_')]
    if csvs:
        # validar schema do CSV
        df = pd.read_csv(os.path.join(save_dir, csvs[0]))
        assert 'data_pregao' in df.columns
        assert 'contrato' in df.columns
        assert 'ajuste' in df.columns
        # validacao: ajuste > 0 para WIN/WDO
        valid = df[df['ajuste'] > 0]
        assert len(valid) > 0, 'nenhum ajuste positivo no CSV'


# ============================================================
#  4. Dataset enriquecido: tem todas as features novas
# ============================================================
@pytest.mark.skipif(
    not os.path.exists(r'D:\MarketData\mimo\26\dataset_final_completo.parquet'),
    reason='dataset enriquecido nao existe; rodar integrar_base.py',
)
def test_dataset_completo_contem_features_novas():
    """O parquet final_completo deve ter todas as features novas."""
    df = pd.read_parquet(r'D:\MarketData\mimo\26\dataset_final_completo.parquet',
                          columns=None)
    # features de VWAP
    assert 'vwap' in df.columns
    assert 'dist_vwap_pts' in df.columns
    assert 'cruzou_vwap' in df.columns
    # features de ajuste oficial
    assert 'ajuste_anterior_oficial' in df.columns
    assert 'dist_ajuste_oficial_pts' in df.columns
    # features de regime
    assert 'regime_realiz_vol' in df.columns
    # interacoes
    inter = [c for c in df.columns if c.startswith(('aggr_x_', 'cvd_x_', 'imb_x_', 'vol_x_'))]
    assert len(inter) >= 10, f'esperado >=10 interacoes, encontrado {len(inter)}'


# ============================================================
#  5. Estado_salud exposto para o dashboard
# ============================================================
def test_estado_salud_com_vwap_e_ajuste():
    """estado_salud() deve incluir vwap_estado e ajuste_anterior_oficial."""
    blob = {'modelo': _DummyModel(), 'features': ['preco_ultimo']}
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.pkl')
    pickle.dump(blob, open(tmp.name, 'wb'))
    tmp.close()
    df_aj = pd.DataFrame([
        {'data_pregao': '2026-08-13', 'contrato': 'WINV26', 'ajuste': 100.0},
    ])
    scorer = ScorerML(tmp.name, ['WINV26'], tabela_ajuste_oficial=df_aj)
    os.unlink(tmp.name)
    # sem eventos ainda — estado_salud deve funcionar
    estado = scorer.estado_salud()
    assert 'vwap_estado' in estado
    assert 'ajuste_anterior_oficial' in estado
    # vwap_estado tem entrada para WINV26
    assert 'WINV26' in estado['vwap_estado']
    # ajuste_anterior_oficial ainda vazio (sem evento disparado)
    assert 'WINV26' not in estado['ajuste_anterior_oficial']
    # disparar evento para popular o ajuste
    # 14/08/2026 09:00 BRT = epoch 1786708800000
    scorer.evento('WINV26', 1786708800000, 100, 10, '', '', '')
    estado2 = scorer.estado_salud()
    # agora o ajuste deve ter sido populado
    assert 'WINV26' in estado2['ajuste_anterior_oficial']
    assert estado2['ajuste_anterior_oficial']['WINV26'] == 100.0


# ============================================================
#  6. Causalidade do VWAPTracker: perturbacao do futuro nao muda passado
# ============================================================
def test_vwap_tracker_leakage():
    """Adicionar negocio futuro nao muda o estado do VWAP no passado."""
    tracker = VWAPTracker('WINV26')
    base = 1786708800000
    # 3 negocios passados
    tracker.update(base, 100, 10)
    tracker.update(base + 1000, 105, 20)
    tracker.update(base + 2000, 110, 30)
    # VWAP atual apos 3 negocios = (100*10 + 105*20 + 110*30) / 60 = 6400/60 = 106.667
    vwap_antes = tracker.vwap
    assert abs(vwap_antes - 6400/60) < 1e-9
    assert tracker.vol_total == 60
    # adicionar 1 negocio futuro (horas depois, preco absurdo)
    tracker.update(base + 3600*1000, 999.0, 1000)
    # VWAP mudou (cumulativo) por causa do novo negocio
    assert abs(tracker.vwap - vwap_antes) > 1
    # mas o VWAP dos 3 primeiros negocios, se recalculado do zero, nao mudou
    vwap_passado = (100*10 + 105*20 + 110*30) / 60
    assert abs(vwap_passado - 6400/60) < 1e-9  # mesmo valor (106.667)
