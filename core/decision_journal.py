# -*- coding: utf-8 -*-
"""FASE 20 P1 — Decision Journal (audit trail de decisões de trading).

Cada decisão de trading é registrada com contexto completo para auditoria,
permitindo responder: "Por que o sistema tomou essa decisão?"

Campos registrados em TradeDecision:
- timestamp_do_evento: quando o evento foi recebido (RTD)
- timestamp_de_processamento: quando a decisão foi tomada
- ativo: símbolo do ativo (PETR4, VALE3, etc.)
- sinal: BUY / SELL / HOLD / BLOCKED
- score: pontuação do modelo ML (0.0-1.0)
- features_schema_version: versão do schema de features usado
- model_version: versão do modelo ML
- risk_decision: ALLOWED / BLOCKED_BY_RISK / BLOCKED_BY_ML / BLOCKED_BY_REPLAY
- motivo: razão detalhada da decisão
- posicao: posição atual antes da decisão (float)
- quantidade: tamanho da ordem proposta (int)
- preco: preço alvo de execução (float)
- estado_sistema: snapshot do estado (environment, replay_validated, ml_available)

Uso:
    from core.decision_journal import DecisionJournal
    
    journal = DecisionJournal()
    decision = journal.record(
        ativo="PETR4",
        sinal="BUY",
        score=0.85,
        motivo="sinal forte de alta",
        posicao=0.0,
        quantidade=100,
        preco=25.50,
    )
    
    # Explicar decisão específica
    print(journal.explain_decision(0))
    
    # Query por ativo/sinal
    buys = journal.query(ativo="PETR4", sinal="BUY")
    
    # Estatísticas
    stats = journal.get_stats()
"""

from __future__ import annotations
import json
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Tipos de decisão
# ---------------------------------------------------------------------------
DECISION_SIGNAL_BUY = "BUY"
DECISION_SIGNAL_SELL = "SELL"
DECISION_SIGNAL_HOLD = "HOLD"
DECISION_SIGNAL_BLOCKED = "BLOCKED"

DECISION_SIGNALS = (
    DECISION_SIGNAL_BUY,
    DECISION_SIGNAL_SELL,
    DECISION_SIGNAL_HOLD,
    DECISION_SIGNAL_BLOCKED,
)

# Motivos de bloco de risco
RISK_REASON_STALE = "stale_data"
RISK_REASON_DRAWDOWN = "drawdown_limit"
RISK_REASON_EXPOSURE = "exposure_limit"
RISK_REASON_SPREAD = "spread_excessive"
RISK_REASON_CIRCUIT_BREAKER = "circuit_breaker"
RISK_REASONS = (
    RISK_REASON_STALE,
    RISK_REASON_DRAWDOWN,
    RISK_REASON_EXPOSURE,
    RISK_REASON_SPREAD,
    RISK_REASON_CIRCUIT_BREAKER,
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class TradeDecision:
    """Registro completo de uma decisão de trading."""
    
    # Timestamps
    timestamp_do_evento: float = 0.0     # when event was received (unix)
    timestamp_de_processamento: float = 0.0  # when decision was made (unix)
    
    # Ativo e sinal
    ativo: str = ""                      # symbol like "PETR4"
    sinal: str = ""                      # BUY / SELL / HOLD / BLOCKED
    
    # ML
    score: float = 0.0                   # model score (0.0-1.0)
    features_schema_version: str = ""    # e.g., "v2.3.1"
    model_version: str = ""              # e.g., "xgb-v4.2"
    
    # Risk decision
    risk_decision: str = ""              # ALLOWED / BLOCKED_BY_*
    motivo: str = ""                     # detailed reason
    
    # Position and order
    posicao: float = 0.0                 # current position before decision
    quantidade: int = 0                  # order size
    preco: float = 0.0                   # target price
    
    # System state
    estado_sistema: Dict[str, Any] = field(default_factory=dict)  # environment, replay, ml status
    
    # Extended fields for backward compatibility (FASE 20)
    lado: str = ""                       # 'C' (compra), 'V' (venda) - legacy
    acao: str = ""                       # 'SINAL', 'ABRIR', 'FECHAR' - legacy
    confianca: float = 0.0               # EWMA confidence - legacy
    ml_prob: float = 0.0                 # ML probability - legacy
    regime: str = ""                     # market regime - legacy
    regime_info: Dict[str, Any] = field(default_factory=dict)  # regime details - legacy
    tp: float = 0.0                      # take profit points - legacy
    sl: float = 0.0                      # stop loss points - legacy
    motivos: List[str] = field(default_factory=list)  # textual reasons - legacy
    features_relevantes: Dict[str, float] = field(default_factory=dict)  # top features - legacy
    preco_ref: float = 0.0               # reference price - legacy
    risk_motivo: str = ""                # risk reason (deprecated, use motivo) - legacy
    modelo: str = ""                     # model source (heuristico/ML) - legacy
    id: int = 0                          # id sequencial, atribuido no registro
    
    def __post_init__(self):
        """Valida e normaliza campos apos criacao."""
        object.__setattr__(self, "ativo", self.ativo.upper())
        object.__setattr__(self, "score", round(self.score, 6))
        
        # Mapear campos legacy para novos (backward compatibility)
        # NOTA: nao existe campo 'ts_ms' nesta classe — quem precisa registrar o
        # instante do evento usa timestamp_do_evento (em SEGUNDOS, nao ms).
        if hasattr(self, 'lado') and self.lado:
            # Mapear lado para sinal
            if self.lado == 'C':
                object.__setattr__(self, "sinal", DECISION_SIGNAL_BUY)
            elif self.lado == 'V':
                object.__setattr__(self, "sinal", DECISION_SIGNAL_SELL)
        if hasattr(self, 'preco_ref') and self.preco_ref:
            object.__setattr__(self, "preco", float(self.preco_ref))
        # 'confianca' (EWMA) e 'score' (probabilidade do modelo) sao conceitos
        # DIFERENTES. Como confianca tem default 0.0, um hasattr() antigo
        # sobrescrevia score sempre, zerando o score do ML no audit trail.
        # Agora confianca so preenche score quando ele nao foi informado.
        if not self.score and self.confianca:
            object.__setattr__(self, "score", round(float(self.confianca), 6))
        if hasattr(self, 'motivos') and self.motivos:
            object.__setattr__(self, "motivo", ", ".join(self.motivos))
        if hasattr(self, 'regime'):
            self.estado_sistema['regime'] = self.regime
        if hasattr(self, 'tp') and hasattr(self, 'sl'):
            self.estado_sistema['tp'] = self.tp
            self.estado_sistema['sl'] = self.sl
        if hasattr(self, 'ml_prob'):
            self.estado_sistema['ml_prob'] = self.ml_prob
        if hasattr(self, 'features_relevantes') and self.features_relevantes:
            self.estado_sistema['features_relevantes'] = self.features_relevantes
        if hasattr(self, 'regime_info') and self.regime_info:
            self.estado_sistema['regime_info'] = self.regime_info
        if hasattr(self, 'modelo'):
            self.estado_sistema['modelo'] = self.modelo
        if hasattr(self, 'risk_motivo') and self.risk_motivo:
            object.__setattr__(self, "motivo", self.risk_motivo)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TradeDecision":
        """Converte dict para TradeDecision."""
        return cls(
            timestamp_do_evento=data["timestamp_do_evento"],
            timestamp_de_processamento=data["timestamp_de_processamento"],
            ativo=data["ativo"],
            sinal=data["sinal"],
            score=float(data["score"]),
            features_schema_version=data["features_schema_version"],
            model_version=data["model_version"],
            risk_decision=data["risk_decision"],
            motivo=data["motivo"],
            posicao=float(data.get("posicao", 0.0)),
            quantidade=int(data.get("quantidade", 0)),
            preco=float(data.get("preco", 0.0)),
            estado_sistema=data.get("estado_sistema", {}),
            id=int(data.get("id", 0)),
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Converte para dict."""
        return asdict(self)
    
    def to_json(self) -> str:
        """Serializa para JSON."""
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)
    
    @property
    def is_trade(self) -> bool:
        """Retorna True se a decisão é uma ordem de trading."""
        return self.sinal in (DECISION_SIGNAL_BUY, DECISION_SIGNAL_SELL)
    
    @property
    def is_blocked(self) -> bool:
        """Retorna True se a decisão foi bloqueada."""
        return self.sinal == DECISION_SIGNAL_BLOCKED
    
    @property
    def human_motivo(self) -> str:
        """Motivo formatado para leitura humana."""
        if self.is_trade:
            return f"{self.sinal} | score={self.score:.6f} | {self.motivo}"
        return f"BLOQUEADO | {self.risk_decision} | {self.motivo}"


@dataclass
class DecisionJournal:
    """Journal de decisões de trading com persistência e query capabilities.
    
    Cada decisão é registrada em lista thread-safe. Suporta persistência em JSON
    e queries temporais.
    """
    
    # Diretorio e sessao. Ficam PRIMEIRO porque core/app.py e run_all_tests.py
    # constroem posicionalmente: DecisionJournal(save_dir, session_ts).
    save_dir: str = ""
    session_ts: str = ""

    entries: List[TradeDecision] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    
    # Metadados do sistema
    features_schema_version: str = "v1.0.0"
    model_version: str = "unknown"
    
    # Configurações persistentes
    _storage_path: Optional[Path] = None
    _auto_save: bool = True
    
    # Stats
    total_decisions: int = 0
    trades_executed: int = 0
    blocks_applied: int = 0

    def __post_init__(self):
        """Normaliza construcoes legadas e garante que _lock seja um Lock real.

        Chamadas existentes passam (save_dir) ou (save_dir, session_ts), que
        antes caíam nos campos entries/_lock e corrompiam o journal em silencio
        (entries virava str e _lock virava str, quebrando todo `with self._lock`).
        """
        if isinstance(self.entries, (str, Path)):
            object.__setattr__(self, "save_dir", str(self.entries))
            object.__setattr__(self, "entries", [])
            if isinstance(self._lock, (str, Path)):
                object.__setattr__(self, "session_ts", str(self._lock))
        if not isinstance(self._lock, type(threading.Lock())):
            object.__setattr__(self, "_lock", threading.Lock())

    def record(
        self,
        ativo: str,
        sinal: str,
        score: float,
        motivo: str,
        risk_decision: str = "ALLOWED",
        posicao: float = 0.0,
        quantidade: int = 0,
        preco: float = 0.0,
        timestamp_evento: Optional[float] = None,
        timestamp_processamento: Optional[float] = None,
        estado_sistema: Optional[Dict[str, Any]] = None,
    ) -> TradeDecision:
        """Registra uma nova decisão no journal.
        
        Args:
            ativo: Símbolo do ativo (PETR4, VALE3, etc.)
            sinal: BUY / SELL / HOLD / BLOCKED
            score: Pontuação do modelo (0.0-1.0)
            motivo: Razão detalhada da decisão
            risk_decision: ALLOWED / BLOCKED_BY_*
            posicao: Posição atual antes da decisão
            quantidade: Tamanho da ordem proposta
            preco: Preço alvo de execução
            timestamp_evento: Quando o evento foi recebido (padrão: now)
            timestamp_processamento: Quando a decisão foi tomada (padrão: now)
            estado_sistema: Snapshot do estado do sistema
        
        Returns:
            TradeDecision registrado
        """
        agora = time.time()
        ts_evento = timestamp_evento or agora
        ts_processamento = timestamp_processamento or agora
        
        decision = TradeDecision(
            timestamp_do_evento=ts_evento,
            timestamp_de_processamento=ts_processamento,
            ativo=ativo,
            sinal=sinal,
            score=score,
            features_schema_version=self.features_schema_version,
            model_version=self.model_version,
            risk_decision=risk_decision,
            motivo=motivo,
            posicao=posicao,
            quantidade=quantidade,
            preco=preco,
            estado_sistema=estado_sistema or {},
        )
        
        with self._lock:
            self.entries.append(decision)
            self.total_decisions += 1
            if decision.is_trade:
                self.trades_executed += 1
            if decision.is_blocked:
                self.blocks_applied += 1
        
        if self._auto_save and self._storage_path:
            self._save_to_disk()
        
        return decision
    
    def query(
        self,
        ativo: Optional[str] = None,
        sinal: Optional[str] = None,
        desde: Optional[float] = None,
        ate: Optional[float] = None,
        risk_decision: Optional[str] = None,
    ) -> List[TradeDecision]:
        """Query no journal com filtros opcionais.
        
        Args:
            ativo: Filtrar por ativo (padrão: todos)
            sinal: Filtrar por sinal (BUY/SELL/HOLD/BLOCKED)
            desde: Timestamp inicial (unix)
            ate: Timestamp final (unix)
            risk_decision: Filtrar por tipo de decisão de risco
        
        Returns:
            Lista de TradeDecision matching os filtros
        """
        with self._lock:
            results = list(self.entries)
        
        if ativo:
            results = [d for d in results if d.ativo == ativo.upper()]
        if sinal:
            results = [d for d in results if d.sinal == sinal]
        if desde is not None:
            results = [d for d in results if d.timestamp_de_processamento >= desde]
        if ate is not None:
            results = [d for d in results if d.timestamp_de_processamento <= ate]
        if risk_decision:
            results = [d for d in results if d.risk_decision == risk_decision]
        
        return sorted(results, key=lambda d: d.timestamp_de_processamento)
    
    def explain_decision(self, decision_index: int) -> str:
        """Gera explicação detalhada de uma decisão específica.
        
        Args:
            decision_index: Índice na lista de entries (0-based)
        
        Returns:
            String com explicação formatada
        """
        with self._lock:
            if decision_index < 0 or decision_index >= len(self.entries):
                return f"Erro: índice {decision_index} fora do range (0-{len(self.entries)-1})"
            decision = self.entries[decision_index]
        
        lines = [
            "=" * 70,
            f"DECISAO #{decision_index + 1}",
            "=" * 70,
            f"Timestamp Evento:     {datetime.fromtimestamp(decision.timestamp_do_evento).isoformat()}",
            f"Timestamp Processamento: {datetime.fromtimestamp(decision.timestamp_de_processamento).isoformat()}",
            f"Ativo:                {decision.ativo}",
            f"Sinal:                {decision.sinal}",
            f"Score ML:             {decision.score:.6f}",
            f"Model Version:        {decision.model_version}",
            f"Features Schema:      {decision.features_schema_version}",
            f"Risk Decision:        {decision.risk_decision}",
            f"Motivo:               {decision.motivo}",
            f"Posição Antes:        {decision.posicao:+.2f}",
            f"Quantidade Proposta:  {decision.quantidade}",
            f"Preco Alvo:           R$ {decision.preco:.2f}",
            "-" * 70,
            "Estado do Sistema:",
        ]
        
        for key, value in decision.estado_sistema.items():
            lines.append(f"  {key}: {value}")
        
        lines.append("=" * 70)
        
        return "\n".join(lines)
    
    def get_decision_history(self, ativo: str, limit: int = 100) -> List[TradeDecision]:
        """Retorna historico de decisoes para um ativo especifico.
        
        Args:
            ativo: Símbolo do ativo
            limit: Maximo de registros a retornar
        
        Returns:
            Lista ordenada decrescente por timestamp
        """
        decisions = self.query(ativo=ativo)
        return sorted(decisions, key=lambda d: d.timestamp_de_processamento, reverse=True)[:limit]
    
    def get_blocked_decisions(self) -> List[TradeDecision]:
        """Retorna todas as decisões bloqueadas."""
        return self.query(sinal=DECISION_SIGNAL_BLOCKED)
    
    def get_trades_only(self) -> List[TradeDecision]:
        """Retorna apenas decisões que são trades (BUY/SELL).
        
        Returns:
            Lista de TradeDecision com sinal BUY ou SELL
        """
        return [d for d in self.entries if d.is_trade]
    
    def get_stats(self) -> Dict[str, Any]:
        """Retorna estatisticas do journal.
        
        Returns:
            Dict com contadores e métricas
        """
        with self._lock:
            buy_count = sum(1 for d in self.entries if d.sinal == DECISION_SIGNAL_BUY)
            sell_count = sum(1 for d in self.entries if d.sinal == DECISION_SIGNAL_SELL)
            hold_count = sum(1 for d in self.entries if d.sinal == DECISION_SIGNAL_HOLD)
            block_count = sum(1 for d in self.entries if d.sinal == DECISION_SIGNAL_BLOCKED)
            
            trade_count = buy_count + sell_count
            block_by_risk = sum(
                1 for d in self.entries 
                if d.risk_decision.startswith("BLOCKED_BY")
            )
            
            # Score distribution for trades
            trade_scores = [d.score for d in self.entries if d.is_trade]
            avg_score = sum(trade_scores) / len(trade_scores) if trade_scores else 0.0
            
            return {
                "total_decisions": self.total_decisions,
                "trades_executed": self.trades_executed,
                "blocks_applied": self.blocks_applied,
                "buy_orders": buy_count,
                "sell_orders": sell_count,
                "hold_signals": hold_count,
                "blocked_decisions": block_count,
                "blocked_by_risk": block_by_risk,
                "avg_ml_score": round(avg_score, 6),
                "features_schema_version": self.features_schema_version,
                "model_version": self.model_version,
            }
    
    # Persistencia
    def save_to_file(self, path: str) -> None:
        """Salva o journal em arquivo JSON.
        
        Args:
            path: Caminho do arquivo
        """
        self._storage_path = Path(path)
        self._save_to_disk()
    
    def _save_to_disk(self) -> None:
        """Salva em disco usando _storage_path."""
        if not self._storage_path:
            return
        
        with self._lock:
            data = {
                "metadata": {
                    "features_schema_version": self.features_schema_version,
                    "model_version": self.model_version,
                    "saved_at": datetime.now().isoformat(),
                    "total_entries": len(self.entries),
                    "total_decisions": self.total_decisions,
                    "trades_executed": self.trades_executed,
                    "blocks_applied": self.blocks_applied,
                },
                "entries": [d.to_dict() for d in self.entries],
            }
        
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._storage_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            pass  # Silencioso em produção
    
    def load_from_file(self, path: str) -> int:
        """Carrega journal de arquivo JSON.
        
        Args:
            path: Caminho do arquivo
        
        Returns:
            Numero de entries carregadas
        """
        filepath = Path(path)
        if not filepath.exists():
            return 0
        
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        loaded = 0
        with self._lock:
            self.entries.clear()
            for entry_data in data.get("entries", []):
                try:
                    decision = TradeDecision.from_dict(entry_data)
                    self.entries.append(decision)
                    loaded += 1
                except (KeyError, ValueError, TypeError):
                    continue
            
            # Restore stats
            self.total_decisions = data.get("metadata", {}).get("total_decisions", len(self.entries))
            self.trades_executed = data.get("metadata", {}).get("trades_executed", 0)
            self.blocks_applied = data.get("metadata", {}).get("blocks_applied", 0)
            self.features_schema_version = data.get("metadata", {}).get(
                "features_schema_version", self.features_schema_version
            )
            self.model_version = data.get("metadata", {}).get(
                "model_version", self.model_version
            )
        
        self._storage_path = filepath
        return loaded
    
    def clear(self) -> None:
        """Limpa todas as entries do journal."""
        with self._lock:
            self.entries.clear()
            self.total_decisions = 0
            self.trades_executed = 0
            self.blocks_applied = 0

    # ------------------------------------------------------------------
    # API de compatibilidade
    #
    # Consumida por core/app.py (registrar), core/signal_engine.py (registrar),
    # run_all_tests.py (registrar/count/buscar) e adapters/dashboard/handlers.py
    # (listar/buscar). Estes metodos nao existiam: qualquer um deles estourava
    # AttributeError e derrubava o fluxo de decisao.
    # ------------------------------------------------------------------
    def registrar(self, entry: TradeDecision) -> TradeDecision:
        """Registra uma TradeDecision ja construida (alias orientado a objeto)."""
        with self._lock:
            self.total_decisions += 1
            if not entry.id:
                object.__setattr__(entry, "id", self.total_decisions)
            self.entries.append(entry)
            if entry.is_trade:
                self.trades_executed += 1
            if entry.is_blocked:
                self.blocks_applied += 1

        if self._auto_save and self._storage_path:
            self._save_to_disk()
        return entry

    def count(self) -> int:
        """Quantidade de decisoes registradas."""
        with self._lock:
            return len(self.entries)

    def buscar(self, id=None, ativo: Optional[str] = None):
        """Busca uma decisao por id, ou lista por ativo.

        `id` aceita str porque o dashboard recebe o id pela URL.
        """
        with self._lock:
            entries = list(self.entries)

        if id is not None:
            try:
                alvo = int(id)
            except (TypeError, ValueError):
                return None
            for d in entries:
                if d.id == alvo:
                    return d
            return None

        if ativo is not None:
            alvo = ativo.upper()
            return [d for d in entries if d.ativo == alvo]

        return entries

    def listar(self, limite: int = 100, ativo: Optional[str] = None) -> List[TradeDecision]:
        """Decisoes mais recentes primeiro (usado pelo dashboard)."""
        with self._lock:
            entries = list(self.entries)
        if ativo:
            alvo = ativo.upper()
            entries = [d for d in entries if d.ativo == alvo]
        return sorted(entries, key=lambda d: d.timestamp_de_processamento, reverse=True)[:limite]

    def resumo(self) -> Dict[str, Any]:
        """Resumo agregado das decisoes registradas.

        'aberturas'/'fechamentos'/'sinais' contam o campo legado `acao`.
        """
        with self._lock:
            entries = list(self.entries)

        return {
            "total": len(entries),
            "aberturas": sum(1 for d in entries if d.acao == "ABRIR"),
            "fechamentos": sum(1 for d in entries if d.acao == "FECHAR"),
            "sinais": sum(1 for d in entries if d.acao == "SINAL"),
            "compras": sum(1 for d in entries if d.lado == "C"),
            "vendas": sum(1 for d in entries if d.lado == "V"),
            "score_medio": round(sum(d.score for d in entries) / len(entries), 6) if entries else 0.0,
        }


# ---------------------------------------------------------------------------
# Instancia global e factory functions
# ---------------------------------------------------------------------------
_default_journal: Optional[DecisionJournal] = None
_journal_lock = threading.Lock()


def get_journal() -> DecisionJournal:
    """Retorna instância global do journal."""
    global _default_journal
    with _journal_lock:
        if _default_journal is None:
            _default_journal = DecisionJournal()
        return _default_journal


def reset_journal() -> None:
    """Reseta instância global do journal."""
    global _default_journal
    with _journal_lock:
        _default_journal = None


# ---------------------------------------------------------------------------
# Facade functions para uso rapido
# ---------------------------------------------------------------------------
def record_decision(
    ativo: str,
    sinal: str,
    score: float,
    motivo: str,
    **kwargs,
) -> TradeDecision:
    """Funcao facade para registrar decisao no journal padrao."""
    return get_journal().record(ativo, sinal, score, motivo, **kwargs)


def query_decisions(ativo: Optional[str] = None, **kwargs) -> List[TradeDecision]:
    """Funcao facade para query no journal padrao."""
    return get_journal().query(ativo=ativo, **kwargs)


def explain(index: int) -> str:
    """Funcao facade para explicar decisao."""
    return get_journal().explain_decision(index)
