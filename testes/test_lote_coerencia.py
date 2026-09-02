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
    """Réplica mínima da lógica de lote do ProfitRTDAdapter para teste.

    v14.7: RTD nunca envia linha duplicada → sem dedup por linha.
    Apenas coerência de lote (Frankenstein) + baseline (1º ciclo).
    v14.8: estado das células separado POR JANELA — TT, RLP e BOOK
    do mesmo ativo não compartilham células (contaminação cruzada).
    """

    def __init__(self, max_per_ativo=50000):
        self._baseline_pending = defaultdict(lambda: True)
        self._cell_lote = defaultdict(dict)  # (sym, kind, janela, linha) -> {field: lote}
        self._lote_atual = 0
        self.eventos = []

    def processar_campo(self, sym, linha, field, val, kind='tt', janela=2):
        """Simula a chegada de 1 campo num ciclo RefreshData.

        Retorna True se um trade foi emitido (campos coerentes).
        Replica o adapter v14.7/v14.8: coerência de lote em qualquer
        campo-chave (DAT/PRE/QUL), sem dedup por linha, estado
        separado por (kind, janela).
        """
        chave = (sym, kind, janela, linha)
        cell = self._cell_lote.setdefault(chave, {}).setdefault('_cell', {})
        lotes = self._cell_lote[chave]
        cell[field] = val
        lotes[field] = self._lote_atual

        # Só processa quando algum dos 3 campos-chave chega
        if field not in ('DAT', 'PRE', 'QUL'):
            return False

        # Coerência de lote: todos os 3 campos devem ter o mesmo lote
        lote_ref = lotes.get('DAT', 0)
        if (lote_ref == 0
                or lotes.get('PRE', 0) != lote_ref
                or lotes.get('QUL', 0) != lote_ref):
            return False  # Frankenstein — aguardar coerência

        pre = fnum(cell.get('PRE'))
        if pre <= 0:
            return False
        qtd = fint(cell.get('QUL'))
        if qtd <= 0:
            return False

        if self._baseline_pending[sym]:
            return False

        self.eventos.append({'sym': sym, 'kind': kind, 'janela': janela,
                             'dat': sstr(cell.get('DAT')), 'pre': pre, 'qtd': qtd})
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
        """DAT+PRE+QUL no mesmo ciclo → emitido quando o ÚLTIMO dos 3
        campos-chave chega (gatilho em qualquer campo, v14.5)."""
        a = FakeAdapter()
        a.fechar_baseline()
        a.novo_ciclo()
        c = _cell()
        for f, v in c.items():
            a.processar_campo('WINV26', 1, f, v)
        # QUL (último dos 3 no dict) completa a coerência → emite no ciclo 1
        assert len(a.eventos) == 1, f"Trade coerente deveria emitir, got {len(a.eventos)}"

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

    def test_sem_dedup_por_linha(self):
        """v14.7: RTD nunca envia linha duplicada → re-entrega coerente
        de uma linha é emitida (cada linha é um trade real)."""
        a = FakeAdapter()
        a.fechar_baseline()

        # Ciclo 1: primeira entrega
        a.novo_ciclo()
        c = _cell()
        for f, v in c.items():
            a.processar_campo('WINV26', 1, f, v)
        # Ciclo 2: re-entrega coerente (mesmo conteúdo, mesmo lote)
        a.novo_ciclo()
        a.processar_campo('WINV26', 1, 'PRE', 187700.0)
        a.processar_campo('WINV26', 1, 'QUL', 5)
        a.processar_campo('WINV26', 1, 'DAT', '10:30:00.000')

        assert len(a.eventos) == 2, f"Sem dedup: 2 eventos, got {len(a.eventos)}"

    def test_trades_distintos_mesmo_lote(self):
        """10 trades distintos no mesmo ciclo → 10 eventos, sem dedup
        por linha (cada linha coerente é um trade real, v14.7)."""
        a = FakeAdapter()
        a.fechar_baseline()
        # Ciclo 1: todas as linhas recebem todos os campos
        a.novo_ciclo()
        for i in range(10):
            c = _cell(dat=f'10:30:{i:02d}.000', pre=187700.0 + i)
            for f, v in c.items():
                a.processar_campo('WINV26', i + 1, f, v)
        assert len(a.eventos) == 10, f"10 trades distintos deveriam emitir, got {len(a.eventos)}"
        precos = {e['pre'] for e in a.eventos}
        assert len(precos) == 10, f"Preços distintos preservados: {len(precos)}"

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


class TestSeparacaoJanelas:
    """v14.8: TT, RLP e BOOK do mesmo ativo têm estado isolado.

    Bug corrigido: `_book_cells[sym]` era compartilhado entre janelas —
    o RLP sobrescrevia o TT (e vice-versa) na mesma linha, gerando trades
    Frankenstein ou perda de captura. Também ACP/AVD do BOOK colidiam
    com ACP/AVD do TT.
    """

    def test_tt_e_rlp_mesmo_simbolo_nao_se_contaminam(self):
        """WIN tem T&T2 (TT) e T&T4 (RLP). Trade na linha 0 de cada janela
        com o MESMO DAT deve gerar 2 eventos independentes — não 1."""
        a = FakeAdapter()
        a.fechar_baseline()

        a.novo_ciclo()
        # TT (janela 2)
        a.processar_campo('WINV26', 0, 'PRE', 187700.0, kind='tt', janela=2)
        a.processar_campo('WINV26', 0, 'QUL', 5, kind='tt', janela=2)
        e1 = a.processar_campo('WINV26', 0, 'DAT', '10:30:00.000', kind='tt', janela=2)
        # RLP (janela 4)
        a.processar_campo('WINV26', 0, 'PRE', 187700.0, kind='rlp', janela=4)
        a.processar_campo('WINV26', 0, 'QUL', 5, kind='rlp', janela=4)
        e2 = a.processar_campo('WINV26', 0, 'DAT', '10:30:00.000', kind='rlp', janela=4)

        assert e1 is True
        assert e2 is True
        assert len(a.eventos) == 2, "TT e RLP deveriam emitir independentes"
        assert a.eventos[0]['janela'] == 2
        assert a.eventos[1]['janela'] == 4

    def test_janelas_nao_criam_frankenstein_cruzado(self):
        """TT na linha 0 com lote do ciclo 1 e RLP na linha 0 com lote do
        ciclo 2: ANTES do fix, a coerência misturava os lotes e bloqueava
        ou emitia Frankenstein. AGORA cada janela tem seu próprio lote."""
        a = FakeAdapter()
        a.fechar_baseline()

        # Ciclo 1: TT completa seu trade na linha 0
        a.novo_ciclo()
        a.processar_campo('WINV26', 0, 'PRE', 187700.0, kind='tt', janela=2)
        a.processar_campo('WINV26', 0, 'QUL', 5, kind='tt', janela=2)
        a.processar_campo('WINV26', 0, 'DAT', '10:30:00.000', kind='tt', janela=2)
        assert len(a.eventos) == 1

        # Ciclo 2: RLP (janela diferente) escreve a MESMA linha com lote novo
        a.novo_ciclo()
        a.processar_campo('WINV26', 0, 'PRE', 187700.0, kind='rlp', janela=4)
        a.processar_campo('WINV26', 0, 'QUL', 5, kind='rlp', janela=4)
        e = a.processar_campo('WINV26', 0, 'DAT', '10:30:01.000', kind='rlp', janela=4)

        assert e is True, "RLP deveria emitir independente do lote antigo do TT"
        assert len(a.eventos) == 2
        assert a.eventos[1]['kind'] == 'rlp'
        assert a.eventos[1]['dat'] == '10:30:01.000'

    def test_sem_mistura_quando_janelas_trocam_de_linha(self):
        """Se o TT re-escreve a linha 0 no ciclo 3 e o RLP não atualizou,
        o trade do TT sai com os campos do TT (não do RLP)."""
        a = FakeAdapter()
        a.fechar_baseline()

        # Ciclo 1: RLP escreve linha 0
        a.novo_ciclo()
        a.processar_campo('WINV26', 0, 'PRE', 187600.0, kind='rlp', janela=4)
        a.processar_campo('WINV26', 0, 'QUL', 1, kind='rlp', janela=4)
        a.processar_campo('WINV26', 0, 'DAT', '10:29:00.000', kind='rlp', janela=4)
        assert len(a.eventos) == 1

        # Ciclo 2: TT (janela 2) escreve a mesma linha 0 com conteúdo próprio
        a.novo_ciclo()
        a.processar_campo('WINV26', 0, 'PRE', 187800.0, kind='tt', janela=2)
        a.processar_campo('WINV26', 0, 'QUL', 3, kind='tt', janela=2)
        a.processar_campo('WINV26', 0, 'DAT', '10:31:00.000', kind='tt', janela=2)

        assert len(a.eventos) == 2
        assert a.eventos[1]['kind'] == 'tt'
        assert a.eventos[1]['pre'] == 187800.0, "Preço do TT, não do RLP"
        assert a.eventos[1]['qtd'] == 3, "Quantidade do TT, não do RLP"


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
