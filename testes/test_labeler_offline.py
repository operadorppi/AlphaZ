import sys, os
_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in [_base, os.path.join(_base, "ml"), os.path.join(_base, "scripts")]:
    if os.path.isdir(_d): sys.path.insert(0, _d)
# test_labeler_offline.py - labelar_offline produz saida 1:1 com o
# dataset e labels canonicos (v9.19). Regressao do bug de contaminacao
# cross-instrument corrigido na reescrita.
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from labelar_offline import generar_labels


def _dataset_temporario():
    # WIN: 100 -> 105 (TP=5 com P0=100) - apenas o P0=100 atinge TP.
    linhas = []
    for i, p in enumerate([100.0, 101.0, 102.0, 103.0, 104.0, 105.0]):
        linhas.append({'ts_ms': 1_000_000 + i * 100, 'ativo': 'WINV26',
                       'preco_ultimo': p, 'vol_total': 10})
    # WDO: compartilha ts com WIN (cortes de 100ms alinhados) - neutro
    for i in range(6):
        linhas.append({'ts_ms': 1_000_000 + i * 100, 'ativo': 'WDOU26',
                       'preco_ultimo': 5000.0, 'vol_total': 10})
    return linhas


def test_saida_1_para_1_e_labels_canonicos():
    linhas = _dataset_temporario()
    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, 'dataset.jsonl')
        out = os.path.join(tmp, 'dataset_labels.jsonl')
        with open(inp, 'w', encoding='utf-8') as f:
            for l in linhas:
                f.write(json.dumps(l) + chr(10))

        total, n_pos, n_neg = generar_labels(inp, out, ativo='WINV26',
                                             tp=5, sl=3, max_holding=30)

        assert total == len(linhas)
        with open(out, encoding='utf-8') as f:
            saida = [json.loads(l) for l in f]

        # saida 1:1 com o dataset, mesma ordem
        assert len(saida) == len(linhas)
        for o, l in zip(saida, linhas):
            assert o['ts_ms'] == l['ts_ms']

        # WIN (indices 0..5): primeiro ts deve ser TP
        assert saida[0]['label'] == 1
        # WDO (indices 6..11): neutro - sem contaminacao cross-instrument
        for i in range(6, 12):
            assert saida[i]['label'] == 0, (
                'WDO no indice %d nao deveria ter label' % i)
        assert n_pos == 1 and n_neg == 0


def test_dataset_vazio():
    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, 'vazio.jsonl')
        out = os.path.join(tmp, 'vazio_labels.jsonl')
        with open(inp, 'w', encoding='utf-8') as f:
            pass
        total, n_pos, n_neg = generar_labels(inp, out, ativo='WINV26')
        assert total == 0 and n_pos == 0 and n_neg == 0
