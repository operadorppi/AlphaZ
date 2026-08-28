# -*- coding: utf-8 -*-
"""
core/calibration.py — Calibração de Probabilidades ML (Fase 11).

Separa MODEL PROBABILITY de TRADING DECISION.

Infraestrutura:
- Calibration Curve: visualizar calibração do modelo
- Brier Score: medir qualidade das probabilidades
- Reliability: verificar se P(y=1) ≈ frequência observada
- Threshold Optimization: encontrar threshold ótimo por regime
- Threshold por regime: thresholds estatisticamente justificados

Conceitos:
- PROBABILIDADE CALIBRADA: P(preço sobe) = 0.7 significa que 70% das vezes sobe
- THRESHOLD: ponto de corte para ação (comprar se P > threshold)
- O threshold ÓTIMO depende do custo de execução e do regime
"""

import numpy as np
import json
import os
from typing import Dict, List, Tuple, Optional, Any
from datetime import datetime
from pathlib import Path
from collections import defaultdict


class ProbabilityCalibrator:
    """Calibrador de probabilidades do modelo ML."""
    
    def __init__(self, n_bins: int = 10):
        self.n_bins = n_bins
        self.bin_edges = np.linspace(0, 1, n_bins + 1)
        
        # Estado acumulado
        self._predictions = []  # probabilidades preditas
        self._outcomes = []     # resultados reais (0/1)
        self._regimes = []      # regime na hora da predição
        
        # Thresholds calibrados
        self.threshold_default = 0.5
        self.thresholds_by_regime = {}
        
        # Métricas
        self.brier_score = 0.0
        self.calibration_curve = {}
        self.expected_calibration_error = 0.0
    
    def update(self, predicted_prob: float, actual_outcome: int, regime: str = 'lateral'):
        """Registra uma predição e seu resultado.
        
        Args:
            predicted_prob: Probabilidade predita pelo modelo (0-1)
            actual_outcome: Resultado real (1=sobe, 0=desce/neutro)
            regime: Regime de mercado na hora da predição
        """
        self._predictions.append(predicted_prob)
        self._outcomes.append(actual_outcome)
        self._regimes.append(regime)
        
        # Recalcular métricas a cada 100 predições
        if len(self._predictions) % 100 == 0:
            self._recalcular()
    
    def _recalcular(self):
        """Recalcula todas as métricas de calibração."""
        if len(self._predictions) < 10:
            return
        
        preds = np.array(self._predictions)
        outcomes = np.array(self._outcomes)
        
        # 1. Brier Score
        self.brier_score = float(np.mean((preds - outcomes) ** 2))
        
        # 2. Calibration Curve
        self.calibration_curve = self._calibration_curve(preds, outcomes)
        
        # 3. Expected Calibration Error (ECE)
        self.expected_calibration_error = self._ece(preds, outcomes)
        
        # 4. Thresholds por regime
        self._calibrate_thresholds_by_regime()
    
    def _calibration_curve(self, preds: np.ndarray, outcomes: np.ndarray) -> Dict:
        """Calcula calibration curve (confiança vs acurácia observada)."""
        bins = defaultdict(lambda: {'preds': [], 'outcomes': []})
        
        for pred, outcome in zip(preds, outcomes):
            bin_idx = min(int(pred * self.n_bins), self.n_bins - 1)
            bins[bin_idx]['preds'].append(pred)
            bins[bin_idx]['outcomes'].append(outcome)
        
        curve = {}
        for bin_idx, data in sorted(bins.items()):
            if data['preds']:
                mean_pred = np.mean(data['preds'])
                mean_outcome = np.mean(data['outcomes'])
                count = len(data['preds'])
                curve[f'{mean_pred:.2f}'] = {
                    'predicted': round(float(mean_pred), 3),
                    'observed': round(float(mean_outcome), 3),
                    'count': count,
                    'gap': round(abs(float(mean_pred) - float(mean_outcome)), 3),
                }
        
        return curve
    
    def _ece(self, preds: np.ndarray, outcomes: np.ndarray) -> float:
        """Expected Calibration Error."""
        bins = defaultdict(lambda: {'preds': [], 'outcomes': []})
        
        for pred, outcome in zip(preds, outcomes):
            bin_idx = min(int(pred * self.n_bins), self.n_bins - 1)
            bins[bin_idx]['preds'].append(pred)
            bins[bin_idx]['outcomes'].append(outcome)
        
        ece = 0.0
        n = len(preds)
        
        for bin_idx, data in bins.items():
            if data['preds']:
                bin_size = len(data['preds'])
                mean_pred = np.mean(data['preds'])
                mean_outcome = np.mean(data['outcomes'])
                ece += (bin_size / n) * abs(mean_pred - mean_outcome)
        
        return float(ece)
    
    def _calibrate_thresholds_by_regime(self):
        """Calibrar threshold ótimo por regime."""
        if len(self._predictions) < 50:
            return
        
        regimes = set(self._regimes)
        
        for regime in regimes:
            indices = [i for i, r in enumerate(self._regimes) if r == regime]
            if len(indices) < 20:
                continue
            
            preds = np.array([self._predictions[i] for i in indices])
            outcomes = np.array([self._outcomes[i] for i in indices])
            
            # Encontrar threshold que maximiza F1
            best_threshold = 0.5
            best_f1 = 0.0
            
            for threshold in np.arange(0.3, 0.8, 0.05):
                tp = np.sum((preds >= threshold) & (outcomes == 1))
                fp = np.sum((preds >= threshold) & (outcomes == 0))
                fn = np.sum((preds < threshold) & (outcomes == 1))
                
                precision = tp / (tp + fp) if (tp + fp) > 0 else 0
                recall = tp / (tp + fn) if (tp + fn) > 0 else 0
                f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
                
                if f1 > best_f1:
                    best_f1 = f1
                    best_threshold = threshold
            
            self.thresholds_by_regime[regime] = {
                'threshold': round(float(best_threshold), 3),
                'f1': round(float(best_f1), 3),
                'n_samples': len(indices),
            }
    
    def get_threshold(self, regime: str = 'lateral') -> float:
        """Retorna threshold calibrado para o regime."""
        if regime in self.thresholds_by_regime:
            return self.thresholds_by_regime[regime]['threshold']
        return self.threshold_default
    
    def get_metrics(self) -> Dict[str, Any]:
        """Retorna todas as métricas de calibração."""
        return {
            'brier_score': round(self.brier_score, 4),
            'ece': round(self.expected_calibration_error, 4),
            'n_predictions': len(self._predictions),
            'calibration_curve': self.calibration_curve,
            'thresholds_by_regime': self.thresholds_by_regime,
            'threshold_default': self.threshold_default,
        }
    
    def plot_calibration_curve(self) -> str:
        """Gera texto da calibration curve para visualização."""
        if not self.calibration_curve:
            return "Sem dados suficientes"
        
        lines = [
            "Calibration Curve:",
            "Predicted → Observed (count)",
            "-" * 40,
        ]
        
        for pred_str, data in sorted(self.calibration_curve.items()):
            bar_len = int(data['observed'] * 20)
            bar = '█' * bar_len + '░' * (20 - bar_len)
            lines.append(f"  {pred_str} → {data['observed']:.3f} ({data['count']:4d}) {bar}")
        
        lines.append("-" * 40)
        lines.append(f"Brier Score: {self.brier_score:.4f} (0=perfeito, 1=pior)")
        lines.append(f"ECE: {self.expected_calibration_error:.4f} (0=perfeito)")
        
        return "\n".join(lines)


class ThresholdOptimizer:
    """Otimizador de thresholds por regime e custo."""
    
    def __init__(self, custo_execucao: float = 5.0):
        self.custo_execucao = custo_execucao
        self._historico = []
    
    def optimize(self, predictions: np.ndarray, outcomes: np.ndarray,
                 regime: str = 'lateral') -> Dict[str, Any]:
        """Encontra threshold ótimo que maximiza lucro esperado.
        
        Args:
            predictions: Array de probabilidades preditas
            outcomes: Array de resultados (1=sobe, 0=desce)
            regime: Regime de mercado
        
        Returns:
            dict com threshold, expected_profit, win_rate, etc.
        """
        if len(predictions) < 20:
            return {'threshold': 0.5, 'expected_profit': 0, 'n_samples': len(predictions)}
        
        best_threshold = 0.5
        best_profit = -999
        
        results_by_threshold = []
        
        for threshold in np.arange(0.3, 0.8, 0.01):
            # Trades que o modelo faria
            trades = predictions >= threshold
            
            if trades.sum() == 0:
                continue
            
            # Resultado desses trades
            trade_outcomes = outcomes[trades]
            
            # Lucro simulado
            n_wins = trade_outcomes.sum()
            n_losses = len(trade_outcomes) - n_wins
            
            # Assumindo TP=50, SL=30 (ajustar conforme config)
            tp_pts = 50
            sl_pts = 30
            profit = n_wins * tp_pts - n_losses * sl_pts - len(trade_outcomes) * self.custo_execucao
            
            win_rate = n_wins / len(trade_outcomes) if len(trade_outcomes) > 0 else 0
            
            results_by_threshold.append({
                'threshold': round(float(threshold), 3),
                'n_trades': int(trades.sum()),
                'win_rate': round(float(win_rate), 3),
                'profit': round(float(profit), 1),
                'profit_per_trade': round(float(profit / trades.sum()), 2) if trades.sum() > 0 else 0,
            })
            
            if profit > best_profit:
                best_profit = profit
                best_threshold = threshold
        
        return {
            'threshold': round(float(best_threshold), 3),
            'expected_profit': round(float(best_profit), 1),
            'regime': regime,
            'n_samples': len(predictions),
            'results_by_threshold': results_by_threshold[:10],  # Top 10
        }
    
    def optimize_by_regime(self, predictions: np.ndarray, outcomes: np.ndarray,
                           regimes: List[str]) -> Dict[str, Dict]:
        """Otimiza threshold para cada regime."""
        unique_regimes = set(regimes)
        results = {}
        
        for regime in unique_regimes:
            indices = [i for i, r in enumerate(regimes) if r == regime]
            if len(indices) < 20:
                continue
            
            preds = np.array([predictions[i] for i in indices])
            outs = np.array([outcomes[i] for i in indices])
            
            results[regime] = self.optimize(preds, outs, regime)
        
        return results


class ModelDecisionSeparator:
    """Separa MODEL PROBABILITY de TRADING DECISION.
    
    Princípio:
    - Modelo produz PROBABILIDADE (P(sobe) = 0.72)
    - Trading engine decide AÇÃO (COMPRAR/VENDER/AGUARDAR)
    - A decisão depende de: probabilidade + custo + regime + risco
    """
    
    def __init__(self, calibrator: ProbabilityCalibrator, custo_execucao: float = 5.0):
        self.calibrator = calibrator
        self.custo_execucao = custo_execucao
    
    def separate(self, ml_prob: float, regime: str = 'lateral',
                 confianca: float = 0.5, score_heuristico: float = 0.0) -> Dict[str, Any]:
        """Separa probabilidade do modelo da decisão de trading.
        
        Returns:
            dict com model_probability, trading_decision, reasoning
        """
        # 1. MODEL PROBABILITY (calibrada)
        calibrated_prob = ml_prob  # TODO: aplicar Platt scaling se disponível
        
        # 2. THRESHOLD calibrado para o regime
        threshold = self.calibrator.get_threshold(regime)
        
        # 3. TRADING DECISION
        if calibrated_prob >= threshold:
            # Modelo diz que vai subir
            signal_direction = 'C'  # compra
            confidence_gap = calibrated_prob - threshold
        elif calibrated_prob <= (1 - threshold):
            # Modelo diz que vai descer
            signal_direction = 'V'  # venda
            confidence_gap = (1 - calibrated_prob) - threshold
        else:
            # Zona de incerteza
            signal_direction = ''  # neutro
            confidence_gap = 0
        
        # 4. Ajuste por custo de execução
        # Se o sinal é fraco, o custo pode comer o lucro
        min_edge = self.custo_execucao / 100  # edge mínimo em probabilidade
        if abs(calibrated_prob - 0.5) < min_edge:
            signal_direction = ''
        
        # 5. Combinar com heurística
        if score_heuristico != 0:
            # Se heurística e ML concordam, aumentar confiança
            if (score_heuristico > 0 and signal_direction == 'C') or \
               (score_heuristico < 0 and signal_direction == 'V'):
                confidence_gap *= 1.2  # Bônus de concordância
        
        return {
            'model_probability': round(calibrated_prob, 4),
            'calibrated': True,
            'threshold': threshold,
            'regime': regime,
            'trading_decision': signal_direction,
            'confidence_gap': round(confidence_gap, 4),
            'reasoning': {
                'ml_prob': round(ml_prob, 4),
                'threshold': threshold,
                'regime': regime,
                'direction': signal_direction,
                'gap': round(confidence_gap, 4),
                'cost_adjusted': abs(calibrated_prob - 0.5) >= min_edge,
            }
        }
    
    def save(self, path: str):
        """Salva estado do calibrador."""
        data = {
            'calibrator': self.calibrator.get_metrics(),
            'custo_execucao': self.custo_execucao,
            'saved_at': datetime.now().isoformat(),
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def load(self, path: str):
        """Carrega estado do calibrador."""
        if not os.path.exists(path):
            return False
        
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        cal_data = data.get('calibrator', {})
        self.calibrator.threshold_default = cal_data.get('threshold_default', 0.5)
        self.calibrator.thresholds_by_regime = cal_data.get('thresholds_by_regime', {})
        self.calibrator.brier_score = cal_data.get('brier_score', 0)
        self.calibrator.expected_calibration_error = cal_data.get('ece', 0)
        
        return True


# ============================================================
# INSTÂNCIA GLOBAL
# ============================================================

def create_calibration_system(config: Dict = None) -> ModelDecisionSeparator:
    """Cria sistema de calibração completo."""
    config = config or {}
    
    calibrator = ProbabilityCalibrator(n_bins=10)
    custo = config.get('trading', {}).get('custo_execucao', {}).get('WIN', 5.0)
    
    separator = ModelDecisionSeparator(calibrator, custo_execucao=custo)
    
    # Tentar carregar estado salvo
    save_dir = config.get('save_dir', 'D:\\MarketData\\mimo')
    state_path = os.path.join(save_dir, 'calibration_state.json')
    separator.load(state_path)
    
    return separator
