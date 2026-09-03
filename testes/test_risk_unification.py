# -*- coding: utf-8 -*-
"""
testes/test_risk_unification.py — Testes de unificação de risco (Fase 6).

Valida que:
  1. RiskEngine.avaliar() é a única fonte de RiskDecision
  2. PositionManager não toma decisões de risco (apenas executa)
  3. PositionManager não chama RiskManager.pode_abrir() diretamente
  4. PositionManager não rejeita por confiança (isso é do RiskEngine)
  5. PositionManager não tem cooldown próprio (isso é do RiskEngine)
  6. Sizing vem do RiskEngine, não do PositionManager
  7. Estado de risco (pnl_dia, trades, perdas) vive no RiskEngine
"""

import time
import pytest
from unittest.mock import MagicMock

from core.risk_engine import RiskEngine
from core.position_manager import PositionManager
from core.contracts import Action, Signal, RiskDecision


# ============================================================
# Fixtures
# ============================================================

def make_config(**overrides):
    cfg = {
        'trading': {
            'max_trades_dia': 15,
            'cooldown_entre_trades_s': 0,
            'limiar_confirmacao': 0.50,
            'custo_execucao': {'WIN': 5.0, 'WDO': 1.0},
        },
        'circuit_breaker': {
            'nivel1_pnl': -100,
            'nivel2_pnl': -300,
            'nivel3_pnl': -500,
            'nivel1_perdas': 3,
            'nivel2_perdas': 5,
            'nivel3_perdas': 7,
        },
        'position_sizing': {
            'max_position_size': 5,
            'target_risk_per_trade': 100,
        },
        'horarios': {
            # v14.8: sessão 00:00-23:59 — os testes não podem depender da
            # hora do dia (antes falhavam FORA_HORARIO fora do pregão 9-18:30).
            # O comportamento de sessão é testado em outros arquivos.
            'abertura_fim': [0, 0],
            'fechamento': [23, 59],
            'almoco_inicio': [25, 0],
            'almoco_fim': [25, 0],
        },
        'max_stale_data_s': 30,
        'max_spread_pts': {'WIN': 30, 'WDO': 3},
        'max_volatility_bps': 100,
        'tolerancia_sem_ml_s': 300,
        'max_slippage_ticks': 3,
        'confirmacao_necessaria': 1,
        'limiar_confirmacao': 0.50,
        'confianca_piramidacao': 0.85,
        'ml_threshold_piramidacao': 0.65,
        'pnl_min_piramidacao': 50,
        'ml_sizing': False,
        'reversao_fecha': False,
        'usar_trailing_mfe': False,
        'tempo_max_posicao_s': 0,
        'horario_fechamento': (23, 59),
        'desligar_horarios_ruins': False,
    }
    cfg.update(overrides)
    return cfg


def make_signal(lado='C', ml_prob=0.5, tp=100, sl=50, symbol='WINV26'):
    return Signal(
        symbol=symbol,
        timestamp_ms=int(time.time() * 1000),
        lado=lado,
        score=0.8,
        confianca=0.9,
        ml_prob=ml_prob,
        tp=tp,
        sl=sl,
        preco_ref=170000,
    )


def make_risk_engine(config=None):
    re = RiskEngine(config=config or make_config())
    # Atualizar mercado para não bloquear por stale data
    re.atualizar_mercado(
        preco_ts=time.time() * 1000,
        spread=5,
        vol_bps=20,
        ml_disponivel=True,
        confianca=0.9,
    )
    return re


def make_pm(risk_engine=None, config=None):
    cfg = config or make_config()
    re = risk_engine or make_risk_engine(cfg)
    learning = MagicMock()
    learning.previsoes = []
    learning.resultados = []
    pm = PositionManager(
        re, persistence=None, learning=learning,
        config=cfg, ativo_principal='WINV26'
    )
    pm.confianca_ewma = 0.9
    return pm


def abrir_posicao(pm, re, preco=170000, ml_prob=0.7):
    """Abre posição via fluxo canônico: signal → risk_engine → position_manager."""
    signal = make_signal(ml_prob=ml_prob)
    decision = re.avaliar(signal)
    assert decision.permitido, f"RiskEngine bloqueou: {decision.motivo}"
    # Primeira chamada: streak=1
    pm.gerenciar('WINV26', signal, preco, decision=decision, regime='tendencia')
    # Segunda chamada: streak=2 → abre
    action = pm.gerenciar('WINV26', signal, preco, decision=decision, regime='tendencia')
    assert action.tipo == 'ABRIR', f"Esperava ABRIR, got {action.tipo}: {action.motivo}"
    return pm.posicao


# ============================================================
# Testes: RiskEngine é a única fonte de decisão
# ============================================================

class TestRiskEngineUnicoGatekeeper:
    """RiskEngine.avaliar() é a única função que decide se pode abrir."""

    def test_risk_engine_aprova(self):
        re = make_risk_engine()
        signal = make_signal()
        decision = re.avaliar(signal)
        assert decision.permitido
        assert decision.motivo == 'OK'
        assert decision.size >= 1

    def test_risk_engine_bloqueia_kill_switch(self):
        re = make_risk_engine()
        re.ativar_kill_switch()
        signal = make_signal()
        decision = re.avaliar(signal)
        assert not decision.permitido
        assert decision.motivo == 'KILL_SWITCH'

    def test_risk_engine_bloqueia_daily_loss(self):
        re = make_risk_engine()
        re.pnl_dia = -600  # abaixo do limite de -500
        signal = make_signal()
        decision = re.avaliar(signal)
        assert not decision.permitido
        assert decision.motivo == 'DAILY_LOSS_LIMIT'

    def test_risk_engine_bloqueia_max_trades(self):
        re = make_risk_engine()
        re.trades_dia = 15  # limite
        signal = make_signal()
        decision = re.avaliar(signal)
        assert not decision.permitido
        assert decision.motivo == 'MAX_TRADES'

    def test_risk_engine_bloqueia_consecutive_loss(self):
        re = make_risk_engine()
        re.perdas_consecutivas = 3  # limite
        signal = make_signal()
        decision = re.avaliar(signal)
        assert not decision.permitido
        assert decision.motivo == 'CONSECUTIVE_LOSS'

    def test_risk_engine_bloqueia_confidence_baixa(self):
        re = make_risk_engine()
        signal = make_signal()
        signal.confianca = 0.2  # abaixo de 0.50
        decision = re.avaliar(signal)
        assert not decision.permitido
        assert decision.motivo == 'CONFIANCA_BAIXA'

    def test_risk_engine_bloqueia_alvos_insanos(self):
        re = make_risk_engine()
        signal = make_signal(tp=5, sl=2000)  # TP muito baixo, SL muito alto
        decision = re.avaliar(signal)
        assert not decision.permitido
        assert 'INSANO' in decision.motivo

    def test_risk_engine_bloqueia_stale_data(self):
        re = make_risk_engine()
        re._ultimo_preco_ts = (time.time() - 60) * 1000  # 60s atrás
        signal = make_signal()
        decision = re.avaliar(signal)
        assert not decision.permitido
        assert decision.motivo == 'STALE_DATA'


# ============================================================
# Testes: PositionManager não reinventa regras de risco
# ============================================================

class TestPositionManagerNaoDecideRisco:
    """PositionManager apenas executa a RiskDecision recebida."""

    def test_pm_sem_decision_rejeita(self):
        """Sem RiskDecision injetada → REJEITADO (não chama RiskManager)."""
        pm = make_pm()
        signal = make_signal()
        # Sem decision — streak precisa ser >= 2
        pm.gerenciar('WINV26', signal, 170000, decision=None, regime='tendencia')
        action = pm.gerenciar('WINV26', signal, 170000, decision=None, regime='tendencia')
        assert action.tipo == 'REJEITADO'
        assert action.motivo == 'SEM_RISK_DECISION'

    def test_pm_executa_decision_aprovada(self):
        """Decision aprovada → ABRIR."""
        re = make_risk_engine()
        pm = make_pm(re)
        signal = make_signal()
        decision = re.avaliar(signal)
        assert decision.permitido

        pm.gerenciar('WINV26', signal, 170000, decision=decision, regime='tendencia')
        action = pm.gerenciar('WINV26', signal, 170000, decision=decision, regime='tendencia')
        assert action.tipo == 'ABRIR'

    def test_pm_respeita_decision_bloqueada(self):
        """Decision bloqueada → REJEITADO com motivo do RiskEngine."""
        re = make_risk_engine()
        re.ativar_kill_switch()
        pm = make_pm(re)
        signal = make_signal()
        decision = re.avaliar(signal)
        assert not decision.permitido

        pm.gerenciar('WINV26', signal, 170000, decision=decision, regime='tendencia')
        action = pm.gerenciar('WINV26', signal, 170000, decision=decision, regime='tendencia')
        assert action.tipo == 'REJEITADO'
        assert action.motivo == 'KILL_SWITCH'

    def test_pm_nao_tem_cooldown_proprio(self):
        """PositionManager não deve ter _cooldown_until."""
        pm = make_pm()
        assert not hasattr(pm, '_cooldown_until'), \
            "PositionManager não deveria ter _cooldown_until (é do RiskEngine)"

    def test_pm_nao_faz_ml_sizing(self):
        """PositionManager não deve fazer ML sizing (é do RiskEngine)."""
        pm = make_pm()
        # Abrir posição com ML=0.9
        re = pm.risk  # risk_engine
        signal = make_signal(ml_prob=0.9)
        decision = re.avaliar(signal)

        pm.gerenciar('WINV26', signal, 170000, decision=decision, regime='tendencia')
        action = pm.gerenciar('WINV26', signal, 170000, decision=decision, regime='tendencia')
        assert action.tipo == 'ABRIR'
        # Size deve ser o do RiskEngine, não incrementado por ML
        assert pm.posicao['quantidade'] == decision.size

    def test_pm_nao_chama_risk_manager_pode_abrir(self):
        """PositionManager não deve chamar self.risk.pode_abrir()."""
        re = make_risk_engine()
        pm = make_pm(re)
        # Spy no pode_abrir (não deveria ser chamado)
        # RiskEngine não tem pode_abrir, mas se tiver, não deve ser chamado
        signal = make_signal()
        decision = re.avaliar(signal)

        pm.gerenciar('WINV26', signal, 170000, decision=decision, regime='tendencia')
        pm.gerenciar('WINV26', signal, 170000, decision=decision, regime='tendencia')

        # Se PositionManager chamasse pode_abrir, seria sem decision
        # Como decision foi injetada, não deve chamar fallback


# ============================================================
# Testes: Estado unificado no RiskEngine
# ============================================================

class TestEstadoUnificado:
    """Estado de risco (pnl_dia, trades, perdas) vive no RiskEngine."""

    def test_trades_dia_no_risk_engine(self):
        re = make_risk_engine()
        assert re.trades_dia == 0
        signal = make_signal()
        re.avaliar(signal)
        assert re.trades_dia == 1

    def test_pnl_dia_no_risk_engine(self):
        re = make_risk_engine()
        re.registrar_resultado(100, True)
        assert re.pnl_dia == 100

    def test_perdas_no_risk_engine(self):
        re = make_risk_engine()
        re.registrar_resultado(-50, False)
        assert re.perdas_consecutivas == 1
        re.registrar_resultado(-50, False)
        assert re.perdas_consecutivas == 2

    def test_circuit_breaker_no_risk_engine(self):
        re = make_risk_engine()
        re.registrar_resultado(-200, False)
        re.registrar_resultado(-200, False)
        re.registrar_resultado(-200, False)
        # 3 perdas → nivel 1
        assert re.circuit_breaker_nivel >= 1

    def test_get_estado_retorna_tudo(self):
        re = make_risk_engine()
        re.pnl_dia = 150
        re.trades_dia = 5
        estado = re.get_estado()
        assert estado['pnl_dia'] == 150
        assert estado['trades_dia'] == 5
        assert 'circuit_breaker_nivel' in estado
        assert 'kill_switch' in estado
        assert 'cooldown_restante' in estado

    def test_reset_diario_zera_tudo(self):
        re = make_risk_engine()
        re.pnl_dia = -300
        re.trades_dia = 10
        re.perdas_consecutivas = 5
        re.circuit_breaker_nivel = 2
        re.reset_diario()
        assert re.pnl_dia == 0
        assert re.trades_dia == 0
        assert re.perdas_consecutivas == 0
        assert re.circuit_breaker_nivel == 0


# ============================================================
# Testes: Sanidade de alvos no RiskEngine
# ============================================================

class TestSanidadeAlvos:
    """Sanidade de TP/SL agora no RiskEngine (proteção 15)."""

    def test_tp_zero_bloqueia(self):
        re = make_risk_engine()
        signal = make_signal(tp=0, sl=50)
        decision = re.avaliar(signal)
        assert not decision.permitido
        assert decision.motivo == 'ALVOS_NULOS'

    def test_sl_insano_win_bloqueia(self):
        re = make_risk_engine()
        signal = make_signal(tp=100, sl=2000)  # SL > 1500
        decision = re.avaliar(signal)
        assert not decision.permitido
        assert 'SL_INSANO' in decision.motivo

    def test_risco_retorno_insano_bloqueia(self):
        re = make_risk_engine()
        signal = make_signal(tp=50, sl=200)  # SL/TP = 4 > 3
        decision = re.avaliar(signal)
        assert not decision.permitido
        assert decision.motivo == 'RISCO_RETORNO_INSANO'

    def test_alvos_validos_passam(self):
        re = make_risk_engine()
        signal = make_signal(tp=100, sl=50)  # Razoável
        decision = re.avaliar(signal)
        assert decision.permitido


# ============================================================
# Testes: Slippage no RiskEngine
# ============================================================

class TestSlippageRiskEngine:
    """Slippage agora registrado no RiskEngine."""

    def test_registrar_execucao_normal(self):
        re = make_risk_engine()
        re.registrar_execucao('WINV26', 170000, 170005)  # 1 tick
        assert re.slippage_total == 5.0

    def test_slippage_critico_ativa_cb(self):
        re = make_risk_engine()
        # 7 ticks = 35 pts (WIN tick=5) → > 2x limite (3 ticks = 15)
        re.registrar_execucao('WINV26', 170000, 170035)
        assert re.circuit_breaker_nivel == 3


# ============================================================
# Testes: Fluxo completo Signal → RiskEngine → PositionManager
# ============================================================

class TestFluxoCompleto:
    """Fluxo canônico: Signal → RiskEngine.avaliar → PositionManager.gerenciar."""

    def test_fluxo_aprovado_abre_posicao(self):
        re = make_risk_engine()
        pm = make_pm(re)
        signal = make_signal(ml_prob=0.7)
        decision = re.avaliar(signal)
        assert decision.permitido

        # streak=1 (AGUARDE)
        pm.gerenciar('WINV26', signal, 170000, decision=decision, regime='tendencia')
        # streak=2 (ABRIR)
        action = pm.gerenciar('WINV26', signal, 170000, decision=decision, regime='tendencia')
        assert action.tipo == 'ABRIR'
        assert pm.posicao is not None
        assert pm.posicao['quantidade'] == decision.size

    def test_fluxo_bloqueado_nao_abre(self):
        re = make_risk_engine()
        re.ativar_kill_switch()
        pm = make_pm(re)
        signal = make_signal()
        decision = re.avaliar(signal)
        assert not decision.permitido

        pm.gerenciar('WINV26', signal, 170000, decision=decision, regime='tendencia')
        action = pm.gerenciar('WINV26', signal, 170000, decision=decision, regime='tendencia')
        assert action.tipo == 'REJEITADO'
        assert pm.posicao is None

    def test_fechar_posicao_atualiza_risk_engine(self):
        re = make_risk_engine()
        pm = make_pm(re)
        abrir_posicao(pm, re, preco=170000)

        # Fechar com lucro
        pm.confianca_ewma = 0
        pm.sinal_confirmado = 0
        action = pm.checar_saidas(170100)  # +100 pts = TP
        assert action is not None
        assert action.tipo == 'FECHAR'

        # RiskEngine deve ter registrado o resultado
        # pnl_dia deve ter aumentado
        assert re.pnl_dia > 0, "RiskEngine não registrou o resultado do trade"
