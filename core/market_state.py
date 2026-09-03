# -*- coding: utf-8 -*-
"""
core/market_state.py — Estado de mercado por ativo. Thread-safe via RLock.

Extrai de Analise:
  - EstadoAtivo (book + T&T rows)
  - historico (deque por ativo)
  - features_por_seg (OrderedDict)
  - buffer (negocios do segundo atual)
  - ohlc intraday
  - stats (n, vc, vv, p0, p1)
  - agressao_por_corretora
  - _ultimo_preco_valido / _preco_plausivel
  - alimentar_lote / alimentar_book
  - get_historico / get_ultimo_preco / get_book_level / get_book_stats
"""

import copy
import threading
import time
import logging
import numpy as np
from collections import defaultdict, deque, OrderedDict
from datetime import date, datetime
from core.contracts import TradeEvent, BookSnapshot, BookLevel
from core.utils import fnum, fint, sstr

from features import (
    PercentilTracker, RangeTracker, AccumulationTracker,
    OFITracker, BookLevelFeatures, CrossAssetEngine, CrossAssetManager, PadroesMemoria,
    InstitutionalContext, classificar_corretora,
)

log = logging.getLogger(__name__)


class EstadoAtivo:
    """Estado bruto do RTD por ativo (book + T&T rows + dedup)."""

    def __init__(self, sym="WINV26", config=None):
        self.sym = sym
        self.config = config or {}
        # Suporte a 500 níveis conforme configurado no rtd.book_linhas
        tamanho_book = self.config.get("rtd", {}).get("book_linhas", self.config.get("book_split", 500))
        self.book_bid = [{} for _ in range(tamanho_book)]
        self.book_ask = [{} for _ in range(tamanho_book)]
        self.tt_rows = [{} for _ in range(self.config.get("tt_linhas", 1000))]
        self.tt_last_ms = [0] * self.config.get("tt_linhas", 1000)
        self.neg_total = 0
        self.neg_detectados = 0
        self.last_book_snap = 0.0
        self.warmup_tt = 0
        self.baseline_tt = False
        self.baseline_pending_tt = False
        self.ciclo_contador_tt = 0
        self.vistos_tt = {}
        self.book_ultimo_snap = None
        self.book_ultimo_t = 0.0
        self.ultimo_neg_tempo = time.time()
        self.ultimo_book_tempo = time.time()
        self.n_neg_total_anterior = 0
        self._ultimo_preco_valido = 0.0


class MarketState:
    """Estado de mercado thread-safe. Substitui o estado de Analise."""

    def __init__(self, config=None, base_dir=None, padroes=None):
        self._lock = threading.RLock()
        # v14.8: fallback para o CONFIG legado quando nenhum config é passado
        # (mesma resolução do App: getattr(CONFIG) -> get_config_dict).
        if config is None:
            try:
                import config as _cfg_mod
                config = getattr(_cfg_mod, 'CONFIG', None) or {}
            except Exception:
                config = {}
        self.config = config
        self.base_dir = base_dir or self.config.get('save_dir')
        self.market_state = self

        book_split = self.config.get('book_split', 30)
        if book_split < 0:
            raise ValueError(f"book_split negativo: {book_split}")
        self.book_bid = [{} for _ in range(book_split)]
        self.book_ask = [{} for _ in range(book_split)]

        # Estado de mercado
        self.buffer = defaultdict(list)         # negocios do segundo atual (POR ATIVO)
        self.seg_atual = 0                     # relógio global (informativo, v15.6)
        # v15.6 (P0-A06): relógio de limpeza do buffer POR ATIVO. O buffer de um
        # ativo só fecha quando ELE avança de segundo — WIN não pode limpar o
        # buffer do WDO (nem misturar contexto temporal entre ativos).
        self._seg_por_ativo = {}
        self._neg_atrasados = defaultdict(int)  # (ativo) -> trades atrasados excluídos do buffer
        self.historico = defaultdict(lambda: deque(maxlen=self.config.get("hist_segs_max", 3600)))
        self.features_por_seg = OrderedDict()    # (ativo, seg) -> features
        self.stats = defaultdict(lambda: {'n': 0, 'vc': 0, 'vv': 0, 'p0': 0.0, 'p1': 0.0})
        self.todos_negocios = deque(maxlen=self.config.get("trades_mem_max", 50000))
        self.agressao_por_corretora = defaultdict(dict)

        # Book
        self.book_snap_ant = {}
        self.book_events = defaultdict(OrderedDict)
        self.book_stats = {}
        self._book_persist = defaultdict(lambda: defaultdict(lambda: {
            'bid_seguidos': 0, 'ask_seguidos': 0,
            'bid_vol_ant': 0, 'ask_vol_ant': 0
        }))

        # OHLC intraday
        self.ohlc = defaultdict(lambda: {
            'abertura': None, 'maxima': None, 'minima': None, 'fechamento': None
        })

        # Sanity check
        self._ultimo_preco_valido = {}
        self._anomalias_preco = defaultdict(int)
        # Rollover auto-adaptação: conta rejeições consecutivas de salto
        # para detectar mudança de nível (rollover de contrato / gap legítimo).
        self._rejeicoes_salto = defaultdict(int)

        # v10.13: Injeção de dependência na factory de trackers
        def trackers_factory():
            c = self.config
            janela = c.get("janela_percentil_segs", 1800)
            amostra = c.get("amostra_minima_percentil", 60)
            return {
                'aggr': PercentilTracker(janela, amostra),
                'eff': PercentilTracker(janela, amostra),
                'acel': PercentilTracker(janela, amostra),
                'book_imb': PercentilTracker(janela, amostra),
                'range': RangeTracker(),
                'acumulacao': AccumulationTracker(),
                'ofi': OFITracker(niveis=5),
                'book_level': BookLevelFeatures(),
                'inst_context': InstitutionalContext(),
            }
        self.trackers = defaultdict(trackers_factory)
        
        # v11.0: CrossAssetManager para múltiplos pares
        cross_pairs = self.config.get('cross_asset_pairs', [])
        if cross_pairs:
            self.cross_manager = CrossAssetManager(
                pairs=cross_pairs,
                janela_corr=60,
                max_lag_ms=2000,
            )
        else:
            # Fallback: par principal × contexto (compatibilidade)
            self.cross_manager = CrossAssetManager(
                pairs=[[
                    self.config.get('ativo_principal', 'WINV26'),
                    self.config.get('ativo_contexto', 'WDOV26'),
                ]],
            )
        # Manter referência para compatibilidade
        self.cross_engine = self.cross_manager

        # Padroes (spoof, stop-hunt)
        padroes_arg = padroes if padroes is not None else (self.base_dir or '.')
        self.padroes = padroes or PadroesMemoria(padroes_arg, config=self.config)

        # Dia
        self.dia_atual = date.today()
        self._ultimo_preco_fim = {}
        self._ewma_ret2 = {}
        self._cvd_extremos = {}

    @property
    def lock(self):
        return self._lock

    # ---- Sanidade de preço ----

    def preco_plausivel(self, sym, preco):
        """Sanity check: rejeita preço fora da faixa do ativo.

        Adapta-se a rollover de contrato / mudança de nível: se o preço
        cruzar o limite de salto repetidamente (N ticks seguidos), assume-se
        uma realocação do baseline em vez de corrupção de dado e re-adota o
        novo nível — evitando que o sanity check derrube o ativo na virada
        do contrato.
        """
        if preco <= 0:
            return False
        # Guarda defensiva: config nunca deve ser None neste hot path.
        # (v14.8: fallback no __init__ garante dict; esta linha torna o P0
        # estruturalmente impossível mesmo se alguém injetar None depois.)
        cfg = self.config or {}
        for prefixo, (lo, hi) in cfg.get("faixas_preco", {}).items():
            if sym.upper().startswith(prefixo):
                if not (lo <= preco <= hi):
                    self._anomalias_preco[sym] += 1
                    log.warning(f"[SANITY] {sym}: preco {preco:.0f} fora da faixa "
                                f"[{lo},{hi}] - REJEITADO")
                    return False
                break
        ultimo = self._ultimo_preco_valido.get(sym)
        if ultimo and ultimo > 0:
            salto = abs(preco - ultimo) / ultimo
            if salto > self.config.get("max_salto_preco_pct", 0.15):
                self._rejeicoes_salto[sym] = self._rejeicoes_salto.get(sym, 0) + 1
                max_rej = self.config.get("rejeicoes_para_rollover", 5)
                if self._rejeicoes_salto[sym] >= max_rej:
                    # Mudança de nível sustentada (rollover/gap legítimo):
                    # re-adota o novo nível em vez de rejeitar para sempre.
                    self._ultimo_preco_valido[sym] = preco
                    self._rejeicoes_salto[sym] = 0
                    log.warning(f"[SANITY] {sym}: salto {salto:.0%} em {max_rej}+ ticks — "
                                f"aceito como rollover/mudança de nível")
                    return True
                self._anomalias_preco[sym] += 1
                log.warning(f"[SANITY] {sym}: salto {salto:.0%} - REJEITADO")
                return False
        self._ultimo_preco_valido[sym] = preco
        self._rejeicoes_salto[sym] = 0
        return True

    def obter_ultimo_preco(self, ativo, features=None):
        """Último preço conhecido do ativo."""
        if features is None:
            features = {}
        f = features.get(ativo)
        if f and f.get('preco_fim', 0) > 0:
            return f['preco_fim']
        st = self.stats.get(ativo)
        if st and st.get('p1', 0) > 0:
            return st['p1']
        negs = self.buffer.get(ativo, [])
        if negs and negs[-1].get('preco', 0) > 0:
            return negs[-1]['preco']
        hist = self.historico.get(ativo, [])
        if hist:
            return hist[-1].get('preco_fim', 0)
        return 0.0

    # ---- Alimentar negócios ----

    def alimentar_negocio(self, *args, **kwargs) -> bool:
        """Adiciona um negócio ao estado através do contrato TradeEvent ou argumentos individuais."""
        if len(args) == 1 and isinstance(args[0], TradeEvent):
            event = args[0]
            ativo = event.symbol
            tms = event.timestamp_ms
            preco = event.price
            qtd = event.quantity
            agr = event.aggressor
            comp = event.buyer
            vend = event.seller
        elif len(args) >= 5 and isinstance(args[0], str):
            ativo = args[0]
            tms = args[1]
            preco = args[2]
            qtd = args[3]
            agr = args[4]
            comp = args[5] if len(args) > 5 else kwargs.get('compradora', '')
            vend = args[6] if len(args) > 6 else kwargs.get('vendedora', '')
        else:
            event = kwargs.get('event')
            if event:
                ativo = event.symbol
                tms = event.timestamp_ms
                preco = event.price
                qtd = event.quantity
                agr = event.aggressor
                comp = event.buyer
                vend = event.seller
            else:
                ativo = kwargs.get('ativo') or kwargs.get('symbol')
                tms = kwargs.get('ts_ms') or kwargs.get('timestamp_ms', 0)
                preco = kwargs.get('preco') or kwargs.get('price', 0.0)
                qtd = kwargs.get('qtd') or kwargs.get('quantity', 0)
                agr = kwargs.get('agressor') or kwargs.get('aggressor', 'neutro')
                comp = kwargs.get('compradora') or kwargs.get('buyer', '')
                vend = kwargs.get('vendedora') or kwargs.get('seller', '')

        if not self.preco_plausivel(ativo, preco):
            return False
        seg = tms // 1000

        # Reset diário
        hoje = date.today()
        if hoje != self.dia_atual:
            self.dia_atual = hoje
            self._ultimo_preco_valido.clear()
            self._rejeicoes_salto.clear()
            self._ultimo_preco_fim.clear()
            self._ewma_ret2.clear()
            self._cvd_extremos.clear()
            self.seg_atual = 0
            self._seg_por_ativo.clear()
            self.buffer.clear()
            self.ohlc.clear()
            self.agressao_por_corretora.clear()

        # Mudança de segundo — POR ATIVO (v15.6, P0-A06)
        # ANTES: `self.seg_atual` global + buffer.clear() — o avanço de segundo
        # de um ativo (ex: WIN 10:00:02.000) limpava o buffer de TODOS os
        # outros (ex: WDO ainda agregando 10:00:01.x) e um evento atrasado de
        # outro ativo caía num buffer cujo contexto temporal já tinha mudado.
        seg_ativo = self._seg_por_ativo.get(ativo, 0)
        _late = False
        if seg > seg_ativo:
            self._seg_por_ativo[ativo] = seg
            if seg > self.seg_atual:
                self.seg_atual = seg  # relógio global: só informativo
            self.buffer[ativo] = []  # fecha e isola SOMENTE o buffer deste ativo
        elif seg < seg_ativo:
            # Evento atrasado do PRÓPRIO ativo (segundo dele já avançou): não
            # contamina o segundo corrente do buffer. O dado segue preservado
            # no RAW (fonte de verdade) e contabilizado nas estatísticas — só
            # fica fora da agregação de features do segundo atual.
            _late = True
            self._neg_atrasados[ativo] += 1
            if self._neg_atrasados[ativo] <= 5 or self._neg_atrasados[ativo] % 1000 == 0:
                log.warning(f"[SANITY] {ativo}: trade atrasado seg={seg} (< {seg_ativo}) "
                            f"excluído do buffer de features (total {self._neg_atrasados[ativo]})")

        # OHLC
        _ohlc = self.ohlc[ativo]
        if _ohlc['abertura'] is None:
            _ohlc['abertura'] = preco
        if _ohlc['maxima'] is None or preco > _ohlc['maxima']:
            _ohlc['maxima'] = preco
        if _ohlc['minima'] is None or preco < _ohlc['minima']:
            _ohlc['minima'] = preco
        _ohlc['fechamento'] = preco

        if not _late:
            self.buffer[ativo].append({
                'preco': preco, 'qtd': qtd, 'agressor': agr,
                'compradora': comp, 'vendedora': vend,
                'ts_ms': tms,  # v15.6: timestamp preservado p/ rótulo por-segundo do ativo
            })

        # AccumulationTracker
        tr = self.trackers[ativo]
        ts_sec = tms // 1000
        broker_agr = comp if agr == 'Comprador' else vend
        if broker_agr and broker_agr not in ('None', ''):
            tr['acumulacao'].registrar(ts_sec, broker_agr, agr, preco, qtd)

        # Stats
        st = self.stats[ativo]
        st['n'] += 1
        if agr == 'Comprador':
            st['vc'] += qtd
        elif agr == 'Vendedor':
            st['vv'] += qtd
        if not st['p0']:
            st['p0'] = preco
        st['p1'] = preco

        # Cross-asset (v11.0: CrossAssetManager)
        self.cross_manager.registrar(
            ativo, tms, preco,
            1.0 if agr == 'Comprador' else (-1.0 if agr == 'Vendedor' else 0.0))

        # Agressão por corretora (comprado/vendido = fluxo completo)
        if comp and comp not in ('None', ''):
            sd = self.agressao_por_corretora[ativo].setdefault(comp, {'c': 0, 'v': 0})
            sd['c'] += qtd  # comprador SEMPRE compra (agressivo ou passivo)
        if vend and vend not in ('None', ''):
            sd = self.agressao_por_corretora[ativo].setdefault(vend, {'c': 0, 'v': 0})
            sd['v'] += qtd  # vendedor SEMPRE vende (agressivo ou passivo)

        return True

    def alimentar_book(self, *args, **kwargs):
        """Alimenta snapshot do book através do contrato BookSnapshot ou parâmetros legados."""
        if len(args) >= 1 and isinstance(args[0], str):
            ativo = args[0]
            snap = args[1] if len(args) > 1 else kwargs.get('snap', {})
            bid_vol = args[2] if len(args) > 2 else kwargs.get('bid_vol', 0)
            ask_vol = args[3] if len(args) > 3 else kwargs.get('ask_vol', 0)
            ofi_data = args[4] if len(args) > 4 else kwargs.get('ofi_data')
            estado = kwargs.get('estado')
            if estado is not None:
                estado.book_ultimo_snap = (bid_vol, ask_vol, ())
            with self._lock:
                self.book_snap_ant[ativo] = snap
            return True

        snapshot = args[0] if len(args) > 0 else kwargs.get('snapshot')
        ofi_data = args[1] if len(args) > 1 else kwargs.get('ofi_data')
        if snapshot is None:
            return False

        ativo = snapshot.symbol
        
        # Transforma contrato em formato legado para compatibilidade com trackers existentes
        snap_legacy = defaultdict(lambda: {'bid_vol': 0, 'ask_vol': 0, 'bid_niveis': 0, 'ask_niveis': 0})
        bid_vol = 0
        for level in snapshot.bids:
            broker = level.broker or '_anon'
            snap_legacy[broker]['bid_vol'] += level.volume
            snap_legacy[broker]['bid_niveis'] += 1
            bid_vol += level.volume
            
        ask_vol = 0
        for level in snapshot.asks:
            broker = level.broker or '_anon'
            snap_legacy[broker]['ask_vol'] += level.volume
            snap_legacy[broker]['ask_niveis'] += 1
            ask_vol += level.volume

        snap = dict(snap_legacy)

        with self._lock:
            seg = self.seg_atual
            ant = self.book_snap_ant.get(ativo)
            persist = self._book_persist[ativo]

            for b in set(snap) | set(persist):
                s = snap.get(b, {})
                p = persist[b]
                if s.get('bid_vol', 0) > 5:
                    p['bid_seguidos'] = p['bid_seguidos'] + 1 if p['bid_vol_ant'] > 5 else 1
                else:
                    p['bid_seguidos'] = 0
                if s.get('ask_vol', 0) > 5:
                    p['ask_seguidos'] = p['ask_seguidos'] + 1 if p['ask_vol_ant'] > 5 else 1
                else:
                    p['ask_seguidos'] = 0
                p['bid_vol_ant'] = s.get('bid_vol', 0)
                p['ask_vol_ant'] = s.get('ask_vol', 0)

            blf = self.trackers[ativo]['book_level']
            # v10.5: Extração NumPy direta (Alta Performance)
            book_snap_dict = {
                'bid_vol': np.array([l.volume for l in snapshot.bids], dtype=np.float32),
                'bid_preco': np.array([l.price for l in snapshot.bids], dtype=np.float32),
                'ask_vol': np.array([l.volume for l in snapshot.asks], dtype=np.float32),
                'ask_preco': np.array([l.price for l in snapshot.asks], dtype=np.float32),
            }
            # P1-A09 (v15.7): o ts das features de book_level deve ser o ts DO
            # SNAPSHOT (que no BOOK é o receive/observação formalizado no adapter) —
            # nunca um time.time() novo aqui (relógio extra criava skew entre o
            # ts do evento persistido no RAW e o ts da feature calculada).
            book_level_data = blf.calcular(book_snap_dict, ativo, snapshot.timestamp_ms) or {}
            # Atualizar OFI tracker do feature_engine (separado do BookLevelFeatures)
            ofi_trk = self.trackers[ativo]['ofi']
            bid_levels_ofi = [(float(p), int(v)) for p, v in zip(book_snap_dict['bid_preco'][:5], book_snap_dict['bid_vol'][:5]) if p > 0]
            ask_levels_ofi = [(float(p), int(v)) for p, v in zip(book_snap_dict['ask_preco'][:5], book_snap_dict['ask_vol'][:5]) if p > 0]
            ofi_trk.atualizar(bid_levels_ofi, ask_levels_ofi)

            if ant:
                snap_ant, bv_ant, av_ant = ant
                result = comparar_books(snap_ant, snap, persist)
                total = bv_ant + av_ant
                imb = (bv_ant - av_ant) / total if total > 0 else 0
                delta_bid = bid_vol - bv_ant
                delta_ask = ask_vol - av_ant
                # v10.4: Usa estatísticas de agressão reais do MarketState
                st = self.stats.get(ativo, {'vc': 0, 'vv': 0})
                agressao = (st['vc'] - st['vv']) / (st['vc'] + st['vv']) if (st['vc'] + st['vv']) > 0 else 0
                absorvedores = []
                for b, s in snap.items():
                    if agressao > 0.3 and s.get('ask_vol', 0) > 10:
                        absorvedores.append({'broker': b, 'lado': 'ask', 'vol': s['ask_vol'],
                                              'seguidos': persist[b].get('ask_seguidos', 0)})
                    elif agressao < -0.3 and s.get('bid_vol', 0) > 10:
                        absorvedores.append({'broker': b, 'lado': 'bid', 'vol': s['bid_vol'],
                                              'seguidos': persist[b].get('bid_seguidos', 0)})

                self.book_stats[ativo] = {
                    'imb': imb, 'bid_vol': bid_vol, 'ask_vol': ask_vol,
                    'delta_bid': delta_bid, 'delta_ask': delta_ask,
                    'thinning_bid': result['thinning_bid'], 'thinning_ask': result['thinning_ask'],
                    'n_retiradas': len(result['retiradas']), 'n_reposicoes': len(result['reposicoes']),
                    'retiradas_bid': sum(1 for r in result['retiradas'] if r['lado'] == 'bid'),
                    'retiradas_ask': sum(1 for r in result['retiradas'] if r['lado'] == 'ask'),
                    'reposicoes_bid': sum(1 for r in result['reposicoes'] if r['lado'] == 'bid'),
                    'reposicoes_ask': sum(1 for r in result['reposicoes'] if r['lado'] == 'ask'),
                    'absorvedores': absorvedores[:10],
                    'defesa_persistente': result['defesa_persistente'],
                    'layering': result['layering'],
                    'book_level': book_level_data,
                }
                evts = [r for r in result['retiradas'] + result['reposicoes'] if r['broker'] != '_anon']
                if evts:
                    be = self.book_events[ativo]
                    be.setdefault(seg, []).extend(evts)
                    while len(be) > self.config.get("book_events_seg_max", 300):
                        be.popitem(last=False)
            else:
                # Primeiro snapshot: ainda não tem anterior, mas salva book_level
                self.book_stats[ativo] = {'book_level': book_level_data or {}}

            self.book_snap_ant[ativo] = (snap, bid_vol, ask_vol)
            for b in list(persist):
                if persist[b]['bid_seguidos'] == 0 and persist[b]['ask_seguidos'] == 0:
                    if b not in snap:
                        del persist[b]

        # Detecção de spoof fora do lock do book: é O(n_brokers) e adquire o
        # próprio lock de patterns — segurá-lo dentro do lock do MarketState
        # aumentaria latência do hot path e criaria risco de deadlock (locking aninhado).
        if snap:
            spoofs = self.padroes.detectar_spoof(ativo, snap, time.time())
            if spoofs:
                for sp in spoofs:
                    log.info(f"[PADROES] spoof: {sp['broker']} {sp['lado']} -{sp['vol_retirada']} vol")

    # ---- Getters ----

    def get_historico(self, segundos=1800):
        with self._lock:
            segs = [s for (_a, s) in self.features_por_seg]
            ref = max(segs) if segs else 0
            corte = ref - segundos
            out = {}
            for (ativo, seg), f in self.features_por_seg.items():
                if seg < corte:
                    continue
                out.setdefault(ativo, []).append({
                    'seg': seg,
                    'preco': f.get('preco_fim', 0) or 0,
                    'aggr': f.get('aggr_imb', 0) or 0,
                    'vol': f.get('vol_total', 0) or 0,
                    'cvd': f.get('cvd_total', 0) or 0,
                    'ofi': f.get('ofi_ewma', 0) or 0,
                })
            return out

    def get_book_level(self):
        with self._lock:
            result = {}
            for ativo, bs in self.book_stats.items():
                bl = bs.get('book_level')
                # v11.0: CrossAssetManager retorna dados por par
                ca_data = self.cross_manager.calcular_para_ativo(ativo)
                result[ativo] = {
                    'book_level': bl or {},
                    'cross_asset': ca_data,
                }
            return result

    def get_book_stats(self):
        with self._lock:
            return {k: dict(v) for k, v in self.book_stats.items()}

    def get_resumo(self, ativo):
        with self._lock:
            st = self.stats.get(ativo)
            if not st or st['n'] == 0:
                return {}
            vc, vv = st['vc'], st['vv']
            return {
                'total_negocios': st['n'], 'vol_comprador': vc, 'vol_vendedor': vv,
                'aggr_imb': (vc - vv) / (vc + vv) if (vc + vv) > 0 else 0,
                'preco_inicio': st['p0'], 'preco_fim': st['p1'],
                'delta_preco': st['p1'] - st['p0']
            }

    def get_saldo_corretoras(self, ativo=None):
        with self._lock:
            resultado = {}
            for sym, cmap in self.agressao_por_corretora.items():
                if ativo and sym != ativo:
                    continue
                saldos = {}
                for corp, sd in cmap.items():
                    c = sd.get('c', 0)
                    v = sd.get('v', 0)
                    total = c - v
                    if abs(total) > 5 or (c + v) > 50:
                        tipo = classificar_corretora(corp)
                        saldos[corp] = {
                            'comprado': round(c, 1),
                            'vendido': round(v, 1),
                            'saldo': round(total, 1),
                            'lado': 'C' if total > 0 else 'V',
                            'tipo': tipo,
                            'label': corp,
                        }
                resultado[sym] = sorted(saldos.values(), key=lambda x: -abs(x['saldo']))
            return resultado

    def get_memoria(self, circuit_breaker_nivel=0, trades_dia=0, pnl_dia=0.0,
                    perdas_consecutivas=0, confianca_ewma=0.0, sinal_confirmado=0,
                    erros_globais=None):
        with self._lock:
            return {
                'total_negocios': sum(s['n'] for s in self.stats.values()),
                'circuit_breaker_nivel': circuit_breaker_nivel,
                'trades_dia': trades_dia, 'pnl_dia': round(pnl_dia, 2),
                'perdas_consecutivas': perdas_consecutivas,
                'confianca_ewma': round(confianca_ewma, 3),
                'sinal_confirmado': sinal_confirmado,
                'anomalias_preco': dict(self._anomalias_preco),
            }


def extrair_niveis_book(estado, n_niveis):
    bid_levels = []
    ask_levels = []
    for lvl in range(n_niveis):
        bid = estado.book_bid[lvl]
        ask = estado.book_ask[lvl]
        bid_levels.append((fnum(bid.get('OCP', 0)), fint(bid.get('VOC', 0))))
        ask_levels.append((fnum(ask.get('OVD', 0)), fint(ask.get('VOV', 0))))
    return bid_levels, ask_levels


def snapshot_book(estado, config=None):
    snap = defaultdict(lambda: {'bid_vol': 0, 'ask_vol': 0, 'bid_preco': 0,
                                'ask_preco': 9e18, 'bid_niveis': 0, 'ask_niveis': 0,
                                'bid_vol_top3': 0, 'ask_vol_top3': 0})
    total_bid_vol = 0
    total_ask_vol = 0
    book_split = (config or {}).get('book_split', 30)
    for lvl in range(book_split):
        d = estado.book_bid[lvl]
        vol = fint(d.get('VOC', 0))
        if vol <= 0: continue
        preco = fnum(d.get('OCP', 0))
        broker = sstr(d.get('ACP', '')) or '_anon'
        if broker == 'None': broker = '_anon'
        snap[broker]['bid_vol'] += vol
        snap[broker]['bid_niveis'] += 1
        if preco > snap[broker]['bid_preco']: snap[broker]['bid_preco'] = preco
        total_bid_vol += vol
    for lvl in range(book_split):
        d = estado.book_ask[lvl]
        vol = fint(d.get('VOV', 0))
        if vol <= 0: continue
        preco = fnum(d.get('OVD', 0))
        broker = sstr(d.get('AVD', '')) or '_anon'
        if broker == 'None': broker = '_anon'
        snap[broker]['ask_vol'] += vol
        snap[broker]['ask_niveis'] += 1
        if 0 < preco < snap[broker]['ask_preco']: snap[broker]['ask_preco'] = preco
        total_ask_vol += vol
    return dict(snap), total_bid_vol, total_ask_vol


def extrair_book_snapshot(estado):
    """Converte EstadoAtivo em book_snapshot compatível com BookLevelFeatures."""
    # Extrai o máximo de níveis disponíveis no estado, limitado pelo book_linhas (500)
    n = len(estado.book_bid)
    bid_vols, bid_precos, ask_vols, ask_precos = [], [], [], []
    for lvl in range(n):
        d = estado.book_bid[lvl]
        bp = fnum(d.get('OCP', 0))
        bv = fint(d.get('VOC', 0))
        if bp > 0 and bv > 0:
            bid_precos.append(bp)
            bid_vols.append(bv)
        da = estado.book_ask[lvl]
        ap = fnum(da.get('OVD', 0))
        av = fint(da.get('VOV', 0))
        if ap > 0 and av > 0:
            ask_precos.append(ap)
            ask_vols.append(av)
    return {
        'bid_vol': bid_vols,
        'bid_preco': bid_precos,
        'ask_vol': ask_vols,
        'ask_preco': ask_precos,
    }


def comparar_books(snap_ant, snap_atu, persist_book=None):
    """Compara dois snapshots de book e detecta eventos."""
    retiradas = []
    reposicoes = []
    defesa_persistente = []
    layering = []
    todos = set(snap_ant) | set(snap_atu)
    bv_ant = sum(v.get('bid_vol', 0) for v in snap_ant.values())
    av_ant = sum(v.get('ask_vol', 0) for v in snap_ant.values())
    bv_atu = sum(v.get('bid_vol', 0) for v in snap_atu.values())
    av_atu = sum(v.get('ask_vol', 0) for v in snap_atu.values())
    thinning_bid = bv_ant - bv_atu
    thinning_ask = av_ant - av_atu

    for b in todos:
        a = snap_ant.get(b, {'bid_vol': 0, 'ask_vol': 0})
        c = snap_atu.get(b, {'bid_vol': 0, 'ask_vol': 0})
        if a['bid_vol'] > 5 and c['bid_vol'] < a['bid_vol'] * 0.5:
            retiradas.append({'broker': b, 'lado': 'bid', 'delta': a['bid_vol'] - c['bid_vol'], 'tipo': 'retirada'})
        if a['ask_vol'] > 5 and c['ask_vol'] < a['ask_vol'] * 0.5:
            retiradas.append({'broker': b, 'lado': 'ask', 'delta': a['ask_vol'] - c['ask_vol'], 'tipo': 'retirada'})
        if c['bid_vol'] > 10 and a['bid_vol'] == 0:
            reposicoes.append({'broker': b, 'lado': 'bid', 'delta': c['bid_vol'], 'tipo': 'reposicao'})
        elif a['bid_vol'] > 0 and c['bid_vol'] > a['bid_vol'] * 1.5:
            reposicoes.append({'broker': b, 'lado': 'bid', 'delta': c['bid_vol'] - a['bid_vol'], 'tipo': 'reposicao'})
        if c['ask_vol'] > 10 and a['ask_vol'] == 0:
            reposicoes.append({'broker': b, 'lado': 'ask', 'delta': c['ask_vol'], 'tipo': 'reposicao'})
        elif a['ask_vol'] > 0 and c['ask_vol'] > a['ask_vol'] * 1.5:
            reposicoes.append({'broker': b, 'lado': 'ask', 'delta': c['ask_vol'] - a['ask_vol'], 'tipo': 'reposicao'})
        if persist_book and b in persist_book:
            pb = persist_book[b]
            if a['bid_vol'] > 10 and c['bid_vol'] > 10 and pb.get('bid_seguidos', 0) >= 2:
                defesa_persistente.append({'broker': b, 'lado': 'bid', 'vol': c['bid_vol'], 'seguidos': pb['bid_seguidos']})
            if a['ask_vol'] > 10 and c['ask_vol'] > 10 and pb.get('ask_seguidos', 0) >= 2:
                defesa_persistente.append({'broker': b, 'lado': 'ask', 'vol': c['ask_vol'], 'seguidos': pb['ask_seguidos']})
        if a['bid_vol'] > 0 and c['bid_vol'] == 0:
            layering.append({'broker': b, 'lado': 'bid', 'tipo': 'layering_remocao'})
        if a['ask_vol'] > 0 and c['ask_vol'] == 0:
            layering.append({'broker': b, 'lado': 'ask', 'tipo': 'layering_remocao'})

    return {'retiradas': retiradas, 'reposicoes': reposicoes,
            'defesa_persistente': defesa_persistente,
            'thinning_bid': thinning_bid, 'thinning_ask': thinning_ask,
            'layering': layering}


def check_staleness(est, agora, pre_abertura=False):
    """Verifica se um ativo está sem dados (negócios e book parados)."""
    tempo_sem_neg = agora - est.ultimo_neg_tempo
    tempo_sem_book = agora - est.ultimo_book_tempo

    if pre_abertura:
        # No pré-leilão só o book importa
        return tempo_sem_book > 30

    # Pregão: book parado > 30s OU ambos parados > 15s
    sem_book = tempo_sem_book > 30
    sem_neg = tempo_sem_neg > 15 and tempo_sem_book > 15
    return sem_book or sem_neg
