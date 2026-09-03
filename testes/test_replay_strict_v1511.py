# -*- coding: utf-8 -*-
"""
testes/test_replay_strict_v1511.py — Replay nunca engole erro (P0-A15).

Antes: replay_dia/replay_multi_dia tinham `except Exception: pass` no loop —
1M eventos com 3 erros silenciosos terminavam com "Replay finalizado" e o
gate podia APROVAR com dados comprometidos.

Agora:
  - modo permissivo (default, diagnostico): erros contados por tipo, logados
    com amostra, e o gate FORCADO a reprovar quando ha qualquer perda;
  - modo STRICT: o 1o erro inesperado ABORTA o replay do dia;
  - linhas JSONL ilegiveis e arquivos Parquet ilegiveis tambem sao contados.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from replay_engine import ReplayEngine  # noqa: E402

DIA = "20260901"


class EngineQuebrado(ReplayEngine):
    """ReplayEngine cujo _process_neg falha no evento com ts_ms == 2000."""

    def _process_neg(self, ev):
        if ev.get("ts_ms") == 2000:
            raise KeyError("feature inexistente (simulacao de erro)")
        return super()._process_neg(ev)


def _eventos(tmp_path):
    """Grava 3 trades de WIN no JSONL legado (caminho mais simples p/ teste)."""
    pasta = Path(tmp_path)
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo = pasta / f"raw_negocios_ms_{DIA}_000000.jsonl"
    linhas = []
    for ts, preco in ((1000, 170000.0), (2000, 170001.0), (3000, 170002.0)):
        linhas.append({
            "_tipo": "NEG", "ativo": "WINV26", "ts_ms": ts,
            "preco": preco, "qtd": 5, "agressor": "Comprador",
            "compradora": "XP", "vendedora": "BTG",
        })
    with open(arquivo, "w", encoding="utf-8") as f:
        for ev in linhas:
            f.write(__import__("json").dumps(ev) + "\n")
    return str(tmp_path)


class TestReplayStrict:
    def test_permissivo_conta_e_gate_reprova(self, tmp_path):
        """Modo permissivo: erro e contado, logado e o gate NUNCA aprova."""
        pasta = _eventos(tmp_path)
        eng = EngineQuebrado(config={"save_dir": pasta}, instrumentos=["WINV26"])
        eng.strict = False
        res = eng.replay_dia(pasta, dia_str=DIA)

        assert res is not None
        assert eng._erros == 1, f"esperado 1 erro, veio {eng._erros}"
        assert eng._erros_por_tipo.get("KeyError") == 1
        assert len(eng._primeiros_erros) == 1
        # Gate forcado a reprovar
        assert res["gate"]["aprovado"] is False
        assert "COMPROMETIDO" in res["gate"]["motivo"]
        assert res["resumo_erros"]["erros"] == 1
        # Os eventos sem erro continuaram processados (permissivo)
        assert eng._events == 3

    def test_strict_aborta_no_primeiro_erro(self, tmp_path):
        """Modo STRICT: 1o erro interrompe o replay do dia (marcado abortado)."""
        pasta = _eventos(tmp_path)
        eng = EngineQuebrado(config={"save_dir": pasta, "replay_strict": True},
                             instrumentos=["WINV26"])
        res = eng.replay_dia(pasta, dia_str=DIA)

        assert res is not None
        assert res.get("abortado") is True
        assert res.get("erros", 0) >= 1
        assert res["metrics"] is None  # nada e reportado como valido

    def test_sem_erros_gate_normal(self, tmp_path):
        """Sem erros: gate normal (nao e forcado a reprovar)."""
        pasta = _eventos(tmp_path)
        eng = ReplayEngine(config={"save_dir": pasta}, instrumentos=["WINV26"])
        res = eng.replay_dia(pasta, dia_str=DIA)

        assert eng._erros == 0
        assert "COMPROMETIDO" not in res["gate"]["motivo"]
        assert res["resumo_erros"]["erros"] == 0

    def test_verdicto_reprova_dia_com_erro(self, tmp_path):
        """Verdicto: 2 dias BONS + 1 dia com erro => NO-GO (regra isolada)."""
        pasta = _eventos(tmp_path)
        eng = ReplayEngine(config={"save_dir": pasta}, instrumentos=["WINV26"])
        gate_bom = {"aprovado": True, "pf_ok": True, "wr_ok": True, "dd_ok": True,
                    "pf_atual": 1.5, "wr_atual": 0.6, "dd_atual": 50,
                    "motivo": "PF=1.50OK | WR=60.0%OK | DD=50OK"}
        metrics_bom = {"n_trades": 10, "total_pnl": 100}
        dia_comprometido = {
            "dia": DIA, "metrics": metrics_bom, "gate": None,
            "resumo_erros": {"erros": 3, "erros_por_tipo": {"KeyError": 3}},
        }
        # Dia comprometido tem gate None (abortado) — deve reprovar o conjunto
        res = eng._verdicto([
            {"dia": "20260902", "metrics": metrics_bom, "gate": gate_bom},
            {"dia": "20260903", "metrics": metrics_bom, "gate": gate_bom},
            dia_comprometido,
        ])
        assert res["aprovado"] is False
        assert res["dias_reprovados"] == 1

    def test_strict_multi_dia_marca_abortado(self, tmp_path):
        """Verdicto multi-dia em strict: dia abortado vira reprovado."""
        pasta = _eventos(tmp_path)
        eng = EngineQuebrado(config={"save_dir": pasta, "replay_strict": True},
                             instrumentos=["WINV26"])
        res = eng.replay_multi_dia(pasta, [DIA])
        assert res["aprovado"] is False
        assert res["dias_reprovados"] >= 1
