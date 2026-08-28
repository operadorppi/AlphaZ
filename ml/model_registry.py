# -*- coding: utf-8 -*-
"""
ml/model_registry.py — Registro Central de Modelos ML.

Gerencia todos os modelos treinados:
- Registrar novos modelos
- Listar modelos
- Buscar por ID/versão
- Promover modelo para produção
- Comparar modelos
- Manter histórico

Estrutura:
{save_dir}/models/
├── registry.json          # Registro central
├── model_20260828_165700/
│   ├── metadata.json      # Metadados completos
│   ├── model.pkl          # Modelo treinado
│   ├── validation.json    # Relatório de validação
│   └── feature_importance.csv
└── production.json        # Modelo em produção
"""

import json
import os
import time
import shutil
from typing import Dict, List, Any, Optional
from datetime import datetime
from pathlib import Path
from ml.model_metadata import ModelMetadata, DatasetInfo, FeatureSet, LabelConfig, TrainConfig, ModelMetrics


class ModelRegistry:
    """Registro central de modelos ML."""
    
    def __init__(self, save_dir: str):
        self.save_dir = save_dir
        self.models_dir = os.path.join(save_dir, 'models')
        self.registry_path = os.path.join(self.models_dir, 'registry.json')
        self.production_path = os.path.join(self.models_dir, 'production.json')
        
        # Criar diretórios
        os.makedirs(self.models_dir, exist_ok=True)
        
        # Carregar registry
        self._registry = self._load_registry()
    
    def _load_registry(self) -> Dict:
        """Carrega registry do disco."""
        if os.path.exists(self.registry_path):
            with open(self.registry_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {'models': [], 'created_at': datetime.now().isoformat()}
    
    def _save_registry(self):
        """Salva registry no disco."""
        self._registry['updated_at'] = datetime.now().isoformat()
        with open(self.registry_path, 'w', encoding='utf-8') as f:
            json.dump(self._registry, f, indent=2, ensure_ascii=False)
    
    def register(self, metadata: ModelMetadata, model_path: str = None,
                 validation_report: Dict = None) -> str:
        """Registra um novo modelo.
        
        Args:
            metadata: Metadados do modelo
            model_path: Caminho do arquivo do modelo
            validation_report: Relatório de validação
        
        Returns:
            model_id do modelo registrado
        """
        # Criar diretório do modelo
        model_dir = os.path.join(self.models_dir, metadata.model_id)
        os.makedirs(model_dir, exist_ok=True)
        
        # Salvar metadados
        metadata_path = os.path.join(model_dir, 'metadata.json')
        metadata.save(metadata_path)
        
        # Copiar modelo se fornecido
        if model_path and os.path.exists(model_path):
            dest_path = os.path.join(model_dir, 'model.pkl')
            shutil.copy2(model_path, dest_path)
            metadata.model_path = dest_path
        
        # Salvar validação
        if validation_report:
            val_path = os.path.join(model_dir, 'validation.json')
            with open(val_path, 'w', encoding='utf-8') as f:
                json.dump(validation_report, f, indent=2, ensure_ascii=False)
        
        # Adicionar ao registry
        model_entry = {
            'model_id': metadata.model_id,
            'model_name': metadata.model_name,
            'version': metadata.version,
            'algorithm': metadata.algorithm,
            'train_date': metadata.train_date,
            'metrics': {
                'accuracy': metadata.metrics.accuracy,
                'auc_roc': metadata.metrics.auc_roc,
                'brier_score': metadata.metrics.brier_score,
            },
            'n_features': metadata.features.n_features,
            'dataset_hash': metadata.dataset.hash_sha256[:16] if metadata.dataset.hash_sha256 else '',
            'registered_at': datetime.now().isoformat(),
            'tags': metadata.tags,
        }
        
        self._registry['models'].append(model_entry)
        self._save_registry()
        
        return metadata.model_id
    
    def get(self, model_id: str) -> Optional[ModelMetadata]:
        """Busca modelo por ID."""
        model_dir = os.path.join(self.models_dir, model_id)
        metadata_path = os.path.join(model_dir, 'metadata.json')
        
        if os.path.exists(metadata_path):
            return ModelMetadata.load(metadata_path)
        return None
    
    def list_all(self) -> List[Dict]:
        """Lista todos os modelos registrados."""
        return self._registry.get('models', [])
    
    def list_by_algorithm(self, algorithm: str) -> List[Dict]:
        """Lista modelos por algoritmo."""
        return [m for m in self._registry.get('models', []) 
                if m.get('algorithm') == algorithm]
    
    def list_by_tag(self, tag: str) -> List[Dict]:
        """Lista modelos por tag."""
        return [m for m in self._registry.get('models', [])
                if tag in m.get('tags', [])]
    
    def get_production(self) -> Optional[Dict]:
        """Retorna modelo em produção."""
        if os.path.exists(self.production_path):
            with open(self.production_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None
    
    def promote(self, model_id: str, reason: str = "") -> bool:
        """Promove modelo para produção."""
        # Verificar se existe
        metadata = self.get(model_id)
        if not metadata:
            return False
        
        # Criar registro de produção
        production = {
            'model_id': model_id,
            'version': metadata.version,
            'promoted_at': datetime.now().isoformat(),
            'reason': reason,
            'previous': self.get_production(),
        }
        
        with open(self.production_path, 'w', encoding='utf-8') as f:
            json.dump(production, f, indent=2, ensure_ascii=False)
        
        return True
    
    def compare(self, model_id_1: str, model_id_2: str) -> Dict:
        """Compara dois modelos."""
        m1 = self.get(model_id_1)
        m2 = self.get(model_id_2)
        
        if not m1 or not m2:
            return {'error': 'Modelo não encontrado'}
        
        comparison = {
            'model_1': model_id_1,
            'model_2': model_id_2,
            'metrics': {
                'accuracy': {
                    'model_1': m1.metrics.accuracy,
                    'model_2': m2.metrics.accuracy,
                    'winner': model_id_1 if m1.metrics.accuracy > m2.metrics.accuracy else model_id_2,
                },
                'auc_roc': {
                    'model_1': m1.metrics.auc_roc,
                    'model_2': m2.metrics.auc_roc,
                    'winner': model_id_1 if m1.metrics.auc_roc > m2.metrics.auc_roc else model_id_2,
                },
                'brier_score': {
                    'model_1': m1.metrics.brier_score,
                    'model_2': m2.metrics.brier_score,
                    'winner': model_id_1 if m1.metrics.brier_score < m2.metrics.brier_score else model_id_2,
                },
            },
            'features': {
                'model_1': m1.features.n_features,
                'model_2': m2.features.n_features,
            },
            'dataset': {
                'model_1_hash': m1.dataset.hash_sha256[:16] if m1.dataset.hash_sha256 else '',
                'model_2_hash': m2.dataset.hash_sha256[:16] if m2.dataset.hash_sha256 else '',
                'same_dataset': m1.dataset.hash_sha256 == m2.dataset.hash_sha256,
            },
        }
        
        # Vencedor geral
        wins = {'model_1': 0, 'model_2': 0}
        for metric, data in comparison['metrics'].items():
            if data['winner'] == model_id_1:
                wins['model_1'] += 1
            else:
                wins['model_2'] += 1
        
        comparison['overall_winner'] = model_id_1 if wins['model_1'] > wins['model_2'] else model_id_2
        
        return comparison
    
    def get_history(self, limit: int = 10) -> List[Dict]:
        """Retorna histórico de modelos."""
        models = self._registry.get('models', [])
        return sorted(models, key=lambda x: x.get('registered_at', ''), reverse=True)[:limit]
    
    def count(self) -> int:
        """Retorna número total de modelos."""
        return len(self._registry.get('models', []))
    
    def export_summary(self) -> str:
        """Gera resumo em Markdown."""
        models = self.list_all()
        production = self.get_production()
        
        lines = [
            "# Model Registry",
            f"",
            f"**Total:** {len(models)} modelos",
            f"**Em produção:** {production['model_id'] if production else 'Nenhum'}",
            f"",
            f"## Modelos",
            f"",
            f"| ID | Nome | Algoritmo | Accuracy | AUC | Data |",
            f"|-----|------|-----------|----------|-----|------|",
        ]
        
        for m in sorted(models, key=lambda x: x.get('registered_at', ''), reverse=True):
            prod_marker = " 🟢" if production and m['model_id'] == production['model_id'] else ""
            lines.append(
                f"| {m['model_id'][:12]}...{prod_marker} | {m.get('model_name', '')} | "
                f"{m.get('algorithm', '')} | {m.get('metrics', {}).get('accuracy', 0):.1%} | "
                f"{m.get('metrics', {}).get('auc_roc', 0):.3f} | {m.get('train_date', '')} |"
            )
        
        return "\n".join(lines)
