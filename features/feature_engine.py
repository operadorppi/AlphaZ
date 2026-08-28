# -*- coding: utf-8 -*-
import math
import numpy as np
from collections import defaultdict
from features import fase_sessao, dias_ate_vencimento

class FeatureEngine:
    """Responsável pelo cálculo determinístico de features de microestrutura (Camada FEATURES)."""
    
    def __init__(self, market_state, config=None):
        self.state = market_state
        self.config = config or {}

    def processar_lote(self, ativo, negs, seg):
        """Transforma negócios brutos em um dicionário de features por segundo."""
        if not negs:
            return None

        # Deduplicação de eventos (v10.2)
        vistos = set()
        negs_unicos = []
        for n in negs:
            sig = (n['preco'], n['qtd'], n['agressor'], n.get('compradora'), n.get('vendedora'))
            if sig not in vistos:
                vistos.add(sig)
                negs_unicos.append(n)
        
        negs = negs_unicos
        # Vetorização NumPy das métricas de agressão e preço (v10.5)
        v_arr = np.array([x['qtd'] for x in negs], dtype=np.int32)
        p_arr = np.array([x['preco'] for x in negs if x['preco'] > 0], dtype=np.float32)
        a_arr = np.array([1 if x['agressor'] == 'Comprador' else (-1 if x['agressor'] == 'Vendedor' else 0) for x in negs], dtype=np.int8)

        vc = int(v_arr[a_arr == 1].sum())
        vv = int(v_arr[a_arr == -1].sum())
        vt = vc + vv
        n = len(negs)
        aggr = float((vc - vv) / vt) if vt > 0 else 0.0
        
        dp = float(p_arr[-1] - p_arr[0]) if p_arr.size >= 2 else 0.0
        eff = abs(dp) / vt if vt > 0 else 0.0
        # Persistência do fluxo
        fp = float(np.count_nonzero(a_arr[1:] == a_arr[:-1]) / (n - 1)) if n > 1 else 0.0

        vc2 = defaultdict(int)
        for nc in negs:
            for lado in ('compradora', 'vendedora'):
                c = nc[lado]
                if c and c not in ('None', ''):
                    vc2[c] += nc['qtd']
        tc = sum(vc2.values())
        # HHI via NumPy
        shares = np.array(list(vc2.values()), dtype=np.float32)
        hhi = float(((shares / tc)**2).sum()) if tc > 0 else 0.0

        f = {
            'time_ms': seg * 1000, 'ativo': ativo, 'n': n, 'vol_total': vt,
            'vol_compr': vc, 'vol_vend': vv, 'aggr_imb': aggr,
            'preco_ini': p_arr[0] if p_arr.size > 0 else 0, 'preco_fim': p_arr[-1] if p_arr.size > 0 else 0,
            'delta_preco': dp, 'price_eff': eff, 'fluxo_persist': fp, 'hhi': hhi,
            'top_corretoras': sorted(vc2.items(), key=lambda x: -x[1])[:6],
            'avg_trade_size': round(vt / n, 1) if n > 0 else 0,
            'max_trade_size': max((x['qtd'] for x in negs), default=0),
            'trades_per_sec': n,
        }

        # Aceleração
        hist_ant = self.state.historico.get(ativo, [])
        if len(hist_ant) >= 3:
            aggr_hist = np.array([h['aggr_imb'] for h in hist_ant[-6:]], dtype=np.float32)
            rec = aggr_hist[-3:].mean()
            ant3 = aggr_hist[:-3].mean() if aggr_hist.size >= 6 else aggr_hist[:min(3, aggr_hist.size)].mean()
            f['aceleracao'] = float(rec - ant3)
        else:
            f['aceleracao'] = 0.0

        # CVD e Divergência (v10.6)
        st2 = self.state.stats.get(ativo)
        cvd = (st2['vc'] - st2['vv']) if st2 else 0
        f['cvd_total'] = cvd
        f['cvd_div'] = 0
        if len(hist_ant) >= 10:
            p_delta = f['preco_fim'] - hist_ant[-10]['preco_fim']
            c_delta = f['cvd_total'] - hist_ant[-10]['cvd_total']
            if p_delta > 0 and c_delta < 0: f['cvd_div'] = -1
            elif p_delta < 0 and c_delta > 0: f['cvd_div'] = 1

        # Volatilidade adaptativa
        prev_p = self.state._ultimo_preco_fim.get(ativo, 0.0)
        preco_f = f['preco_fim']
        if prev_p > 0 and preco_f > 0:
            ret = preco_f / prev_p - 1.0
            self.state._ewma_ret2[ativo] = 0.9 * self.state._ewma_ret2.get(ativo, 0.0) + 0.1 * ret * ret
        self.state._ultimo_preco_fim[ativo] = preco_f
        f['realized_vol_bps'] = round(math.sqrt(self.state._ewma_ret2.get(ativo, 0.0)) * 10000, 2)
        
        hp = np.array([h['preco_fim'] for h in hist_ant[-60:] if h.get('preco_fim', 0) > 0], dtype=np.float32)
        if hp.size >= 2:
            p_max, p_min = hp.max(), hp.min()
            mid_p = (p_max + p_min) / 2
            f['range_vol_bps'] = round(float((p_max - p_min) / mid_p * 10000), 2) if mid_p > 0 else 0.0
        else:
            f['range_vol_bps'] = 0.0
        
        f['fase_sessao'] = fase_sessao(seg * 1000)
        f['dias_ate_venc'] = dias_ate_vencimento(ativo) or 0

        # Absorção
        if vt > 10 and abs(dp) > 0: f['absorcao_ratio'] = vt / abs(dp)
        elif vt > 10: f['absorcao_ratio'] = vt * 10
        else: f['absorcao_ratio'] = 0

        # OFI
        ofi_tracker = self.state.trackers[ativo]['ofi']
        ofi_d = ofi_tracker.get_ofi()
        f['ofi_total'] = ofi_d['ofi_total']
        f['ofi_ewma'] = ofi_d['ofi_ewma']

        return f