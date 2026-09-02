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
        Replica o adapter v14.3: check de coerência nos 3 campos,
        porque células chegam em ordem arbitrária dentro do ciclo.
        """
        cell = self._cell_lote.setdefault((sym, linha), {}).setdefault('_cell', {})
        lotes = self._cell_lote[(sym, linha)]
        cell[field] = val
        lotes[field] = self._lote_atual

        # Coerência de lote (replica o adapter v14.3 — check nos 3 campos)
        # APENAS no gatilho DAT: é o momento de emissão. PRE/QUL que chegam
        # antes do DAT apenas preenchem a linha (o trade é emitido quando o
        # DAT re-chegar/confirmar, o que o RTD faz no ciclo seguinte).
        if field == 'DAT':
            lote_dat = lotes.get('DAT', 0)
            if lotes.get('PRE', 0) != lote_dat or lotes.get('QUL', 0) != lote_dat:
                return False  # Frankenstein — aguardar coerência
        else:
            # Só o DAT dispara emissão (replica o adapter)
            return False

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
        """DAT+PRE+QUL no mesmo ciclo. DAT pode chegar antes dos outros:
        o trade é emitido quando o RTD re-entrega o DAT no ciclo seguinte
        (células estáveis são re-entregues a cada refresh)."""
        a = FakeAdapter()
        a.fechar_baseline()
        a.novo_ciclo()
        c = _cell()
        for f, v in c.items():
            a.processar_campo('WINV26', 1, f, v)
        # DAT chegou 1o no ciclo 1 → ainda não emitido
        assert len(a.eventos) == 0
        # Ciclo 2: RTD re-entrega a linha estável (PRE/QUL depois DAT)
        a.novo_ciclo()
        a.processar_campo('WINV26', 1, 'PRE', 187700.0)
        a.processar_campo('WINV26', 1, 'QUL', 5)
        emitido = a.processar_campo('WINV26', 1, 'DAT', '10:30:00.000')
        assert emitido, "DAT re-entregue com PRE/QUL do mesmo ciclo deve emitir"
        assert len(a.eventos) == 1

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

        # Ciclo 1: primeira entrega
        a.novo_ciclo()
        c = _cell()
        for f, v in c.items():
            a.processar_campo('WINV26', 1, f, v)
        # Ciclos 2-3: RTD re-entrega a linha estável
        for _ in range(2):
            a.novo_ciclo()
            a.processar_campo('WINV26', 1, 'PRE', 187700.0)
            a.processar_campo('WINV26', 1, 'QUL', 5)
            a.processar_campo('WINV26', 1, 'DAT', '10:30:00.000')

        assert len(a.eventos) == 1, f"Dedup quebrado: {len(a.eventos)} eventos"

    def test_trades_distintos_mesmo_lote(self):
        """10 trades distintos no mesmo ciclo → 10 eventos (após re-entrega do DAT)."""
        a = FakeAdapter()
        a.fechar_baseline()
        # Ciclo 1: todas as linhas recebem todos os campos
        a.novo_ciclo()
        for i in range(10):
            c = _cell(dat=f'10:30:{i:02d}.000', pre=187700.0 + i)
            for f, v in c.items():
                a.processar_campo('WINV26', i + 1, f, v)
        assert len(a.eventos) == 0  # DATs chegaram antes dos PRE/QUL
        # Ciclo 2: RTD re-entrega (PRE/QUL depois DAT)
        a.novo_ciclo()
        for i in range(10):
            a.processar_campo('WINV26', i + 1, 'PRE', 187700.0 + i)
            a.processar_campo('WINV26', i + 1, 'QUL', 5)
            a.processar_campo('WINV26', i + 1, 'DAT', f'10:30:{i:02d}.000')
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
        # Ciclo 2: re-entrega
        a.novo_ciclo()
        for sym in ('WINV26', 'WDOV26'):
            a.processar_campo(sym, 1, 'PRE', 187700.0)
            a.processar_campo(sym, 1, 'QUL', 5)
            a.processar_campo(sym, 1, 'DAT', '10:30:00.000')
        syms = {e['sym'] for e in a.eventos}
        assert syms == {'WINV26', 'WDOV26'}, f"Ativos emitidos: {syms}"

    def test_frankenstein_nao_consoma_dedup(self):
        """Linha bloqueada por lote não grava assinatura no dedup.

        Cenário real do shift: linha inteira re-entregue no ciclo seguinte
        (PRE, QUL e DAT do MESMO ciclo) → trade legítimo emitido.
        """
        a = FakeAdapter()
        a.fechar_baseline()

        # Ciclo 1: trade antigo Z (linha completa)
        a.novo_ciclo()
        a.processar_campo('WINV26', 3, 'PRE', 187650.0)
        a.processar_campo('WINV26', 3, 'QUL', 3)
        a.processar_campo('WINV26', 3, 'DAT', '10:30:00.000')
        assert len(a.eventos) == 1

        # Ciclo 2: shift — trade X substitui Z, TODOS os campos no ciclo 2
        a.novo_ciclo()
        a.processar_campo('WINV26', 3, 'PRE', 187700.0)
        a.processar_campo('WINV26', 3, 'QUL', 5)
        emitido = a.processar_campo('WINV26', 3, 'DAT', '10:31:00.000')
        assert emitido, "Trade X coerente deveria ser emitido"
        assert len(a.eventos) == 2
        assert a.eventos[1]['pre'] == 187700.0, "Trade X (não Z) emitido"

    def test_multiplas_linhas_shift_simultaneo(self):
        """Shift afeta várias linhas ao mesmo tempo — cada linha emite
        quando seus 3 campos convergem no mesmo ciclo.

        Cenário real: 1 trade novo → 5 linhas descem, cada uma re-entregue
        completa no ciclo seguinte.
        """
        a = FakeAdapter()
        a.fechar_baseline()

        # Ciclo 1: 5 linhas com trades antigos (completos)
        a.novo_ciclo()
        for linha in range(5):
            a.processar_campo('WINV26', linha, 'PRE', 187600.0 + linha)
            a.processar_campo('WINV26', linha, 'QUL', 1)
            a.processar_campo('WINV26', linha, 'DAT', f'10:29:{linha:02d}.000')
        assert len(a.eventos) == 5

        # Ciclo 2: shift — 5 linhas re-entregues com conteúdo novo (completo)
        a.novo_ciclo()
        for linha in range(5):
            a.processar_campo('WINV26', linha, 'PRE', 187700.0 + linha)
            a.processar_campo('WINV26', linha, 'QUL', 2)
            a.processar_campo('WINV26', linha, 'DAT', f'10:30:{linha:02d}.000')

        assert len(a.eventos) == 10, f"Esperado 10 (5+5), got {len(a.eventos)}"
        # Todos os novos com o preço do shift
        assert all(e['pre'] >= 187700.0 for e in a.eventos[5:])


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
