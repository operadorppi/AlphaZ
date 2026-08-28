# v9.36: PrecoContextTracker - contexto de preco causal ao vivo
_TZ_OFF = 3 * 3600 * 1000
_DIA_MS = 86400000
_VA = 0.005
_VE = 1e-9
_RE = 1e-9

def _sctx(v):
    if v is None: return None
    try:
        import math; f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except: return None

def _sdc(n, d):
    if n is None or d is None: return None
    try:
        n, d = float(n), float(d)
        if abs(d) <= _VE: return 0.0
        r = n / d; import math
        return None if (math.isnan(r) or math.isinf(r)) else r
    except: return None

class PrecoContextTracker:
    def __init__(self, ativo, ref_d1=None):
        self.ativo = ativo
        self.abertura = self.maxima = self.minima = self.fechamento = None
        self._d1 = dict(abertura=None, fechamento=None, ajuste=None, maxima=None, minima=None)
        if ref_d1: self._d1.update(ref_d1)
        self._vol = 0.0
        self._ultimo_preco = self._ultimo_dia = None
        self._dbuf = []; self._dhist = None; self._mxp = self._mnp = None

    def set_d1(self, d): self._d1.update(d)

    def update(self, ts_ms, preco, qtd=1):
        if preco is None or preco <= 0: return
        preco = float(preco)
        dia = (int(ts_ms) - _TZ_OFF) // _DIA_MS
        if self._ultimo_dia is not None and dia != self._ultimo_dia: self._reset()
        self._ultimo_dia = dia
        if self.abertura is None: self.abertura = preco
        if self.maxima is None or preco > self.maxima: self.maxima = preco
        if self.minima is None or preco < self.minima: self.minima = preco
        self.fechamento = preco
        if self._ultimo_preco and self._ultimo_preco > 0:
            self._vol = _VA * abs(preco - self._ultimo_preco) + (1 - _VA) * self._vol
        self._ultimo_preco = preco

    def _reset(self):
        if self.abertura is not None:
            self._d1 = dict(abertura=self.abertura, fechamento=self.fechamento,
                           ajuste=self.fechamento, maxima=self.maxima, minima=self.minima)
        self.abertura = self.maxima = self.minima = self.fechamento = None
        self._vol = 0.0; self._ultimo_preco = None
        self._dbuf.clear(); self._dhist = None; self._mxp = self._mnp = None

    def snapshot(self):
        p = self._ultimo_preco; vol = max(self._vol, _VE)
        d1 = self._d1; mx = self.maxima; mn = self.minima; ab = self.abertura
        s = dict(abertura_dia=ab, maxima_dia=mx, minima_dia=mn, fechamento_dia=p,
                 abertura_anterior=d1['abertura'], fechamento_anterior=d1['fechamento'],
                 ajuste_anterior=d1['ajuste'], maxima_anterior=d1['maxima'],
                 minima_anterior=d1['minima'])
        s['faixa_anterior'] = (d1['maxima'] - d1['minima']) if d1['maxima'] is not None and d1['minima'] is not None else None
        if p is None or p <= 0: return s
        s['_vol_pts'] = round(self._vol, 4)
        fa, aa, mxa, mna = d1['fechamento'], d1['ajuste'], d1['maxima'], d1['minima']
        for k, v in [('dist_fechamento_anterior_pts', (p-fa) if fa is not None else None),
                      ('dist_ajuste_pts', (p-aa) if aa is not None else None),
                      ('dist_abertura_pts', (p-ab) if ab is not None else None),
                      ('dist_maxima_dia_pts', (p-mx) if mx is not None else None),
                      ('dist_minima_dia_pts', (p-mn) if mn is not None else None),
                      ('dist_maxima_anterior_pts', (p-mxa) if mxa is not None else None),
                      ('dist_minima_anterior_pts', (p-mna) if mna is not None else None)]:
            s[k] = _sctx(v) if v is not None else None
        for k, v in [('dist_fechamento_anterior_norm', (p-fa, vol) if fa is not None else None),
                      ('dist_ajuste_norm', (p-aa, vol) if aa is not None else None),
                      ('dist_abertura_norm', (p-ab, vol) if ab is not None else None),
                      ('dist_maxima_dia_norm', (p-mx, vol) if mx is not None else None),
                      ('dist_minima_dia_norm', (p-mn, vol) if mn is not None else None),
                      ('dist_maxima_anterior_norm', (p-mxa, vol) if mxa is not None else None),
                      ('dist_minima_anterior_norm', (p-mna, vol) if mna is not None else None)]:
            s[k] = _sdc(*v) if v is not None else None
        rdm = (mx - mn) if mx is not None and mn is not None else None
        s['posicao_range_dia'] = (p - mn) / rdm if rdm and rdm > _RE else None
        rda = (mxa - mna) if mxa is not None and mna is not None else None
        s['posicao_range_anterior'] = (p - mna) / rda if rda and rda > _RE else None
        for k, v in [('gap_abertura_fechamento_anterior', (ab-fa, vol) if ab is not None and fa is not None else None),
                      ('gap_abertura_ajuste', (ab-aa, vol) if ab is not None and aa is not None else None)]:
            s[k] = _sdc(*v) if v else None
        s['gap_abertura_fechamento_anterior_pts'] = _sctx(ab-fa) if ab and fa else None
        s['gap_abertura_ajuste_pts'] = _sctx(ab-aa) if ab and aa else None
        if aa is not None:
            s['acima_ajuste'] = float(p > aa); s['abaixo_ajuste'] = float(p < aa)
            s['retorno_em_relacao_ao_ajuste'] = _sdc(p - aa, aa)
        else: s['acima_ajuste'] = s['abaixo_ajuste'] = s['retorno_em_relacao_ao_ajuste'] = None
        if ab is not None:
            s['acima_abertura'] = float(p > ab); s['abaixo_abertura'] = float(p < ab)
            da = abs(p - ab)
            s['dist_abertura_reduzindo'] = float(da <= self._dhist) if self._dhist is not None and self._dhist > 0 else None
            self._dbuf.append(da)
            if len(self._dbuf) > 600: self._dhist = self._dbuf.pop(0)
            else: self._dhist = None
        else: s['acima_abertura'] = s['abaixo_abertura'] = s['dist_abertura_reduzindo'] = None
        K = 1.0
        if mx is not None:
            s['perto_maxima'] = float((mx-p) <= K*vol)
            s['rompimento_maxima'] = float(p >= mx-1e-9 and (self._mxp is None or mx > self._mxp))
            s['rejeicao_maxima'] = float((mx-p) > K*vol); self._mxp = mx
        else: s['perto_maxima'] = s['rompimento_maxima'] = s['rejeicao_maxima'] = None
        if mn is not None:
            s['perto_minima'] = float((p-mn) <= K*vol)
            s['rompimento_minima'] = float(p <= mn+1e-9 and (self._mnp is None or mn < self._mnp))
            s['rejeicao_minima'] = float((p-mn) > K*vol); self._mnp = mn
        else: s['perto_minima'] = s['rompimento_minima'] = s['rejeicao_minima'] = None
        s['range_anterior_pts'] = s['faixa_anterior']
        s['posicao_vs_range_anterior'] = s['posicao_range_anterior']
        s['dist_maxima_anterior'] = s['dist_maxima_anterior_pts']
        s['dist_minima_anterior'] = s['dist_minima_anterior_pts']
        s['preco_acima_maxima_anterior'] = float(p > mxa) if mxa is not None else None
        s['rompimento_maxima_anterior'] = float(p > mxa) if mxa is not None else None
        s['preco_abaixo_minima_anterior'] = float(p < mna) if mna is not None else None
        s['rompimento_minima_anterior'] = float(p < mna) if mna is not None else None
        return s
