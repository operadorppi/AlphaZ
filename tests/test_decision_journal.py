# -*- coding: utf-8 -*-
"""FASE 20 P1 — Testes do Decision Journal (audit trail de decisões)."""

import json
import os
import sys
import time
import pytest
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

# Adiciona o diretório atual ao sys.path para garantir importação correta
_work_dir = str(Path(__file__).parent.parent)
if _work_dir not in sys.path:
    sys.path.insert(0, _work_dir)

from core.decision_journal import (
    TradeDecision,
    DecisionJournal,
    DECISION_SIGNAL_BUY,
    DECISION_SIGNAL_SELL,
    DECISION_SIGNAL_HOLD,
    DECISION_SIGNAL_BLOCKED,
    RISK_REASON_STALE,
    RISK_REASON_DRAWDOWN,
    RISK_REASON_EXPOSURE,
    get_journal,
    reset_journal,
    record_decision,
    query_decisions,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def make_decision(
    ativo: str = "PETR4",
    sinal: str = DECISION_SIGNAL_BUY,
    score: float = 0.75,
    motivo: str = "sinal forte de alta",
    risk_decision: str = "ALLOWED",
    posicao: float = 0.0,
    quantidade: int = 100,
    preco: float = 25.50,
    extra_estado: Dict[str, Any] | None = None,
) -> TradeDecision:
    """Cria uma TradeDecision de teste."""
    return TradeDecision(
        timestamp_do_evento=time.time() - 1.0,
        timestamp_de_processamento=time.time(),
        ativo=ativo,
        sinal=sinal,
        score=score,
        features_schema_version="v2.3.1",
        model_version="xgb-v4.2",
        risk_decision=risk_decision,
        motivo=motivo,
        posicao=posicao,
        quantidade=quantidade,
        preco=preco,
        estado_sistema=extra_estado or {
            "environment": "PRODUCTION",
            "replay_validated": True,
            "ml_available": True,
        },
    )


# ---------------------------------------------------------------------------
# Testes TradeDecision
# ---------------------------------------------------------------------------
class TestTradeDecision:
    def test_is_trade_buy(self):
        d = make_decision(sinal=DECISION_SIGNAL_BUY)
        assert d.is_trade is True
        assert d.sinal == DECISION_SIGNAL_BUY

    def test_is_trade_sell(self):
        d = make_decision(sinal=DECISION_SIGNAL_SELL)
        assert d.is_trade is True
        assert d.sinal == DECISION_SIGNAL_SELL

    def test_not_trade_hold(self):
        d = make_decision(sinal=DECISION_SIGNAL_HOLD)
        assert d.is_trade is False

    def test_is_blocked(self):
        d = make_decision(sinal=DECISION_SIGNAL_BLOCKED)
        assert d.is_blocked is True

    def test_upcases_ativo(self):
        d = make_decision(ativo="petr4")
        assert d.ativo == "PETR4"

    def test_rounds_score(self):
        d = make_decision(score=0.123456789)
        assert d.score == 0.123457

    def test_to_dict_and_from_dict(self):
        original = make_decision()
        d = original.to_dict()
        restored = TradeDecision.from_dict(d)
        
        assert restored.ativo == original.ativo
        assert restored.sinal == original.sinal
        assert restored.score == original.score
        assert restored.motivo == original.motivo
        assert restored.risk_decision == original.risk_decision

    def test_to_json_serialization(self):
        d = make_decision()
        json_str = d.to_json()
        parsed = json.loads(json_str)
        assert parsed["ativo"] == "PETR4"
        assert parsed["sinal"] == DECISION_SIGNAL_BUY

    def test_human_motivo_trade(self):
        d = make_decision(sinal=DECISION_SIGNAL_BUY, score=0.85)
        assert d.human_motivo.startswith("BUY")
        assert "0.850000" in d.human_motivo

    def test_human_motivo_blocked(self):
        d = make_decision(
            sinal=DECISION_SIGNAL_BLOCKED,
            risk_decision="BLOCKED_BY_RISK",
            motivo=RISK_REASON_STALE,
        )
        assert "BLOQUEADO" in d.human_motivo

    def test_dataclass_properties(self):
        d = make_decision()
        # Propriedades calculadas funcionam
        assert d.is_trade is True
        assert d.is_blocked is False
        assert "BUY" in d.human_motivo


# ---------------------------------------------------------------------------
# Testes DecisionJournal
# ---------------------------------------------------------------------------
class TestDecisionJournal:
    def setup_method(self):
        self.journal = DecisionJournal()
        self.journal.features_schema_version = "v2.3.1"
        self.journal.model_version = "xgb-v4.2"

    def test_record_buy_decision(self):
        d = self.journal.record(
            ativo="PETR4",
            sinal=DECISION_SIGNAL_BUY,
            score=0.85,
            motivo="sinal forte de alta",
        )
        assert d.ativo == "PETR4"
        assert d.sinal == DECISION_SIGNAL_BUY
        assert d.score == 0.85
        assert d.risk_decision == "ALLOWED"

    def test_record_sell_decision(self):
        d = self.journal.record(
            ativo="VALE3",
            sinal=DECISION_SIGNAL_SELL,
            score=0.72,
            motivo="sinal de venda",
        )
        assert d.sinal == DECISION_SIGNAL_SELL
        assert d.is_trade is True

    def test_record_hold_decision(self):
        d = self.journal.record(
            ativo="ITUB4",
            sinal=DECISION_SIGNAL_HOLD,
            score=0.45,
            motivo="score insuficiente",
        )
        assert d.sinal == DECISION_SIGNAL_HOLD
        assert d.is_trade is False

    def test_record_blocked_decision(self):
        d = self.journal.record(
            ativo="PETR4",
            sinal=DECISION_SIGNAL_BLOCKED,
            score=0.0,
            motivo="dados obsoletos",
            risk_decision="BLOCKED_BY_RISK",
        )
        assert d.is_blocked is True
        assert d.risk_decision == "BLOCKED_BY_RISK"

    def test_records_have_correct_values(self):
        self.journal.record(ativo="petr4", sinal=DECISION_SIGNAL_BUY, score=0.8, motivo="teste")
        entry = self.journal.entries[0]
        # Ativo deve ser uppercase
        assert entry.ativo == "PETR4"
        # Score deve ser arredondado
        assert entry.score == 0.8

    def test_timestamps_are_set(self):
        before = time.time()
        d = self.journal.record(
            ativo="PETR4",
            sinal=DECISION_SIGNAL_BUY,
            score=0.8,
            motivo="teste",
        )
        after = time.time()
        
        assert d.timestamp_do_evento >= before - 1
        assert d.timestamp_de_processamento >= before
        assert d.timestamp_de_processamento <= after + 1

    def test_custom_timestamps(self):
        ts_evento = 1000000.0
        ts_processamento = 1000001.0
        
        d = self.journal.record(
            ativo="PETR4",
            sinal=DECISION_SIGNAL_BUY,
            score=0.8,
            motivo="teste",
            timestamp_evento=ts_evento,
            timestamp_processamento=ts_processamento,
        )
        
        assert d.timestamp_do_evento == ts_evento
        assert d.timestamp_de_processamento == ts_processamento

    def test_estado_sistema_passed(self):
        estado = {
            "environment": "PRODUCTION",
            "replay_validated": True,
            "ml_available": False,
        }
        d = self.journal.record(
            ativo="PETR4",
            sinal=DECISION_SIGNAL_HOLD,
            score=0.5,
            motivo="ML indisponivel",
            estado_sistema=estado,
        )
        assert d.estado_sistema["environment"] == "PRODUCTION"
        assert d.estado_sistema["ml_available"] is False

    def test_position_and_quantity_recorded(self):
        d = self.journal.record(
            ativo="PETR4",
            sinal=DECISION_SIGNAL_BUY,
            score=0.9,
            motivo="entrada",
            posicao=-50.0,
            quantidade=100,
            preco=25.50,
        )
        assert d.posicao == -50.0
        assert d.quantidade == 100
        assert d.preco == 25.50


# ---------------------------------------------------------------------------
# Testes Query
# ---------------------------------------------------------------------------
class TestQuery:
    def setup_method(self):
        self.journal = DecisionJournal()
        self._populate_journal()

    def _populate_journal(self):
        """Popula journal com decisoes de teste."""
        self.journal.record("PETR4", DECISION_SIGNAL_BUY, 0.85, "sinal forte")
        self.journal.record("PETR4", DECISION_SIGNAL_SELL, 0.72, "saida parcial")
        self.journal.record("VALE3", DECISION_SIGNAL_BUY, 0.65, "compra vale")
        self.journal.record("PETR4", DECISION_SIGNAL_BLOCKED, 0.0, "bloqueado risco",
                          risk_decision="BLOCKED_BY_RISK")
        self.journal.record("ITUB4", DECISION_SIGNAL_HOLD, 0.45, "score baixo")

    def test_query_by_ativo(self):
        petr4 = self.journal.query(ativo="PETR4")
        assert all(d.ativo == "PETR4" for d in petr4)
        assert len(petr4) == 3

    def test_query_by_sinal(self):
        buys = self.journal.query(sinal=DECISION_SIGNAL_BUY)
        assert all(d.sinal == DECISION_SIGNAL_BUY for d in buys)
        assert len(buys) == 2

    def test_query_by_risk_decision(self):
        blocked = self.journal.query(risk_decision="BLOCKED_BY_RISK")
        assert all(d.risk_decision == "BLOCKED_BY_RISK" for d in blocked)
        assert len(blocked) == 1

    def test_query_combined_filters(self):
        petr4_buys = self.journal.query(ativo="PETR4", sinal=DECISION_SIGNAL_BUY)
        assert len(petr4_buys) == 1
        assert petr4_buys[0].score == 0.85

    def test_query_sorted_by_timestamp(self):
        decisions = self.journal.query()
        timestamps = [d.timestamp_de_processamento for d in decisions]
        assert timestamps == sorted(timestamps)

    def test_query_empty_result(self):
        result = self.journal.query(ativo="ZZZZ9")
        assert result == []


# ---------------------------------------------------------------------------
# Testes Stats
# ---------------------------------------------------------------------------
class TestStats:
    def setup_method(self):
        self.journal = DecisionJournal()
        self._populate_stats_journal()

    def _populate_stats_journal(self):
        self.journal.record("PETR4", DECISION_SIGNAL_BUY, 0.85, "compra")
        self.journal.record("PETR4", DECISION_SIGNAL_BUY, 0.75, "compra 2")
        self.journal.record("VALE3", DECISION_SIGNAL_SELL, 0.70, "venda")
        self.journal.record("PETR4", DECISION_SIGNAL_BLOCKED, 0.0, "bloqueado",
                          risk_decision="BLOCKED_BY_RISK")
        self.journal.record("ITUB4", DECISION_SIGNAL_HOLD, 0.45, "hold")

    def test_total_decisions(self):
        stats = self.journal.get_stats()
        assert stats["total_decisions"] == 5

    def test_trades_executed(self):
        stats = self.journal.get_stats()
        assert stats["trades_executed"] == 3  # 2 BUY + 1 SELL

    def test_blocks_applied(self):
        stats = self.journal.get_stats()
        assert stats["blocks_applied"] == 1

    def test_buy_sell_counts(self):
        stats = self.journal.get_stats()
        assert stats["buy_orders"] == 2
        assert stats["sell_orders"] == 1

    def test_avg_score(self):
        stats = self.journal.get_stats()
        # Apenas trades: (0.85 + 0.75 + 0.70) / 3
        expected_avg = (0.85 + 0.75 + 0.70) / 3
        assert stats["avg_ml_score"] == pytest.approx(expected_avg, abs=0.001)

    def test_blocked_by_risk_count(self):
        stats = self.journal.get_stats()
        assert stats["blocked_by_risk"] == 1


# ---------------------------------------------------------------------------
# Testes Blocked Decisions
# ---------------------------------------------------------------------------
class TestBlockedDecisions:
    def setup_method(self):
        self.journal = DecisionJournal()
        self.journal.record("PETR4", DECISION_SIGNAL_BLOCKED, 0.0, "stale",
                          risk_decision="BLOCKED_BY_RISK")
        self.journal.record("VALE3", DECISION_SIGNAL_BLOCKED, 0.0, "drawdown",
                          risk_decision="BLOCKED_BY_DRAWDOWN")
        self.journal.record("ITUB4", DECISION_SIGNAL_BUY, 0.85, "normal")

    def test_get_blocked_decisions(self):
        blocked = self.journal.get_blocked_decisions()
        assert len(blocked) == 2
        assert all(d.is_blocked for d in blocked)

    def test_get_trades_only(self):
        trades = self.journal.get_trades_only()
        assert len(trades) == 1
        assert trades[0].ativo == "ITUB4"


# ---------------------------------------------------------------------------
# Testes Get Decision History
# ---------------------------------------------------------------------------
class TestDecisionHistory:
    def setup_method(self):
        self.journal = DecisionJournal()
        self.journal.record("PETR4", DECISION_SIGNAL_BUY, 0.80, "compra 1")
        time.sleep(0.01)
        self.journal.record("PETR4", DECISION_SIGNAL_SELL, 0.70, "venda 1")
        self.journal.record("VALE3", DECISION_SIGNAL_BUY, 0.65, "compra vale")

    def test_history_returns_most_recent_first(self):
        history = self.journal.get_decision_history("PETR4", limit=10)
        assert history[0].motivo == "venda 1"
        assert history[1].motivo == "compra 1"

    def test_history_limit(self):
        history = self.journal.get_decision_history("PETR4", limit=1)
        assert len(history) == 1
        assert history[0].motivo == "venda 1"


# ---------------------------------------------------------------------------
# Testes Explain Decision
# ---------------------------------------------------------------------------
class TestExplainDecision:
    def setup_method(self):
        self.journal = DecisionJournal()
        self.journal.record("PETR4", DECISION_SIGNAL_BUY, 0.85, "sinal forte",
                          posicao=0.0, quantidade=100, preco=25.50)
        self.journal.record("PETR4", DECISION_SIGNAL_BLOCKED, 0.0, "bloq risco",
                          risk_decision="BLOCKED_BY_RISK")

    def test_explain_valid_index(self):
        explanation = self.journal.explain_decision(0)
        assert "DECISAO #1" in explanation
        assert "PETR4" in explanation
        assert "BUY" in explanation
        assert "0.850000" in explanation

    def test_explain_blocked_decision(self):
        explanation = self.journal.explain_decision(1)
        assert "BLOCKED" in explanation
        assert "BLOCKED_BY_RISK" in explanation

    def test_explain_out_of_range(self):
        explanation = self.journal.explain_decision(999)
        assert "Erro" in explanation

    def test_explain_negative_index(self):
        explanation = self.journal.explain_decision(-1)
        assert "Erro" in explanation


# ---------------------------------------------------------------------------
# Testes Persistencia
# ---------------------------------------------------------------------------
class TestPersistence:
    def test_save_and_load(self, tmp_path):
        journal = DecisionJournal()
        journal.record("PETR4", DECISION_SIGNAL_BUY, 0.85, "compra teste")
        journal.record("VALE3", DECISION_SIGNAL_SELL, 0.70, "venda teste")
        
        filepath = tmp_path / "journal.json"
        journal.save_to_file(str(filepath))
        
        # Novo journal carrega do arquivo
        loaded = DecisionJournal()
        count = loaded.load_from_file(str(filepath))
        
        assert count == 2
        assert loaded.total_decisions == 2
        assert loaded.query(ativo="PETR4")[0].score == 0.85

    def test_load_nonexistent_file(self, tmp_path):
        journal = DecisionJournal()
        count = journal.load_from_file(str(tmp_path / "nonexistent.json"))
        assert count == 0

    def test_clear_journal(self):
        journal = DecisionJournal()
        journal.record("PETR4", DECISION_SIGNAL_BUY, 0.85, "compra")
        assert journal.total_decisions == 1
        
        journal.clear()
        assert journal.total_decisions == 0
        assert len(journal.entries) == 0


# ---------------------------------------------------------------------------
# Testes Thread Safety
# ---------------------------------------------------------------------------
class TestThreadSafety:
    def test_concurrent_records(self):
        import threading
        
        journal = DecisionJournal()
        errors = []
        
        def record_batch(start, count):
            try:
                for i in range(count):
                    journal.record(
                        f"ATIVO{start + i}",
                        DECISION_SIGNAL_BUY,
                        0.5 + i * 0.01,
                        f"concurrent record {i}",
                    )
            except Exception as e:
                errors.append(e)
        
        threads = [threading.Thread(target=record_batch, args=(i, 10)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert errors == []
        assert journal.total_decisions == 100

    def test_concurrent_queries(self):
        import threading
        
        journal = DecisionJournal()
        for i in range(50):
            journal.record(f"ATIVO{i}", DECISION_SIGNAL_BUY, 0.5, "teste")
        
        queries = []
        errors = []
        
        def query_batch():
            try:
                result = journal.query()
                queries.append(len(result))
            except Exception as e:
                errors.append(e)
        
        threads = [threading.Thread(target=query_batch) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert errors == []
        assert all(q == 50 for q in queries)


# ---------------------------------------------------------------------------
# Testes Global Facade Functions
# ---------------------------------------------------------------------------
class TestFacadeFunctions:
    def setup_method(self):
        reset_journal()

    def test_get_journal_creates_instance(self):
        j1 = get_journal()
        j2 = get_journal()
        assert j1 is j2

    def test_reset_journal(self):
        j1 = get_journal()
        j1.record("PETR4", DECISION_SIGNAL_BUY, 0.85, "teste")
        
        reset_journal()
        j2 = get_journal()
        assert j2 is not j1
        assert j2.total_decisions == 0

    def test_record_decision_facade(self):
        d = record_decision(
            ativo="PETR4",
            sinal=DECISION_SIGNAL_BUY,
            score=0.85,
            motivo="facade test",
        )
        assert d.ativo == "PETR4"
        assert d.score == 0.85

    def test_query_decisions_facade(self):
        record_decision("PETR4", DECISION_SIGNAL_BUY, 0.85, "compra")
        record_decision("VALE3", DECISION_SIGNAL_SELL, 0.70, "venda")
        
        results = query_decisions(ativo="PETR4")
        assert len(results) == 1
        assert results[0].sinal == DECISION_SIGNAL_BUY


# ---------------------------------------------------------------------------
# Testes Integridade de Dados
# ---------------------------------------------------------------------------
class TestDataIntegrity:
    def test_decision_values_preserved(self):
        journal = DecisionJournal()
        d = journal.record("PETR4", DECISION_SIGNAL_BUY, 0.85, "compra")
        
        # Valores devem estar corretos
        assert d.ativo == "PETR4"
        assert d.sinal == DECISION_SIGNAL_BUY
        assert d.score == 0.85
        assert d.motivo == "compra"

    def test_all_required_fields_present(self):
        journal = DecisionJournal()
        d = journal.record(
            ativo="PETR4",
            sinal=DECISION_SIGNAL_BUY,
            score=0.85,
            motivo="completo",
            posicao=100.0,
            quantidade=50,
            preco=25.00,
        )
        
        assert d.timestamp_do_evento > 0
        assert d.timestamp_de_processamento > 0
        assert d.ativo == "PETR4"
        assert d.sinal == DECISION_SIGNAL_BUY
        assert d.score == 0.85
        assert d.motivo == "completo"
        assert d.posicao == 100.0
        assert d.quantidade == 50
        assert d.preco == 25.00

    def test_scores_are_truncated_to_6_decimals(self):
        journal = DecisionJournal()
        d = journal.record("PETR4", DECISION_SIGNAL_BUY, 0.123456789, "teste")
        assert d.score == 0.123457


# ---------------------------------------------------------------------------
# Testes Edge Cases
# ---------------------------------------------------------------------------
class TestEdgeCases:
    def test_empty_journal_stats(self):
        journal = DecisionJournal()
        stats = journal.get_stats()
        assert stats["total_decisions"] == 0
        assert stats["trades_executed"] == 0
        assert stats["blocked_decisions"] == 0
        assert stats["avg_ml_score"] == 0.0

    def test_zero_score(self):
        journal = DecisionJournal()
        d = journal.record("PETR4", DECISION_SIGNAL_HOLD, 0.0, "sem sinal")
        assert d.score == 0.0

    def test_maximum_score(self):
        journal = DecisionJournal()
        d = journal.record("PETR4", DECISION_SIGNAL_BUY, 1.0, "maximo")
        assert d.score == 1.0

    def test_decimal_posicao_negativa(self):
        journal = DecisionJournal()
        d = journal.record("PETR4", DECISION_SIGNAL_SELL, 0.8, "cobrir short",
                          posicao=-150.75)
        assert d.posicao == -150.75

    def test_many_consecutive_records(self):
        journal = DecisionJournal()
        for i in range(1000):
            journal.record("PETR4", DECISION_SIGNAL_BUY, 0.5, f"batch {i}")
        
        assert journal.total_decisions == 1000
        assert journal.trades_executed == 1000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
