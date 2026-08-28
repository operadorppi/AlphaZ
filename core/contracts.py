# -*- coding: utf-8 -*-
"""
core/contracts.py — Contratos (dataclasses) partilhados entre camadas.

Estes tipos definem as interfaces entre as camadas da arquitetura:
  - Signal:     saída do SignalEngine (o que o modelo/heurística sugere)
  - Action:     saída do PositionManager (o que fazer com a posição)
  - ExitSignal: saída do PositionManager quando uma posição fecha
  - RiskDecision: saída do RiskManager (pode ou não abrir)
  - Position:   estado de uma posição aberta

Nenhuma lógica de negócio aqui — apenas tipos e validação de integridade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Union, Literal


@dataclass(frozen=True)
class TradeEvent:
    """Evento de negócio individual (T&T)."""
    symbol: str
    timestamp_ms: int
    price: float
    quantity: int
    aggressor: str       # 'Comprador', 'Vendedor', 'neutro'
    buyer: str
    seller: str
    received_at: int     # Timestamp epoch ms do recebimento no Python
    schema_version: str = "1.0"

    def __post_init__(self):
        if self.price <= 0:
            raise ValueError(f"Preço inválido para {self.symbol}: {self.price}")
        if self.quantity <= 0:
            raise ValueError(f"Quantidade inválida para {self.symbol}: {self.quantity}")
        if not self.symbol:
            raise ValueError("Símbolo obrigatório")


@dataclass(frozen=True)
class BookLevel:
    """Um nível de preço no livro de ofertas."""
    price: float
    volume: int
    broker: str = ""


@dataclass(frozen=True)
class BookSnapshot:
    """Estado completo do livro em um instante T."""
    symbol: str
    timestamp_ms: int
    bids: List[BookLevel]
    asks: List[BookLevel]
    received_at: int
    schema_version: str = "1.0"


@dataclass(frozen=True)
class MarketEvent:
    """Envelope unificado para eventos de mercado."""
    type: str           # 'TRADE' ou 'BOOK'
    payload: Union[TradeEvent, BookSnapshot]
    timestamp_ms: int
    symbol: str
    schema_version: str = "1.0"


@dataclass(frozen=True)
class FeatureVector:
    """Vetor de features normalizado enviado ao modelo."""
    symbol: str
    timestamp_ms: int
    features: Dict[str, float]
    schema_version: str = "1.0"


@dataclass(frozen=True)
class Prediction:
    """Resultado bruto da inferência do modelo ML."""
    symbol: str
    timestamp_ms: int
    probability: float
    model_version: str
    schema_version: str = "1.0"


@dataclass
class Signal:
    """Sinal produzido pelo SignalEngine a partir de features."""
    symbol: str
    timestamp_ms: int
    lado: str            # 'C' (compra), 'V' (venda), '' (neutro)
    score: float         # 0.0 a 1.0
    confianca: float     # EWMA do score
    motivos: list[str]   # Razões textuais do sinal
    contrib: list[Any]   # Contribuição por feature
    tp: float = 0.0
    sl: float = 0.0
    ml_prob: float = 0.5
    preco_ref: float = 0.0
    horizonte: int = 60
    schema_version: str = "1.0"

    def __post_init__(self):
        if self.lado not in ('C', 'V', ''):
            raise ValueError(f"Lado inválido: {self.lado}")


@dataclass
class Action:
    """Decisão operacional enviada para execução."""
    tipo: str            # 'ABRIR', 'FECHAR', 'MANTER'
    lado: str            # 'C', 'V'
    preco: float
    tp: float            # Take-profit
    sl: float            # Stop-loss
    motivo: str
    pnl: float = 0.0     # PnL realizado (usado em FECHAR)
    schema_version: str = "1.0"


@dataclass
class ExitSignal:
    """Sinal de saída de uma posição."""
    symbol: str
    preco: float
    motivo: str          # 'TP', 'SL', 'REVERSAO', 'TEMPO', 'SHUTDOWN'
    pnl: float
    holding_s: float     # Tempo em posição (segundos)
    schema_version: str = "1.0"


@dataclass
class RiskDecision:
    """Resposta do RiskManager sobre permissão de abrir posição."""
    symbol: str
    timestamp_ms: int
    permitido: bool
    motivo: str          # 'OK', 'COOLDOWN', 'CIRCUIT_BREAKER', etc.
    size: int = 0
    tp: float = 0.0
    sl: float = 0.0
    risk_score: float = 0.0
    cooldown_restante: float = 0.0
    schema_version: str = "1.0"


@dataclass
class Position:
    """Estado de uma posição aberta."""
    symbol: str
    lado: str            # 'C', 'V'
    preco_entrada: float
    tp: float
    sl: float
    aberta_em: float     # Timestamp epoch
    regime_abertura: Optional[str] = None
    stop_preco: Optional[float] = None  # Trailing stop
    breakeven: bool = False
    schema_version: str = "1.0"


@dataclass(frozen=True)
class OrderIntent:
    """Intenção de envio de ordem para a corretora/exchange."""
    symbol: str
    timestamp_ms: int
    lado: Literal['C', 'V']
    preco: float
    quantidade: int
    tipo_ordem: Literal['MARKET', 'LIMIT', 'STOP']
    signal_id: str
    schema_version: str = "1.0"


@dataclass(frozen=True)
class ExecutionReport:
    """Relatório de execução retornado pelo broker."""
    symbol: str
    timestamp_ms: int
    order_id: str
    status: Literal['FILLED', 'PARTIAL', 'REJECTED', 'CANCELED']
    preco_executado: float
    quantidade_executada: int
    slippage: float
    schema_version: str = "1.0"


@dataclass(frozen=True)
class MarketRegime:
    """Classificação atual do regime de mercado."""
    symbol: str
    timestamp_ms: int
    regime: str          # Ex: 'tendencia_alta', 'lateral_vol_alta'
    direcao: Literal['alta', 'baixa', 'neutro']
    vol: Literal['alta', 'normal', 'baixa']
    schema_version: str = "1.0"


@dataclass(frozen=True)
class DatasetManifest:
    """Metadados de um dataset para treinamento ou backtest."""
    symbol: str
    data_inicio: str
    data_fim: str
    n_linhas: int
    features: List[str]
    hash_sha256: str
    schema_version: str = "1.0"


@dataclass(frozen=True)
class ModelMetadata:
    """Metadados de um modelo treinado."""
    nome: str
    versao: str
    data_treino: str
    accuracy_val: float
    profit_factor_val: float
    features_count: int
    hyperparams: Dict[str, Any]
    schema_version: str = "1.0"
