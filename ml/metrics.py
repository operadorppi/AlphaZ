# -*- coding: utf-8 -*-
"""
ml/metrics.py — Métricas unificadas para avaliação de modelos.

Funções centrais:
  - calcular_ece: Expected Calibration Error
  - calcular_brier: Brier Score
  - calcular_profit_factor: Profit Factor
  - calcular_expectancy: Expectancy por trade
  - calcular_hit_rate: Taxa de acerto
  - calcular_sharpe: Sharpe ratio simplificado

Uso:
  from ml.metrics import calcular_ece, calcular_profit_factor
"""
import numpy as np
from sklearn.metrics import roc_auc_score, brier_score_loss


def calcular_ece(y_true, y_proba, n_bins=10):
    """Expected Calibration Error.
    
    Mede quão bem as probabilidades previstas calibram com acurácia real.
    ECE baixo (<0.05) indica boa calibração.
    """
    y_bin = (np.array(y_true) == 1).astype(int)
    bins = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    for i in range(n_bins):
        mask = (y_proba >= bins[i]) & (y_proba < bins[i+1])
        if mask.sum() > 0:
            bin_acc = y_bin[mask].mean()
            bin_conf = y_proba[mask].mean()
            ece += mask.sum() / len(y_bin) * abs(bin_acc - bin_conf)
    return ece


def calcular_brier(y_true, y_proba):
    """Brier Score — medida de precisão de probabilidades."""
    y_bin = (np.array(y_true) == 1).astype(int)
    return brier_score_loss(y_bin, y_proba)


def calcular_profit_factor(retornos):
    """Profit Factor — soma dos ganhos / soma das perdas."""
    ret = np.array(retornos)
    ganhos = ret[ret > 0].sum()
    perdas = abs(ret[ret < 0].sum())
    if perdas == 0:
        return float('inf') if ganhos > 0 else 0.0
    return ganhos / perdas


def calcular_expectancy(retornos, taxa_acerto):
    """Expectancy — retorno médio esperado por trade."""
    if len(retornos) == 0:
        return 0.0
    media_retorno = np.mean(retornos)
    return media_retorno * taxa_acerto


def calcular_hit_rate(y_true, y_pred):
    """Hit Rate — taxa de acerto do modelo."""
    if len(y_true) == 0:
        return 0.0
    return np.mean(np.array(y_true) == np.array(y_pred))


def calcular_auc(y_true, y_proba):
    """AUC-ROC — capacidade discriminativa."""
    y_bin = (np.array(y_true) == 1).astype(int)
    try:
        return roc_auc_score(y_bin, y_proba)
    except ValueError:
        return 0.5


def calcular_sharpe(retornos, risk_free=0.0):
    """Sharpe ratio simplificado."""
    if len(retornos) < 2:
        return 0.0
    excedente = np.array(retornos) - risk_free
    if np.std(excedente) == 0:
        return 0.0
    return np.mean(excedente) / np.std(excedente)


def calcular_max_drawdown(retornos):
    """Maximum Drawdown — maior queda acumulada."""
    if len(retornos) == 0:
        return 0.0
    arc = np.cumsum(retornos)
    max_val = np.maximum.accumulate(arc)
    drawdown = (max_val - arc) / max_val
    return drawdown.max() if drawdown.size > 0 else 0.0


# Mapa de métricas por nome
METRICAS = {
    'ece': calcular_ece,
    'brier': calcular_brier,
    'profit_factor': calcular_profit_factor,
    'expectancy': calcular_expectancy,
    'hit_rate': calcular_hit_rate,
    'auc': calcular_auc,
    'sharpe': calcular_sharpe,
    'max_drawdown': calcular_max_drawdown,
}
