"""Submódulo formulas para compatibilidade com check_integration.py"""

from exposure import (
    nominal_exposure,
    risk_at_stop,
    max_profit_at_target,
    points_to_stop,
    points_to_target,
    exposure_risk_ratio,
    risk_reward_ratio,
    stop_price_from_distance,
    stop_price_from_risk,
    aggregate_exposure,
)

__all__ = [
    "nominal_exposure",
    "risk_at_stop",
    "max_profit_at_target",
    "points_to_stop",
    "points_to_target",
    "exposure_risk_ratio",
    "risk_reward_ratio",
    "stop_price_from_distance",
    "stop_price_from_risk",
    "aggregate_exposure",
]
__name__ = "exposure.formulas"
