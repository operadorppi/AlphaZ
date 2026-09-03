# -*- coding: utf-8 -*-
"""
features/cross_asset.py — CrossAssetEngine + CrossAssetManager.

Detecta liderança temporal entre pares de ativos, correlação rolling,
divergência de fluxo, e resposta entre ativos.

v11.0: CrossAssetManager suporta múltiplos pares (WIN↔IND, DOL↔WDO).
P0-A23 (v15.17): NENHUMA métrica usa wall clock. `calcular(ts_ms)` recebe o
timestamp do evento; todas as janelas/cutoffs são relativas a ele. Quando
`ts_ms` é omitido (ex.: leitura do dashboard), o ref é o ÚLTIMO ts registrado
(as-of). Todas as leituras de histórico são fatiadas por `ts <= ref` — um
evento registrado depois do ref nunca entra em nenhuma métrica.
P0-A24 (v15.18): SOMENTE ativo_principal/ativo_contexto são aceitos; ativo
fora do par é rejeitado e contado, nunca vira contexto.
P1-A25 (v15.19): SEMÂNTICA FORMAL da correlação rolling. Cada lado é
amostrado em buckets de `bucket_ms` (default 100ms = grid do master clock,
mesmo contrato temporal das demais features — A20/A21) e cada bucket é
representado por UM valor segundo o agregador explícito:

  - 'mean' (DEFAULT): média dos fluxos do bucket. Para aggr (±1 por trade),
    média = saldo direcional médio do bucket; preserva a dinâmica
    intrasegundo (100 eventos no segundo não viram 'só o último').
  - 'sum': soma dos fluxos do bucket (fluxo líquido).
  - 'last': último valor do bucket (comportamento antigo implícito —
    perde a dinâmica intrasegundo; mantido só p/ compatibilidade).

A correlação de Pearson usa os representantes dos buckets COMUNS (>= 10)
dentro da janela `janela_corr` (s). Buckets sem evento em um lado são gaps
(não zeros) — não fabricam amostra.
"""

import bisect
import logging
from collections import deque, defaultdict

log = logging.getLogger(__name__)

_ZEROS = {
    'lag_ms': 0, 'corr_aggr': 0.0, 'corr_imb_book': 0.0,
    'divergencia': 0.0, 'wdo_leading': 0.0,
    'resposta_win': 0.0, 'wdo_delta': 0.0,
}


class CrossAssetEngine:
    """Detecta liderança temporal entre dois ativos, correlação rolling,
    divergência de fluxo, e resposta de um ao movimento do outro."""

    def __init__(self, ativo_principal=None, ativo_contexto=None,
                 janela_corr=60, max_lag_ms=2000,
                 bucket_ms=100, agregador='mean'):
        self.janela_corr = janela_corr
        self.max_lag_ms = max_lag_ms
        # P1-A25 (v15.19): resolução do bucket (grid do master clock) e
        # agregador explícito do representante de cada bucket — ver docstring
        # do módulo. Nunca implícito ('último valor do segundo').
        if agregador not in ('mean', 'sum', 'last'):
            raise ValueError(f"agregador inválido: {agregador!r} "
                             "(use 'mean', 'sum' ou 'last')")
        self.bucket_ms = int(bucket_ms)
        self.agregador = agregador
        self.ativo_principal = ativo_principal
        self.ativo_contexto = ativo_contexto
        # P0-A24 (v15.18): ativos fora do par são REJEITADOS, nunca viram
        # contexto. Contador por ativo + total para auditoria (nada silencioso).
        self._rejeitados = {}
        self._avisou_rejeicao = set()
        self.hist_win = deque(maxlen=1000)
        self.hist_wdo = deque(maxlen=1000)
        self._ultimo_wdo_preco = 0.0
        self._ultimo_wdo_ts = 0
        self._ultimo_win_preco = 0.0
        self._ultimo_win_ts = 0
        self._win_precos = []
        self._win_precos_ts = []
        self._ref_ts = 0

    def registrar(self, ativo, ts_ms, preco, aggr_imb, imb_book=0.0):
        """Registra um trade no lado do par (principal ou contexto).

        P0-A24 (v15.18): SOMENTE ativo_principal e ativo_contexto são
        aceitos. Qualquer outro instrumento é REJEITADO e contado — nunca
        vira silenciosamente o contexto (a contaminação antiga: qualquer
        símbolo ≠ principal caía no hist_wdo). Retorna True se aceito,
        False se rejeitado.
        """
        if ativo == self.ativo_principal:
            hist = self.hist_win
            eh_principal = True
        elif ativo == self.ativo_contexto:
            hist = self.hist_wdo
            eh_principal = False
        else:
            self._rejeitados[ativo] = self._rejeitados.get(ativo, 0) + 1
            if ativo not in self._avisou_rejeicao:
                self._avisou_rejeicao.add(ativo)
                log.warning(
                    '[CROSS-ASSET] Rejeitando ativo fora do par %s×%s: %s '
                    '(contador=%d) — eventos NÃO entram no contexto',
                    self.ativo_principal, self.ativo_contexto, ativo,
                    self._rejeitados[ativo])
            return False
        hist.append((ts_ms, preco, aggr_imb, imb_book))
        if eh_principal:
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
        return True

    @property
    def total_rejeitados(self):
        """Total de eventos rejeitados (fora do par) — auditoria P0-A24."""
        return sum(self._rejeitados.values())

    def _ref_ts_ms(self, ts_ms=None):
        """Timestamp de referencia do calculo.

        P0-A23: NUNCA wall clock. ts_ms do evento quando fornecido; senao o
        ULTIMO ts registrado no par (as-of) — replay deterministico: as
        janelas sao relativas ao evento, nao as 'que horas o computador
        esta rodando'.
        """
        if ts_ms:
            return int(ts_ms)
        ref = 0
        if self.hist_win:
            ref = max(ref, self.hist_win[-1][0])
        if self.hist_wdo:
            ref = max(ref, self.hist_wdo[-1][0])
        return ref or 0

    def _asof(self, hist):
        """Fatia de eventos com ts <= ref (P0-A23): nada do 'futuro' do ref
        entra em metrica alguma. `hist` pode ser deque ou lista."""
        ref = self._ref_ts
        if ref <= 0:
            return list(hist)
        return [ev for ev in hist if ev[0] <= ref]

    def calcular(self, ts_ms=None):
        if not self.hist_win or not self.hist_wdo:
            return dict(_ZEROS)
        # P0-A23: ref do evento (replay deterministico); usado por TODAS as
        # janelas internas (cutoffs relativos ao evento, nao ao relogio).
        self._ref_ts = self._ref_ts_ms(ts_ms)
        if not self._ref_ts:
            return dict(_ZEROS)
        win = self._asof(self.hist_win)
        wdo = self._asof(self.hist_wdo)
        if not win or not wdo:
            return dict(_ZEROS)
        lag_ms = self._calcular_lag(win, wdo)
        corr_aggr = self._correlacao_rolling('aggr', win, wdo)
        corr_imb = self._correlacao_rolling('imb', win, wdo)
        divergencia = self._calcular_divergencia(win, wdo)
        wdo_leading = self._wdo_leading_score(win, wdo)
        resposta = self._resposta_ao_wdo(win, wdo)
        wdo_delta = 0.0
        if len(wdo) >= 2:
            t1, p1 = wdo[-2][0], wdo[-2][1]
            t2, p2 = wdo[-1][0], wdo[-1][1]
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

    def _calcular_lag(self, win, wdo):
        cutoff = self._ref_ts - 5000
        wdo_moves = []
        for i in range(1, len(wdo)):
            t = wdo[i][0]
            if t < cutoff:
                continue
            delta = wdo[i][1] - wdo[i - 1][1]
            if abs(delta) >= 1:
                wdo_moves.append((t, delta))
        if not wdo_moves:
            return 0
        wp_ts = [ev[0] for ev in win]
        wp_p = [ev[1] for ev in win]
        lags = []
        for wdo_t, wdo_delta in wdo_moves[-5:]:
            idx = bisect.bisect_right(wp_ts, wdo_t)
            if idx < len(wp_ts):
                win_t = wp_ts[idx]
                win_p = wp_p[idx]
                win_prev = wp_p[idx - 1] if idx > 0 else 0.0
                win_delta = win_p - win_prev
                if abs(win_delta) >= 1 and (win_delta * wdo_delta > 0):
                    lag = win_t - wdo_t
                    if lag <= self.max_lag_ms:
                        lags.append(lag)
        return int(sum(lags) / len(lags)) if lags else 0

    def _get_prev_price(self, hist, ts_ms):
        prev = 0.0
        # hist é uma lista as-of (cópia estável): sem race com a thread viva.
        for t, p, _, _ in hist:
            if t >= ts_ms:
                return prev
            prev = p
        return prev

    def _bucketizar(self, hist, campo, cutoff):
        """Amostra `hist` em buckets de `bucket_ms` dentro da janela.

        P1-A25 (v15.19): cada bucket (b = t // bucket_ms) é representado por
        UM valor segundo `self.agregador` ('mean' | 'sum' | 'last'). Buckets
        sem evento não existem (gap, não zero). `campo`: 'aggr' ou 'imb'.
        """
        vals_por_bucket = {}
        for t, p, aggr, imb in hist:
            if t < cutoff or t > self._ref_ts:
                continue
            b = t // self.bucket_ms
            v = aggr if campo == 'aggr' else imb
            vals_por_bucket.setdefault(b, []).append(v)
        if self.agregador == 'last':
            return {b: vs[-1] for b, vs in vals_por_bucket.items()}
        if self.agregador == 'sum':
            return {b: sum(vs) for b, vs in vals_por_bucket.items()}
        return {b: sum(vs) / len(vs) for b, vs in vals_por_bucket.items()}

    def _correlacao_rolling(self, campo, win, wdo):
        cutoff = self._ref_ts - self.janela_corr * 1000
        bins_win = self._bucketizar(win, campo, cutoff)
        bins_wdo = self._bucketizar(wdo, campo, cutoff)
        common = sorted(set(bins_win) & set(bins_wdo))
        if len(common) < 10:
            return 0.0
        x = [bins_win[b] for b in common]
        y = [bins_wdo[b] for b in common]
        n = len(x)
        mx = sum(x) / n
        my = sum(y) / n
        cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / n
        sx = (sum((xi - mx) ** 2 for xi in x) / n) ** 0.5
        sy = (sum((yi - my) ** 2 for yi in y) / n) ** 0.5
        if sx > 0 and sy > 0:
            return cov / (sx * sy)
        return 0.0

    def _calcular_divergencia(self, win, wdo):
        cutoff = self._ref_ts - 5000
        wdo_range = 0.0
        if wdo:
            recentes = [p for t, p, _, _ in wdo if t >= cutoff]
            if len(recentes) >= 2:
                wdo_range = max(recentes) - min(recentes)
        win_range = 0.0
        if win:
            recentes = [p for t, p, _, _ in win if t >= cutoff]
            if len(recentes) >= 2:
                win_range = max(recentes) - min(recentes)
        if wdo_range > 5 and win_range < 3:
            return -1.0
        elif win_range > 5 and wdo_range < 3:
            return 1.0
        elif wdo_range > 5 and win_range > 5:
            wdo_dir = wdo[-1][1] - wdo[max(0, len(wdo) - 10)][1]
            win_dir = win[-1][1] - win[max(0, len(win) - 10)][1]
            if wdo_dir * win_dir > 0:
                return 0.0
            else:
                return -0.5
        return 0.0

    def _wdo_leading_score(self, win, wdo):
        cutoff = self._ref_ts - 5000
        wdo_move_t = 0
        wdo_move_delta = 0
        for i in range(len(wdo) - 1, 0, -1):
            t = wdo[i][0]
            if t < cutoff:
                break
            delta = wdo[i][1] - wdo[i - 1][1]
            if abs(delta) >= 2:
                wdo_move_t = t
                wdo_move_delta = delta
                break
        if wdo_move_t == 0:
            return 0.0
        for t, p, _, _ in win:
            if t > wdo_move_t:
                win_prev = self._get_prev_price(win, t)
                win_delta = p - win_prev
                if abs(win_delta) >= 1:
                    lag = t - wdo_move_t
                    if lag < 2000 and (win_delta * wdo_move_delta > 0):
                        return 1.0 - (lag / 2000.0)
                    return 0.0
        return -0.3

    def _resposta_ao_wdo(self, win, wdo):
        if len(wdo) < 2 or len(win) < 2:
            return 0.0
        wdo_delta = wdo[-1][1] - wdo[-2][1]
        if abs(wdo_delta) < 1:
            return 0.0
        win_recentes = [(t, p) for t, p, _, _ in win if t >= self._ref_ts - 2000]
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

    def __init__(self, pairs=None, janela_corr=60, max_lag_ms=2000,
                 bucket_ms=100, agregador='mean'):
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
                bucket_ms=bucket_ms,
                agregador=agregador,
            )
            self._asset_to_pairs[principal].append(chave)
            self._asset_to_pairs[contexto].append(chave)

    def registrar(self, ativo, ts_ms, preco, aggr_imb, imb_book=0.0):
        """Registra um trade em todos os pares que contêm este ativo."""
        for chave in self._asset_to_pairs.get(ativo, []):
            self.engines[chave].registrar(ativo, ts_ms, preco, aggr_imb, imb_book)

    def calcular(self, ts_ms=None):
        """Calcula features para todos os pares (P0-A23: ts_ms do evento)."""
        result = {}
        for chave, engine in self.engines.items():
            result[chave] = engine.calcular(ts_ms)
        return result

    def calcular_para_ativo(self, ativo, ts_ms=None):
        """Calcula features cross-asset para um ativo (P0-A23: ts_ms do
        evento quando disponivel; senao as-of do ultimo ts registrado)."""
        for chave in self._asset_to_pairs.get(ativo, []):
            engine = self.engines[chave]
            if engine.ativo_principal == ativo:
                return engine.calcular(ts_ms)
        for chave in self._asset_to_pairs.get(ativo, []):
            return self.engines[chave].calcular(ts_ms)
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
