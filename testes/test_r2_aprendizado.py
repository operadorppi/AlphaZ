import sys, os
_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in [_base, os.path.join(_base, "ml"), os.path.join(_base, "scripts")]:
    if os.path.isdir(_d): sys.path.insert(0, _d)
"""
test_r2_aprendizado.py — Testes R2: restore do aprendizado mantém deque(maxlen=5000)

ANTES do fix: `carregar_aprendizado` convertia resultados/previsoes em list
comum (crescimento ilimitado). DEPOIS: restaura como deque(maxlen=5000),
preservando o limite original do __init__.

NOTA v10.0: A classe Analise foi migrada para core.app._AnaliseShim.
Estes testes agora testam core.learning.Learning diretamente.
"""
import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import deque
from core.learning import Learning as Analise


class TestRestoreMantemDequeLimitado:
    """R2: carregar_aprendizado nao desfaz o deque(maxlen=5000) do __init__."""

    def test_restore_reusa_deque_limitado(self, tmp_path):
        st = {
            'pesos': {},
            'feature_hits': {},
            'resultados': [{'idx': i, 'ts': '2024-08-18T10:00:00'} for i in range(100)],
            'previsoes': [{'idx': i, 'ts': '2024-08-18T10:00:00'} for i in range(100)],
        }
        (tmp_path / 'learning_state.json').write_text(
            json.dumps(st, ensure_ascii=False), encoding='utf-8'
        )

        analise = Analise()
        # recarrega com o json de teste (substitui o do init)
        analise.carregar_aprendizado(str(tmp_path))

        # resultado e previsoes continuam sendo deque com maxlen 5000
        assert isinstance(analise.resultados, deque), (
            "ANTES: resultados virava list comum — R2 regression (fix v9.16 #19)"
        )
        assert analise.resultados.maxlen == 5000
        assert isinstance(analise.previsoes, deque)
        assert analise.previsoes.maxlen == 5000

        # append apos restore nao cresce ilimitado
        for i in range(6000):
            analise.resultados.append({'idx': i})
        assert len(analise.resultados) == 5000, "append apos restore cresceu alem do maxlen"

    def test_restore_vazio_mantem_deque(self, tmp_path):
        """Sem arquivo, o __init__ ja cria deques — nada quebrado."""
        st = {'pesos': {}, 'feature_hits': {}, 'resultados': [], 'previsoes': []}
        (tmp_path / 'learning_state.json').write_text(
            json.dumps(st, ensure_ascii=False), encoding='utf-8'
        )

        analise = Analise()
        analise.carregar_aprendizado(str(tmp_path))

        assert isinstance(analise.resultados, deque)
        assert analise.resultados.maxlen == 5000
        assert len(analise.resultados) == 0

    def test_sem_arquivo_nao_quebra(self, tmp_path):
        """carregar_aprendizado retorna cedo sem log call."""
        analise = Analise()
        analise.carregar_aprendizado(str(tmp_path))
        assert isinstance(analise.resultados, deque)
        assert isinstance(analise.previsoes, deque)