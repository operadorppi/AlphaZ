# -*- coding: utf-8 -*-
"""
testes/test_leak_encoding_v1512.py — Sem leak de preprocessing no encoding (P1-A16).

O walk_forward antigo fazia:

    combinado = pd.concat([X_train[cat_cols], X_test[cat_cols]])
    combinado = aplicar_encoding(combinado, cat_cols)   # get_dummies conjunto

Categoria presente SÓ no teste criava coluna dummy "visível" — informação do
teste influenciava a preparação (leak de preprocessing).

Correto: fit no TREINO, transform em ambos com as MESMAS colunas.
Cobertura:
  1. Categoria so no teste -> descartada (nao cria coluna)
  2. Categoria so no treino -> presente, 0 no teste (vocabulario do fit)
  3. Colunas de X_test == colunas de X_train exatamente
  4. Sem categoria -> identidade (sem alteracao)
"""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ml.treino_lib import aplicar_encoding_fit  # noqa: E402


def _df(cats, num=None):
    """DataFrame com coluna categorica 'fase' + numerica 'num'."""
    if num is None:
        num = list(range(len(cats)))
    return pd.DataFrame({"fase": cats, "num": num})


class TestLeakEncoding:
    def test_categoria_so_no_teste_nao_cria_coluna(self):
        """Leak classico: 'LATERAL' existe so no teste -> sem coluna dummy."""
        X_tr = _df(["ABERTURA", "ALMOCO", "ABERTURA"])
        X_te = _df(["ABERTURA", "LATERAL"])
        Xtr, Xte = aplicar_encoding_fit(X_tr, X_te, ["fase"])

        cols_tr = set(Xtr.columns)
        cols_te = set(Xte.columns)
        # Nenhuma coluna com 'LATERAL' em lugar nenhum
        assert not any("LATERAL" in c for c in cols_tr | cols_te)
        # Treino nao tem coluna do vocabulario do teste
        assert cols_te == cols_tr, "colunas do teste divergem do treino (fit)"

    def test_colunas_teste_iguais_ao_treino(self):
        """Contrato central: X_test tem EXATAMENTE as colunas de X_train."""
        X_tr = _df(["ABERTURA", "ALMOCO", "FECHAMENTO"])
        X_te = _df(["ABERTURA", "ALMOCO", "LATERAL", "LATERAL"])
        Xtr, Xte = aplicar_encoding_fit(X_tr, X_te, ["fase"])

        assert list(Xte.columns) == list(Xtr.columns)
        # fit = categorias do treino: ABERTURA, ALMOCO, FECHAMENTO
        assert "fase_FECHAMENTO" in Xtr.columns
        # teste tem 0 na dummy FECHAMENTO (categoria ausente -> 0)
        assert (Xte["fase_FECHAMENTO"] == 0).all()

    def test_vocabulario_do_teste_descartado(self):
        """Valor real de uma linha do teste na categoria desconhecida = 0
        (modelo trata como ausente, igual a inferencia em producao)."""
        X_tr = _df(["ABERTURA", "ABERTURA"])
        X_te = _df(["LATERAL"])
        _, Xte = aplicar_encoding_fit(X_tr, X_te, ["fase"])

        assert "fase_LATERAL" not in Xte.columns
        # a linha vira tudo 0 nas dummies (categoria fora do vocabulario)
        dummies = [c for c in Xte.columns if c.startswith("fase_")]
        assert (Xte[dummies] == 0).all().all()

    def test_sem_categorica_retorna_identidade(self):
        """Sem colunas categoricas: nada muda."""
        X_tr = pd.DataFrame({"num": [1, 2, 3]})
        X_te = pd.DataFrame({"num": [4, 5]})
        Xtr, Xte = aplicar_encoding_fit(X_tr, X_te, [])
        assert Xtr.equals(X_tr) and Xte.equals(X_te)

    def test_nao_ha_pd_concat_treino_teste_no_walk_forward(self):
        """Guard: o padrao vazado (concat + get_dummies conjunto) nao existe
        mais no walk_forward.py."""
        caminho = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                               "ml", "walk_forward.py")
        with open(caminho, encoding="utf-8", errors="replace") as f:
            codigo = f.read()
        assert "combinado = pd.concat" not in codigo
        assert "aplicar_encoding_fit" in codigo
