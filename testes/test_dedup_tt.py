# -*- coding: utf-8 -*-
"""
testes/test_dedup_tt.py — Testes de deduplicacao de T&T (Fase 1).

Testa a logica de dedup do ProfitRTDAdapter sem precisar de COM.
Simula o fluxo: RefreshData -> parse -> dedup -> MarketEvent.

Cobertura:
  1. Mesmo trade recebido 1x -> 1 evento
  2. Mesmo trade recebido 10x -> 1 evento
  3. 10 trades distintos -> 10 eventos
  4. Trades iguais exceto timestamp -> verificar comportamento
  5. Trades iguais exceto AGAG -> nao eliminar indevidamente
  6. Multiplos ativos -> dedup independente
  7. Reinicializacao do adapter -> dedup resetado
  8. Limite de memoria (LRU eviction)
"""

import pytest
from collections import defaultdict, OrderedDict


# ============================================================
#  Helper: simular a logica de dedup do adapter
# ============================================================

class DedupSimulator:
    """Simula a logica de dedup do ProfitRTDAdapter sem COM."""

    def __init__(self, max_per_ativo=50000):
        self._vistos_tt = defaultdict(OrderedDict)
        self._baseline_pending = defaultdict(lambda: True)
        self._max = max_per_ativo
        self.eventos = []

    def _fazer_assinatura(self, cell):
        """Replica a assinatura do adapter: (DAT, ACP, PRE, QUL, AVD, AGR, AGAG)."""
        from adapters.rtd_connection import sstr, fnum, fint
        pre = fnum(cell.get('PRE'))
        qtd = fint(cell.get('QUL'))
        return (
            sstr(cell.get('DAT')),
            sstr(cell.get('ACP')),
            pre,
            qtd,
            sstr(cell.get('AVD')),
            sstr(cell.get('AGR')),
            sstr(cell.get('AGAG')),
        )

    def processar_linha_tt(self, sym, cell):
        """Simula o processamento de uma linha T&T no adapter.
        Retorna True se o evento foi emitido, False se descartado."""
        pre = cell.get('PRE', 0)
        if not pre or float(pre) <= 0:
            return False
        qtd = cell.get('QUL', 0)
        if not qtd or int(qtd) <= 0:
            return False

        sig = self._fazer_assinatura(cell)

        # Baseline: primeiro refresh absorve
        if self._baseline_pending[sym]:
            self._vistos_tt[sym][sig] = True
            return False

        # Dedup
        if sig in self._vistos_tt[sym]:
            return False  # Duplicata

        # Marcar como visto (LRU)
        vistos = self._vistos_tt[sym]
        vistos[sig] = True
        if len(vistos) > self._max:
            vistos.popitem(last=False)

        self.eventos.append({'sym': sym, 'cell': dict(cell)})
        return True

    def desativar_baseline(self, ativos=None):
        """Simula o fim do primeiro RefreshData.
        
        Args:
            ativos: lista de ativos para desativar o baseline.
                    Se None, desativa para WINV26 e WDOU26 (default).
        """
        if ativos is None:
            ativos = ['WINV26', 'WDOU26', 'INDV26', 'DOLU26']
        for s in ativos:
            self._baseline_pending[s] = False

    def reset(self):
        """Reseta estado de dedup."""
        self._vistos_tt = defaultdict(OrderedDict)
        self._baseline_pending = defaultdict(lambda: True)
        self.eventos = []


def make_cell(dat='09:00:00.123', acp='BTG', pre=177500, qul=10,
              avd='XP', agr='Comprador', agag=''):
    """Cria uma celula T&T com valores default."""
    return {
        'DAT': dat, 'ACP': acp, 'PRE': pre, 'QUL': qul,
        'AVD': avd, 'AGR': agr, 'AGAG': agag,
    }


# ============================================================
#  TESTES
# ============================================================

class TestDedupBasico:
    """Testes basicos de deduplicacao."""

    def test_mesmo_trade_1x_gera_1_evento(self):
        """Mesmo trade recebido 1x -> 1 evento."""
        sim = DedupSimulator()
        sim.desativar_baseline()

        cell = make_cell()
        emitido = sim.processar_linha_tt('WINV26', cell)

        assert emitido is True
        assert len(sim.eventos) == 1

    def test_mesmo_trade_10x_gera_1_evento(self):
        """Mesmo trade recebido 10x -> 1 evento."""
        sim = DedupSimulator()
        sim.desativar_baseline()

        cell = make_cell()
        for i in range(10):
            sim.processar_linha_tt('WINV26', cell)

        assert len(sim.eventos) == 1, f"Esperado 1 evento, obtido {len(sim.eventos)}"

    def test_10_trades_distintos_geram_10_eventos(self):
        """10 trades distintos -> 10 eventos."""
        sim = DedupSimulator()
        sim.desativar_baseline()

        for i in range(10):
            cell = make_cell(dat=f'09:00:0{i}.000', pre=177500 + i * 5)
            sim.processar_linha_tt('WINV26', cell)

        assert len(sim.eventos) == 10, f"Esperado 10 eventos, obtido {len(sim.eventos)}"

    def test_trade_repetido_entre_distintos(self):
        """Trades distintos intercalados com repeticoes."""
        sim = DedupSimulator()
        sim.desativar_baseline()

        # Trade 1 (novo)
        cell1 = make_cell(dat='09:00:00.000', pre=177500)
        assert sim.processar_linha_tt('WINV26', cell1) is True

        # Trade 2 (novo)
        cell2 = make_cell(dat='09:00:01.000', pre=177505)
        assert sim.processar_linha_tt('WINV26', cell2) is True

        # Trade 1 repetido (descartado)
        assert sim.processar_linha_tt('WINV26', cell1) is False

        # Trade 3 (novo)
        cell3 = make_cell(dat='09:00:02.000', pre=177510)
        assert sim.processar_linha_tt('WINV26', cell3) is True

        # Trade 2 repetido (descartado)
        assert sim.processar_linha_tt('WINV26', cell2) is False

        assert len(sim.eventos) == 3


class TestDedupTimestamp:
    """Testes com variacao de timestamp."""

    def test_trades_iguais_exceto_timestamp_sao_distintos(self):
        """Trades com mesmos campos mas DAT (timestamp) diferente
        sao considerados distintos — nao devem ser eliminados."""
        sim = DedupSimulator()
        sim.desativar_baseline()

        cell1 = make_cell(dat='09:00:00.000')
        cell2 = make_cell(dat='09:00:01.000')  # mesmo trade, 1s depois

        sim.processar_linha_tt('WINV26', cell1)
        sim.processar_linha_tt('WINV26', cell2)

        # São distintos porque o DAT faz parte da assinatura
        assert len(sim.eventos) == 2, "Trades com DAT diferente devem ser distintos"

    def test_mesmo_dat_mesmo_trade(self):
        """Trades com o mesmo DAT e mesmos campos sao a mesma operacao."""
        sim = DedupSimulator()
        sim.desativar_baseline()

        cell = make_cell(dat='09:00:00.000')
        sim.processar_linha_tt('WINV26', cell)
        sim.processar_linha_tt('WINV26', cell)
        sim.processar_linha_tt('WINV26', cell)

        assert len(sim.eventos) == 1


class TestDedupAGAG:
    """Testes com variacao de AGAG (agressor agregado)."""

    def test_trades_iguais_exceto_agag_nao_eliminados(self):
        """Trades com mesmos campos mas AGAG diferente nao devem ser
        eliminados indevidamente. AGAG pode indicar direto vs carteira."""
        sim = DedupSimulator()
        sim.desativar_baseline()

        cell1 = make_cell(agag='DIRETO')
        cell2 = make_cell(agag='CARTEIRA')

        sim.processar_linha_tt('WINV26', cell1)
        sim.processar_linha_tt('WINV26', cell2)

        assert len(sim.eventos) == 2, (
            "Trades com AGAG diferente devem ser considerados distintos"
        )

    def test_agag_vazio_vs_preenchido(self):
        """AGAG vazio vs preenchido deve ser distinto."""
        sim = DedupSimulator()
        sim.desativar_baseline()

        cell1 = make_cell(agag='')
        cell2 = make_cell(agag='DIRETO')

        sim.processar_linha_tt('WINV26', cell1)
        sim.processar_linha_tt('WINV26', cell2)

        assert len(sim.eventos) == 2


class TestDedupMultiAtivo:
    """Testes com multiplos ativos."""

    def test_dedup_independente_por_ativo(self):
        """Mesma assinatura em ativos diferentes -> 2 eventos (1 por ativo)."""
        sim = DedupSimulator()
        sim.desativar_baseline()

        cell = make_cell()

        sim.processar_linha_tt('WINV26', cell)
        sim.processar_linha_tt('WDOU26', cell)
        sim.processar_linha_tt('WINV26', cell)  # duplicata WIN
        sim.processar_linha_tt('WDOU26', cell)  # duplicata WDO

        assert len(sim.eventos) == 2, f"Esperado 2 eventos (1 por ativo), obtido {len(sim.eventos)}"

    def test_repeticao_em_um_ativo_nao_afeta_outro(self):
        """Repetir trade em WINV26 10x nao bloqueia o mesmo trade em WDOU26."""
        sim = DedupSimulator()
        sim.desativar_baseline()

        cell = make_cell()

        for _ in range(10):
            sim.processar_linha_tt('WINV26', cell)

        emitido_wdo = sim.processar_linha_tt('WDOU26', cell)
        assert emitido_wdo is True
        assert len(sim.eventos) == 2


class TestDedupReinicializacao:
    """Testes de reinicializacao do adapter."""

    def test_reset_dedup_permite_reemissao(self):
        """Apos reset, o mesmo trade pode ser emitido novamente."""
        sim = DedupSimulator()
        sim.desativar_baseline()

        cell = make_cell()
        sim.processar_linha_tt('WINV26', cell)
        assert len(sim.eventos) == 1

        # Repeticao descartada
        sim.processar_linha_tt('WINV26', cell)
        assert len(sim.eventos) == 1

        # Reset
        sim.reset()
        sim.desativar_baseline()

        # Mesmo trade agora e emitido novamente
        sim.processar_linha_tt('WINV26', cell)
        assert len(sim.eventos) == 1  # 1 evento novo apos reset


class TestDedupMemoria:
    """Testes de controle de memoria (LRU eviction)."""

    def test_lru_eviction_mantem_limite(self):
        """A estrutura de dedup nao cresce indefinidamente."""
        sim = DedupSimulator(max_per_ativo=5)
        sim.desativar_baseline()

        # Inserir 10 trades distintos (limite e 5)
        for i in range(10):
            cell = make_cell(dat=f'09:00:0{i}.000', pre=177500 + i)
            sim.processar_linha_tt('WINV26', cell)

        # A estrutura deve ter no maximo 5 assinaturas
        assert len(sim._vistos_tt['WINV26']) <= 5

    def test_lru_eviction_nao_bloqueia_trades_novos(self):
        """Apos eviction, trades novos ainda sao emitidos."""
        sim = DedupSimulator(max_per_ativo=3)
        sim.desativar_baseline()

        for i in range(3):
            cell = make_cell(dat=f'09:00:0{i}.000', pre=177500 + i)
            sim.processar_linha_tt('WINV26', cell)

        assert len(sim.eventos) == 3

        # O 4o trade deve ser emitido (estrutura ja fez eviction)
        cell4 = make_cell(dat='09:00:03.000', pre=177503)
        emitido = sim.processar_linha_tt('WINV26', cell4)
        assert emitido is True
        assert len(sim.eventos) == 4

    def test_lru_eviction_descarta_mais_antigo(self):
        """O trade mais antigo e removido primeiro (LRU)."""
        sim = DedupSimulator(max_per_ativo=2)
        sim.desativar_baseline()

        cell1 = make_cell(dat='09:00:00.000', pre=177500)
        cell2 = make_cell(dat='09:00:01.000', pre=177501)
        sim.processar_linha_tt('WINV26', cell1)
        sim.processar_linha_tt('WINV26', cell2)

        # Inserir 3o -> cell1 deve ser removido da estrutura
        cell3 = make_cell(dat='09:00:02.000', pre=177502)
        sim.processar_linha_tt('WINV26', cell3)

        # cell1 foi removido da estrutura, entao se for reenviado
        # deve ser considerado NOVO (emitido novamente)
        # Isso e o comportamento esperado do LRU: trades muito antigos
        # podem ser reemitidos se ja foram evictados.
        emitido = sim.processar_linha_tt('WINV26', cell1)
        assert emitido is True, (
            "Trade removido por LRU eviction deve ser reemitido se reenviado"
        )


class TestDedupBaseline:
    """Testes do baseline (primeiro refresh)."""

    def test_baseline_descarta_primeiro_refresh(self):
        """O primeiro refresh absorve dados como baseline, sem emitir eventos."""
        sim = DedupSimulator()
        # NAO desativa baseline

        cell = make_cell()
        emitido = sim.processar_linha_tt('WINV26', cell)
        assert emitido is False, "Baseline deve absorver sem emitir"

    def test_apos_baseline_segundo_refresh_emite(self):
        """Apos o baseline, o segundo refresh de um trade novo emite evento."""
        sim = DedupSimulator()

        cell1 = make_cell(dat='09:00:00.000')
        sim.processar_linha_tt('WINV26', cell1)  # absorvido pelo baseline

        sim.desativar_baseline()

        # Mesmo trade no segundo refresh -> descartado (ja no baseline)
        assert sim.processar_linha_tt('WINV26', cell1) is False

        # Trade novo -> emitido
        cell2 = make_cell(dat='09:00:01.000', pre=177505)
        assert sim.processar_linha_tt('WINV26', cell2) is True

    def test_baseline_independente_por_ativo(self):
        """Baseline e processado independentemente por ativo."""
        sim = DedupSimulator()

        cell = make_cell()
        sim.processar_linha_tt('WINV26', cell)  # baseline WIN
        sim.processar_linha_tt('WDOU26', cell)  # baseline WDO

        sim.desativar_baseline()

        # Ambos tem o trade no baseline
        assert sim.processar_linha_tt('WINV26', cell) is False
        assert sim.processar_linha_tt('WDOU26', cell) is False
