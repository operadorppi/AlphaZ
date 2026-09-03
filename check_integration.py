"""Smoke test de integracao ponta a ponta (FASE 7 P1 + FASE 8 P1 + FASE 9 P1)."""

import inspect
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from exposure import (  # noqa: E402
    Direction,
    Position,
    aggregate_exposure,
    exposure_risk_ratio,
    max_profit_at_target,
    nominal_exposure,
    risk_at_stop,
    risk_reward_ratio,
    signed_notional,
    stop_price_from_distance,
    stop_price_from_risk,
)
from exposure import direction, formulas, position, portfolio  # noqa: E402

print("=== 1) imports do pacote OK ===")
print(f"  direction: {direction.__name__}")
print(f"  position:  {position.__name__}")
print(f"  formulas:  {formulas.__name__}")
print(f"  portfolio: {portfolio.__name__}")

print("\n=== 2) agregacao delega as formulas para formulas.py (mesma origem) ===")
src = inspect.getsource(portfolio)
assert "def nominal_exposure" not in src
assert "def risk_at_stop" not in src
assert "from .formulas import" in src
print("  portfolio.py NAO redefine E/R; importa de formulas.py [OK]")

print("\n=== 3) fluxo ponta a ponta: WIN + LOSS hedge + posicao sem stop ===")
win = Position("WIN", Direction.WIN, 10, "150000", "0.20", stop="149800", target="150400")
loss = Position("WIN", Direction.LOSS, 10, "150000", "0.20", stop="150200", target="149600")
dolz = Position("DOL", Direction.LOSS, 5, "5.00", "0.50", stop="5.05")
noseg = Position("WING", Direction.WIN, 3, "100", "0.10")  # sem stop/alvo

assert nominal_exposure(win) == Decimal("300000")
assert risk_at_stop(win) == Decimal("400")
assert max_profit_at_target(win) == Decimal("800")
assert risk_reward_ratio(win) == Decimal("2")
assert exposure_risk_ratio(win) == Decimal("400") / Decimal("300000")
assert signed_notional(loss) == -Decimal("300000")
print("  metricas individuais [OK]")

assert stop_price_from_risk(win, "400") == Decimal("149800")
assert stop_price_from_distance(win, "200") == Decimal("149800")
print("  stop por moeda e por pontos consistentes [OK]")

agg = aggregate_exposure([win, loss, dolz, noseg])
assert agg.total_nominal_exposure == Decimal("300000") + Decimal("300000") + Decimal("12.5") + Decimal("30")
assert agg.total_max_risk == Decimal("400") + Decimal("400") + Decimal("0.125")  # noseg excluido
assert agg.net_exposure == Decimal("30") - Decimal("12.5")  # hedge WIN se cancela
assert agg.net_by_symbol["WIN"] == Decimal("0")
assert agg.positions_without_stop == 1
print(f"  E_agg = {agg.total_nominal_exposure}")
print(f"  R_agg = {agg.total_max_risk}")
print(f"  E_net = {agg.net_exposure}  (WIN hedge: {agg.net_by_symbol['WIN']})")
print("  agregacao integrada [OK]")

print("\n=== 4) Position.with_stop_risk / with_stop_distance (derivam via formulas.py) ===")
p2 = Position("DOL", Direction.LOSS, 2, "5.50", "0.10").with_stop_risk("0.01")
assert p2.stop == stop_price_from_risk(Position("DOL", Direction.LOSS, 2, "5.50", "0.10"), "0.01")
assert risk_at_stop(p2) == Decimal("0.01")
print("  metodos de Position reutilizam as mesmas formulas [OK]")

print("\n=== 5) FASE 8 P1: gate de ML (mlgate) ===")
from mlgate import MlAvailability, PRODUCTION_POLICY, DEVELOPMENT_POLICY, evaluate_gate, MLDecisionLog  # noqa: E402

ml_down = MlAvailability.down("timeout no servico de scoring")
d = evaluate_gate(ml_down, PRODUCTION_POLICY, heuristic_decision=lambda: True)
assert d.allowed is False and d.decision_source == "BLOCKED"
d2 = evaluate_gate(ml_down, DEVELOPMENT_POLICY, heuristic_decision=lambda: True)
assert d2.allowed is True and d2.decision_source == "HEURISTIC_FALLBACK"
log8 = MLDecisionLog()
log8.record(d)
log8.record(d2)
log8.assert_no_hidden_ml_absence()
print("  producao bloqueia (ML fora + obrigatorio) [OK]")
print("  dev usa fallback registrado [OK]")

print("\n=== 6) FASE 9 P1: replay gate por ambiente (replaygate) ===")
from replaygate import (  # noqa: E402
    Environment, GateDecisionLog, ReplayStatus,
    environment_policy, evaluate_replay_gate,
)

replay_no = ReplayStatus.pending("replay pendente da v2.3")
replay_ok = ReplayStatus.validated_()

prod = environment_policy(Environment.PRODUCTION)
assert prod.require_replay_validated is True
dp = evaluate_replay_gate(MlAvailability.up(), replay_no, prod, heuristic_decision=lambda: True)
assert dp.allowed is False and dp.decision_source == "BLOCKED"
assert dp.replay_reason == "replay pendente da v2.3"
print("  PRODUCTION: replay pendente -> BLOQUEADO (mesmo com ML ok) [OK]")

dev = environment_policy(Environment.DEVELOPMENT)
assert dev.require_replay_validated is False
dd = evaluate_replay_gate(MlAvailability.up(), replay_no, dev, heuristic_decision=lambda: True)
assert dd.allowed is True and dd.decision_source == "ML"
print("  DEVELOPMENT: replay pendente -> informativo (operou) [OK]")

gate_log = GateDecisionLog()
gate_log.record(dp)
gate_log.record(dd)
gate_log.assert_no_hidden_ml_absence()
gate_log.assert_no_hidden_replay_absence()
print("  auditoria FASE 8 + FASE 9 integrada [OK]")

print("\nINTEGRACAO OK: FASE 7 (exposure), FASE 8 (mlgate) e FASE 9 (replaygate) usam as mesmas bases e nao se contradizem.")
