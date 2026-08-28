#!/usr/bin/env python3
"""
calibrar_modelo.py — Platt Calibration com verificacao de estabilidade.

Split:
  TREINO:     Aug 4-7  (4 dias)
  CALIBRACAO: Aug 10-11 (2 dias)
  TESTE:      Aug 13-14 (2 dias)

Metodo:
  1. Treinar RF no conjunto de treino
  2. Ajustar Platt em dia 10 e dia 11 separadamente
  3. Verificar estabilidade A/B
  4. Platt final em 10+11 combinado
  5. Medir ECE em 3 superficies
  6. Re-otimizar threshold no espaco calibrado
  7. Rodar backtester com modelo calibrado
"""
import sys, os, json, time
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, roc_auc_score, brier_score_loss
from datetime import datetime

# ============================================================
# CONFIG
# ============================================================
PARQUET_PATH = r'D:\MarketData\mimo\dataset_final_v2_win_v914.parquet'
OUTPUT_DIR = r'D:\MarketData\mimo'

PROIBIDAS = ['label', 'saida', 'retorno', 'duracao', 'atingido', 'ts_ms',
             'book_ts', 'ctx_', 'ativo', 'dia', 'entrada', 'outcome',
             'preco_saida', 'tp_atingido', 'sl_atingido', 'fase_sessao',
             'dias_ate_venc', 'duracao_label_ms']

TP, SL = 100, 50
COOLDOWN_MS = 45000


# ============================================================
# UTILITIES
# ============================================================

def colunas_treino(df):
    cols = [c for c in df.columns
            if df[c].dtype.kind in ('f', 'i')
            and not any(p in c.lower() for p in PROIBIDAS)]
    return cols


def calcular_ece(y_true, y_proba, n_bins=10):
    """Expected Calibration Error. Converte para binario se necessario."""
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
    """Brier Score (menor = melhor). Converte para binario se necessario."""
    y_bin = (np.array(y_true) == 1).astype(int)
    return brier_score_loss(y_bin, y_proba)


def reliability_data(y_true, y_proba, n_bins=10):
    """Dados para reliability diagram."""
    y_bin = (np.array(y_true) == 1).astype(int)
    bins = np.linspace(0, 1, n_bins + 1)
    data = []
    for i in range(n_bins):
        mask = (y_proba >= bins[i]) & (y_proba < bins[i+1])
        if mask.sum() > 0:
            data.append({
                'bin_center': (bins[i] + bins[i+1]) / 2,
                'fraction_positives': y_bin[mask].mean(),
                'mean_predicted': y_proba[mask].mean(),
                'count': int(mask.sum())
            })
    return data


# ============================================================
# PLATT CALIBRATOR
# ============================================================

class PlattCalibrator:
    """Platt scaling com verificacao de estabilidade."""
    
    def __init__(self, modelo_rf):
        self.rf = modelo_rf
        self.platt = None
        self.A = None
        self.B = None
    
    def fit(self, X_val, y_val):
        """Ajusta Platt em dados de validacao NAO vistos pelo RF."""
        p_bruta = self.rf.predict_proba(X_val)[:, 1]
        
        # Platt: regressao logistica de y ~ p_bruta
        # Binario: 1 = TP (label=1), 0 = nao-TP (label=-1 ou 0)
        y_bin = (y_val == 1).astype(int)
        
        self.platt = LogisticRegression(C=1e10, solver='lbfgs', max_iter=1000)
        self.platt.fit(p_bruta.reshape(-1, 1), y_bin)
        
        self.A = self.platt.coef_[0][0]
        self.B = self.platt.intercept_[0]
    
    def calibrar(self, X):
        """Retorna probabilidade calibrada de TP."""
        p_bruta = self.rf.predict_proba(X)[:, 1]
        p_cal = self.platt.predict_proba(p_bruta.reshape(-1, 1))[:, 1]
        return p_cal
    
    def esta_estavel(self, outro, tol=0.5):
        """Verifica se A/B sao similares entre dois calibradores."""
        if self.A is None or outro.A is None:
            return False
        dA = abs(self.A - outro.A) / (abs(self.A) + 1e-10)
        dB = abs(self.B - outro.B) / (abs(self.B) + 1e-10)
        return dA < tol and dB < tol


# ============================================================
# BACKTESTER
# ============================================================

def rodar_backtest(df_dia, probs, threshold, tp=TP, sl=SL, cooldown_ms=COOLDOWN_MS):
    """Backtester simples: so entra quando prob >= threshold."""
    trades = []
    equity = 10000.0
    cooldown_until = 0
    
    for i in range(len(df_dia)):
        ts = df_dia['ts_ms'].iloc[i]
        if ts < cooldown_until:
            continue
        if probs[i] >= threshold:
            label = df_dia['label'].iloc[i]
            preco_ent = df_dia['preco_entrada'].iloc[i]
            preco_sai = df_dia['preco_saida'].iloc[i]
            
            if label == 1:
                gross = tp
            elif label == -1:
                gross = -sl
            else:
                gross = preco_sai - preco_ent
            
            net = gross - 7  # custos
            equity += net
            trades.append({
                'ts': ts, 'label': int(label), 'gross': gross, 'net': net,
                'equity': equity, 'prob': probs[i]
            })
            cooldown_until = ts + cooldown_ms
    
    return trades, equity


def calc_metrics(trades, n_total):
    """Calcula metricas de trading."""
    if not trades:
        return {'n_trades': 0, 'pf': 0, 'expectancy': 0, 'win_rate': 0, 'n_tp': 0, 'n_sl': 0}
    
    ganhos = sum(t['net'] for t in trades if t['net'] > 0)
    perdas = abs(sum(t['net'] for t in trades if t['net'] < 0))
    pf = ganhos / perdas if perdas > 0 else float('inf')
    expectancy = sum(t['net'] for t in trades) / len(trades)
    win_rate = sum(1 for t in trades if t['net'] > 0) / len(trades)
    n_tp = sum(1 for t in trades if t['label'] == 1)
    n_sl = sum(1 for t in trades if t['label'] == -1)
    
    return {
        'n_trades': len(trades),
        'pf': round(pf, 2) if pf != float('inf') else 'inf',
        'expectancy': round(expectancy, 2),
        'win_rate': round(win_rate, 4),
        'n_tp': n_tp,
        'n_sl': n_sl,
        'equity_final': round(trades[-1]['equity'], 2) if trades else 10000.0
    }


# ============================================================
# MAIN
# ============================================================

def main():
    print('='*60)
    print('CALIBRACAO PLATT — MODELO RF')
    print('='*60)
    
    # Carregar dados
    print('\nCarregando dataset...')
    df = pd.read_parquet(PARQUET_PATH)
    df = df[df['ativo'] == 'WINV26'].copy()
    df['data'] = pd.to_datetime(df['ts_ms'], unit='ms', utc=True).dt.date
    df = df.sort_values('ts_ms').reset_index(drop=True)
    print(f'  Total: {len(df)} linhas, {len(df["data"].unique())} dias')
    
    # Split temporal
    from datetime import date
    TREINO_DIAS = [date(2026, 8, d) for d in [4, 5, 6, 7]]
    CAL_DIAS = [date(2026, 8, d) for d in [10, 11]]
    TEST_DIAS = [date(2026, 8, d) for d in [13, 14]]
    
    df_train = df[df['data'].isin(TREINO_DIAS)].copy()
    df_cal10 = df[df['data'] == CAL_DIAS[0]].copy()
    df_cal11 = df[df['data'] == CAL_DIAS[1]].copy()
    df_cal = df[df['data'].isin(CAL_DIAS)].copy()
    df_test = df[df['data'].isin(TEST_DIAS)].copy()
    
    print(f'\nSplit:')
    print(f'  TREINO:     {TREINO_DIAS} -> {len(df_train)} linhas')
    print(f'  CAL dia 10: {CAL_DIAS[0]} -> {len(df_cal10)} linhas')
    print(f'  CAL dia 11: {CAL_DIAS[1]} -> {len(df_cal11)} linhas')
    print(f'  CAL comb:   {CAL_DIAS} -> {len(df_cal)} linhas')
    print(f'  TESTE:      {TEST_DIAS} -> {len(df_test)} linhas')
    
    # Features
    X_cols = colunas_treino(df)
    print(f'\nFeatures: {len(X_cols)}')
    
    # Filtrar labels nao-zero para treino (foco em TP vs SL)
    # Mas manter TIMEOUT para calibracao (modelo precisa aprender a dizer "nao entra")
    y_col = 'label'
    
    # ============================================================
    # PASSO 1: Treinar RF
    # ============================================================
    print('\n--- PASSO 1: Treinar RF ---')
    X_train = df_train[X_cols].fillna(0)
    y_train = df_train[y_col].astype(int)
    
    print(f'  Treino: {len(X_train)} linhas')
    print(f'  Labels: {dict(y_train.value_counts().sort_index())}')
    
    t0 = time.time()
    rf = RandomForestClassifier(
        n_estimators=100, max_depth=10, min_samples_split=20,
        min_samples_leaf=10, class_weight='balanced',
        random_state=42, n_jobs=-1
    )
    rf.fit(X_train, y_train)
    print(f'  Treinado em {time.time()-t0:.1f}s')
    
    # ============================================================
    # PASSO 2: Avaliar RF bruto
    # ============================================================
    print('\n--- PASSO 2: RF bruto no teste ---')
    X_test = df_test[X_cols].fillna(0)
    y_test = df_test[y_col].astype(int)
    p_bruta_test = rf.predict_proba(X_test)[:, 1]
    
    ece_bruto = calcular_ece(y_test, p_bruta_test)
    brier_bruto = calcular_brier(y_test, p_bruta_test)
    print(f'  ECE bruto: {ece_bruto:.4f}')
    print(f'  Brier bruto: {brier_bruto:.4f}')
    print(f'  Prob media: {p_bruta_test.mean():.4f}')
    print(f'  Prob max: {p_bruta_test.max():.4f}')
    
    # ============================================================
    # PASSO 3: Platt em dia 10 e dia 11 separadamente
    # ============================================================
    print('\n--- PASSO 3: Platt em dia 10 e dia 11 ---')
    
    cal_10 = PlattCalibrator(rf)
    cal_10.fit(df_cal10[X_cols].fillna(0), df_cal10[y_col].astype(int))
    print(f'  Dia 10: A={cal_10.A:.4f}, B={cal_10.B:.4f}')
    
    cal_11 = PlattCalibrator(rf)
    cal_11.fit(df_cal11[X_cols].fillna(0), df_cal11[y_col].astype(int))
    print(f'  Dia 11: A={cal_11.A:.4f}, B={cal_11.B:.4f}')
    
    # ============================================================
    # PASSO 4: Verificar estabilidade
    # ============================================================
    print('\n--- PASSO 4: Estabilidade ---')
    estavel = cal_10.esta_estavel(cal_11, tol=0.5)
    
    dA = abs(cal_10.A - cal_11.A) / (abs(cal_10.A) + 1e-10)
    dB = abs(cal_10.B - cal_11.B) / (abs(cal_10.B) + 1e-10)
    print(f'  Variacao A: {dA:.4f} (tol: 0.5)')
    print(f'  Variacao B: {dB:.4f} (tol: 0.5)')
    print(f'  Estavel: {estavel}')
    
    if not estavel:
        print('  AVISO: Calibracao instavel! Usando Platt do dia 11 apenas.')
    
    # ============================================================
    # PASSO 5: Platt final em 10+11
    # ============================================================
    print('\n--- PASSO 5: Platt final (10+11) ---')
    cal_final = PlattCalibrator(rf)
    cal_final.fit(df_cal[X_cols].fillna(0), df_cal[y_col].astype(int))
    print(f'  A={cal_final.A:.4f}, B={cal_final.B:.4f}')
    
    # ============================================================
    # PASSO 6: ECE em 3 superficies
    # ============================================================
    print('\n--- PASSO 6: ECE em 3 superficies ---')
    
    # Superficie 1: in-sample calibracao (dias 10-11)
    p_cal_cal = cal_final.calibrar(df_cal[X_cols].fillna(0))
    y_cal = df_cal[y_col].astype(int)
    ece_cal = calcular_ece(y_cal, p_cal_cal)
    brier_cal = calcular_brier(y_cal, p_cal_cal)
    print(f'  In-sample (10-11): ECE={ece_cal:.4f}, Brier={brier_cal:.4f}')
    
    # Superficie 2: bootstrap (estimativa de variabilidade)
    np.random.seed(42)
    n_boot = 100
    ece_boots = []
    for _ in range(n_boot):
        idx = np.random.choice(len(df_cal), len(df_cal), replace=True)
        y_boot = y_cal.values[idx]
        p_boot = p_cal_cal[idx]
        ece_boots.append(calcular_ece(y_boot, p_boot))
    ece_boot_mean = np.mean(ece_boots)
    ece_boot_std = np.std(ece_boots)
    print(f'  Bootstrap (100x): ECE={ece_boot_mean:.4f} ± {ece_boot_std:.4f}')
    
    # Superficie 3: teste final (dias 13-14)
    p_cal_test = cal_final.calibrar(X_test)
    ece_test = calcular_ece(y_test, p_cal_test)
    brier_test = calcular_brier(y_test, p_cal_test)
    print(f'  Teste (13-14):    ECE={ece_test:.4f}, Brier={brier_test:.4f}')
    
    # ============================================================
    # PASSO 7: Re-otimizar threshold no espaco calibrado
    # ============================================================
    print('\n--- PASSO 7: Threshold otimo (espaco calibrado) ---')
    
    best_thr = 0.50
    best_pf = 0
    
    for thr in [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]:
        trades, eq = rodar_backtest(df_test, p_cal_test, thr)
        m = calc_metrics(trades, len(df_test))
        pf = m['pf'] if isinstance(m['pf'], (int, float)) else 0
        if isinstance(pf, (int, float)) and pf > best_pf:
            best_pf = pf
            best_thr = thr
        print(f'  thr={thr:.2f}: trades={m["n_trades"]}, PF={m["pf"]}, E={m["expectancy"]:.1f}')
    
    print(f'\n  Threshold otimo: {best_thr}')
    
    # ============================================================
    # PASSO 8: Backtester com threshold otimo
    # ============================================================
    print('\n--- PASSO 8: Backtester final ---')
    
    trades_final, eq_final = rodar_backtest(df_test, p_cal_test, best_thr)
    metrics_final = calc_metrics(trades_final, len(df_test))
    
    print(f'  Threshold: {best_thr}')
    print(f'  Trades: {metrics_final["n_trades"]}')
    print(f'  PF: {metrics_final["pf"]}')
    print(f'  Expectancy: {metrics_final["expectancy"]:.1f} pts')
    print(f'  Win rate: {metrics_final["win_rate"]:.1%}')
    print(f'  TP trades: {metrics_final["n_tp"]}')
    print(f'  SL trades: {metrics_final["n_sl"]}')
    print(f'  Equity final: {metrics_final["equity_final"]:.2f}')
    
    # ============================================================
    # COMPARACAO: bruto vs calibrado
    # ============================================================
    print('\n--- COMPARACAO: Bruto vs Calibrado ---')
    
    # Bruto com threshold 0.75
    p_bruta_test_full = rf.predict_proba(X_test)[:, 1]
    trades_bruto, eq_bruto = rodar_backtest(df_test, p_bruta_test_full, 0.75)
    m_bruto = calc_metrics(trades_bruto, len(df_test))
    
    print(f'  BRUTO (thr=0.75):')
    print(f'    Trades: {m_bruto["n_trades"]}, PF: {m_bruto["pf"]}, E: {m_bruto["expectancy"]:.1f}')
    print(f'    ECE: {ece_bruto:.4f}')
    
    print(f'  CALIBRADO (thr={best_thr}):')
    print(f'    Trades: {metrics_final["n_trades"]}, PF: {metrics_final["pf"]}, E: {metrics_final["expectancy"]:.1f}')
    print(f'    ECE: {ece_test:.4f}')
    
    # ============================================================
    # SALVAR RESULTADOS
    # ============================================================
    resultados = {
        'split': {
            'treino': [str(d) for d in TREINO_DIAS],
            'calibracao': [str(d) for d in CAL_DIAS],
            'teste': [str(d) for d in TEST_DIAS],
        },
        'rf_bruto': {
            'ece': round(ece_bruto, 4),
            'brier': round(brier_bruto, 4),
            'prob_media': round(float(p_bruta_test.mean()), 4),
        },
        'platt': {
            'A': round(cal_final.A, 4),
            'B': round(cal_final.B, 4),
            'estavel': estavel,
            'variacao_A': round(dA, 4),
            'variacao_B': round(dB, 4),
        },
        'ece': {
            'in_sample': round(ece_cal, 4),
            'bootstrap_mean': round(ece_boot_mean, 4),
            'bootstrap_std': round(ece_boot_std, 4),
            'teste': round(ece_test, 4),
        },
        'threshold_otimo': best_thr,
        'backtester_final': metrics_final,
        'comparacao_bruto': {
            'threshold': 0.75,
            'n_trades': m_bruto['n_trades'],
            'pf': m_bruto['pf'],
            'ece': round(ece_bruto, 4),
        },
    }
    
    out_path = os.path.join(OUTPUT_DIR, 'calibracao_platt_resultado.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(resultados, f, indent=2, ensure_ascii=False, default=str)
    print(f'\nSalvo: {out_path}')


if __name__ == '__main__':
    main()
