# -*- coding: utf-8 -*-
"""
testes/test_ml_status_risco_v1526.py — Propagacao do status MODEL_ERROR ao
RiskEngine/app (P0-A30 aprofundado, v15.26).

ANTES: o app informava `ml_disponivel=self.scorer is not None` — modelo
carregado mas com a ultima inferencia FALHA (MODEL_ERROR/ECE_ALTO) ou sem
inferir ainda (NAO_INFERIDO) contava como "ML disponivel" para o RiskEngine.

AGORA:
  - app: `_ml_operacional(scorer, ativo)` = scorer presente E status OK;
    `_classificar_modelo` rotula o journal com ML(ERRO) quando a inferencia
    falhou (antes seria ML(USADO)/ML(BLOQUEADO) enganoso);
  - risk_engine: `atualizar_mercado` aceita ml_status/ml_ativo e o motivo de
    ML down registra status + ativo (auditoria distingue MODEL_ERROR).
"""

import time

import pytest

from core.app import _classificar_modelo, _ml_operacional, _status_do_ml
from core.contracts import Signal
from core.risk_engine import RiskEngine


class _ScorerStub:
    """Stub minimo: status controlado (e prob p/ signals)."""
    def __init__(self, status=None):
        self.status = status


def _config(**overrides):
    cfg = {
        'trading': {'max_trades_dia': 15, 'cooldown_entre_trades_s': 0,
                    'limiar_confirmacao': 0.50},
        'circuit_breaker': {'nivel1_pnl': -100, 'nivel2_pnl': -300,
                            'nivel3_pnl': -500, 'nivel1_perdas': 3,
                            'nivel2_perdas': 5, 'nivel3_perdas': 7},
        'position_sizing': {'max_position_size': 5,
                            'target_risk_per_trade': 100},
        'horarios': {'abertura_fim': [0, 0], 'fechamento': [23, 59],
                     'almoco_inicio': [25, 0], 'almoco_fim': [25, 0]},
        'max_stale_data_s': 30,
        'max_spread_pts': {'WIN': 30, 'WDO': 3},
        'max_volatility_bps': 100,
        'tolerancia_sem_ml_s': 300,
        'max_slippage_ticks': 3,
        'confirmacao_necessaria': 1,
        'ml_threshold_piramidacao': 0.65,
        'ml_sizing': False,
        'horario_fechamento': (23, 59),
    }
    cfg.update(overrides)
    return cfg


def _signal(ml_prob=0.7, lado='C', motivos=None):
    return Signal(symbol='WINV26', timestamp_ms=int(time.time() * 1000),
                  lado=lado, score=0.8, confianca=0.9, ml_prob=ml_prob,
                  tp=100, sl=50, preco_ref=170000, motivos=motivos or [])


def _risk_engine():
    re = RiskEngine(config=_config())
    re.atualizar_mercado(preco_ts=time.time() * 1000, spread=5, vol_bps=20,
                         confianca=0.9)
    return re


# ======================================================================
#  1. Helpers puros do app
# ======================================================================

class TestAppHelpers:
    def test_ml_operacional_sem_scorer_false(self):
        assert _ml_operacional(None, 'WINV26') is False

    def test_ml_operacional_status_ok_true(self):
        scorer = _ScorerStub({'WINV26': 'OK'})
        assert _ml_operacional(scorer, 'WINV26') is True

    @pytest.mark.parametrize('st', ['MODEL_ERROR', 'ECE_ALTO',
                                    'NAO_INFERIDO'])
    def test_ml_operacional_status_nao_ok_false(self, st):
        """ANTES: scorer is not None -> True mesmo com inferencia falha."""
        scorer = _ScorerStub({'WINV26': st})
        assert _ml_operacional(scorer, 'WINV26') is False

    def test_ml_operacional_sem_status_attr_compat_true(self):
        class _SemStatus:
            pass
        assert _ml_operacional(_SemStatus(), 'WINV26') is True

    def test_ml_operacional_por_ativo(self):
        scorer = _ScorerStub({'WINV26': 'OK', 'WDOV26': 'MODEL_ERROR'})
        assert _ml_operacional(scorer, 'WINV26') is True
        assert _ml_operacional(scorer, 'WDOV26') is False

    def test_classificar_modelo_ml_erro_prioritario(self):
        scorer = _ScorerStub({'WINV26': 'MODEL_ERROR'})
        sig = _signal(ml_prob=0.5, motivos=['ML_ERRO (inferencia falhou)'])
        assert _classificar_modelo(scorer, sig) == 'heuristico+ML(ERRO)'
        # mesmo com prob alta, erro manda
        sig2 = _signal(ml_prob=0.8, motivos=['ML_ERRO (inferencia falhou)'])
        assert _classificar_modelo(scorer, sig2) == 'heuristico+ML(ERRO)'

    def test_classificar_modelo_sem_scorer(self):
        assert _classificar_modelo(None, _signal()) == 'heuristico'

    def test_classificar_modelo_usado_e_bloqueado(self):
        scorer = _ScorerStub({'WINV26': 'OK'})
        assert _classificar_modelo(scorer, _signal(ml_prob=0.8, lado='C')) \
            == 'heuristico+ML(USADO)'
        assert _classificar_modelo(scorer, _signal(ml_prob=0.4, lado='')) \
            == 'heuristico+ML(BLOQUEADO)'

    def test_status_do_ml(self):
        scorer = _ScorerStub({'WINV26': 'MODEL_ERROR'})
        assert _status_do_ml(scorer, 'WINV26') == 'MODEL_ERROR'
        assert _status_do_ml(None, 'WINV26') is None


# ======================================================================
#  2. RiskEngine: motivo de ML down com status + ativo
# ======================================================================

class TestRiskEnginePropagacao:
    def test_ml_down_model_error_registra_status_e_ativo(self):
        re = _risk_engine()
        re.atualizar_mercado(preco_ts=time.time() * 1000, spread=5,
                             vol_bps=20, confianca=0.9,
                             ml_disponivel=False,
                             ml_status='MODEL_ERROR', ml_ativo='WINV26')
        prot = re._check_model_availability()
        assert prot['ml_available'] is False
        assert 'MODEL_ERROR' in prot['detail']
        assert 'WINV26' in prot['detail']

    def test_ml_up_ok(self):
        re = _risk_engine()
        re.atualizar_mercado(preco_ts=time.time() * 1000, spread=5,
                             vol_bps=20, confianca=0.9,
                             ml_disponivel=True, ml_status='OK',
                             ml_ativo='WINV26')
        prot = re._check_model_availability()
        assert prot['ml_available'] is True

    def test_avaliar_aprova_e_registra_protecao_model(self):
        """Fluxo real: a decisao carrega risk_components['model'] com o
        status correto (auditoria do journal)."""
        re = _risk_engine()
        re.atualizar_mercado(preco_ts=time.time() * 1000, spread=5,
                             vol_bps=20, confianca=0.9,
                             ml_disponivel=False,
                             ml_status='MODEL_ERROR', ml_ativo='WINV26')
        decision = re.avaliar(_signal())
        assert decision.permitido is True  # warning, nao bloqueia (dev)
        model = decision.risk_components.get('model', {})
        assert model.get('ml_available') is False
        assert 'MODEL_ERROR' in model.get('detail', '')
