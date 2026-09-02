# -*- coding: utf-8 -*-
"""
test_lote_coerencia.py — Testes da coerência de lote no RTD adapter (v14.3).

Valida que linhas "Frankenstein" (campos de ciclos RefreshData diferentes,
criadas pelo shift de linhas da janela T&T) não são emitidas como trades.

Cenário coberto:
  1. DAT e PRE/QUL do mesmo ciclo → trade emitido
  2. DAT de ciclo novo + PRE/QUL de ciclo antigo (shift) → BLOQUEADO
  3. Campos chegam em ciclos separados mas convergem → emitido quando coerente
  4. Dedup por assinatura continua funcionando (mesmo trade não re-emitido)
  5. Trades distintos no mesmo lote → todos emitidos
"""

import sys
import os
from collections import OrderedDict, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.rtd_connection import sstr, fnum, fint


class FakeAdapter:
    """Réplica mínima da lógica de lote do ProfitRTDAdapter para teste."""

    def __init__(self, max_per_ativo=50000):
        self._vistos_tt = defaultdict(OrderedDict)
        self._baseline_pending = defaultdict(lambda: True)
        self._cell_lote = defaultdict(dict)
        self._lote_atual = 0
        self._max = max_per_ativo
        self.eventos = []

    def processar_campo(self, sym, linha, field, val):
        """Simula a chegada de 1 campo num ciclo RefreshData.

        Retorna True se um trade foi emitido (gatilho DAT coerente).
        """
        cell = self._cell_lote.setdefault((sym, linha), {}).setdefault('_cell', {})
        lotes = self._cell_lote[(sym, linha)]
        cell[field] = val
        lotes[field] = self._lote_atual

        if field != 'DAT':
            return False

        # Coerência de lote (replica o adapter)
        lote_dat = lotes.get('DAT', 0)
        if lotes.get('PRE', 0) != lote_dat or lotes.get('QUL', 0) != lote_dat:
            return False  # Frankenstein — aguardar coerência

        pre = fnum(cell.get('PRE'))
        if pre <= 0:
            return False
        qtd = fint(cell.get('QUL'))
        if qtd <= 0:
            return False

        sig = (
            sstr(cell.get('DAT')),
            sstr(cell.get('ACP')),
            pre,
            qtd,
            sstr(cell.get('AVD')),
            sstr(cell.get('AGR')),
            sstr(cell.get('AGAG')),
        )

        if self._baseline_pending[sym]:
            self._vistos_tt[sym][sig] = True
            return False
        if sig in self._vistos_tt[sym]:
            return False

        vistos = self._vistos_tt[sym]
        vistos[sig] = True
        if len(vistos) > self._max:
            vistos.popitem(last=False)

        self.eventos.append({'sym': sym, 'dat': sstr(cell.get('DAT')), 'pre': pre, 'qtd': qtd})
        return True

    def novo_ciclo(self):
        """Simula o início de um novo ciclo RefreshData."""
        self._lote_atual += 1

    def fechar_baseline(self):
        self._baseline_pending = defaultdict(lambda: False)


def _cell(dat='10:30:00.000', acp='XP', pre=187700.0, qtd=5, avd='BTG', agr='comprador', agag='dir'):
    return {'DAT': dat, 'ACP': acp, 'PRE': pre, 'QUL': qtd, 'AVD': avd, 'AGR': agr, 'AGAG': agag}


class TestLoteCoerencia:
    def test_mesmo_lote_emite_trade(self):
        """DAT+PRE+QUL chegaram no mesmo ciclo → trade emitido."""
        a = FakeAdapter()
        a.fechar_baseline()
        a.novo_ciclo()
        c = _cell()
        for f, v in c.items():
            a.processar_campo('WINV26', 1, f, v)
        assert len(a.eventos) == 1, f"Esperado 1 trade, got {len(a.eventos)}"

    def test_shift_frankenstein_bloqueado(self):
        """DAT novo + PRE/QUL antigos (shift de linha) → BLOQUEADO."""
        a = FakeAdapter()
        a.fechar_baseline()

        # Ciclo 1: linha recebe PRE/QUL do trade antigo Z
        a.novo_ciclo()
        a.processar_campo('WINV26', 4, 'PRE', 187650.0)
        a.processar_campo('WINV26', 4, 'QUL', 3)

        # Ciclo 2: shift — DAT do trade X chega na linha 4, PRE/QUL continuam Z
        a.novo_ciclo()
        emitido = a.processar_campo('WINV26', 4, 'DAT', '10:31:00.000')

        assert not emitido, "Frankenstein (DAT novo + PRE/QUL antigos) deveria ser bloqueado"
        assert len(a.eventos) == 0

    def test_campos_separados_convergem_emite(self):
        """PRE/QUL de ciclo anterior + DAT do MESMO ciclo que os atualiza → emite."""
        a = FakeAdapter()
        a.fechar_baseline()

        # Ciclo 1: PRE/QUL do trade X
        a.novo_ciclo()
        a.processar_campo('WINV26', 2, 'PRE', 187700.0)
        a.processar_campo('WINV26', 2, 'QUL', 5)

        # Ciclo 2: RTD reenvia DAT do X (sem shift) — todos agora no ciclo 2
        a.novo_ciclo()
        a.processar_campo('WINV26', 2, 'PRE', 187700.0)
        a.processar_campo('WINV26', 2, 'QUL', 5)
        emitido = a.processar_campo('WINV26', 2, 'DAT', '10:30:00.000')

        assert emitido, "Trade coerente deveria ser emitido"
        assert len(a.eventos) == 1

    def test_dedup_assinatura_mantido(self):
        """Mesmo trade (mesma assinatura) não é re-emitido em ciclos posteriores."""
        a = FakeAdapter()
        a.fechar_baseline()

        for _ in range(3):
            a.novo_ciclo()
            c = _cell()
            for f, v in c.items():
                a.processar_campo('WINV26', 1, f, v)

        assert len(a.eventos) == 1, f"Dedup quebrado: {len(a.eventos)} eventos"

    def test_trades_distintos_mesmo_lote(self):
        """10 trades distintos no mesmo ciclo → 10 eventos."""
        a = FakeAdapter()
        a.fechar_baseline()
        a.novo_ciclo()
        for i in range(10):
            c = _cell(dat=f'10:30:{i:02d}.000', pre=187700.0 + i)
            for f, v in c.items():
                a.processar_campo('WINV26', i + 1, f, v)
        assert len(a.eventos) == 10, f"Esperado 10, got {len(a.eventos)}"

    def test_dedup_independente_por_ativo(self):
        """Mesma assinatura em ativos diferentes → emitido em ambos."""
        a = FakeAdapter()
        a.fechar_baseline()
        a.novo_ciclo()
        c = _cell()
        for f, v in c.items():
            a.processar_campo('WINV26', 1, f, v)
            a.processar_campo('WDOV26', 1, f, v)
        syms = {e['sym'] for e in a.eventos}
        assert syms == {'WINV26', 'WDOV26'}, f"Ativos emitidos: {syms}"

    def test_frankenstein_nao_consoma_dedup(self):
        """Linha bloqueada por lote não grava assinatura no dedup.

        IMPORTANTE: quando os campos convergirem depois, o trade
        legítimo ainda pode ser emitido.
        """
        a = FakeAdapter()
        a.fechar_baseline()

        # Ciclo 1: PRE/QUL antigos
        a.novo_ciclo()
        a.processar_campo('WINV26', 3, 'PRE', 187650.0)
        a.processar_campo('WINV26', 3, 'QUL', 3)

        # Ciclo 2: DAT novo → Frankenstein, bloqueado
        a.novo_ciclo()
        a.processar_campo('WINV26', 3, 'DAT', '10:31:00.000')
        assert len(a.eventos) == 0

        # Ciclo 3: RTD entrega PRE/QUL do trade X (convergência)
        a.novo_ciclo()
        a.processar_campo('WINV26', 3, 'PRE', 187700.0)
        emitido = a.processar_campo('WINV26', 3, 'QUL', 5)
        # DAT ainda é do ciclo 2, PRE/QUL do 3 → ainda incoerente
        assert not emitido

        # Ciclo 4: DAT reenviado → todos no ciclo 4 → emite
        a.novo_ciclo()
        emitido = a.processar_campo('WINV26', 3, 'DAT', '10:31:00.000')
        assert emitido, "Trade convergido deveria ser emitido"

    def test_multiplas_linhas_shift_simultaneo(self):
        """Shift afeta várias linhas ao mesmo tempo — só as coerentes emitem."""
        a = FakeAdapter()
        a.fechar_baseline()

        # 5 linhas com PRE/QUL do ciclo 1
        a.novo_ciclo()
        for linha in range(5):
            a.processar_campo('WINV26', linha, 'PRE', 187600.0 + linha)
            a.processar_campo('WINV26', linha, 'QUL', 1)

        # Ciclo 2: DATs novos chegam em 3 linhas (shift), 2 linhas ficam paradas
        a.novo_ciclo()
        for linha in range(3):
            a.processar_campo('WINV26', linha, 'DAT', f'10:3{linha}:00.000')

        assert len(a.eventos) == 0, "Nenhuma linha coerente no ciclo 2"

        # Ciclo 3: RTD entrega PRE/QUL das 3 linhas shiftadas
        a.novo_ciclo()
        for linha in range(3):
            a.processar_campo('WINV26', linha, 'PRE', 187700.0 + linha)
            a.processar_campo('WINV26', linha, 'QUL', 2)

        # Ainda não: DAT é do ciclo 2, PRE/QUL do 3
        assert len(a.eventos) == 0

        # Ciclo 4: DATs reenviados → convergem
        a.novo_ciclo()
        for linha in range(3):
            a.processar_campo('WINV26', linha, 'DAT', f'10:3{linha}:00.000')

        assert len(a.eventos) == 3, f"Esperado 3 convergidos, got {len(a.eventos)}"


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
