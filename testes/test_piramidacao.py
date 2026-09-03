# -*- coding: utf-8 -*-
"""
testes/test_piramidacao.py — Testes do bug de piramidação (Fase 5).

Testa a máquina de estados:
  PIRAMIDAÇÃO_SOLICITADA → VALIDAR → APROVADA → EXECUTAR → ATUALIZAR_POSIÇÃO
  PIRAMIDAÇÃO_SOLICITADA → VALIDAR → REJEITADA → NÃO_ALTERAR_POSIÇÃO

Cobertura:
  1. ML < threshold → piramidação rejeitada (qtd_add não definido)
  2. ML >= threshold → piramidação aprovada
  3. Lucro insuficiente → piramidação não solicitada
  4. Quantidade máxima atingida → piramidação não solicitada
  5. Lado contrário → piramidação não solicitada
  6. Posição inexistente → abre nova (não piramida)
  7. Stop/preço médio NÃO mudam quando rejeitada
  8. Stop/preço médio mudam quando aprovada
  9. NameError não ocorre quando ML bloqueia
"""

import time
import pytest
from unittest.mock import MagicMock

from core.position_manager import PositionManager
from core.contracts import Action, Signal, RiskDecision


# ============================================================
# Fixtures
# ============================================================

def make_pm(**overrides):
    config = {
        'confirmacao_necessaria': 1,
        'limiar_confirmacao': 0.50,
        'confianca_piramidacao': 0.80,
        'ml_threshold_piramidacao': 0.65,
        'pnl_min_piramidacao': 50,
        'position_sizing': {'max_position_size': 5},
        'ml_sizing': False,
        'reversao_fecha': False,
        'usar_trailing_mfe': False,
        'tempo_max_posicao_s': 0,  # desativa timeout
        'horario_fechamento': (23, 59),
        'desligar_horarios_ruins': False,
        'cooldown_entre_trades_ms': 0,
    }
    config.update(overrides)
    risk = MagicMock()
    risk.trades_dia = 0
    risk.pode_abrir.return_value = RiskDecision(
        symbol='WINV26', timestamp_ms=0,
        permitido=True, motivo='OK', size=1, tp=1000, sl=50
    )
    risk.custo = 5
    learning = MagicMock()
    learning.previsoes = []
    learning.resultados = []
    pm = PositionManager(risk, persistence=None, learning=learning,
                         config=config, ativo_principal='WINV26')
    return pm


def make_signal(lado='C', ml_prob=0.5, preco_ref=170000):
    return Signal(
        lado=lado,
        preco_ref=preco_ref,
        score=0.8,
        confianca=0.9,
        ml_prob=ml_prob,
        motivos=['TESTE'],
        contrib=[],
        tp=1000,
        sl=50,
    )


def abrir_posicao(pm, preco=170000, ml_prob=0.5, lado='C'):
    """Abre uma posição para depois testar piramidação.
    Chama gerenciar() duas vezes para satisfazer _sinal_streak >= 2.
    Injeta RiskDecision (Fase 6: PositionManager não decide risco).
    """
    pm.confianca_ewma = 0.9
    signal = make_signal(lado=lado, ml_prob=ml_prob, preco_ref=preco)
    decision = RiskDecision(
        symbol='WINV26', timestamp_ms=0,
        permitido=True, motivo='OK', size=1, tp=1000, sl=50,
    )
    # Primeira chamada: estabelece streak=1 (AGUARDE)
    pm.gerenciar('WINV26', signal, preco, decision=decision, regime='tendencia')
    # Segunda chamada: streak=2, abre posição
    action = pm.gerenciar('WINV26', signal, preco, decision=decision, regime='tendencia')
    assert action.tipo == 'ABRIR', f"Esperava ABRIR, got {action.tipo}"
    assert pm.posicao is not None
    return pm.posicao


# ============================================================
# Testes
# ============================================================

class TestPiramidacaoRejeitadaML:
    """Piramidação solicitada mas ML bloqueia."""

    def test_ml_abaixo_threshold_nao_piramida(self):
        """ML=0.5 < threshold=0.65 → piramidação rejeitada."""
        pm = make_pm()
        pos = abrir_posicao(pm, preco=170000, ml_prob=0.5)

        # Simular preço subiu 100 pts (pnl_atual = 100 >= 50)
        pm.confianca_ewma = 0.90  # >= conf_piramide (0.80)
        signal = make_signal(lado='C', ml_prob=0.5, preco_ref=170100)
        action = pm.gerenciar('WINV26', signal, 170100, regime='tendencia')

        # Posição não piramidou
        assert pos['quantidade'] == 1, "Quantidade não deveria mudar"
        assert pos['preco_medio'] == 170000, "Preço médio não deveria mudar"

    def test_ml_abaixo_threshold_nao_altera_stop(self):
        """Stop NÃO deve mudar quando piramidação rejeitada por ML."""
        pm = make_pm()
        pos = abrir_posicao(pm, preco=170000, ml_prob=0.5)
        stop_original = pos['stop_preco']

        pm.confianca_ewma = 0.90
        signal = make_signal(lado='C', ml_prob=0.5, preco_ref=170100)
        pm.gerenciar('WINV26', signal, 170100, regime='tendencia')

        assert pos['stop_preco'] == stop_original, \
            f"Stop mudou de {stop_original} para {pos['stop_preco']} mesmo com piramidação rejeitada"

    def test_ml_abaixo_threshold_nao_altera_preco_medio(self):
        """Preço médio NÃO deve mudar quando piramidação rejeitada."""
        pm = make_pm()
        pos = abrir_posicao(pm, preco=170000, ml_prob=0.5)
        pm_original = pos['preco_medio']

        pm.confianca_ewma = 0.90
        signal = make_signal(lado='C', ml_prob=0.5, preco_ref=170100)
        pm.gerenciar('WINV26', signal, 170100, regime='tendencia')

        assert pos['preco_medio'] == pm_original, \
            f"Preço médio mudou de {pm_original} para {pos['preco_medio']}"

    def test_nao_ocorre_nameerror(self):
        """Garantir que qtd_add/nova_qtd não geram NameError quando ML bloqueia."""
        pm = make_pm()
        abrir_posicao(pm, preco=170000, ml_prob=0.5)

        pm.confianca_ewma = 0.90
        signal = make_signal(lado='C', ml_prob=0.5, preco_ref=170100)
        # Se houvesse NameError, isto lançaria exceção
        action = pm.gerenciar('WINV26', signal, 170100, regime='tendencia')
        assert action is not None

    def test_motivo_registra_rejeicao(self):
        """Motivo deve registrar PIRAMIDE_REJEITADA quando ML bloqueia."""
        pm = make_pm()
        pos = abrir_posicao(pm, preco=170000, ml_prob=0.5)

        pm.confianca_ewma = 0.90
        signal = make_signal(lado='C', ml_prob=0.5, preco_ref=170100)
        pm.gerenciar('WINV26', signal, 170100, regime='tendencia')

        motivos_str = ' '.join(pos['motivos'])
        assert 'PIRAMIDE_REJEITADA' in motivos_str, \
            f"Motivo de rejeição não registrado: {pos['motivos']}"


class TestPiramidacaoAprovada:
    """Piramidação aprovada (ML >= threshold)."""

    def test_ml_acima_threshold_piramida(self):
        """ML=0.75 >= threshold=0.65 → piramidação aprovada."""
        pm = make_pm()
        pos = abrir_posicao(pm, preco=170000, ml_prob=0.75)

        pm.confianca_ewma = 0.90
        signal = make_signal(lado='C', ml_prob=0.75, preco_ref=170100)
        action = pm.gerenciar('WINV26', signal, 170100, regime='tendencia')

        assert pos['quantidade'] == 2, f"Esperava qtd=2, got {pos['quantidade']}"

    def test_preco_medio_atualizado(self):
        """Preço médio deve ser recalculado após piramidação aprovada."""
        pm = make_pm()
        pos = abrir_posicao(pm, preco=170000, ml_prob=0.75)

        pm.confianca_ewma = 0.90
        signal = make_signal(lado='C', ml_prob=0.75, preco_ref=170100)
        pm.gerenciar('WINV26', signal, 170100, regime='tendencia')

        # PM = (170000 * 1 + 170100 * 1) / 2 = 170050
        assert pos['preco_medio'] == 170050.0, \
            f"Preço médio errado: {pos['preco_medio']}"

    def test_stop_atualizado_para_breakeven(self):
        """Stop deve ir para breakeven do novo preço médio após piramidação."""
        pm = make_pm()
        pos = abrir_posicao(pm, preco=170000, ml_prob=0.75)

        pm.confianca_ewma = 0.90
        signal = make_signal(lado='C', ml_prob=0.75, preco_ref=170100)
        pm.gerenciar('WINV26', signal, 170100, regime='tendencia')

        assert pos['stop_preco'] == 170050.0, \
            f"Stop deveria ser 170050 (breakeven do novo PM), got {pos['stop_preco']}"

    def test_motivo_registra_aprovacao(self):
        """Motivo deve registrar PIRAMIDE_CONF quando aprovada."""
        pm = make_pm()
        pos = abrir_posicao(pm, preco=170000, ml_prob=0.75)

        pm.confianca_ewma = 0.90
        signal = make_signal(lado='C', ml_prob=0.75, preco_ref=170100)
        pm.gerenciar('WINV26', signal, 170100, regime='tendencia')

        motivos_str = ' '.join(pos['motivos'])
        assert 'PIRAMIDE_CONF' in motivos_str


class TestCondicoesPiramidacao:
    """Condições que impedem piramidação."""

    def test_lucro_insuficiente_nao_piramida(self):
        """PnL < 50 → piramidação não é solicitada."""
        pm = make_pm()
        pos = abrir_posicao(pm, preco=170000, ml_prob=0.90)

        # Preço subiu só 30 pts (< 50 mínimo)
        pm.confianca_ewma = 0.90
        signal = make_signal(lado='C', ml_prob=0.90, preco_ref=170030)
        pm.gerenciar('WINV26', signal, 170030, regime='tendencia')

        assert pos['quantidade'] == 1, "Não deveria piramidar com lucro < 50"
        assert pos['preco_medio'] == 170000

    def test_quantidade_maxima_nao_piramida(self):
        """Qtd já no máximo → piramidação não é solicitada."""
        pm = make_pm(position_sizing={'max_position_size': 2})
        pos = abrir_posicao(pm, preco=170000, ml_prob=0.90)

        # Primeira piramidação (qtd 1→2)
        pm.confianca_ewma = 0.90
        signal = make_signal(lado='C', ml_prob=0.90, preco_ref=170100)
        pm.gerenciar('WINV26', signal, 170100, regime='tendencia')
        assert pos['quantidade'] == 2

        # Segunda piramidação — deve bloquear (qtd=2 == max=2)
        signal2 = make_signal(lado='C', ml_prob=0.90, preco_ref=170200)
        pm.gerenciar('WINV26', signal2, 170200, regime='tendencia')
        assert pos['quantidade'] == 2, "Não deveria exceder max_position_size"

    def test_lado_contrario_nao_piramida(self):
        """Sinal de venda com posição de compra → não piramida."""
        pm = make_pm()
        pos = abrir_posicao(pm, preco=170000, ml_prob=0.90, lado='C')

        # Sinal contrário (Venda)
        pm.confianca_ewma = 0.90
        signal = make_signal(lado='V', ml_prob=0.90, preco_ref=170100)
        pm.gerenciar('WINV26', signal, 170100, regime='tendencia')

        assert pos['quantidade'] == 1, "Não deveria piramidar no lado contrário"
        assert pos['preco_medio'] == 170000

    def test_confianca_baixa_nao_piramida(self):
        """Confiança < conf_piramide → piramidação não solicitada."""
        pm = make_pm(confianca_piramidacao=0.85)
        pos = abrir_posicao(pm, preco=170000, ml_prob=0.90)

        # Confiança baixa (0.50 < 0.85)
        pm.confianca_ewma = 0.50
        signal = make_signal(lado='C', ml_prob=0.90, preco_ref=170100)
        pm.gerenciar('WINV26', signal, 170100, regime='tendencia')

        assert pos['quantidade'] == 1
        assert pos['preco_medio'] == 170000


class TestSemPosicao:
    """Quando não há posição aberta."""

    def test_sem_posicao_abre_nova(self):
        """Sem posição + sinal válido → abre nova (não piramida)."""
        pm = make_pm()
        pm.confianca_ewma = 0.90
        signal = make_signal(lado='C', ml_prob=0.70, preco_ref=170000)
        decision = RiskDecision(
            symbol='WINV26', timestamp_ms=0,
            permitido=True, motivo='OK', size=1, tp=1000, sl=50,
        )
        # Primeira chamada: streak=1
        pm.gerenciar('WINV26', signal, 170000, decision=decision, regime='tendencia')
        # Segunda chamada: streak=2, abre
        action = pm.gerenciar('WINV26', signal, 170000, decision=decision, regime='tendencia')

        assert action.tipo == 'ABRIR'
        assert pm.posicao is not None
        assert pm.posicao['quantidade'] == 1


class TestStateMachine:
    """Validar que a máquina de estados é determinística."""

    def test_rejeitada_nao_altera_nada(self):
        """Após rejeição, NENHUM campo da posição muda."""
        pm = make_pm()
        pos = abrir_posicao(pm, preco=170000, ml_prob=0.5)

        snapshot_antes = dict(pos)
        pm.confianca_ewma = 0.90
        signal = make_signal(lado='C', ml_prob=0.5, preco_ref=170100)
        pm.gerenciar('WINV26', signal, 170100, regime='tendencia')

        # Quantidade e preço médio não mudam
        assert pos['quantidade'] == snapshot_antes['quantidade']
        assert pos['preco_medio'] == snapshot_antes['preco_medio']
        assert pos['stop_preco'] == snapshot_antes['stop_preco']

    def test_aprovada_altera_tudo(self):
        """Após aprovação, quantidade, PM e stop mudam."""
        pm = make_pm()
        pos = abrir_posicao(pm, preco=170000, ml_prob=0.80)

        qtd_antes = pos['quantidade']
        pm_antes = pos['preco_medio']
        stop_antes = pos['stop_preco']

        pm.confianca_ewma = 0.90
        signal = make_signal(lado='C', ml_prob=0.80, preco_ref=170100)
        pm.gerenciar('WINV26', signal, 170100, regime='tendencia')

        assert pos['quantidade'] != qtd_antes, "Quantidade deveria mudar"
        assert pos['preco_medio'] != pm_antes, "Preço médio deveria mudar"
        assert pos['stop_preco'] != stop_antes, "Stop deveria mudar"

    def test_multiplas_piramidacoes_alternadas(self):
        """Aprova, rejeita, aprova — estado correto a cada passo.

        A piramidação usa pos['ml_prob'] (setado na abertura, não muda
        com o sinal atual). Para rejeitar a piramidação no passo 2,
        usamos confianca baixa (abaixo de conf_piramide).
        """
        pm = make_pm(position_sizing={'max_position_size': 5},
                     confianca_piramidacao=0.85)
        pos = abrir_posicao(pm, preco=170000, ml_prob=0.80)

        # Passo 1: Aprovada (conf=0.90 >= 0.85, ML=0.80 na pos, pnl=100)
        pm.confianca_ewma = 0.90
        pm.gerenciar('WINV26', make_signal(lado='C', ml_prob=0.80, preco_ref=170100),
                     170100, regime='tendencia')
        assert pos['quantidade'] == 2

        # Passo 2: Rejeitada (conf=0.50 < 0.85 → piramidação não solicitada)
        pm.confianca_ewma = 0.50
        pm.gerenciar('WINV26', make_signal(lado='C', ml_prob=0.80, preco_ref=170200),
                     170200, regime='tendencia')
        assert pos['quantidade'] == 2, "Não deveria piramidar após rejeição"
        assert pos['preco_medio'] == 170050.0, "PM não deveria mudar após rejeição"

        # Passo 3: Aprovada novamente (conf=0.90 >= 0.85)
        pm.confianca_ewma = 0.90
        pm.gerenciar('WINV26', make_signal(lado='C', ml_prob=0.80, preco_ref=170300),
                     170300, regime='tendencia')
        assert pos['quantidade'] == 3, "Deveria piramidar novamente"
