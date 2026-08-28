# -*- coding: utf-8 -*-
"""
core/decision_journal.py — Decision Journal.

Cada decisão de trading é persistida com contexto completo para auditoria.
Permite responder "Por que o sistema decidiu comprar neste timestamp?"
sem depender dos logs.

Formato: JSONL (append-only, um JSON por linha)
Localização: {save_dir}/decisoes_{session_ts}.jsonl

Uso:
    journal = DecisionJournal(save_dir)
    
    # Registrar decisão
    journal.registrar(DecisionEntry(
        ts_ms=1787948721410,
        ativo='WINV26',
        acao='ABRIR',
        lado='C',
        preco=178000.0,
        score=0.75,
        confianca=0.68,
        ml_prob=0.72,
        regime='tendencia_alta',
        tp=150.0,
        sl=100.0,
        size=1,
        motivos=['aggr_imb > 0.3', 'book_imb > 0.2'],
        features_relevantes={'aggr_imb': 0.45, 'ofi': 12.3},
        risk_decision='APROVADO',
        modelo='LightGBM',
        model_version='v4_limpo',
        latencia_ms=12.5,
    ))
    
    # Buscar decisão
    entry = journal.buscar(ts_ms=1787948721410)
    
    # Listar decisões
    decisions = journal.listar(ativo='WINV26', acao='ABRIR', limite=100)
"""

import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
from datetime import datetime
from pathlib import Path


@dataclass
class DecisionEntry:
    """Entrada completa de uma decisão de trading."""
    
    # Identificação
    id: str = ""                          # ID único (UUID)
    ts_ms: int = 0                        # Timestamp em ms
    
    # Ativo e ação
    ativo: str = ""                       # Símbolo (WINV26, WDOU26)
    acao: str = ""                        # ABIR, FECHAR, MANTER, CANCELAR
    lado: str = ""                        # C (compra), V (venda), '' (neutro)
    preco: float = 0.0                    # Preço da decisão
    
    # Score e ML
    score: float = 0.0                    # Score heurístico
    confianca: float = 0.0                # Confiança EWMA
    ml_prob: float = 0.5                  # Probabilidade do modelo ML
    sinal: int = 0                        # +1 (compra), -1 (venda), 0 (neutro)
    
    # Regime
    regime: str = "lateral"               # Regime detectado
    regime_info: Dict = field(default_factory=dict)  # Info detalhada do regime
    
    # Risk Management
    risk_decision: str = ""               # APROVADO, REJEITADO, COOLDOWN, CB
    risk_motivo: str = ""                 # Motivo da rejeição
    tp: float = 0.0                       # Take-profit
    sl: float = 0.0                       # Stop-loss
    rr_ratio: float = 0.0                 # Risco/Retorno
    size: int = 1                         # Tamanho da posição
    
    # Motivos e features
    motivos: List[str] = field(default_factory=list)  # Lista de motivos
    features_relevantes: Dict[str, float] = field(default_factory=dict)  # Top features
    
    # Contexto do mercado
    preco_ref: float = 0.0                # Preço de referência
    spread: float = 0.0                   # Spread bid-ask
    ofi: float = 0.0                      # Order Flow Imbalance
    microprice: float = 0.0               # Microprice
    dist_vwap: float = 0.0                # Distância ao VWAP
    dist_abertura: float = 0.0            # Distância à abertura
    
    # Modelo
    modelo: str = ""                      # Nome do modelo (LightGBM, heurístico)
    model_version: str = ""               # Versão do modelo
    
    # Performance
    latencia_ms: float = 0.0              # Latência da decisão (ms)
    loop_count: int = 0                   # Contador do loop
    
    # Metadata
    session_ts: str = ""                  # Timestamp da sessão
    created_at: str = ""                  # ISO timestamp
    
    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = datetime.now().isoformat(timespec='seconds')
        if self.tp > 0 and self.sl > 0:
            self.rr_ratio = round(self.tp / self.sl, 2)
    
    def to_dict(self) -> Dict[str, Any]:
        """Converte para dict (para serialização JSON)."""
        d = asdict(self)
        return d
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'DecisionEntry':
        """Cria DecisionEntry a partir de dict."""
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class DecisionJournal:
    """Journal persistente de decisões de trading."""
    
    def __init__(self, save_dir: str, session_ts: str = ""):
        self.save_dir = save_dir
        self.session_ts = session_ts or datetime.now().strftime('%Y%m%d_%H%M%S')
        self._filepath = os.path.join(save_dir, f'decisoes_{self.session_ts}.jsonl')
        self._count = 0
        
        # Garantir que o diretório existe
        os.makedirs(save_dir, exist_ok=True)
        
        # Contar decisões existentes
        if os.path.exists(self._filepath):
            with open(self._filepath, 'r', encoding='utf-8') as f:
                self._count = sum(1 for _ in f)
    
    def registrar(self, entry: DecisionEntry) -> str:
        """Registra uma decisão no journal.
        
        Returns:
            ID da decisão registrada.
        """
        if not entry.session_ts:
            entry.session_ts = self.session_ts
        
        with open(self._filepath, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + '\n')
        
        self._count += 1
        return entry.id
    
    def registrar_rapido(self, **kwargs) -> str:
        """Atalho para registrar com parâmetros keyword."""
        entry = DecisionEntry(**kwargs)
        return self.registrar(entry)
    
    def buscar(self, ts_ms: Optional[int] = None, id: Optional[str] = None) -> Optional[DecisionEntry]:
        """Busca uma decisão por timestamp ou ID."""
        if not os.path.exists(self._filepath):
            return None
        
        with open(self._filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    if id and d.get('id') == id:
                        return DecisionEntry.from_dict(d)
                    if ts_ms and d.get('ts_ms') == ts_ms:
                        return DecisionEntry.from_dict(d)
                except json.JSONDecodeError:
                    continue
        return None
    
    def listar(self, ativo: Optional[str] = None, acao: Optional[str] = None,
               limite: int = 100, offset: int = 0) -> List[DecisionEntry]:
        """Lista decisões com filtros opcionais."""
        if not os.path.exists(self._filepath):
            return []
        
        results = []
        with open(self._filepath, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                if i < offset:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    if ativo and d.get('ativo') != ativo:
                        continue
                    if acao and d.get('acao') != acao:
                        continue
                    results.append(DecisionEntry.from_dict(d))
                    if len(results) >= limite:
                        break
                except json.JSONDecodeError:
                    continue
        return results
    
    def ultima_decisao(self, ativo: Optional[str] = None) -> Optional[DecisionEntry]:
        """Retorna a última decisão registrada."""
        results = self.listar(ativo=ativo, limite=1)
        return results[0] if results else None
    
    def resumo(self, ativo: Optional[str] = None) -> Dict[str, Any]:
        """Retorna resumo das decisões."""
        decisions = self.listar(ativo=ativo, limite=10000)
        
        if not decisions:
            return {'total': 0}
        
        aberturas = [d for d in decisions if d.acao == 'ABRIR']
        fechamentos = [d for d in decisions if d.acao == 'FECHAR']
        
        return {
            'total': len(decisions),
            'aberturas': len(aberturas),
            'fechamentos': len(fechamentos),
            'compras': len([d for d in decisions if d.lado == 'C']),
            'vendas': len([d for d in decisions if d.lado == 'V']),
            'pnl_total': sum(d.preco for d in fechamentos),
            'ultima_decisao': decisions[-1].created_at if decisions else None,
        }
    
    def count(self) -> int:
        """Retorna número total de decisões."""
        return self._count
    
    def exportar_json(self, path: str) -> None:
        """Exporta todas as decisões para um arquivo JSON."""
        decisions = self.listar(limite=100000)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump([d.to_dict() for d in decisions], f, indent=2, ensure_ascii=False)
