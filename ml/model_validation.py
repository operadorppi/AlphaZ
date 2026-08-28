# -*- coding: utf-8 -*-
"""
ml/model_validation.py — Validação de Modelos ML.

Gera relatórios de validação completos:
- Métricas por fold
- Análise de overfitting
- Comparação com baseline
- Sanity checks
- Recomendação de uso
"""

import json
import os
import time
import numpy as np
from typing import Dict, List, Any, Optional
from datetime import datetime
from ml.model_metadata import ModelMetadata, ModelMetrics


class ModelValidator:
    """Validador de modelos ML."""
    
    def __init__(self):
        self.baseline_accuracy = 0.5  # Aleatório
        self.min_accuracy = 0.55      # Mínimo para uso
        self.min_auc = 0.55
        self.max_ece = 0.20           # Máximo ECE aceitável
        self.max_overfitting_gap = 0.15  # Gap treino/teste máximo
    
    def validate(self, metadata: ModelMetadata, 
                 train_predictions: Optional[np.ndarray] = None,
                 train_outcomes: Optional[np.ndarray] = None,
                 test_predictions: Optional[np.ndarray] = None,
                 test_outcomes: Optional[np.ndarray] = None) -> Dict[str, Any]:
        """Gera relatório de validação completo.
        
        Returns:
            dict com validação completa
        """
        report = {
            'model_id': metadata.model_id,
            'timestamp': datetime.now().isoformat(),
            'checks': [],
            'overall_status': 'PASS',
            'warnings': [],
            'recommendations': [],
        }
        
        # 1. Métricas básicas
        self._check_metrics(metadata, report)
        
        # 2. Sanity checks
        self._check_sanity(metadata, report)
        
        # 3. Overfitting
        if train_predictions is not None and test_predictions is not None:
            self._check_overfitting(
                train_predictions, train_outcomes,
                test_predictions, test_outcomes,
                report
            )
        
        # 4. Feature importance
        self._check_feature_importance(metadata, report)
        
        # 5. Dataset
        self._check_dataset(metadata, report)
        
        # 6. Recomendações
        self._generate_recommendations(metadata, report)
        
        # Status geral
        failed_checks = [c for c in report['checks'] if c['status'] == 'FAIL']
        if failed_checks:
            report['overall_status'] = 'FAIL'
        elif report['warnings']:
            report['overall_status'] = 'WARN'
        
        return report
    
    def _check_metrics(self, metadata: ModelMetadata, report: Dict):
        """Verifica métricas básicas."""
        m = metadata.metrics
        
        # Accuracy
        status = 'PASS' if m.accuracy >= self.min_accuracy else 'FAIL'
        report['checks'].append({
            'name': 'accuracy',
            'status': status,
            'value': round(m.accuracy, 4),
            'threshold': self.min_accuracy,
            'detail': f'{m.accuracy:.1%} >= {self.min_accuracy:.1%}' if status == 'PASS' 
                      else f'{m.accuracy:.1%} < {self.min_accuracy:.1%}',
        })
        
        # AUC
        status = 'PASS' if m.auc_roc >= self.min_auc else 'FAIL'
        report['checks'].append({
            'name': 'auc_roc',
            'status': status,
            'value': round(m.auc_roc, 4),
            'threshold': self.min_auc,
            'detail': f'{m.auc_roc:.4f} >= {self.min_auc}' if status == 'PASS'
                      else f'{m.auc_roc:.4f} < {self.min_auc}',
        })
        
        # ECE (calibração)
        status = 'PASS' if m.ece <= self.max_ece else 'WARN'
        report['checks'].append({
            'name': 'ece',
            'status': status,
            'value': round(m.ece, 4),
            'threshold': self.max_ece,
            'detail': f'{m.ece:.4f} <= {self.max_ece}' if status == 'PASS'
                      else f'{m.ece:.4f} > {self.max_ece} (calibração ruim)',
        })
        
        # Brier Score
        status = 'PASS' if m.brier_score <= 0.25 else 'WARN'
        report['checks'].append({
            'name': 'brier_score',
            'status': status,
            'value': round(m.brier_score, 4),
            'detail': f'{m.brier_score:.4f}',
        })
    
    def _check_sanity(self, metadata: ModelMetadata, report: Dict):
        """Sanity checks básicos."""
        m = metadata.metrics
        
        # Accuracy > 50% (melhor que aleatório)
        status = 'PASS' if m.accuracy > 0.5 else 'FAIL'
        report['checks'].append({
            'name': 'better_than_random',
            'status': status,
            'value': round(m.accuracy, 4),
            'detail': 'Melhor que aleatório' if status == 'PASS' else 'Pior que aleatório!',
        })
        
        # AUC > 0.5
        status = 'PASS' if m.auc_roc > 0.5 else 'FAIL'
        report['checks'].append({
            'name': 'auc_above_chance',
            'status': status,
            'value': round(m.auc_roc, 4),
            'detail': 'AUC > 0.5' if status == 'PASS' else 'AUC <= 0.5!',
        })
        
        # Número mínimo de features
        n_features = metadata.features.n_features
        status = 'PASS' if n_features >= 5 else 'WARN'
        report['checks'].append({
            'name': 'feature_count',
            'status': status,
            'value': n_features,
            'detail': f'{n_features} features',
        })
        
        # Dataset size
        n_rows = metadata.dataset.n_rows
        status = 'PASS' if n_rows >= 1000 else 'WARN'
        report['checks'].append({
            'name': 'dataset_size',
            'status': status,
            'value': n_rows,
            'detail': f'{n_rows:,} linhas',
        })
    
    def _check_overfitting(self, train_pred: np.ndarray, train_out: np.ndarray,
                           test_pred: np.ndarray, test_out: np.ndarray, report: Dict):
        """Verifica overfitting."""
        from sklearn.metrics import accuracy_score
        
        train_acc = accuracy_score(train_out, (train_pred > 0.5).astype(int))
        test_acc = accuracy_score(test_out, (test_pred > 0.5).astype(int))
        gap = train_acc - test_acc
        
        status = 'PASS' if gap <= self.max_overfitting_gap else 'WARN'
        if gap > 0.2:
            status = 'FAIL'
        
        report['checks'].append({
            'name': 'overfitting',
            'status': status,
            'train_accuracy': round(train_acc, 4),
            'test_accuracy': round(test_acc, 4),
            'gap': round(gap, 4),
            'threshold': self.max_overfitting_gap,
            'detail': f'Gap treino-teste: {gap:.1%}',
        })
        
        if gap > self.max_overfitting_gap:
            report['warnings'].append(f'Possível overfitting: gap={gap:.1%}')
    
    def _check_feature_importance(self, metadata: ModelMetadata, report: Dict):
        """Verifica importância das features."""
        fi = metadata.feature_importance
        
        if not fi:
            report['checks'].append({
                'name': 'feature_importance',
                'status': 'WARN',
                'detail': 'Feature importance não disponível',
            })
            return
        
        # Top 3 features devem ter importância significativa
        sorted_fi = sorted(fi.items(), key=lambda x: x[1], reverse=True)
        top3_importance = sum(v for _, v in sorted_fi[:3])
        total_importance = sum(fi.values())
        
        if total_importance > 0:
            top3_ratio = top3_importance / total_importance
        else:
            top3_ratio = 0
        
        status = 'PASS' if top3_ratio > 0.3 else 'WARN'
        report['checks'].append({
            'name': 'feature_importance',
            'status': status,
            'top3_features': [f[0] for f in sorted_fi[:3]],
            'top3_ratio': round(top3_ratio, 3),
            'detail': f'Top 3 features = {top3_ratio:.1%} da importância',
        })
    
    def _check_dataset(self, metadata: ModelMetadata, report: Dict):
        """Verifica dataset."""
        d = metadata.dataset
        
        # Hash existe
        status = 'PASS' if d.hash_sha256 else 'WARN'
        report['checks'].append({
            'name': 'dataset_hash',
            'status': status,
            'hash': d.hash_sha256[:16] + '...' if d.hash_sha256 else 'N/A',
            'detail': 'Dataset hashado' if status == 'PASS' else 'Dataset sem hash',
        })
        
        # Balanceamento de classes
        total = d.n_labels_pos + d.n_labels_neg + d.n_labels_neutro
        if total > 0:
            pos_ratio = d.n_labels_pos / total
            neg_ratio = d.n_labels_neg / total
            
            status = 'PASS' if 0.2 <= pos_ratio <= 0.8 else 'WARN'
            report['checks'].append({
                'name': 'class_balance',
                'status': status,
                'pos_ratio': round(pos_ratio, 3),
                'neg_ratio': round(neg_ratio, 3),
                'detail': f'Pos={pos_ratio:.1%} Neg={neg_ratio:.1%}',
            })
    
    def _generate_recommendations(self, metadata: ModelMetadata, report: Dict):
        """Gera recomendações."""
        m = metadata.metrics
        
        if m.accuracy < 0.55:
            report['recommendations'].append(
                'Acurácia baixa. Considere: mais features, mais dados, ou ajuste de hiperparâmetros.'
            )
        
        if m.ece > 0.15:
            report['recommendations'].append(
                'Calibração ruim (ECE alto). Considere Platt scaling ou isotonic regression.'
            )
        
        if m.auc_roc < 0.6:
            report['recommendations'].append(
                'AUC baixo. O modelo tem dificuldade em separar classes.'
            )
        
        if metadata.features.n_features < 10:
            report['recommendations'].append(
                'Poucas features. Considere adicionar features de contexto institucional.'
            )
        
        if not report['warnings'] and not [c for c in report['checks'] if c['status'] == 'FAIL']:
            report['recommendations'].append(
                'Modelo válido para uso em produção.'
            )
    
    def to_markdown(self, report: Dict) -> str:
        """Gera relatório em Markdown."""
        lines = [
            f"# Model Validation Report",
            f"",
            f"**Model:** {report['model_id']}",
            f"**Status:** {report['overall_status']}",
            f"**Timestamp:** {report['timestamp']}",
            f"",
            f"## Checks",
            f"",
            f"| Check | Status | Value | Detail |",
            f"|-------|--------|-------|--------|",
        ]
        
        for check in report['checks']:
            status_icon = {'PASS': '✅', 'WARN': '⚠️', 'FAIL': '❌'}.get(check['status'], '?')
            value = check.get('value', check.get('gap', ''))
            lines.append(f"| {check['name']} | {status_icon} {check['status']} | {value} | {check['detail']} |")
        
        if report['warnings']:
            lines.append(f"")
            lines.append(f"## Warnings")
            for w in report['warnings']:
                lines.append(f"- ⚠️ {w}")
        
        if report['recommendations']:
            lines.append(f"")
            lines.append(f"## Recommendations")
            for r in report['recommendations']:
                lines.append(f"- {r}")
        
        return "\n".join(lines)
