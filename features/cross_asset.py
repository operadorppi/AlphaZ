# -*- coding: utf-8 -*-
"""
features/cross_asset.py — CrossAssetEngine + CrossAssetManager.

Detecta liderança temporal entre pares de ativos, correlação rolling,
divergência de fluxo, e resposta entre ativos.

v11.0: CrossAssetManager suporta múltiplos pares (WIN↔IND, DOL↔WDO).
"""

import bisect
from collections import deque, defaultdict


def _tod_ms():
    """Placeholder — importado do módulo onde é definido em produção."""
    import time
    return int(time.time() * 1000) % 86400000


class CrossAssetEngine:
    """Detecta liderança temporal entre dois ativos, correlação rolling,
    divergência de fluxo, e resposta de um ao movimento do outro."""

    def __init__(self, janela_corr=60, max_lag_ms=2000,
                 ativo_principal=None, ativo_contexto=None):
        self.janela_corr = janela_corr
        self.max_lag_ms = max_lag_ms
        self.ativo_principal = ativo_principal
        self.ativo_contexto = ativo_contexto
        self.hist_win = deque(maxlen=1000)
        self.hist_wdo = deque(maxlen=1000)
        self._ultimo_wdo_preco = 0.0
        self._ultimo_wdo_ts = 0
        self._ultimo_win_preco = 0.0
        self._ultimo_win_ts = 0
        self._win_precos = []
        self._win_precos_ts = []

    def registrar(self, ativo, ts_ms, preco, aggr_imb, imb_book=0.0):
        hist = self.hist_win if ativo == self.ativo_principal else self.hist_wdo
        hist.append((ts_ms, preco, aggr_imb, imb_book))
        if ativo == self.ativo_principal:
            self._ultimo_win_preco = preco
            self._ultimo_win_ts = ts_ms
            self._win_precos.append(preco)
            self._win_precos_ts.append(ts_ms)
            if len(self._win_precos) > 1000:
                self._win_precos.pop(0)
                self._win_precos_ts.pop(0)
        else:
            self._ultimo_wdo_preco = preco
            self._ultimo_wdo_ts = ts_ms

    def calcular(self):
        if not self.hist_win or not self.hist_wdo:
            return {
                'lag_ms': 0, 'corr_aggr': 0.0, 'corr_imb_book': 0.0,
                'divergencia': 0.0, 'wdo_leading': 0.0,
                'resposta_win': 0.0, 'wdo_delta': 0.0,
            }
        lag_ms = self._calcular_lag()
        corr_aggr = self._correlacao_rolling('aggr')
        corr_imb = self._correlacao_rolling('imb')
        divergencia = self._calcular_divergencia()
        wdo_leading = self._wdo_leading_score()
        resposta = self._resposta_ao_wdo()
        wdo_delta = 0.0
        if len(self.hist_wdo) >= 2:
            t1, p1 = self.hist_wdo[-2][0], self.hist_wdo[-2][1]
            t2, p2 = self.hist_wdo[-1][0], self.hist_wdo[-1][1]
            dt = (t2 - t1) / 1000.0
            if dt > 0:
                wdo_delta = (p2 - p1) / dt
        return {
            'lag_ms': lag_ms,
            'corr_aggr': round(corr_aggr, 3),
            'corr_imb_book': round(corr_imb, 3),
            'divergencia': round(divergencia, 3),
            'wdo_leading': round(wdo_leading, 3),
            'resposta_win': round(resposta, 3),
            'wdo_delta': round(wdo_delta, 1),
        }

    def _calcular_lag(self):
        agora_ms = _tod_ms()
        cutoff = agora_ms - 5000
        wdo_moves = []
        for i in range(1, len(self.hist_wdo)):
            t = self.hist_wdo[i][0]
            if t < cutoff:
                continue
            delta = self.hist_wdo[i][1] - self.hist_wdo[i-1][1]
            if abs(delta) >= 1:
                wdo_moves.append((t, delta))
        if not wdo_moves:
            return 0
        lags = []
        for wdo_t, wdo_delta in wdo_moves[-5:]:
            idx = bisect.bisect_right(self._win_precos_ts, wdo_t)
            if idx < len(self._win_precos_ts):
                win_t = self._win_precos_ts[idx]
                win_p = self._win_precos[idx]
                win_prev = self._win_precos[idx - 1] if idx > 0 else 0.0
                win_delta = win_p - win_prev
                if abs(win_delta) >= 1 and (win_delta * wdo_delta > 0):
                    lag = win_t - wdo_t
                    if lag <= self.max_lag_ms:
                        lags.append(lag)
        return int(sum(lags) / len(lags)) if lags else 0

    def _get_prev_price(self, hist, ts_ms):
        prev = 0.0
        # Snapshot: o deque pode estar sendo mutado pela thread de trading
        # enquanto o dashboard itera (race condition → RuntimeError)
        for t, p, _, _ in list(hist):
            if t >= ts_ms:
                return prev
            prev = p
        return prev

    def _correlacao_rolling(self, campo):
        agora_ms = _tod_ms()
        cutoff = agora_ms - self.janela_corr * 1000
        bins_win = {}
        for t, p, aggr, imb in list(self.hist_win):
            if t < cutoff:
                continue
            b = t // 1000
            bins_win[b] = aggr if campo == 'aggr' else imb
        bins_wdo = {}
        for t, p, aggr, imb in list(self.hist_wdo):
            if t < cutoff:
                continue
            b = t // 1000
            bins_wdo[b] = aggr if campo == 'aggr' else imb
        common = sorted(set(bins_win) & set(bins_wdo))
        if len(common) < 10:
            return 0.0
        x = [bins_win[b] for b in common]
        y = [bins_wdo[b] for b in common]
        n = len(x)
        mx = sum(x) / n
        my = sum(y) / n
        cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / n
        sx = (sum((xi - mx)**2 for xi in x) / n) ** 0.5
        sy = (sum((yi - my)**2 for yi in y) / n) ** 0.5
        if sx > 0 and sy > 0:
            return cov / (sx * sy)
        return 0.0

    def _calcular_divergencia(self):
        agora_ms = _tod_ms()
        cutoff = agora_ms - 5000
        wdo_range = 0.0
        if self.hist_wdo:
            recentes = [p for t, p, _, _ in list(self.hist_wdo) if t >= cutoff]
            if len(recentes) >= 2:
                wdo_range = max(recentes) - min(recentes)
        win_range = 0.0
        if self.hist_win:
            recentes = [p for t, p, _, _ in list(self.hist_win) if t >= cutoff]
            if len(recentes) >= 2:
                win_range = max(recentes) - min(recentes)
        if wdo_range > 5 and win_range < 3:
            return -1.0
        elif win_range > 5 and wdo_range < 3:
            return 1.0
        elif wdo_range > 5 and win_range > 5:
            wdo_dir = self.hist_wdo[-1][1] - self.hist_wdo[max(0, len(self.hist_wdo)-10)][1]
            win_dir = self.hist_win[-1][1] - self.hist_win[max(0, len(self.hist_win)-10)][1]
            if wdo_dir * win_dir > 0:
                return 0.0
            else:
                return -0.5
        return 0.0

    def _wdo_leading_score(self):
        agora_ms = _tod_ms()
        cutoff = agora_ms - 5000
        wdo_move_t = 0
        wdo_move_delta = 0
        for i in range(len(self.hist_wdo)-1, 0, -1):
            t = self.hist_wdo[i][0]
            if t < cutoff:
                break
            delta = self.hist_wdo[i][1] - self.hist_wdo[i-1][1]
            if abs(delta) >= 2:
                wdo_move_t = t
                wdo_move_delta = delta
                break
        if wdo_move_t == 0:
            return 0.0
        for t, p, _, _ in list(self.hist_win):
            if t > wdo_move_t:
                win_prev = self._get_prev_price(self.hist_win, t)
                win_delta = p - win_prev
                if abs(win_delta) >= 1:
                    lag = t - wdo_move_t
                    if lag < 2000 and (win_delta * wdo_move_delta > 0):
                        return 1.0 - (lag / 2000.0)
                    return 0.0
        return -0.3

    def _resposta_ao_wdo(self):
        if len(self.hist_wdo) < 2 or len(self.hist_win) < 2:
            return 0.0
        wdo_delta = self.hist_wdo[-1][1] - self.hist_wdo[-2][1]
        if abs(wdo_delta) < 1:
            return 0.0
        agora_ms = _tod_ms()
        win_recentes = [(t, p) for t, p, _, _ in list(self.hist_win) if t >= agora_ms - 2000]
        if len(win_recentes) < 2:
            return 0.0
        win_delta = win_recentes[-1][1] - win_recentes[0][1]
        if wdo_delta > 0:
            return min(1.0, win_delta / max(abs(wdo_delta), 1))
        else:
            return min(1.0, -win_delta / max(abs(wdo_delta), 1))


class CrossAssetManager:
    """Gerencia múltiplos pares de CrossAssetEngine.

    Exemplo:
        pairs = [["WINV26", "INDV26"], ["WDOV26", "DOLV26"]]
        manager = CrossAssetManager(pairs)

        # Ao receber um trade:
        manager.registrar("WINV26", ts_ms, preco, aggr_imb)

        # Ao calcular features:
        dados = manager.calcular()
        # dados = {
        #     'WINV26_INDV26': {...},
        #     'WDOU26_DOLU26': {...},
        # }
    """

    def __init__(self, pairs=None, janela_corr=60, max_lag_ms=2000):
        self.pairs = pairs or []
        self.engines = {}
        self._asset_to_pairs = defaultdict(list)

        for pair in self.pairs:
            if len(pair) != 2:
                continue
            principal, contexto = pair[0], pair[1]
            chave = f"{principal}_{contexto}"
            self.engines[chave] = CrossAssetEngine(
                ativo_principal=principal,
                ativo_contexto=contexto,
                janela_corr=janela_corr,
                max_lag_ms=max_lag_ms,
            )
            self._asset_to_pairs[principal].append(chave)
            self._asset_to_pairs[contexto].append(chave)

    def registrar(self, ativo, ts_ms, preco, aggr_imb, imb_book=0.0):
        """Registra um trade em todos os pares que contêm este ativo."""
        for chave in self._asset_to_pairs.get(ativo, []):
            self.engines[chave].registrar(ativo, ts_ms, preco, aggr_imb, imb_book)

    def calcular(self):
        """Calcula features para todos os pares."""
        result = {}
        for chave, engine in self.engines.items():
            result[chave] = engine.calcular()
        return result

    def calcular_para_ativo(self, ativo):
        """Calcula features cross-asset para um ativo específico."""
        for chave in self._asset_to_pairs.get(ativo, []):
            engine = self.engines[chave]
            if engine.ativo_principal == ativo:
                return engine.calcular()
        for chave in self._asset_to_pairs.get(ativo, []):
            return self.engines[chave].calcular()
        return {}

    def get_pairs(self):
        """Retorna a lista de pares configurados."""
        return list(self.engines.keys())

    def get_summary(self):
        """Retorna resumo de todos os pares para monitoramento."""
        summary = {}
        for chave, engine in self.engines.items():
            dados = engine.calcular()
            summary[chave] = {
                'principal': engine.ativo_principal,
                'contexto': engine.ativo_contexto,
                'hist_win': len(engine.hist_win),
                'hist_wdo': len(engine.hist_wdo),
                'lag_ms': dados.get('lag_ms', 0),
                'corr_aggr': dados.get('corr_aggr', 0.0),
            }
        return summary
