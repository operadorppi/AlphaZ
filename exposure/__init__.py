"""exposure — Cálculo de exposição financeira (FASE 7).

Este módulo implementa as fórmulas corretas de exposição nominal e risco,
corrigindo o bug da FASE 4 que usava TP + SL como medida de exposição.

Fórmulas corretas:
    E = N * P * V          # exposição nominal (notional)
    R = d_stop * N * V     # risco máximo no stop
    L = d_target * N * V   # lucro máximo no alvo

Onde:
    N = quantidade de contratos
    P = preço de entrada
    V = valor do ponto em moeda
    d_stop = distância do stop em pontos
    d_target = distância do alvo em pontos

Documentação completa: docs/FORMULAS.md
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional, Dict, Any

__all__ = [
    "Direction",
    "Position",
    "PositionMetric",
    "AggregateResult",
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
    "signed_notional",
]


class _Direction:
    """Direção da posição (com sinal numérico)."""
    def __init__(self, name: str, sign: int):
        self._name = name
        self._sign = sign

    @property
    def sign(self) -> int:
        return self._sign

    def __repr__(self) -> str:
        return f"Direction.{self._name}"

    def __eq__(self, other):
        if isinstance(other, _Direction):
            return self._name == other._name
        if isinstance(other, str):
            return self._name == other
        return NotImplemented

    def __hash__(self):
        return hash(self._name)


class Direction:
    """Direção da posição."""
    WIN = _Direction("WIN", 1)      # Long (compra)
    LOSS = _Direction("LOSS", -1)   # Short (venda)


@dataclass(frozen=True)
class Position:
    """Representação de uma posição de trading.
    
    Args:
        asset: Símbolo do ativo (ex: "WINV26")
        direction: Direção da posição (Direction.WIN ou Direction.LOSS)
        quantity: Quantidade de contratos (N > 0)
        price: Preço de entrada em pontos (P > 0)
        point_value: Valor do ponto em moeda (V > 0)
        stop: Preço do stop em pontos (opcional)
        target: Preço do alvo/TP em pontos (opcional)
    
    Raises:
        ValueError: Se quantidades ou valores forem inválidos
    """
    
    asset: str
    direction: Direction
    quantity: int
    price: Decimal
    point_value: Decimal
    stop: Optional[Decimal] = None
    target: Optional[Decimal] = None
    
    def __post_init__(self):
        """Valida os campos após criação."""
        # Validar asset
        if not isinstance(self.asset, str) or not self.asset.strip():
            raise ValueError(f"asset must be a non-empty string, got {self.asset!r}")
        
        # Validar direction
        if not isinstance(self.direction, _Direction):
            raise TypeError(
                f"direction must be Direction.WIN or Direction.LOSS, got {self.direction!r}"
            )
        
        # Converter strings/floats para Decimal
        if isinstance(self.price, (str, float)):
            object.__setattr__(self, 'price', Decimal(str(self.price)))
        if isinstance(self.point_value, (str, float)):
            object.__setattr__(self, 'point_value', Decimal(str(self.point_value)))
        if isinstance(self.stop, (str, float)):
            object.__setattr__(self, 'stop', Decimal(str(self.stop)))
        if isinstance(self.target, (str, float)):
            object.__setattr__(self, 'target', Decimal(str(self.target)))
        
        if self.quantity <= 0:
            raise ValueError(f"quantity must be > 0, got {self.quantity}")
        if self.price <= 0:
            raise ValueError(f"price must be > 0, got {self.price}")
        if self.point_value <= 0:
            raise ValueError(f"point_value must be > 0, got {self.point_value}")
        
        # Validações de stop/target
        if self.stop is not None:
            if self.direction == Direction.WIN:
                if self.stop >= self.price:
                    raise ValueError("stop do lado errado: stop >= entry para WIN")
            else:  # LOSS
                if self.stop <= self.price:
                    raise ValueError("stop do lado errado: stop <= entry para LOSS")
        
        if self.target is not None:
            if self.direction == Direction.WIN:
                if self.target <= self.price:
                    raise ValueError("alvo do lado errado: target <= entry para WIN")
            else:  # LOSS
                if self.target >= self.price:
                    raise ValueError("alvo do lado errado: target >= entry para LOSS")
    
    @property
    def sign(self) -> int:
        """Retorna o sinal da posição: +1 para WIN, -1 para LOSS."""
        return 1 if self.direction == Direction.WIN else -1
    
    @property
    def signed_quantity(self) -> Decimal:
        """Retorna a quantidade com sinal (para netting)."""
        return Decimal(self.sign * self.quantity)
    
    def with_stop_risk(self, risk_amount: Decimal) -> "Position":
        """Retorna uma nova posição com stop calculado a partir de risco em moeda."""
        new_stop = stop_price_from_risk(self, risk_amount)
        return Position(
            asset=self.asset,
            direction=self.direction,
            quantity=self.quantity,
            price=self.price,
            point_value=self.point_value,
            stop=new_stop,
            target=self.target,
        )
    
    def with_stop_distance(self, distance_pts: Decimal) -> "Position":
        """Retorna uma nova posição com stop calculado a partir de distância em pontos."""
        new_stop = stop_price_from_distance(self, distance_pts)
        return Position(
            asset=self.asset,
            direction=self.direction,
            quantity=self.quantity,
            price=self.price,
            point_value=self.point_value,
            stop=new_stop,
            target=self.target,
        )


@dataclass
class PositionMetric:
    """Métricas de uma posição individual na agregação."""
    asset: str
    nominal_exposure: Decimal
    signed_notional: Decimal
    risk_at_stop: Decimal | None
    max_profit_at_target: Decimal | None


@dataclass
class AggregateResult:
    """Resultado da agregação de exposição."""
    total_nominal_exposure: Decimal
    total_max_risk: Decimal
    net_exposure: Decimal
    net_by_symbol: Dict[str, Decimal]
    positions_without_stop: int
    metrics: list = None

    def __post_init__(self):
        if self.metrics is None:
            self.metrics = []

    @property
    def risk_to_nominal_ratio(self) -> Decimal:
        """Razão risco/exposição total."""
        if self.total_nominal_exposure == 0:
            return Decimal("0")
        return self.total_max_risk / self.total_nominal_exposure


def nominal_exposure(pos: Position) -> Decimal:
    """Calcula a exposição nominal (E) da posição.
    
    E = N * P * V
    
    Args:
        pos: Posição de trading
    
    Returns:
        Exposição nominal em moeda
    """
    return pos.quantity * pos.price * pos.point_value


def signed_notional(pos: Position) -> Decimal:
    """Calcula o notional assinado (E_s) para netting.
    
    E_s = sgn * N * P * V
    
    Args:
        pos: Posição de trading
    
    Returns:
        Notional assinado em moeda (+WIN, -LOSS)
    """
    return pos.sign * nominal_exposure(pos)


def risk_at_stop(pos: Position) -> Decimal:
    """Calcula o risco máximo no stop (R).

    R = d_stop * N * V

    Args:
        pos: Posição de trading

    Returns:
        Risco máximo em moeda

    Raises:
        ValueError: Se a posição não tiver stop definido
    """
    if pos.stop is None:
        raise ValueError(f"Position {pos.asset} sem stop definido")
    
    d_stop = points_to_stop(pos)
    return d_stop * pos.quantity * pos.point_value


def max_profit_at_target(pos: Position) -> Decimal | None:
    """Calcula o lucro máximo no alvo (L).

    L = d_target * N * V

    Args:
        pos: Posição de trading

    Returns:
        Lucro máximo em moeda, ou None se sem target
    """
    if pos.target is None:
        return None
    
    d_target = points_to_target(pos)
    return d_target * pos.quantity * pos.point_value


def points_to_stop(pos: Position) -> Decimal:
    """Calcula a distância do stop em pontos.
    
    Args:
        pos: Posição de trading
    
    Returns:
        Distância do stop em pontos (sempre positivo)
    """
    if pos.stop is None:
        raise ValueError(f"Position {pos.asset} has no stop defined")
    
    if pos.direction == Direction.WIN:
        return pos.price - pos.stop
    else:
        return pos.stop - pos.price


def points_to_target(pos: Position) -> Decimal:
    """Calcula a distância do alvo em pontos.
    
    Args:
        pos: Posição de trading
    
    Returns:
        Distância do alvo em pontos (sempre positivo)
    """
    if pos.target is None:
        raise ValueError(f"Position {pos.asset} has no target defined")
    
    if pos.direction == Direction.WIN:
        return pos.target - pos.price
    else:
        return pos.price - pos.target


def exposure_risk_ratio(pos: Position) -> Decimal | None:
    """Calcula a razão exposure/risk (R/E).

    Args:
        pos: Posição de trading

    Returns:
        Razão R/E, ou None se sem stop
    """
    if pos.stop is None:
        return None
    
    E = nominal_exposure(pos)
    R = risk_at_stop(pos)
    return R / E


def risk_reward_ratio(pos: Position) -> Decimal | None:
    """Calcula a razão risk/reward (R:R).

    R:R = L / R (lucro potencial / risco potencial)

    Args:
        pos: Posição de trading

    Returns:
        Razão risk/reward, ou None se sem stop ou target
    """
    if pos.stop is None or pos.target is None:
        return None
    
    R = risk_at_stop(pos)
    L = max_profit_at_target(pos)
    return L / R


def stop_price_from_distance(pos: Position, distance_pts) -> Decimal:
    """Calcula o preço do stop baseado numa distância em pontos.

    Args:
        pos: Posição de referência
        distance_pts: Distância em pontos (str or Decimal)

    Returns:
        Preço do stop calculado

    Raises:
        ValueError: Se distance_pts <= 0
    """
    if isinstance(distance_pts, str):
        distance_pts = Decimal(distance_pts)
    if distance_pts <= 0:
        raise ValueError(f"distance_points must be > 0, got {distance_pts}")
    if pos.direction == Direction.WIN:
        return pos.price - distance_pts
    else:
        return pos.price + distance_pts


def stop_price_from_risk(pos: Position, risk_amount) -> Decimal:
    """Calcula o preço do stop baseado num risco em moeda.

    R = d_stop * N * V  =>  d_stop = R / (N * V)

    Args:
        pos: Posição de referência
        risk_amount: Risco desejado em moeda (str ou Decimal)

    Returns:
        Preço do stop calculado

    Raises:
        ValueError: Se risk_amount <= 0
    """
    if isinstance(risk_amount, str):
        risk_amount = Decimal(risk_amount)
    if risk_amount <= 0:
        raise ValueError(f"risk_amount must be > 0, got {risk_amount}")
    if pos.point_value <= 0 or pos.quantity <= 0:
        raise ValueError("point_value and quantity must be positive")
    
    distance_pts = risk_amount / (pos.quantity * pos.point_value)
    return stop_price_from_distance(pos, distance_pts)


def aggregate_exposure(positions: list) -> AggregateResult:
    """Calcula métricas agregadas de exposição para uma carteira.

    Args:
        positions: Lista de posições

    Returns:
        AggregateResult com:
            - total_nominal_exposure: Soma das exposições brutas (|E|)
            - total_max_risk: Soma dos riscos (pior caso)
            - net_exposure: Exposição líquida com sinal
            - net_by_symbol: Exposição líquida por símbolo
            - positions_without_stop: Número de posições sem stop
            - metrics: Lista de PositionMetric por posição
    """
    E_gross = Decimal("0")
    R_total = Decimal("0")
    E_net = Decimal("0")
    net_by_symbol: Dict[str, Decimal] = {}
    positions_without_stop = 0
    metrics = []
    
    for pos in positions:
        E = nominal_exposure(pos)
        E_gross += abs(E)
        E_net += pos.sign * E
        
        # Acumula por símbolo
        sym = pos.asset
        if sym not in net_by_symbol:
            net_by_symbol[sym] = Decimal("0")
        net_by_symbol[sym] += pos.sign * E
        
        # Risco
        try:
            R = risk_at_stop(pos)
            R_total += R
        except ValueError:
            R = None
            positions_without_stop += 1
        
        L = max_profit_at_target(pos)
        metrics.append(PositionMetric(
            asset=pos.asset,
            nominal_exposure=E,
            signed_notional=pos.sign * E,
            risk_at_stop=R,
            max_profit_at_target=L,
        ))
    
    return AggregateResult(
        total_nominal_exposure=E_gross,
        total_max_risk=R_total,
        net_exposure=E_net,
        net_by_symbol=net_by_symbol,
        positions_without_stop=positions_without_stop,
        metrics=metrics,
    )
