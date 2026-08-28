# -*- coding: utf-8 -*-
"""
ml/model_metadata.py — Metadados de Modelos ML.

Cada modelo possui metadados completos para reprodutibilidade:
- model_id: identificador único
- algoritmo: LightGBM, XGBoost, RandomForest
- versão: semver (1.0.0)
- features: lista de features usadas
- labels: definição dos labels
- período de treino: datas
- folds: estratégia de validação
- parâmetros: hiperparâmetros
- métricas: performance
- data de treinamento: timestamp
- hash do dataset: SHA256
- hash dos artefatos: SHA256
"""

import hashlib
import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Any, Optional
from datetime import datetime
from pathlib import Path


@dataclass
class DatasetInfo:
    """Informações sobre o dataset usado no treino."""
    path: str = ""
    hash_sha256: str = ""
    n_rows: int = 0
    n_cols: int = 0
    n_features: int = 0
    n_labels_pos: int = 0
    n_labels_neg: int = 0
    n_labels_neutro: int = 0
    date_start: str = ""
    date_end: str = ""
    ativo: str = ""
    
    def compute_hash(self, path: str) -> str:
        """Calcula SHA256 do arquivo."""
        sha256 = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)
        self.hash_sha256 = sha256.hexdigest()
        self.path = path
        return self.hash_sha256
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class FeatureSet:
    """Conjunto de features usado no treino."""
    names: List[str] = field(default_factory=list)
    version: str = "1.0"
    n_features: int = 0
    description: str = ""
    
    def __post_init__(self):
        self.n_features = len(self.names)
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class LabelConfig:
    """Configuração dos labels."""
    method: str = "triple_barrier"  # triple_barrier, binary, regression
    tp_pts: float = 20.0
    sl_pts: float = 15.0
    max_holding_s: int = 30
    purge_s: int = 5
    embargo_s: int = 30
    version: str = "1.0"
    description: str = ""
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class TrainConfig:
    """Configuração do treinamento."""
    algorithm: str = "LightGBM"
    n_estimators: int = 500
    max_depth: int = 8
    learning_rate: float = 0.05
    subsample: float = 0.8
    colsample_bytree: float = 0.8
    class_weight: str = "balanced"
    random_state: int = 42
    early_stopping_rounds: int = 50
    cv_strategy: str = "temporal"  # temporal, kfold, stratified
    n_folds: int = 5
    purge_days: int = 1
    embargo_days: int = 2
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ModelMetrics:
    """Métricas de performance do modelo."""
    accuracy: float = 0.0
    auc_roc: float = 0.0
    brier_score: float = 0.0
    ece: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    n_trades_simulated: int = 0
    win_rate: float = 0.0
    
    # Métricas por fold
    fold_metrics: List[Dict] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ModelMetadata:
    """Metadados completos de um modelo ML."""
    
    # Identificação
    model_id: str = ""
    model_name: str = ""
    version: str = "1.0.0"
    description: str = ""
    
    # Algoritmo
    algorithm: str = "LightGBM"
    framework_version: str = ""
    
    # Dados
    dataset: DatasetInfo = field(default_factory=DatasetInfo)
    features: FeatureSet = field(default_factory=FeatureSet)
    labels: LabelConfig = field(default_factory=LabelConfig)
    
    # Treino
    train_config: TrainConfig = field(default_factory=TrainConfig)
    train_start: str = ""
    train_end: str = ""
    train_date: str = ""
    
    # Métricas
    metrics: ModelMetrics = field(default_factory=ModelMetrics)
    
    # Artefatos
    model_path: str = ""
    model_hash: str = ""
    feature_importance: Dict[str, float] = field(default_factory=dict)
    
    # Metadata
    author: str = "Buffy"
    created_at: str = ""
    updated_at: str = ""
    notes: str = ""
    tags: List[str] = field(default_factory=list)
    
    def __post_init__(self):
        if not self.model_id:
            self.model_id = f"model_{int(time.time())}"
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.train_date:
            self.train_date = datetime.now().strftime('%Y-%m-%d')
    
    def to_dict(self) -> Dict:
        """Converte para dict (serializável)."""
        return {
            'model_id': self.model_id,
            'model_name': self.model_name,
            'version': self.version,
            'description': self.description,
            'algorithm': self.algorithm,
            'framework_version': self.framework_version,
            'dataset': self.dataset.to_dict(),
            'features': self.features.to_dict(),
            'labels': self.labels.to_dict(),
            'train_config': self.train_config.to_dict(),
            'train_start': self.train_start,
            'train_end': self.train_end,
            'train_date': self.train_date,
            'metrics': self.metrics.to_dict(),
            'model_path': self.model_path,
            'model_hash': self.model_hash,
            'feature_importance': self.feature_importance,
            'author': self.author,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'notes': self.notes,
            'tags': self.tags,
        }
    
    @classmethod
    def from_dict(cls, d: Dict) -> 'ModelMetadata':
        """Cria ModelMetadata a partir de dict."""
        d = dict(d)
        d['dataset'] = DatasetInfo(**d.get('dataset', {}))
        d['features'] = FeatureSet(**d.get('features', {}))
        d['labels'] = LabelConfig(**d.get('labels', {}))
        d['train_config'] = TrainConfig(**d.get('train_config', {}))
        d['metrics'] = ModelMetrics(**d.get('metrics', {}))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
    
    def save(self, path: str):
        """Salva metadados em JSON."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
    
    @classmethod
    def load(cls, path: str) -> 'ModelMetadata':
        """Carrega metadados de JSON."""
        with open(path, 'r', encoding='utf-8') as f:
            return cls.from_dict(json.load(f))


def compute_file_hash(path: str) -> str:
    """Calcula SHA256 de um arquivo."""
    sha256 = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)
    return sha256.hexdigest()
