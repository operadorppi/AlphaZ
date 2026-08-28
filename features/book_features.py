# -*- coding: utf-8 -*-
"""
features/book_features.py — BookLevelFeatures + OFITracker.

Features de microestrutura do book por profundidade (spread, mid,
microprice, imbalance, OFI, velocidade, HHI).
"""

import numpy as np
from .utils import ewma_update, hhi
from .vpin import VPINTracker  # usado internamente por BookLevelFeatures


class OFITracker:
    """Order Flow Imbalance — Cont-Kukanov-Stoikov, alinhado por preço."""

    def __init__(self, niveis=5, decay=0.92):
        self.niveis = niveis
        self.decay = decay
        self.ofi_total = 0.0
        self.ofi_ewma = 0.0
        self._last_bid = {}
        self._last_ask = {}
        self._inic = False

    def atualizar(self, bid_levels, ask_levels):
        bid_cur = {float(p): int(v) for p, v in bid_levels if p and p > 0}
        ask_cur = {float(p): int(v) for p, v in ask_levels if p and p > 0}

        if not self._inic:
            self._last_bid = bid_cur
            self._last_ask = ask_cur
            self._inic = True
            return

        ofi = 0.0
        for p in set(bid_cur) | set(self._last_bid):
            ofi += bid_cur.get(p, 0) - self._last_bid.get(p, 0)
        for p in set(ask_cur) | set(self._last_ask):
            ofi -= ask_cur.get(p, 0) - self._last_ask.get(p, 0)

        self._last_bid = bid_cur
        self._last_ask = ask_cur
        self.ofi_total = ofi

        if abs(ofi) > 0.1:
            self.ofi_ewma = self.decay * self.ofi_ewma + (1 - self.decay) * ofi
        else:
            self.ofi_ewma *= 0.95

    def get_ofi(self):
        return {
            'ofi_total': round(self.ofi_total, 1),
            'ofi_ewma': round(self.ofi_ewma, 1),
            'ofi_niveis': self.niveis,
        }


class BookLevelFeatures:
    """Features de microestrutura do book por profundidade.
    Estado incremental: snapshot anterior guardado para calcular
    derivadas (velocidade, aceleração) sem recalculação."""

    DEPTHS = [1, 3, 5, 10, 20, 30, 50, 100, 200, 250, 500]

    def __init__(self, decay_vel=0.9):
        self._prev = {}
        self.decay_vel = decay_vel
        self._vel_bid_ewma = 0.0
        self._vel_ask_ewma = 0.0
        self._inic = False
        self._micro_drift_ewma = 0.0
        self._micro_drift_inic = False

    def reset_diario(self):
        """Zera estado incremental do dia anterior (OFI + derivadas)."""
        self._prev = {}
        self._vel_bid_ewma = 0.0
        self._vel_ask_ewma = 0.0
        self._inic = False
        self._micro_drift_ewma = 0.0
        self._micro_drift_inic = False
        if getattr(self, '_ofi_interno', None):
            self._ofi_interno = {}

    def calcular(self, book_snapshot, ativo, ts_ms):
        # v10.5: Vetorização NumPy para processar 500 níveis instantaneamente
        bid_v = np.array(self._extrair_vols(book_snapshot, 'bid'), dtype=np.float32)
        ask_v = np.array(self._extrair_vols(book_snapshot, 'ask'), dtype=np.float32)
        bid_p = np.array(self._extrair_precos(book_snapshot, 'bid'), dtype=np.float32)
        ask_p = np.array(self._extrair_precos(book_snapshot, 'ask'), dtype=np.float32)

        if bid_v.size == 0 or ask_v.size == 0:
            return None

        best_bid, best_ask = bid_p[0], ask_p[0]
        total_bid, total_ask = bid_v.sum(), ask_v.sum()

        if best_bid <= 0 or best_ask <= 0 or total_bid <= 0 or total_ask <= 0:
            return None

        spread = best_ask - best_bid
        mid = (best_ask + best_bid) / 2.0
        denom = bid_v[0] + ask_v[0]
        microprice = (best_bid * ask_v[0] + best_ask * bid_v[0]) / denom if denom > 0 else mid

        # Otimização: cumsum permite calcular todos os níveis em O(N)
        cum_bid = np.cumsum(bid_v)
        cum_ask = np.cumsum(ask_v)
        
        imb_by_depth = {}
        cum_bid_by_depth = {}
        cum_ask_by_depth = {}
        for d in self.DEPTHS:
            # Garante que não estoure o tamanho do array se houver menos níveis
            idx = min(d, bid_v.size, ask_v.size) - 1
            cb, ca = cum_bid[idx], cum_ask[idx]
            total = cb + ca
            key = f'L{d}'
            imb_by_depth[key] = round(float((cb - ca) / total), 4) if total > 0 else 0.0
            cum_bid_by_depth[key] = float(cb)
            cum_ask_by_depth[key] = float(ca)

        # Preço médio ponderado via produto escalar (dot product)
        weighted_bid = np.dot(bid_p, bid_v) / total_bid if total_bid > 0 else best_bid
        weighted_ask = np.dot(ask_p, ask_v) / total_ask if total_ask > 0 else best_ask

        liq_dist_bid = round(mid - weighted_bid, 1)
        liq_dist_ask = round(weighted_ask - mid, 1)

        ofi_raw = book_snapshot.get('ofi')
        if isinstance(ofi_raw, dict):
            ofi = ofi_raw.get('ofi_total', 0)
        elif isinstance(ofi_raw, (int, float)):
            ofi = ofi_raw
        else:
            ofi = self._calcular_ofi(bid_v.tolist(), bid_p.tolist(), ask_v.tolist(), ask_p.tolist(), ativo)

        micro_drift_bps = (microprice - mid) / mid * 10000 if mid > 0 else 0.0
        if not self._micro_drift_inic:
            self._micro_drift_ewma = micro_drift_bps
            self._micro_drift_inic = True
        else:
            self._micro_drift_ewma = ewma_update(self._micro_drift_ewma, micro_drift_bps, 0.25)

        n_min = min(bid_v.size, ask_v.size, 10)
        imb_ponderado = 0.0
        if n_min > 0:
            # Pesos exponenciais para o imbalance ponderado
            pesos = 0.85 ** np.arange(n_min)
            num = np.dot(bid_v[:n_min] - ask_v[:n_min], pesos)
            den = np.dot(bid_v[:n_min] + ask_v[:n_min], pesos)
            imb_ponderado = num / den if den > 0 else 0.0

        slope_bid = 0.0
        slope_ask = 0.0
        if bid_v.size >= 6:
            mid_pt = 3
            bid_near = bid_v[:mid_pt].mean()
            bid_far = bid_v[mid_pt:6].mean()
            denom = bid_near + bid_far
            slope_bid = (bid_near - bid_far) / denom if denom > 0 else 0.0
        if ask_v.size >= 6:
            mid_pt = 3
            ask_near = ask_v[:mid_pt].mean()
            ask_far = ask_v[mid_pt:6].mean()
            denom = ask_near + ask_far
            slope_ask = (ask_near - ask_far) / denom if denom > 0 else 0.0

        vel_bid = 0.0
        vel_ask = 0.0
        vel_imb = {}
        prev = self._prev.get(ativo, {})
        if prev and ts_ms - prev.get('ts', 0) < 5000:
            dt = max(ts_ms - prev['ts'], 1) / 1000.0
            vel_bid = (total_bid - prev.get('total_bid', 0)) / dt
            vel_ask = (total_ask - prev.get('total_ask', 0)) / dt
            for d in self.DEPTHS:
                key = 'L' + str(d)
                d_bid = cum_bid_by_depth[key] - prev.get('cum_bid_' + key, 0)
                d_ask = cum_ask_by_depth[key] - prev.get('cum_ask_' + key, 0)
                vel_imb[key] = round((d_bid - d_ask) / dt, 1)
            if not self._inic:
                self._vel_bid_ewma = vel_bid
                self._vel_ask_ewma = vel_ask
                self._inic = True
            else:
                self._vel_bid_ewma = ewma_update(self._vel_bid_ewma, vel_bid, self.decay_vel)
                self._vel_ask_ewma = ewma_update(self._vel_ask_ewma, vel_ask, self.decay_vel)

        self._prev[ativo] = {
            'ts': ts_ms, 'total_bid': total_bid, 'total_ask': total_ask,
        }
        for k, v in cum_bid_by_depth.items(): self._prev[ativo]['cum_bid_' + k] = v
        for k, v in cum_ask_by_depth.items(): self._prev[ativo]['cum_ask_' + k] = v
        self._prev[ativo]['ofi'] = ofi

        res = {
            'ts_ms': ts_ms,
            'spread': round(spread, 1),
            'mid': round(mid, 1),
            'microprice': round(microprice, 1),
            'microprice_vs_mid': round(microprice - mid, 2),
            'imbalance': imb_by_depth,
            'cum_bid': cum_bid_by_depth,
            'cum_ask': cum_ask_by_depth,
            'hhi_book': round(hhi_book, 4),
            'liq_dist_bid': liq_dist_bid,
            'liq_dist_ask': liq_dist_ask,
            'ofi': round(ofi, 1),
            'micro_drift_bps': round(micro_drift_bps, 2),
            'micro_drift_ewma': round(self._micro_drift_ewma, 2),
            'imb_ponderado': round(imb_ponderado, 4),
            'slope_bid': round(slope_bid, 4),
            'slope_ask': round(slope_ask, 4),
            'vel_bid': round(vel_bid, 1),
            'vel_ask': round(vel_ask, 1),
            'vel_bid_ewma': round(self._vel_bid_ewma, 1),
            'vel_ask_ewma': round(self._vel_ask_ewma, 1),
            'vel_imb': vel_imb,
            'n_bid_levels': len(bid_vols),
            'n_ask_levels': len(ask_vols),
        }

        # Adiciona atalhos planos para todos os imbalances (compatibilidade com flatten_snapshot)
        for k, v in imb_by_depth.items():
            res[f'imb_{k}'] = v
            
        return res

    def _extrair_pares(self, snap, lado):
        key_p = lado + '_preco'
        key_v = lado + '_vol'
        pares = []
        if key_p in snap and isinstance(snap[key_p], list) and \
           key_v in snap and isinstance(snap[key_v], list):
            for p, v in zip(snap[key_p], snap[key_v]):
                if p > 0 and v > 0:
                    pares.append((float(p), int(v)))
            return pares
        if key_p in snap and isinstance(snap[key_p], dict) and \
           key_v in snap and isinstance(snap[key_v], dict):
            n_max = max(len(snap[key_p]), len(snap[key_v]))
            for i in range(n_max):
                p = snap[key_p].get(i, 0)
                v = snap[key_v].get(i, 0)
                if p > 0 and v > 0:
                    pares.append((float(p), int(v)))
            return pares
        return pares

    def _extrair_vols(self, snap, lado):
        return [v for _, v in self._extrair_pares(snap, lado)]

    def _extrair_precos(self, snap, lado):
        return [p for p, _ in self._extrair_pares(snap, lado)]

    def _calcular_ofi(self, bid_vols, bid_precos, ask_vols, ask_precos, ativo):
        if not hasattr(self, '_ofi_interno'):
            self._ofi_interno = {}
        if ativo not in self._ofi_interno:
            self._ofi_interno[ativo] = OFITracker(niveis=5)
        bid_levels = list(zip(bid_precos[:5], bid_vols[:5]))
        ask_levels = list(zip(ask_precos[:5], ask_vols[:5]))
        self._ofi_interno[ativo].atualizar(bid_levels, ask_levels)
        return self._ofi_interno[ativo].ofi_ewma
