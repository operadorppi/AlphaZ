"""FASE 12 P1 — Auditoria completa do sistema de testes.

Categorias analisadas:
1. Unitários (test_position, test_formulas) - existentes ✓
2. Integração (test_aggregate, test_config) - existentes ✓
3. End-to-end (teste_replaygate) - existente ✓
4. Replay (test_replaygate) - existente ✓
5. Causalidade - NOVO
6. Temporalidade - NOVO
7. Carga - NOVO
8. Recuperação - NOVO
9. Falhas RTD - NOVO

Problemas encontrados na auditoria:
- test_config.py linha 156: variável 'd' não definida (erro de escopo corrigido)
- Ausência de testes nas categorias 5-9
"""

import asyncio
import json
import os
import tempfile
import time
from decimal import Decimal

import pytest

from exposure import Direction, Position, aggregate_exposure
from mlgate import DECISION_SOURCE_BLOCKED, MlAvailability, evaluate_gate, PRODUCTION_POLICY
from replaygate import (
    Environment,
    evaluate_replay_gate,
    GateDecision,
    GateDecisionLog,
    ReplayStatus,
    PRODUCTION_ENV_POLICY,
)


class TestAuditReport:
    """Relatório da auditoria dos testes existentes."""

    def test_all_test_files_exist(self):
        """Verifica se todos os arquivos de teste existem."""
        test_dir = os.path.dirname(__file__)
        expected = [
            "test_position.py",
            "test_formulas.py",
            "test_aggregate.py",
            "test_invariants.py",
            "test_mlgate.py",
            "test_replaygate.py",
            "test_config.py",
        ]
        actual = [f for f in os.listdir(test_dir) if f.startswith("test_") and f.endswith(".py")]
        for exp in expected:
            assert exp in actual, f"Faltando: {exp}"

    def test_total_tests_count(self):
        """Verifica quantidade mínima de testes (>= 137)."""
        import subprocess
        result = subprocess.run(
            ["python", "-m", "pytest", "--co", "-q"],
            capture_output=True, text=True, cwd=os.path.dirname(__file__)
        )
        for line in result.stdout.splitlines():
            if "collected" in line:
                count = int(line.split()[1])
                assert count >= 137, f"Esperado >= 137 testes, encontrado {count}"
                return
        pytest.fail("Não encontrou contagem de testes")

    def test_no_pass_trap_tests(self):
        """Verifica se existem testes que são apenas 'pass'."""
        import ast
        test_dir = os.path.dirname(__file__)
        for filename in os.listdir(test_dir):
            if not filename.endswith(".py") or not filename.startswith("test_"):
                continue
            filepath = os.path.join(test_dir, filename)
            with open(filepath, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                    if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                        pytest.fail(
                            f"Teste 'pass trap' em {filename}:{node.lineno} - "
                            f"{node.name} não faz nenhuma afirmação"
                        )


class TestCausalidade:
    """Testes de causalidade: input específico → output previsível."""

    def test_stop_price_determines_risk(self):
        """Causalidade: mudar stop altera risco proporcionalmente."""
        from exposure import risk_at_stop
        p1 = Position("WIN", Direction.WIN, 10, "150000", "0.20", stop="149800")  # 200 pts
        p2 = Position("WIN", Direction.WIN, 10, "150000", "0.20", stop="149900")  # 100 pts (mais perto)
        r1, r2 = risk_at_stop(p1), risk_at_stop(p2)
        assert r2 < r1  # stop mais perto = MENOR risco
        assert r1 / r2 == Decimal("2")  # exatamente a metade

    def test_quantity_doubles_exposure(self):
        """Causalidade: dobrar quantidade dobra exposição e risco."""
        from exposure import nominal_exposure, risk_at_stop
        p1 = Position("WIN", Direction.WIN, 10, "150000", "0.20", stop="149800")
        p2 = Position("WIN", Direction.WIN, 20, "150000", "0.20", stop="149800")
        assert nominal_exposure(p2) == 2 * nominal_exposure(p1)
        assert risk_at_stop(p2) == 2 * risk_at_stop(p1)

    def test_ml_availability_determines_decision_source(self):
        """Causalidade: ML disponível → source=ML; ML indisponível → source=BLOCKED."""
        d_up = evaluate_gate(MlAvailability.up(), PRODUCTION_POLICY)
        d_down = evaluate_gate(MlAvailability.down("falha"), PRODUCTION_POLICY)
        assert d_up.decision_source == "ML"
        assert d_down.decision_source == DECISION_SOURCE_BLOCKED

    def test_replay_status_determines_production_block(self):
        """Causalidade: replay pendente em produção bloqueia."""
        d_ok = evaluate_replay_gate(
            MlAvailability.up(), ReplayStatus.validated_(), PRODUCTION_ENV_POLICY
        )
        d_pending = evaluate_replay_gate(
            MlAvailability.up(), ReplayStatus.pending("pendente"), PRODUCTION_ENV_POLICY
        )
        assert d_ok.allowed is True
        assert d_pending.allowed is False

    def test_config_priority_causality(self):
        """Causalidade: overrides > JSON > defaults."""
        from config import load_config
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "config.json")
            with open(cfg_path, "w") as f:
                json.dump({"ml_required": False}, f)
            c_default = load_config(path=cfg_path)
            c_override = load_config(path=cfg_path, overrides={"ml_required": True})
            assert c_default.ml_required is False
            assert c_override.ml_required is True


class TestTemporalidade:
    """Testes de temporalidade: comportamento consistente ao longo do tempo."""

    def test_same_input_same_output_deterministic(self):
        """Determinismo: mesma entrada produz mesma saída."""
        from exposure import nominal_exposure, risk_at_stop
        p = Position("WIN", Direction.WIN, 10, "150000", "0.20", stop="149800")
        results = [(nominal_exposure(p), risk_at_stop(p)) for _ in range(100)]
        assert all(r == results[0] for r in results)

    def test_gate_consistent_across_calls(self):
        """Consistência: gate produz mesmo resultado para mesmas entradas."""
        decisions = []
        for _ in range(50):
            d = evaluate_gate(MlAvailability.up(), PRODUCTION_POLICY)
            decisions.append(d.decision_source)
        assert all(d == decisions[0] for d in decisions)

    def test_config_persistent_across_loads(self):
        """Persistência: config carregada mantêm valores."""
        from config import load_config
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "config.json")
            with open(cfg_path, "w") as f:
                json.dump({"environment": "PRODUCTION"}, f)
            c1 = load_config(path=cfg_path)
            c2 = load_config(path=cfg_path)
            assert c1.environment == c2.environment == "PRODUCTION"
            assert c1.require_replay_validated == c2.require_replay_validated is True

    def test_portfolio_snapshot_isolated(self):
        """Isolamento: portfólios não interferem entre si."""
        p1 = Position("WIN", Direction.WIN, 10, "150000", "0.20", stop="149800")
        p2 = Position("WIN", Direction.LOSS, 10, "150000", "0.20", stop="150200")
        agg1 = aggregate_exposure([p1])
        agg2 = aggregate_exposure([p2])
        agg_both = aggregate_exposure([p1, p2])
        assert agg1.total_nominal_exposure == Decimal("300000")
        assert agg2.total_nominal_exposure == Decimal("300000")
        assert agg_both.net_exposure == Decimal("0")  # hedge perfeito


class TestCarga:
    """Testes de carga: comportamento sob estresse."""

    def test_large_portfolio_aggregation(self):
        """Agregação com 1000 posições."""
        positions = []
        for i in range(1000):
            if i % 2 == 0:
                # WIN (long): stop below price
                positions.append(Position(
                    f"ASSET{i}", Direction.WIN,
                    Decimal(i + 1), Decimal("100000"), Decimal("0.10"),
                    stop=Decimal("99000"), target=Decimal("101000")
                ))
            else:
                # LOSS (short): stop above price
                positions.append(Position(
                    f"ASSET{i}", Direction.LOSS,
                    Decimal(i + 1), Decimal("100000"), Decimal("0.10"),
                    stop=Decimal("101000"), target=Decimal("99000")
                ))
        start = time.time()
        agg = aggregate_exposure(positions)
        elapsed = time.time() - start
        assert elapsed < 1.0, f"Agregação levou {elapsed:.2f}s (> 1s)"
        assert agg.total_nominal_exposure > 0
        assert len(agg.metrics) == 1000

    def test_many_gate_evaluations(self):
        """Avaliação repetida do gate."""
        start = time.time()
        for _ in range(1000):
            evaluate_gate(MlAvailability.up(), PRODUCTION_POLICY)
        elapsed = time.time() - start
        assert elapsed < 0.5, f"Gate levou {elapsed:.2f}s (> 0.5s)"

    def test_many_config_loads(self):
        """Carregamentos repetidos de config."""
        from config import load_config
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "config.json")
            with open(cfg_path, "w") as f:
                json.dump({"environment": "PRODUCTION", "ml_required": True}, f)
            start = time.time()
            for _ in range(100):
                load_config(path=cfg_path)
            elapsed = time.time() - start
            assert elapsed < 0.5, f"Config loads levaram {elapsed:.2f}s (> 0.5s)"

    def test_stress_invariant_validation(self):
        """Validação de invariantes com many random samples."""
        import random
        rng = random.Random(12345)
        positions = []
        for _ in range(1000):
            q = Decimal(rng.randint(1, 100))
            price = Decimal(str(round(rng.uniform(100, 200_000), 2)))
            pv = Decimal(str(round(rng.uniform(0.05, 5.0), 2)))
            d_stop = price * Decimal(str(round(rng.uniform(0.001, 0.05), 4)))
            d_tgt = price * Decimal(str(round(rng.uniform(0.001, 0.08), 4)))
            d = rng.choice([Direction.WIN, Direction.LOSS])
            sgn = Decimal(d.sign)
            stop = price - sgn * d_stop
            target = price + sgn * d_tgt
            positions.append(Position(f"A{rng.randint(0,99)}", d, q, price, pv, stop=stop, target=target))
        agg = aggregate_exposure(positions)
        assert agg.total_nominal_exposure > 0
        assert agg.total_max_risk > 0


class TestRecuperacao:
    """Testes de recuperação: sistema recupera de estados errados."""

    def test_recovery_after_invalid_position_creation(self):
        """Recuperação: após erro na criação, novo position é válido."""
        with pytest.raises(ValueError):
            Position("WIN", Direction.WIN, 0, "100", "0.10")
        p = Position("WIN", Direction.WIN, 10, "100", "0.10", stop="99")
        assert p.quantity == 10

    def test_recovery_after_invalid_config(self):
        """Recuperação: após ConfigError, novo load funciona."""
        from config import ConfigError, load_config
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "config.json")
            # Arquivo inválido
            with open(cfg_path, "w") as f:
                json.dump({"max_drawdown_dia": 0}, f)
            with pytest.raises(ConfigError):
                load_config(path=cfg_path)
            # Arquivo válido funciona
            with open(cfg_path, "w") as f:
                json.dump({"max_drawdown_dia": 0.02}, f)
            c = load_config(path=cfg_path)
            assert c.max_drawdown_dia == Decimal("0.02")

    def test_recovery_after_blocked_decision(self):
        """Recuperação: após decisão bloqueada, nova avaliação funciona."""
        d1 = evaluate_gate(MlAvailability.down("falha"), PRODUCTION_POLICY)
        assert d1.allowed is False
        d2 = evaluate_gate(MlAvailability.up(), PRODUCTION_POLICY)
        assert d2.allowed is True

    def test_log_recovers_after_rejected_entry(self):
        """Log recupera após rejeitar entrada inválida."""
        from mlgate import MLDecisionLog, Decision
        log = MLDecisionLog()
        fake = Decision(
            allowed=True, decision_source="ML",
            ml_available=False, ml_unavailable_reason="x",
            heuristic_decision=None, policy_label="t", note="fake"
        )
        with pytest.raises(ValueError):
            log.record(fake)
        assert log.total == 0
        log.assert_no_hidden_ml_absence()


class TestFalhasRTD:
    """Testes de falhas de Runtime Delivery: comportamento em condições adversas."""

    def test_partial_portfolio_with_missing_stops(self):
        """Cenário RTD: portfolio com algumas posições sem stop."""
        p1 = Position("WIN", Direction.WIN, 10, "150000", "0.20", stop="149800")
        p2 = Position("WIN", Direction.WIN, 5, "100000", "0.10")
        agg = aggregate_exposure([p1, p2])
        assert agg.positions_without_stop == 1
        assert agg.total_max_risk == Decimal("400")
        assert agg.total_nominal_exposure == Decimal("300000") + Decimal("50000")

    def test_concurrent_gate_evaluations(self):
        """RTD: múltiplas avaliações concorrentes do gate."""
        async def eval_many(n):
            tasks = [asyncio.to_thread(evaluate_gate, MlAvailability.up(), PRODUCTION_POLICY)
                     for _ in range(n)]
            return await asyncio.gather(*tasks)
        decisions = asyncio.run(eval_many(100))
        assert all(d.allowed for d in decisions)
        assert all(d.decision_source == "ML" for d in decisions)

    def test_rapid_state_changes(self):
        """RTD: estado muda rapidamente (ML sobe/desce)."""
        decisions = []
        for up in [True, False, True, False, True]:
            avail = MlAvailability.up() if up else MlAvailability.down("oscila")
            decisions.append(evaluate_gate(avail, PRODUCTION_POLICY))
        assert decisions[0].allowed is True
        assert decisions[1].allowed is False
        assert decisions[2].allowed is True

    def test_config_migration_scenario(self):
        """RTD: migração de config legado para novo formato."""
        from config import load_config
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "config.json")
            with open(cfg_path, "w") as f:
                json.dump({
                    "drawdown_max_dia": 0.03,
                    "ml_obrigatorio": True,
                    "usar_fallback": False,
                }, f)
            c = load_config(path=cfg_path)
            assert c.max_drawdown_dia == Decimal("0.03")
            assert c.ml_required is True
            assert c.fallback_enabled is False
            assert "drawdown_max_dia" in c.legacy_used
            assert "ml_obrigatorio" in c.legacy_used
            assert "usar_fallback" in c.legacy_used

    def test_error_isolation_between_phases(self):
        """RTD: erro em fase não afeta outras."""
        with pytest.raises(ValueError):
            Position("WIN", Direction.WIN, -1, "100", "0.10")
        d = evaluate_gate(MlAvailability.up(), PRODUCTION_POLICY)
        assert d.allowed is True
        d = evaluate_replay_gate(MlAvailability.up(), ReplayStatus.validated_(), PRODUCTION_ENV_POLICY)
        assert d.allowed is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
