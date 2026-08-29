# -*- coding: utf-8 -*-
"""
scorer.py — Motor de Inferência ML (v10.1).

Responsável por transformar eventos de mercado em probabilidades através
de um modelo pré-treinado. Mantém paridade total de features com a 
pipeline de treinamento.

Histórico de Versões:
  - v10.1: Desacoplamento de lógica de decisão e risco.
  - v9.32: Inclusão de VWAP intraday causal e ajuste D-1.
  - v9.19: Adicionada observabilidade para falhas de predição (P0-5).
  - v10.2: Camada completa de contexto (POC, VolRel, Intermarket, Distâncias).

Atribuições:
  - Manutenção de trackers de features (VWAP, Volatilidade, Contexto).
  - Normalização e achatamento de snapshots (flatten).
  - Execução do modelo (inference).
  - Diagnóstico de saúde do motor ML.
"""
import logging
import pickle
import time
import numpy as np
import pandas as pd
from ml.features_lib import GeradorJanelas
from ml.treino_lib import flatten_snapshot, feature_importances
from features.vwap_tracker import VWAPTracker
from features.price_context import PrecoContextTracker
from features.volatility import VolatilityTracker
from features.returns import ReturnsTracker
from features.session_time import SessionTimeTracker
from features.volume_profile import VolumeProfileTracker
from features.poc_migration import PocMigrationTracker
from features.volume_relativo import VolumeRelativoTracker
from features.cross_asset import CrossAssetEngine

log = logging.getLogger('scorer')

class ScorerML:
    """Scorer ML com features de microestrutura + VWAP + ajuste oficial.

    O scorer recebe eventos TT (negocios) e snapshots de book, mantem o
    estado do features_lib (GeradorJanelas) e adiciona:
      - VWAP intraday causal (VWAPTracker por ativo)
      - ajuste_anterior_oficial (carregado de uma tabela D-1)
    """

    def __init__(self, caminho_modelo, instrumentos,
                 tabela_ajuste_oficial=None, ticks_por_ativo=None):
        """
        Args:
            caminho_modelo: caminho para o pickle do modelo treinado
            instrumentos: lista de ativos (ex.: ['WINV26', 'WDOU26'])
            tabela_ajuste_oficial: DataFrame com colunas
                ['data_pregao', 'contrato', 'ajuste', ...]
                Cada chamada de evento() checa se a data mudou e atualiza
                o ajuste_anterior_oficial para o novo dia.
            ticks_por_ativo: dict {contrato: tick} (default: WIN=5, WDO=0.5)
        """
        with open(caminho_modelo, 'rb') as f:
            blob = pickle.load(f)
        self.modelo = blob['modelo']
        self.features = blob['features']
        self.gerador = GeradorJanelas(instrumentos=instrumentos)
        
        # v10.4: Extração de importância para auditoria de L500
        imp_series = feature_importances(self.modelo, self.features, top_n=50, importance_type='gain')
        self.importancias = imp_series.to_dict()
        
        self.ultimo_snap = {}
        self.prob = {}
        # v9.19: observabilidade
        self.fallos = 0
        self.ultimo_fallo_ts = None
        self.ultimo_ok_ts = None
        self.ultimo_error = None

        # v9.33: indice da classe 1 (TP) no predict_proba
        # Modelo tem classes_ = [-1, 0, 1] ou [0, 1] — encontramos o indice de 1
        self._idx_tp = 1  # default para binario (classes [0,1])
        if hasattr(self.modelo, 'classes_'):
            cls = list(self.modelo.classes_)
            if 1 in cls:
                self._idx_tp = cls.index(1)

        # v9.32: VWAP intraday por ativo
        if ticks_por_ativo is None:
            ticks_por_ativo = {a: (5.0 if a.startswith('WIN') else 0.5) for a in instrumentos}
        self.vwaps = {a: VWAPTracker(a, tick=ticks_por_ativo.get(a, 5.0)) for a in instrumentos}

        # v9.36: contexto de preco causal por ativo
        self.ctx = {a: PrecoContextTracker(a) for a in instrumentos}
        # v9.37: volatilidade multi-TF, retornos multi-horizonte, tempo sessao
        self.vol = {a: VolatilityTracker() for a in instrumentos}
        self.ret = {a: ReturnsTracker() for a in instrumentos}
        self.session_time = SessionTimeTracker()

        # v10.2: Novas camadas de contexto
        self.vps = {a: VolumeProfileTracker(tick=(5.0 if a.startswith('WIN') else 0.5)) for a in instrumentos}
        self.mig = {a: PocMigrationTracker() for a in instrumentos}
        self.vrels = {a: VolumeRelativoTracker() for a in instrumentos}
        self.inter = CrossAssetEngine(ativo_principal=instrumentos[0], 
                                     ativo_contexto=instrumentos[1] if len(instrumentos)>1 else None)

        # Estado para detecção de aproximação/afastamento
        self._prev_dist_vwap = {}
        self._prev_dist_poc = {}

        # v9.32: ajuste oficial D-1
        # mapa: contrato -> {data_pregao: ajuste}
        self.tabela_ajuste = {}
        self.ajuste_anterior_oficial = {}  # contrato -> ajuste_anterior
        if tabela_ajuste_oficial is not None and not tabela_ajuste_oficial.empty:
            self._carregar_ajuste_oficial(tabela_ajuste_oficial)

        # estado do dia atual (para detectar virada de dia)
        self._ultimo_dia = {}  # contrato -> dia atual
        # estado do VWAP por contrato (map: contrato -> VWAPTracker)
        # ja criado em self.vwaps

        self._log_importancia_l500()

    def _log_importancia_l500(self):
        """Loga especificamente o peso das features de profundidade L500."""
        l500_keys = [k for k in self.importancias if 'L500' in k]
        if l500_keys:
            for k in l500_keys:
                log.info(f"[ML-AUDIT] Feature {k} - Importância: {self.importancias[k]}")
        else:
            log.warning("[ML-AUDIT] Nenhuma feature L500 encontrada no modelo carregado.")

    def _carregar_ajuste_oficial(self, df_ajuste):
        """Constroi mapa {contrato: {data: ajuste}} para lookup O(1)."""
        for contrato, sub in df_ajuste.groupby('contrato'):
            sub = sub.sort_values('data_pregao')
            self.tabela_ajuste[contrato] = dict(zip(sub['data_pregao'], sub['ajuste']))

    def _atualizar_ajuste_para_dia(self, ativo, ts_ms):
        """Detecta virada de dia e atualiza ajuste_anterior_oficial."""
        dia = (int(ts_ms) - 3 * 3600 * 1000) // 86_400_000
        if self._ultimo_dia.get(ativo) == dia:
            return
        self._ultimo_dia[ativo] = dia
        # calcular data_anterior = dia - 1
        from datetime import date, timedelta
        d = date(1970, 1, 1) + timedelta(days=dia)
        d_ant = d - timedelta(days=1)
        d_ant_str = d_ant.isoformat()
        tabela = self.tabela_ajuste.get(ativo, {})
        # pegar o ultimo ajuste disponivel ate d_ant (pula fds/feriados)
        ajuste_ant = None
        for k in sorted(tabela.keys(), reverse=True):
            if k <= d_ant_str:
                ajuste_ant = tabela[k]
                break
        self.ajuste_anterior_oficial[ativo] = ajuste_ant

    def evento(self, ativo, ts_ms, preco, qtd, agressor, compradora, vendedora):
        snaps = self.gerador.processar_evento(ativo, ts_ms, preco, qtd, agressor,
                                              compradora, vendedora)
        
        # atualizar VWAP intraday causal
        if ativo in self.vwaps:
            self.vwaps[ativo].update(ts_ms, preco, qtd)
        
        # v10.2: Atualizar Volume Profile e Migração POC
        if ativo in self.vps:
            self.vps[ativo].atualizar(preco, qtd, agressor)
            vp_snap = self.vps[ativo].calcular(preco)
            # POC Migration (causal: usa o POC calculado até t)
            poc_t = preco + vp_snap['poc_dist'] # poc = preco + (poc-preco)
            self.mig[ativo].update(preco, poc_t)
            
        # v10.2: Volume Relativo e Intermarket
        if ativo in self.vrels:
            self.vrels[ativo].update(qtd, ts_ms)
        self.inter.registrar(ativo, ts_ms, preco, (1.0 if agressor=='Comprador' else -1.0))

        if ativo in self.ctx:
            self.ctx[ativo].update(ts_ms, preco, qtd)
        # v9.37: atualizar volatilidade, retornos, tempo
        if ativo in self.vol:
            self.vol[ativo].update(preco)
        if ativo in self.ret:
            self.ret[ativo].update(preco)
        self.session_time.update(ts_ms)
        # atualizar ajuste oficial D-1
        self._atualizar_ajuste_para_dia(ativo, ts_ms)
        self._consumir(snaps)

    def book(self, ativo, ts_ms, snap):
        """Recebe contrato BookSnapshot e converte para o formato interno do GeradorJanelas."""
        # Transforma o objeto BookSnapshot do contrato em dict legível pelos trackers de features
        snap_dict = {
            'bid_preco': [l.price for l in snap.bids],
            'bid_vol': [l.volume for l in snap.bids],
            'ask_preco': [l.price for l in snap.asks],
            'ask_vol': [l.volume for l in snap.asks]
        }
        # As features de book entram no PRÓXIMO snapshot TT emitido por processar_evento
        self.gerador.processar_book(ativo, ts_ms, snap_dict)

    def _consumir(self, snaps):
        if not snaps:
            return
        for item in snaps:
            # processar_evento retorna lista de TUPLAS (ativo, snapshot)
            a, snap = item
            self.ultimo_snap[a] = snap
        for a in list(self.ultimo_snap):
            self.prob[a] = self._prever(self.ultimo_snap[a])

    @staticmethod
    def _flatten(snap):
        return flatten_snapshot(snap)

    def _prever(self, snap):
        ativo = snap.get('ativo', 'WINV26') if isinstance(snap, dict) else 'WINV26'
        ts_ms = snap.get('ts_ms', time.time()*1000)
        row = self._flatten(snap)

        # 1. Injeção de Contexto Base
        for tracker_set in [self.vwaps, self.ctx, self.vol, self.ret, self.vrels, self.mig]:
            if ativo in tracker_set:
                row.update(tracker_set[ativo].snapshot())
        
        # Extração direta para evitar lookups repetidos no dict
        preco = snap.get('preco_ultimo', 0.0)

        # 2. Volume Profile e Derivações POC
        if ativo in self.vps:
            vp = self.vps[ativo].calcular(preco)
            row.update(vp)
            # dist_preco_poc_ticks
            tick = self.vps[ativo].tick
            row['dist_preco_poc_ticks'] = vp['poc_dist'] / tick
            row['preco_acima_poc'] = float(vp['poc_dist'] < 0)
            row['preco_abaixo_poc'] = float(vp['poc_dist'] > 0)
            
            # v10.2: Aproximando/Afastando POC
            d_poc = abs(vp['poc_dist'])
            prev_d_poc = self._prev_dist_poc.get(ativo, d_poc)
            row['aproximando_poc'] = float(d_poc < prev_d_poc)
            row['afastando_poc'] = float(d_poc > prev_d_poc)
            self._prev_dist_poc[ativo] = d_poc

        # 3. Derivações VWAP Avançadas
        if ativo in self.vwaps:
            v_data = self.vwaps[ativo].snapshot()
            tick = self.vwaps[ativo].tick
            row['dist_vwap_ticks'] = v_data['dist_vwap_pts'] / tick
            row['dist_vwap_norm'] = v_data['dist_vwap_pts'] / max(row.get('_vol_pts', 1.0), 0.1)
            
            # Aproximando/Afastando VWAP
            d_vwap = abs(v_data['dist_vwap_pts'])
            prev_d_vwap = self._prev_dist_vwap.get(ativo, d_vwap)
            row['aproximando_vwap'] = float(d_vwap < prev_d_vwap)
            row['afastando_vwap'] = float(d_vwap > prev_d_vwap)
            self._prev_dist_vwap[ativo] = d_vwap

        # 4. Intermarket (WIN x WDO)
        row.update(self.inter.calcular())
        
        row.update(self.session_time.snapshot(ts_ms))

        # 5. Interações Sugeridas (Seção 16)
        if 'aggr_imb' in row and 'dist_vwap_norm' in row:
            row['inter_aggr_vwap'] = row['aggr_imb'] * row['dist_vwap_norm']
        if 'dist_preco_poc' in row and 'vol_1s' in row:
            row['inter_poc_vol'] = row['dist_preco_poc'] * row['vol_1s']

        if ativo in self.ajuste_anterior_oficial:
            row['ajuste_anterior_oficial'] = self.ajuste_anterior_oficial[ativo]
            adj = self.ajuste_anterior_oficial[ativo]
            if adj is not None and not np.isnan(adj) and 'preco_ultimo' in row:
                preco = row.get('preco_ultimo', 0)
                if preco > 0:
                    row['dist_ajuste_oficial_pts'] = preco - adj
                    row['dist_ajuste_oficial_norm'] = (preco - adj) / max(row.get('_vol_pts', 1), 1e-9)
                    row['acima_ajuste_oficial'] = float(preco > adj)
                    row['abaixo_ajuste_oficial'] = float(preco < adj)

        # v10.4: Otimização para Python 3.13 - lookup direto no mapeamento
        # Pre-conversão para float32 se o modelo for LightGBM/XGBoost economiza memória
        vals = [float(row.get(c, 0.0)) for c in self.features]

        try:
            import warnings as _w
            with _w.catch_warnings():
                _w.simplefilter('ignore')
                p = float(self.modelo.predict_proba(np.array([vals], dtype=np.float32))[0, self._idx_tp])
        except Exception as exc:  # v9.19: NUNCA falhar em silencio
            self.fallos += 1
            self.ultimo_fallo_ts = time.time()
            self.ultimo_error = repr(exc)
            log.error('[scorer] predict falhou: %r (fallos=%d)', exc, self.fallos)
            return 0.5
        self.ultimo_ok_ts = time.time()
        return p

    def get_raw_signal(self, ativo):
        """Retorna apenas a probabilidade bruta para o RiskManager."""
        p = self.prob.get(ativo, 0.5)
        return p

    def decisao(self, ativo, threshold=0.65):
        """Retorna (lado, prob) baseado no threshold de probabilidade."""
        p = self.prob.get(ativo, 0.5)
        if p >= threshold:
            return 1, p
        elif p <= (1.0 - threshold):
            return -1, p
        return 0, p

    def estado_salud(self):
        """Estado do scorer para monitoramento (motor/watchdog)."""
        vwap_estado = {}
        for a, vt in self.vwaps.items():
            vwap_estado[a] = {
                'vwap': vt.vwap,
                'dist_vwap_pts': vt.dist_vwap_pts,
                'acima_vwap': vt.acima_vwap,
                'cruzou_vwap': vt.cruzou_vwap,
                'vol_total': vt.vol_total,
            }
        return {
            'ativos': list(self.prob.keys()),
            'prob': dict(self.prob),
            'fallos': self.fallos,
            'ultimo_fallo_ts': self.ultimo_fallo_ts,
            'ultimo_ok_ts': self.ultimo_ok_ts,
            'ultimo_error': self.ultimo_error,
            'n_features_modelo': len(self.features),
            'top_features': self.importancias,
            'features_modelo': list(self.features),
            'vwap_estado': vwap_estado,
            'ajuste_anterior_oficial': dict(self.ajuste_anterior_oficial),
        }
