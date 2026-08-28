# -*- coding: utf-8 -*-
"""
features/feature_registry.py — Registro Formal de Features.

Catálogo central de todas as features do sistema. Cada feature possui:
- Metadados completos (nome, versão, descrição, tipo, etc.)
- Informação de causalidade (lookback, sem lookahead)
- Tratamento de NaN e warmup
- Dependências entre features
- Origem e módulo responsável

Uso:
    registry = FeatureRegistry()
    registry.register(FeatureDefinition(
        name="ofi",
        version="1.0",
        causal=True,
        lookback_ms=1000,
        source="book"
    ))
    
    # Validar dataset
    registry.validate_dataset(df)
    
    # Gerar documentação
    registry.to_markdown()
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime
import json


@dataclass
class FeatureDefinition:
    """Definição formal de uma feature."""
    
    # Identificação
    name: str                           # Nome da feature (chave no dataset)
    version: str = "1.0"                # Versão (semver)
    description: str = ""               # Descrição humana
    
    # Tipo e unidade
    dtype: str = "float64"              # Tipo de dado (float64, int64, bool, category)
    unit: str = "pts"                   # Unidade (pts, bps, ratio, count, bool, etc.)
    
    # Causalidade
    causal: bool = True                 # True = não usa futuro
    lookback_ms: int = 0                # Janela de lookback em ms (0 = instantâneo)
    max_timestamp_ms: Optional[int] = None  # Timestamp máximo utilizado
    
    # Origem
    source: str = "unknown"             # Origem: book, trade, vwap, ajuste, etc.
    module: str = "unknown"             # Módulo que calcula a feature
    
    # Dependências
    dependencies: List[str] = field(default_factory=list)  # Features que depende
    lag_ms: int = 0                     # Lag em relação ao timestamp
    
    # Tratamento
    nan_strategy: str = "zero"          # zero, forward, backward, interpolate, drop
    warmup_periods: int = 0             # Períodos necessários para warmup
    warmup_strategy: str = "zero"       # zero, nan, forward
    
    # Regime
    regime_aware: bool = False          # Muda comportamento por regime
    regimes: List[str] = field(default_factory=list)  # Regimes onde é relevante
    
    # Métricas
    min_value: Optional[float] = None   # Valor mínimo esperado
    max_value: Optional[float] = None   # Valor máximo esperado
    expected_range: Optional[tuple] = None  # (min, max) esperado
    
    # Metadata
    author: str = "Buffy"               # Quem criou
    created_at: str = "2026-08-28"      # Data de criação
    updated_at: str = "2026-08-28"      # Última atualização
    notes: str = ""                     # Notas adicionais
    
    def to_dict(self) -> Dict[str, Any]:
        """Converte para dict (para serialização JSON)."""
        return {
            'name': self.name,
            'version': self.version,
            'description': self.description,
            'dtype': self.dtype,
            'unit': self.unit,
            'causal': self.causal,
            'lookback_ms': self.lookback_ms,
            'source': self.source,
            'module': self.module,
            'dependencies': self.dependencies,
            'lag_ms': self.lag_ms,
            'nan_strategy': self.nan_strategy,
            'warmup_periods': self.warmup_periods,
            'warmup_strategy': self.warmup_strategy,
            'regime_aware': self.regime_aware,
            'regimes': self.regimes,
            'min_value': self.min_value,
            'max_value': self.max_value,
            'author': self.author,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'notes': self.notes,
        }
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'FeatureDefinition':
        """Cria FeatureDefinition a partir de dict."""
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class FeatureRegistry:
    """Registro central de todas as features do sistema."""
    
    def __init__(self):
        self._features: Dict[str, FeatureDefinition] = {}
        self._version = "1.0"
        self._created_at = datetime.now().isoformat()
    
    def register(self, feature: FeatureDefinition) -> None:
        """Registra uma feature."""
        if feature.name in self._features:
            existing = self._features[feature.name]
            if existing.version != feature.version:
                print(f"[REGISTRY] Atualizando {feature.name}: {existing.version} → {feature.version}")
        self._features[feature.name] = feature
    
    def get(self, name: str) -> Optional[FeatureDefinition]:
        """Obtém definição de uma feature."""
        return self._features.get(name)
    
    def list_all(self) -> List[str]:
        """Lista nomes de todas as features registradas."""
        return sorted(self._features.keys())
    
    def list_by_source(self, source: str) -> List[str]:
        """Lista features por origem."""
        return sorted([f.name for f in self._features.values() if f.source == source])
    
    def list_by_module(self, module: str) -> List[str]:
        """Lista features por módulo."""
        return sorted([f.name for f in self._features.values() if f.module == module])
    
    def list_causal_only(self) -> List[str]:
        """Lista apenas features causais (sem lookahead)."""
        return sorted([f.name for f in self._features.values() if f.causal])
    
    def list_non_causal(self) -> List[str]:
        """Lista features não-causais (possível lookahead)."""
        return sorted([f.name for f in self._features.values() if not f.causal])
    
    def validate_dataset(self, columns: List[str]) -> Dict[str, Any]:
        """Valida se um dataset contém apenas features registradas e causais.
        
        Returns:
            dict com 'valid', 'unknown_features', 'non_causal_features'
        """
        unknown = [c for c in columns if c not in self._features]
        non_causal = [c for c in columns if c in self._features and not self._features[c].causal]
        
        return {
            'valid': len(unknown) == 0 and len(non_causal) == 0,
            'total_features': len(columns),
            'registered': len(columns) - len(unknown),
            'unknown_features': unknown,
            'non_causal_features': non_causal,
        }
    
    def get_feature_matrix(self) -> List[Dict[str, Any]]:
        """Retorna matriz de features para documentação."""
        return [f.to_dict() for f in sorted(self._features.values(), key=lambda x: x.name)]
    
    def to_json(self, path: str) -> None:
        """Exporta registry para JSON."""
        data = {
            'version': self._version,
            'created_at': self._created_at,
            'updated_at': datetime.now().isoformat(),
            'total_features': len(self._features),
            'features': self.get_feature_matrix(),
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def to_markdown(self) -> str:
        """Gera documentação em Markdown."""
        lines = [
            "# Feature Registry",
            f"\n**Total:** {len(self._features)} features",
            f"**Causais:** {len(self.list_causal_only())}",
            f"**Não-causais:** {len(self.list_non_causal())}",
            "",
            "## Features por Origem",
            "",
        ]
        
        # Agrupar por source
        sources = {}
        for f in self._features.values():
            sources.setdefault(f.source, []).append(f)
        
        for source, features in sorted(sources.items()):
            lines.append(f"### {source.upper()} ({len(features)} features)")
            lines.append("")
            lines.append("| Feature | Versão | Tipo | Unidade | Lookback | Causal | Descrição |")
            lines.append("|---------|--------|------|---------|----------|--------|-----------|")
            for f in sorted(features, key=lambda x: x.name):
                causal = "✅" if f.causal else "❌"
                lookback = f"{f.lookback_ms}ms" if f.lookback_ms > 0 else "—"
                lines.append(f"| `{f.name}` | {f.version} | {f.dtype} | {f.unit} | {lookback} | {causal} | {f.description} |")
            lines.append("")
        
        return "\n".join(lines)


# ============================================================
# REGISTRY GLOBAL — Todas as features do sistema
# ============================================================

def create_default_registry() -> FeatureRegistry:
    """Cria registry com todas as features padrão do sistema."""
    registry = FeatureRegistry()
    
    # ============================================================
    # TRADE FEATURES (JanelaFeatures)
    # ============================================================
    _source = "trade"
    _module = "features.trade_features"
    
    registry.register(FeatureDefinition(
        name="aggr_imb",
        version="1.0",
        description="Imbalance de agressão: (vol_comprador - vol_vendedor) / vol_total",
        dtype="float64", unit="ratio",
        causal=True, lookback_ms=1000,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=-1.0, max_value=1.0,
    ))
    
    registry.register(FeatureDefinition(
        name="price_eff",
        version="1.0",
        description="Eficiência de preço: retorno / volatilidade (proxy de tendência)",
        dtype="float64", unit="ratio",
        causal=True, lookback_ms=1000,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=2,
    ))
    
    registry.register(FeatureDefinition(
        name="delta_preco_janela",
        version="1.0",
        description="Variação de preço na janela (preco_fim - preco_ini)",
        dtype="float64", unit="pts",
        causal=True, lookback_ms=1000,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
    ))
    
    registry.register(FeatureDefinition(
        name="cvd_total",
        version="1.0",
        description="Cumulative Volume Delta acumulado",
        dtype="int64", unit="contracts",
        causal=True, lookback_ms=1000,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
    ))
    
    registry.register(FeatureDefinition(
        name="vol_total",
        version="1.0",
        description="Volume total na janela",
        dtype="int64", unit="contracts",
        causal=True, lookback_ms=1000,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=0,
    ))
    
    registry.register(FeatureDefinition(
        name="vol_compr",
        version="1.0",
        description="Volume comprador na janela",
        dtype="int64", unit="contracts",
        causal=True, lookback_ms=1000,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=0,
    ))
    
    registry.register(FeatureDefinition(
        name="vol_venda",
        version="1.0",
        description="Volume vendedor na janela",
        dtype="int64", unit="contracts",
        causal=True, lookback_ms=1000,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=0,
    ))
    
    registry.register(FeatureDefinition(
        name="cvd_div",
        version="1.0",
        description="Divergência entre preço e CVD (possível reversão)",
        dtype="float64", unit="ratio",
        causal=True, lookback_ms=1000,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=2,
    ))
    
    registry.register(FeatureDefinition(
        name="hhi",
        version="1.0",
        description="Índice Herfindahl-Hirschman de concentração de volume",
        dtype="float64", unit="ratio",
        causal=True, lookback_ms=1000,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=0.0, max_value=1.0,
    ))
    
    registry.register(FeatureDefinition(
        name="range_vol_bps",
        version="1.0",
        description="Volatilidade realizada em bps (range da janela)",
        dtype="float64", unit="bps",
        causal=True, lookback_ms=1000,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=0,
    ))
    
    registry.register(FeatureDefinition(
        name="realized_vol_bps",
        version="1.0",
        description="Volatilidade realizada em bps (desvio padrão de retornos)",
        dtype="float64", unit="bps",
        causal=True, lookback_ms=1000,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=2,
        min_value=0,
    ))
    
    registry.register(FeatureDefinition(
        name="absorcao_ratio",
        version="1.0",
        description="Ratio de absorção: volume passivo / volume total",
        dtype="float64", unit="ratio",
        causal=True, lookback_ms=1000,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=0.0, max_value=1.0,
    ))
    
    registry.register(FeatureDefinition(
        name="fluxo_persist",
        version="1.0",
        description="Persistência do fluxo: se o lado dominante se mantém",
        dtype="float64", unit="ratio",
        causal=True, lookback_ms=1000,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
    ))
    
    registry.register(FeatureDefinition(
        name="top_corretoras",
        version="1.0",
        description="Top corretoras por volume (lista)",
        dtype="object", unit="list",
        causal=True, lookback_ms=1000,
        source=_source, module=_module,
        nan_strategy="empty_list", warmup_periods=1,
    ))
    
    registry.register(FeatureDefinition(
        name="trades_per_sec",
        version="1.0",
        description="Taxa de trades por segundo",
        dtype="float64", unit="trades/s",
        causal=True, lookback_ms=1000,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=0,
    ))
    
    registry.register(FeatureDefinition(
        name="avg_trade_size",
        version="1.0",
        description="Tamanho médio do trade",
        dtype="float64", unit="contracts",
        causal=True, lookback_ms=1000,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=0,
    ))
    
    registry.register(FeatureDefinition(
        name="max_trade_size",
        version="1.0",
        description="Tamanho máximo do trade na janela",
        dtype="int64", unit="contracts",
        causal=True, lookback_ms=1000,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=0,
    ))
    
    registry.register(FeatureDefinition(
        name="n",
        version="1.0",
        description="Número de eventos na janela",
        dtype="int64", unit="count",
        causal=True, lookback_ms=1000,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=0,
    ))
    
    # ============================================================
    # BOOK FEATURES (BookLevelFeatures)
    # ============================================================
    _source = "book"
    _module = "features.book_features"
    
    registry.register(FeatureDefinition(
        name="spread",
        version="1.0",
        description="Spread bid-ask: best_ask - best_bid",
        dtype="float64", unit="pts",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=0,
    ))
    
    registry.register(FeatureDefinition(
        name="microprice",
        version="1.0",
        description="Microprice: preço ponderado por volumes bid/ask",
        dtype="float64", unit="pts",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        notes="Stoikov (2008): melhor estimador de preço futuro em mercados de alta frequência",
    ))
    
    registry.register(FeatureDefinition(
        name="microprice_vs_mid",
        version="1.0",
        description="Diferença microprice - mid (pressão compradora/vendedora)",
        dtype="float64", unit="pts",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
    ))
    
    registry.register(FeatureDefinition(
        name="hhi_book",
        version="1.0",
        description="HHI de concentração do book (distribuição de volume)",
        dtype="float64", unit="ratio",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=0.0, max_value=1.0,
    ))
    
    registry.register(FeatureDefinition(
        name="ofi",
        version="1.0",
        description="Order Flow Imbalance: desequilíbrio de ordens limitadas",
        dtype="float64", unit="contracts",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        notes="Cont-Kukanov-Stoikov: compara mudanças nível a nível",
    ))
    
    registry.register(FeatureDefinition(
        name="imbalance",
        version="1.0",
        description="Imbalance por profundidade (L1, L3, L5, L10, etc.)",
        dtype="dict", unit="ratio",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        notes="Dict com chaves L1, L3, L5, L10, L20, L30, L50, L100, L200, L250, L500",
    ))
    
    registry.register(FeatureDefinition(
        name="liq_dist_bid",
        version="1.0",
        description="Distância média ponderada do bid ao mid",
        dtype="float64", unit="pts",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
    ))
    
    registry.register(FeatureDefinition(
        name="liq_dist_ask",
        version="1.0",
        description="Distância média ponderada do ask ao mid",
        dtype="float64", unit="pts",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
    ))
    
    registry.register(FeatureDefinition(
        name="vel_bid",
        version="1.0",
        description="Velocidade de mudança do volume bid (EWMA)",
        dtype="float64", unit="contracts/s",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=2,
    ))
    
    registry.register(FeatureDefinition(
        name="vel_ask",
        version="1.0",
        description="Velocidade de mudança do volume ask (EWMA)",
        dtype="float64", unit="contracts/s",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=2,
    ))
    
    registry.register(FeatureDefinition(
        name="micro_drift_bps",
        version="1.0",
        description="Drift do microprice em bps (microprice - mid) / mid * 10000",
        dtype="float64", unit="bps",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        notes="Stoikov (2008): pressão compradora/vendedora suavizada",
    ))
    
    registry.register(FeatureDefinition(
        name="micro_drift_ewma",
        version="1.0",
        description="EWMA do microprice drift (suavizado)",
        dtype="float64", unit="bps",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=2,
    ))
    
    registry.register(FeatureDefinition(
        name="imb_ponderado",
        version="1.0",
        description="Imbalance ponderado por profundidade (decay geométrico 0.85^i)",
        dtype="float64", unit="ratio",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        notes="Cartea et al. (2015): níveis mais próximos têm mais peso",
    ))
    
    registry.register(FeatureDefinition(
        name="slope_bid",
        version="1.0",
        description="Slope do book bid: (near - far) / (near + far)",
        dtype="float64", unit="ratio",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        notes=">0 = parede (liquidez concentrada), <0 = rampa (liquidez espalhada)",
    ))
    
    registry.register(FeatureDefinition(
        name="slope_ask",
        version="1.0",
        description="Slope do book ask: (near - far) / (near + far)",
        dtype="float64", unit="ratio",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
    ))
    
    # ============================================================
    # OFI FEATURES
    # ============================================================
    _source = "ofi"
    _module = "features.book_features"
    
    registry.register(FeatureDefinition(
        name="ofi_total",
        version="1.0",
        description="OFI total: soma de (bid_event - ask_event) por nível",
        dtype="float64", unit="contracts",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        notes="Cont-Kukanov-Stoikov: mede desequilíbrio de ordens limitadas",
    ))
    
    registry.register(FeatureDefinition(
        name="ofi_ewma",
        version="1.0",
        description="OFI suavizado por EWMA (decay 0.92)",
        dtype="float64", unit="contracts",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=2,
    ))
    
    # ============================================================
    # VOLUME PROFILE FEATURES
    # ============================================================
    _source = "volume_profile"
    _module = "features.volume_profile"
    
    registry.register(FeatureDefinition(
        name="vp_poc_dist",
        version="1.0",
        description="Distância do preço ao POC (Point of Control)",
        dtype="float64", unit="pts",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=10,
        notes="POC = nível com maior volume acumulado",
    ))
    
    registry.register(FeatureDefinition(
        name="vp_vah_dist",
        version="1.0",
        description="Distância do preço ao VAH (Value Area High)",
        dtype="float64", unit="pts",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=10,
    ))
    
    registry.register(FeatureDefinition(
        name="vp_val_dist",
        version="1.0",
        description="Distância do preço ao VAL (Value Area Low)",
        dtype="float64", unit="pts",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=10,
    ))
    
    registry.register(FeatureDefinition(
        name="vp_vp_total",
        version="1.0",
        description="Volume total do Volume Profile",
        dtype="float64", unit="contracts",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=10,
        min_value=0,
    ))
    
    # ============================================================
    # KYLE'S LAMBDA
    # ============================================================
    _source = "kyle"
    _module = "features.kyle_lambda"
    
    registry.register(FeatureDefinition(
        name="kyle_kyle_lambda",
        version="1.0",
        description="Kyle's Lambda: impacto de preço / liquidez (regressão ΔP ~ λ·V)",
        dtype="float64", unit="pts/contract",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=20,
        notes="λ alto = liquidez fina, propenso a reversão",
    ))
    
    # ============================================================
    # VPIN
    # ============================================================
    _source = "vpin"
    _module = "features.vpin"
    
    registry.register(FeatureDefinition(
        name="vpin",
        version="1.0",
        description="Volume-synchronized Probability of Informed Trading",
        dtype="float64", unit="ratio",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=20,
        min_value=0.0, max_value=1.0,
        notes="Easley, López de Prado, O'Hara (2012)",
    ))
    
    # ============================================================
    # CROSS-ASSET
    # ============================================================
    _source = "cross_asset"
    _module = "features.cross_asset"
    
    registry.register(FeatureDefinition(
        name="cross_lag",
        version="1.0",
        description="Lag temporal entre WIN e WDO (ms)",
        dtype="float64", unit="ms",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=30,
        min_value=0,
        notes="WDO geralmente lidera WIN",
    ))
    
    registry.register(FeatureDefinition(
        name="cross_corr_aggr",
        version="1.0",
        description="Correlação rolling de agressão entre WIN e WDO",
        dtype="float64", unit="ratio",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=30,
        min_value=-1.0, max_value=1.0,
    ))
    
    registry.register(FeatureDefinition(
        name="cross_divergencia",
        version="1.0",
        description="Divergência de preço entre WIN e WDO",
        dtype="float64", unit="pts",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=30,
    ))
    
    # ============================================================
    # INSTITUTIONAL CONTEXT
    # ============================================================
    _source = "institutional"
    _module = "features.institutional_context"
    
    registry.register(FeatureDefinition(
        name="dist_vwap_pts",
        version="1.0",
        description="Distância ao VWAP em pontos",
        dtype="float64", unit="pts",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        notes="VWAP = preço médio ponderado por volume do dia",
    ))
    
    registry.register(FeatureDefinition(
        name="dist_vwap_norm",
        version="1.0",
        description="Distância ao VWAP normalizada (em ticks de 5pts)",
        dtype="float64", unit="ticks",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
    ))
    
    registry.register(FeatureDefinition(
        name="dist_abertura_pts",
        version="1.0",
        description="Distância à abertura do dia em pontos",
        dtype="float64", unit="pts",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
    ))
    
    registry.register(FeatureDefinition(
        name="dist_abertura_norm",
        version="1.0",
        description="Distância à abertura normalizada (em ticks)",
        dtype="float64", unit="ticks",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
    ))
    
    registry.register(FeatureDefinition(
        name="dist_maxima_pts",
        version="1.0",
        description="Distância à máxima do dia em pontos",
        dtype="float64", unit="pts",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
    ))
    
    registry.register(FeatureDefinition(
        name="dist_minima_pts",
        version="1.0",
        description="Distância à mínima do dia em pontos",
        dtype="float64", unit="pts",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
    ))
    
    registry.register(FeatureDefinition(
        name="dist_ajuste_pts",
        version="1.0",
        description="Distância ao ajuste (settlement D-1) em pontos",
        dtype="float64", unit="pts",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        notes="Ajuste é valor estático do dia anterior",
    ))
    
    registry.register(FeatureDefinition(
        name="dist_ajuste_norm",
        version="1.0",
        description="Distância ao ajuste normalizada (em ticks)",
        dtype="float64", unit="ticks",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
    ))
    
    registry.register(FeatureDefinition(
        name="zona_vwap",
        version="1.0",
        description="Zona em relação ao VWAP: 0=far, 1=near, 2=at",
        dtype="int64", unit="category",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=0, max_value=2,
    ))
    
    registry.register(FeatureDefinition(
        name="zona_abertura",
        version="1.0",
        description="Zona em relação à abertura: 0=far, 1=near, 2=at",
        dtype="int64", unit="category",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=0, max_value=2,
    ))
    
    registry.register(FeatureDefinition(
        name="zona_maxima",
        version="1.0",
        description="Zona em relação à máxima: 0=far, 1=near, 2=at",
        dtype="int64", unit="category",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=0, max_value=2,
    ))
    
    registry.register(FeatureDefinition(
        name="zona_minima",
        version="1.0",
        description="Zona em relação à mínima: 0=far, 1=near, 2=at",
        dtype="int64", unit="category",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=0, max_value=2,
    ))
    
    registry.register(FeatureDefinition(
        name="zona_ajuste",
        version="1.0",
        description="Zona em relação ao ajuste: 0=far, 1=near, 2=at",
        dtype="int64", unit="category",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=0, max_value=2,
    ))
    
    registry.register(FeatureDefinition(
        name="posicao_relativa",
        version="1.0",
        description="Posição relativa no range do dia (0=fundo, 1=topo)",
        dtype="float64", unit="ratio",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=0.0, max_value=1.0,
    ))
    
    registry.register(FeatureDefinition(
        name="amplitude_dia_pts",
        version="1.0",
        description="Amplitude do dia (máxima - mínima)",
        dtype="float64", unit="pts",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=0,
    ))
    
    registry.register(FeatureDefinition(
        name="bounces_vwap_norm",
        version="1.0",
        description="Número de bounces no VWAP normalizado (0-1)",
        dtype="float64", unit="ratio",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=0.0, max_value=1.0,
    ))
    
    registry.register(FeatureDefinition(
        name="bounces_ajuste_norm",
        version="1.0",
        description="Número de bounces no ajuste normalizado (0-1)",
        dtype="float64", unit="ratio",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=0.0, max_value=1.0,
    ))
    
    registry.register(FeatureDefinition(
        name="reversao_perto_vwap",
        version="1.0",
        description="Sinal de reversão perto do VWAP (1.0 se <15pts e direção oposta)",
        dtype="float64", unit="bool",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=0.0, max_value=1.0,
    ))
    
    registry.register(FeatureDefinition(
        name="reversao_perto_ajuste",
        version="1.0",
        description="Sinal de reversão perto do ajuste",
        dtype="float64", unit="bool",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=0.0, max_value=1.0,
    ))
    
    # ============================================================
    # METADATA FEATURES
    # ============================================================
    _source = "metadata"
    _module = "features.trade_features"
    
    registry.register(FeatureDefinition(
        name="preco_fim",
        version="1.0",
        description="Preço final da janela (último trade)",
        dtype="float64", unit="pts",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=0,
    ))
    
    registry.register(FeatureDefinition(
        name="preco_ini",
        version="1.0",
        description="Preço inicial da janela (primeiro trade)",
        dtype="float64", unit="pts",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
        min_value=0,
    ))
    
    registry.register(FeatureDefinition(
        name="time_ms",
        version="1.0",
        description="Timestamp da janela em milissegundos",
        dtype="int64", unit="ms",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=1,
    ))
    
    registry.register(FeatureDefinition(
        name="ativo",
        version="1.0",
        description="Símbolo do ativo",
        dtype="object", unit="string",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="drop", warmup_periods=0,
    ))
    
    registry.register(FeatureDefinition(
        name="dias_ate_venc",
        version="1.0",
        description="Dias até o vencimento do contrato",
        dtype="int64", unit="days",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=0,
        min_value=0,
    ))
    
    registry.register(FeatureDefinition(
        name="fase_sessao",
        version="1.0",
        description="Fase da sessão: abertura, meio, fechamento",
        dtype="object", unit="category",
        causal=True, lookback_ms=0,
        source=_source, module=_module,
        nan_strategy="zero", warmup_periods=0,
    ))
    
    registry.register(FeatureDefinition(
        name="regime",
        version="1.0",
        description="Regime de mercado detectado",
        dtype="object", unit="category",
        causal=True, lookback_ms=0,
        source="regime", module="core.regime_detector",
        nan_strategy="zero", warmup_periods=5,
        regimes=["tendencia_alta", "tendencia_baixa", "lateral", "vol_alta", "vol_baixa"],
    ))
    
    return registry


# ============================================================
# INSTÂNCIA GLOBAL
# ============================================================
REGISTRY = create_default_registry()
