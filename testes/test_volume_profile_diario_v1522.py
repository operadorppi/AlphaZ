# -*- coding: utf-8 -*-
"""
testes/test_volume_profile_diario_v1522.py — VolumeProfile com rollover de
sessao interno (P0-A27).

ANTES: o VolumeProfileTracker nao conhecia a data — dependia de o chamador
chamar reset()/reset_diario() na virada de sessao. No ScorerML a ordem era:
  vps.atualizar() ... depois ... _atualizar_ajuste_para_dia() (reset)
Na 1a linha de um dia novo, o trade entrava no perfil do dia ANTERIOR
(contaminando POC/VAH/VAL daquele instante) e o reset posterior apagava o
1o trade do dia novo (perfil novo começava vazio ate o 2o trade).

AGORA: atualizar(ts_ms, preco, qtd, agressor) exige ts_ms e o tracker faz
rollover interno por dia de Brasilia (dia_de_ts_br, fonte unica P0-A22) —
na 1a atualizacao do dia novo o perfil anterior e descartado ANTES de
acumular, preservando o 1o trade da sessao nova.

Cobertura:
  1. Dia 1 e dia 2 acumulam em perfis SEPARADOS (sem contaminacao)
  2. O 1o trade do dia novo entra no perfil novo (nao e perdido)
  3. POC/VAH/VAL calculados no dia 2 nao usam o dia 1
  4. Mesmo dia acumula (sem reset no meio do dia)
  5. calcular() sem volume -> zeros
  6. reset() zera perfil E identidade de dia (proxima sessao comeca nova)
  7. Instancias separadas por ativo sao independentes
  8. Guard estrutural: scorer nao reseta mais vps no dia (rollover interno)
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from features.volume_profile import VolumeProfileTracker  # noqa: E402


def _epoch_brt(ano, mes, dia, hora, minu=0):
    """Epoch ms de um horario de Brasilia (UTC-3)."""
    utc = datetime(ano, mes, dia, hora, minu, tzinfo=timezone.utc) + timedelta(hours=3)
    return int(utc.timestamp() * 1000)


_DIA1_09H = _epoch_brt(2026, 9, 1, 9, 0)
_DIA1_10H = _epoch_brt(2026, 9, 1, 10, 0)
_DIA2_09H = _epoch_brt(2026, 9, 2, 9, 0)


class TestVolumeProfileRollover:

    def test_dias_separados_sem_contaminacao(self):
        """Dia 2 so acumula volume do dia 2; dia 1 nao vaza."""
        vp = VolumeProfileTracker(tick=5)
        # Dia 1: 100 contratos em 1000.0
        vp.atualizar(_DIA1_09H, 1000.0, 100, 'Comprador')
        assert vp.calcular(1000.0)['vp_total'] == 100
        # Dia 2: 200 contratos em 2000.0 (outro patamar de preco)
        vp.atualizar(_DIA2_09H, 2000.0, 200, 'Vendedor')
        r = vp.calcular(2000.0)
        assert r['vp_total'] == 200          # so o dia 2
        assert 1000.0 not in vp.volumes      # nivel do dia 1 descartado
        assert 2000.0 in vp.volumes

    def test_primeiro_trade_do_dia_novo_preservado(self):
        """O 1o trade do dia novo NAO e perdido (entra no perfil novo)."""
        vp = VolumeProfileTracker(tick=5)
        vp.atualizar(_DIA1_09H, 1000.0, 100, 'Comprador')
        # Primeiro trade do dia 2 — antes, caia no perfil do dia 1 e era
        # apagado pelo reset posterior. Agora o rollover roda ANTES de somar.
        vp.atualizar(_DIA2_09H, 1500.0, 7, 'Comprador')
        r = vp.calcular(1500.0)
        assert r['vp_total'] == 7
        assert 1500.0 in vp.volumes
        assert vp.volumes[1500.0] == 7

    def test_poc_do_dia_2_nao_usa_dia_1(self):
        """POC no dia 2 reflete so o perfil do dia 2."""
        vp = VolumeProfileTracker(tick=5)
        # Dia 1: POC forte em 1000.0 (900 contratos)
        for _ in range(9):
            vp.atualizar(_DIA1_09H, 1000.0, 100, 'Comprador')
        # Dia 2: 1 trade de 1 contrato em 2000.0
        vp.atualizar(_DIA2_09H, 2000.0, 1, 'Vendedor')
        r = vp.calcular(2000.0)
        # POC deve ser 2000.0 (distancia 0 do preco atual) — nao 1000.0 do
        # dia anterior. ANTES, o POC do dia 1 contaminava o instante inicial.
        assert r['poc_dist'] == 0.0
        assert r['vp_total'] == 1
        assert 1000.0 not in vp.volumes

    def test_mesmo_dia_acumula_sem_reset(self):
        """Trades do mesmo dia acumulam (sem reset no meio do dia)."""
        vp = VolumeProfileTracker(tick=5)
        vp.atualizar(_DIA1_09H, 1000.0, 10, 'Comprador')
        vp.atualizar(_DIA1_10H, 1005.0, 20, 'Vendedor')
        r = vp.calcular(1002.5)
        assert r['vp_total'] == 30
        assert vp.volumes[1000.0] == 10
        assert vp.volumes[1005.0] == 20

    def test_calcular_sem_volume_zeros(self):
        vp = VolumeProfileTracker(tick=5)
        r = vp.calcular(1000.0)
        assert r == {'poc_dist': 0, 'vah_dist': 0, 'val_dist': 0,
                     'poc_acima': 0, 'vp_total': 0}

    def test_reset_zera_perfil_e_identidade(self):
        """reset() limpa perfil e dia; proxima atualizacao comeca sessao nova."""
        vp = VolumeProfileTracker(tick=5)
        vp.atualizar(_DIA1_09H, 1000.0, 50, 'Comprador')
        vp.reset()
        assert vp.calcular(1000.0)['vp_total'] == 0
        # Mesmo dia apos reset: acumula do zero
        vp.atualizar(_DIA1_10H, 1005.0, 5, 'Comprador')
        assert vp.calcular(1005.0)['vp_total'] == 5

    def test_reset_diario_compat_com_reset(self):
        vp = VolumeProfileTracker(tick=5)
        vp.atualizar(_DIA1_09H, 1000.0, 50, 'Comprador')
        vp.reset_diario()
        assert vp.calcular(1000.0)['vp_total'] == 0

    def test_instancias_por_ativo_independentes(self):
        vp_win = VolumeProfileTracker(tick=5)
        vp_wdo = VolumeProfileTracker(tick=0.5)
        vp_win.atualizar(_DIA1_09H, 130000.0, 100, 'Comprador')
        vp_wdo.atualizar(_DIA1_09H, 5123.0, 50, 'Vendedor')
        assert vp_win.calcular(130000.0)['vp_total'] == 100
        assert vp_wdo.calcular(5123.0)['vp_total'] == 50
        assert 5123.0 not in vp_win.volumes
        assert 130000.0 not in vp_wdo.volumes


class TestGuardScorer:
    def test_scorer_nao_reseta_vps_na_virada(self):
        """ml/scorer._atualizar_ajuste_para_dia nao pode resetar vps:
        reset externo rodava depois do 1o update do dia novo e apagava o
        1o trade da sessao (P0-A27). O rollover e interno ao tracker."""
        src = open(os.path.join(os.path.dirname(__file__), "..", "ml", "scorer.py"),
                   encoding="utf-8").read()
        assert "self.vps[ativo].reset_diario()" not in src
        assert "self.vps[ativo].atualizar(ts_ms, preco, qtd, agressor)" in src

    def test_atualizar_exige_ts_ms(self):
        """Assinatura exige ts_ms (identidade temporal explicita)."""
        import inspect
        src = inspect.getsource(VolumeProfileTracker.atualizar)
        assert "ts_ms" in src
        assert "dia_de_ts_br" in src

    def test_assinatura_antiga_sem_ts_falha(self):
        """Chamar atualizar(preco, qtd, agressor) (sem ts) quebra — o tracker
        nao pode mais ser usado sem identidade temporal."""
        vp = VolumeProfileTracker(tick=5)
        with pytest.raises(TypeError):
            vp.atualizar(1000.0, 10, 'Comprador')  # falta ts_ms
