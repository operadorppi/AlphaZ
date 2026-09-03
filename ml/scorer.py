# -*- coding: utf-8 -*-
"""
scorer.py — Motor de Inferência ML (v12.0).

Responsável por transformar eventos de mercado em probabilidades através
de um modelo pré-treinado. Mantém paridade total de features com a 
pipeline de treinamento.

Histórico de Versões:
  - v10.1: Desacoplamento de lógica de decisão e risco.
  - v9.32: Inclusão de VWAP intraday causal e ajuste D-1.
  - v9.19: Adicionada observabilidade para falhas de predição (P0-5).
  - v10.2: Camada completa de contexto (POC, VolRel, Intermarket, Distâncias).
  - v12.0: Sincronização completa com batch — adiciona 25 features de regime,
           ATR, volatilidade expandida, e interações micro×contexto.

Atribuições:
  - Manutenção de trackers de features (VWAP, Volatilidade, Contexto).
  - Normalização e achatamento de snapshots (flatten).
  - Execução do modelo (inference).
  - Diagnóstico de saúde do motor ML.
  - Cálculo de features de regime e ATR (sincronizadas com batch v950).
"""
import logging
import os
import pickle
import time
import math
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
from ml.feature_manifest import FeatureManifest, _valor_numerico

log = logging.getLogger('scorer')


class RegimeTracker:
    """Calcula features de regime de mercado sincronizadas com o batch v950.
    
    Features calculadas:
    - regime_realiz_vol: EWMA de volatilidade realizada (curto vs longo)
    - regime_realiz_vol_bps: volatilidade em bps
    - regime_vol_zscore: z-score da volatilidade
    - regime_aggr_persistencia: EWMA suave do aggr_imb
    - regime_cvd_aceleracao: aceleração do CVD
    - regime_range_dia_norm: range do dia normalizado
    - regime_pos_vs_vwap: posição relativa vs VWAP
    - regime_pos_vs_ajuste: posição relativa vs ajuste
    """
    
    def __init__(self):
        self._precos = []  # buffer de preços
        self._cvd_total = 0.0
        self._cvd_history = []  # últimos 300 valores de CVD
        self._maxima_dia = None
        self._minima_dia = None
        self._volta_dia = None
        self._ultimo_ts = None
        self._ultimo_dia = None
        self._vwap_value = None  # v12.1: VWAP atual para regime_pos_vs_vwap
        
        # EWMA para volatilidade
        self._vol_ewma_curto = 0.0
        self._vol_ewma_longo = 0.0
        self._vol_ewma_2x = 0.0  # EWMA da EWMA (para z-score)
        
        # EWMA para aggr_imb
        self._aggr_ewma = 0.0
        
        # Contadores
        self._n_updates = 0
    
    def _dia_brt(self, ts_ms):
        return (int(ts_ms) - 3 * 3600 * 1000) // 86_400_000
    
    def reset_diario(self):
        """Reseta estado para novo dia."""
        self._maxima_dia = None
        self._minima_dia = None
        self._volta_dia = None
        self._vwap_value = None  # v12.1: reset VWAP também
        self._vol_ewma_curto = 0.0
        self._vol_ewma_longo = 0.0
        self._vol_ewma_2x = 0.0
        self._aggr_ewma = 0.0
        self._n_updates = 0
        self._cvd_history = []
    
    def update(self, ts_ms, preco, vol_pts, aggr_imb, cvd_total, vwap):
        """Atualiza tracker com novo dado."""
        dia = self._dia_brt(ts_ms)
        if self._ultimo_dia is not None and dia != self._ultimo_dia:
            self.reset_diario()
        self._ultimo_dia = dia
        
        self._n_updates += 1
        
        # Atualizar máxima/mínima do dia
        if preco > 0:
            if self._maxima_dia is None or preco > self._maxima_dia:
                self._maxima_dia = preco
            if self._minima_dia is None or preco < self._minima_dia:
                self._minima_dia = preco
            self._volta_dia = preco
            self._vwap_value = vwap  # v12.1: guardar VWAP para regime_pos_vs_vwap
        
        # Volatilidade realizada (abs delta preço)
        # v12.1: Unificar alphas com batch (features_contexto_avancado.py)
        # Batch usa: alpha_curto=0.005 (janela ~200 ticks), alpha_longo=0.01
        if len(self._precos) > 0:
            ret = abs(preco - self._precos[-1])
            alpha_curto = 0.005  # UNIFICADO: mesmo alpha do batch v950
            alpha_longo = 0.01
            self._vol_ewma_curto = alpha_curto * ret + (1 - alpha_curto) * self._vol_ewma_curto
            self._vol_ewma_longo = alpha_longo * ret + (1 - alpha_longo) * self._vol_ewma_longo
            self._vol_ewma_2x = alpha_longo * self._vol_ewma_curto + (1 - alpha_longo) * self._vol_ewma_2x
        
        self._precos.append(preco)
        if len(self._precos) > 1600:
            self._precos = self._precos[-1500:]
        
        # Atualizar CVo history para aceleração
        self._cvd_history.append(cvd_total)
        if len(self._cvd_history) > 300:
            self._cvd_history = self._cvd_history[-300:]
        
        # EWMA do aggr_imb
        if aggr_imb is not None:
            self._aggr_ewma = 0.05 * aggr_imb + (1 - 0.05) * self._aggr_ewma
    
    def snapshot(self):
        """Retorna features de regime calculadas."""
        result = {}
        
        # regime_realiz_vol: ratio vol curto/longo (expansão/compressão)
        if self._vol_ewma_longo > 1e-9:
            result['regime_realiz_vol'] = self._vol_ewma_curto / self._vol_ewma_longo
        else:
            result['regime_realiz_vol'] = 1.0
        
        # regime_realiz_vol_bps: volatilidade em bps
        if self._volta_dia and self._volta_dia > 0:
            result['regime_realiz_vol_bps'] = self._vol_ewma_curto / self._volta_dia * 10000
        else:
            result['regime_realiz_vol_bps'] = 0.0
        
        # regime_vol_zscore: z-score da vol (vol de vol)
        if self._vol_ewma_2x > 1e-9 and self._vol_ewma_longo > 1e-9:
            result['regime_vol_zscore'] = (self._vol_ewma_curto - self._vol_ewma_longo) / self._vol_ewma_longo
        else:
            result['regime_vol_zscore'] = 0.0
        
        # regime_aggr_persistencia: EWMA suave do aggr_imb
        result['regime_aggr_persistencia'] = self._aggr_ewma
        
        # regime_cvd_aceleracao: (cvd[t] - cvd[t-300]) / 300
        if len(self._cvd_history) >= 300:
            result['regime_cvd_aceleracao'] = (self._cvd_history[-1] - self._cvd_history[0]) / 300.0
        else:
            result['regime_cvd_aceleracao'] = 0.0
        
        # regime_range_dia_norm: (max - min) / vol_ref
        if self._maxima_dia and self._minima_dia and self._volta_dia:
            range_dia = self._maxima_dia - self._minima_dia
            vol_ref = max(self._vol_ewma_curto, 1.0)
            result['regime_range_dia_norm'] = range_dia / vol_ref
        else:
            result['regime_range_dia_norm'] = 0.0
        
        # regime_pos_vs_vwap: posição relativa vs VWAP
        # Usa o vwap_value passado no update() (não self._volta_dia)
        if hasattr(self, '_vwap_value') and self._vwap_value and self._vwap_value > 0:
            result['regime_pos_vs_vwap'] = (self._volta_dia - self._vwap_value) / max(self._vol_ewma_curto, 1.0) if self._volta_dia else 0.0
        else:
            result['regime_pos_vs_vwap'] = 0.0
        
        # regime_pos_vs_ajuste: será preenchido externamente
        result['regime_pos_vs_ajuste'] = 0.0
        
        return result


class ScorerML:
    """Scorer ML com features de microestrutura + VWAP + ajuste oficial + regime.
    
    O scorer recebe eventos TT (negocios) e snapshots de book, mantem o
    estado do features_lib (GeradorJanelas) e adiciona:
      - VWAP intraday causal (VWAPTracker por ativo)
      - ajuste_anterior_oficial (carregado de uma tabela D-1)
      - Features de regime (RegimeTracker) sincronizadas com batch v950
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
        
        # v11.11: Feature Manifest — paridade treino ↔ produção
        self.manifest = None
        manifest_path = os.path.join(os.path.dirname(caminho_modelo), 'feature_manifest.json')
        if os.path.exists(manifest_path):
            try:
                self.manifest = FeatureManifest.load(manifest_path)
                log.info(f'[MANIFEST] Carregado: {self.manifest.n_features} features required')
            except Exception as e:
                log.warning(f'[MANIFEST] Falha ao carregar: {e}')
        else:
            log.warning(f'[MANIFEST] Não encontrado: {manifest_path} — usando lista do .pkl')
        
        self.ultimo_snap = {}
        self.prob = {}
        # P0-A30 (v15.25): status da ULTIMA inferencia por ativo. O 0.5 de
        # fallback (erro/cobertura) NAO pode ser tratado como probabilidade
        # valida por quem consome — o status e a fonte de verdade:
        #   'OK'            -> inferencia valida
        #   'NAO_INFERIDO'  -> ainda nao houve snap p/ o ativo
        #   'MODEL_ERROR'   -> inferencia falhou — prob() NAO vale (None)
        #   'ECE_ALTO'      -> inferiu mas ECE > 0.15 — neutro POLITICO
        self.status = {a: 'NAO_INFERIDO' for a in instrumentos}
        # v9.19: observabilidade
        self.fallos = 0
        self.ultimo_fallo_ts = None
        self.ultimo_ok_ts = None
        self.ultimo_error = None
        # P0-A29 (v15.24): assinatura do ultimo problema de cobertura, p/
        # throttling do log (feature sistematicamente ausente nao inunda log)
        self._ultimo_erro_cobertura = None
        self._ece = 0.0  # v12.2: ECE tracking para fallback

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
        # P0-A24 (v15.18): só o par principal×contexto alimenta o engine do
        # intermarket. Instrumentos fora do par (ex.: IND/DOL capturados no
        # mesmo motor) nunca chegam ao engine — antes viravam o contexto WDO
        # por padrão (contaminação). O engine também rejeita por segurança.
        self._inter_ativos = {instrumentos[0]}
        if len(instrumentos) > 1:
            self._inter_ativos.add(instrumentos[1])
        # v15.20: último imb_L1 de book por ativo (ts_ms, imb) para alimentar
        # corr_imb_book real no cross-asset (antes: 0.0 por construção).
        self._inter_book_imb = {}

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
        self._prev_preco = {}  # v12.2: preco anterior por ativo (para calcular vol_pts)
        
        # v12.0: Regime tracker por ativo
        self.regime = {a: RegimeTracker() for a in instrumentos}

        # v12.0: ATR tracker por ativo
        self._atr_alpha = 2.0 / 15.0  # ~14-period EMA
        self._atr_values = {a: 0.0 for a in instrumentos}
        self._atr_prev = {a: None for a in instrumentos}

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
        
        # v12.2: Reset diário de todos os trackers
        # P0-A27 (v15.22): vps NAO e resetado aqui — o VolumeProfileTracker faz
        # rollover interno por dia BRT no atualizar(ts_ms, ...). Reset externo
        # aqui rodava DEPOIS do 1o update do dia novo e apagava o 1o trade
        # (que ja tinha entrado no perfil da sessao nova).
        # P0-A28 (v15.23): mig NAO e resetado aqui — o PocMigrationTracker faz
        # rollover interno por dia BRT no update(ts_ms, ...) pelo mesmo motivo
        # (reset externo pos-update contaminava/perdia a 1a linha do dia novo).
        # P1-A26 (v15.21): vrels NAO e resetado aqui. O VolumeRelativoTracker
        # faz o proprio rollover no update() (arquiva o dia anterior em
        # _historico e zera o dia corrente). Reset externo aqui apagava o
        # historico recem-arquivado — em live a referencia entre dias nunca
        # acumulava e volume_relativo ficava preso no fallback 1.0.
        if ativo in self.vol:
            self.vol[ativo].reset_diario()
        if ativo in self.ret:
            self.ret[ativo].reset_diario()
        if ativo in self.vwaps:
            self.vwaps[ativo].reset_diario()
        if ativo in self.regime:
            self.regime[ativo].reset_diario()
        
        # Reset ATR
        self._atr_values[ativo] = 0.0
        self._atr_prev[ativo] = None
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
        
        # Reset regime tracker para novo dia
        if ativo in self.regime:
            self.regime[ativo].reset_diario()

    def evento(self, ativo, ts_ms, preco, qtd, agressor, compradora, vendedora):
        snaps = self.gerador.processar_evento(ativo, ts_ms, preco, qtd, agressor,
                                              compradora, vendedora)
        
        # atualizar VWAP intraday causal
        if ativo in self.vwaps:
            self.vwaps[ativo].update(ts_ms, preco, qtd)
        
        # v10.2: Atualizar Volume Profile e Migração POC
        # P0-A27 (v15.22): ts_ms obrigatorio — rollover de sessao interno.
        if ativo in self.vps:
            self.vps[ativo].atualizar(ts_ms, preco, qtd, agressor)
            vp_snap = self.vps[ativo].calcular(preco)
            # POC Migration (causal: usa o POC calculado até t)
            # P0-A28 (v15.23): ts_ms agora e passado — a velocidade do POC é
            # calculada no grid temporal de 100ms (delta/linha + EWMA), não
            # como delta entre atualizações consecutivas.
            poc_t = preco + vp_snap['poc_dist'] # poc = preco + (poc-preco)
            self.mig[ativo].update(ts_ms, preco, poc_t)
            
        # v10.2: Volume Relativo e Intermarket
        if ativo in self.vrels:
            self.vrels[ativo].update(qtd, ts_ms)
        if ativo in self._inter_ativos:
            # v15.20: imb_book = imb_L1 do ÚLTIMO book processado antes deste
            # trade no fluxo (streaming as-of — mesma convenção do _ultimo_book
            # do gerador). Sem book ainda → 0.0.
            _ib = self._inter_book_imb.get(ativo)
            self.inter.registrar(ativo, ts_ms, preco,
                                 (1.0 if agressor == 'Comprador' else -1.0),
                                 imb_book=(_ib[1] if _ib else 0.0))

        if ativo in self.ctx:
            self.ctx[ativo].update(ts_ms, preco, qtd)
        # v9.37: atualizar volatilidade, retornos, tempo
        # P0-A21 (v15.15): VolatilityTracker agora e TEMPORAL (grid de 100ms
        # do master clock) — o ts estava disponivel aqui e nunca era passado.
        if ativo in self.vol:
            self.vol[ativo].update(ts_ms, preco)
        # P0-A20 (v15.14): ReturnsTracker agora e temporal — janelas por
        # MASTER CLOCK (ts_ms do evento), nao por contagem de trades. O ts
        # estava disponivel aqui mas nunca era passado (rajadas de 100 trades
        # em 20ms eram tratadas como 100 janelas de 100ms).
        if ativo in self.ret:
            self.ret[ativo].update(ts_ms, preco)
        self.session_time.update(ts_ms)
        # atualizar ajuste oficial D-1
        self._atualizar_ajuste_para_dia(ativo, ts_ms)
        
        # v12.0: Atualizar regime tracker
        if ativo in self.regime:
            # Calcular vol_pts como delta do preço
            self._prev_preco = self._prev_preco or {}
            prev_preco = self._prev_preco.get(ativo)
            vol_pts = abs(preco - prev_preco) if prev_preco is not None else 0.0
            self._prev_preco[ativo] = preco
            
            # Obter aggr_imb e cvd do snapshot (será usado em _prever)
            self.regime[ativo].update(ts_ms, preco, vol_pts, 0.0, 0.0, 
                                     self.vwaps[ativo].vwap if ativo in self.vwaps else None)
        
        self._consumir(snaps)

    def book(self, ativo, ts_ms, snap):
        """Recebe contrato BookSnapshot e converte para o formato interno do GeradorJanelas."""
        # v15.20: imb L1 do snapshot p/ cross-asset (corr_imb_book real)
        imb = 0.0
        if getattr(snap, 'bids', None) and getattr(snap, 'asks', None):
            bv = float(snap.bids[0].volume)
            av = float(snap.asks[0].volume)
            if bv + av > 0:
                imb = (bv - av) / (bv + av)
        self._inter_book_imb[ativo] = (ts_ms, imb)
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
        # P0-A31 (v15.27): fonte do VP para o ROW do modelo = o vp embutido
        # no snap do GeradorJanelas (self.gerador.vp_trackers) — MESMA
        # semantica do dataset_100ms de treino (corte emitido ANTES do trade
        # que cruza a borda: lag de 1 trade). ANTES o row era sobrescrito com
        # self.vps[ativo].calcular(preco) em instante de trade (sem o lag) —
        # duas implementacoes paralelas de VP dentro do scorer divergindo do
        # dataset em ~1 trade (A31). O fallback p/ self.vps so cobre snaps
        # sem vp (chamadas diretas/warm-up de testes).
        if ativo in self.vps:
            _vp_snap = {k: row.get(f'vp_{k}') for k in
                        ('poc_dist', 'vah_dist', 'val_dist', 'vp_total',
                         'poc_acima')}
            if _vp_snap['poc_dist'] is not None:
                vp = {k: float(v) if v is not None else 0.0
                      for k, v in _vp_snap.items()}
            else:
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
            
            # VWAP inclinação (v12.1: sincronizado com batch)
            # Batch calcula em: features_contexto_avancado.py:393-396
            # vwap_inclinacao_1m = (vwap[t] - vwap[t-600]) / vwap[t-600]
            # vwap_inclinacao_5m = (vwap[t] - vwap[t-3000]) / vwap[t-3000]
            if not hasattr(self, '_vwap_history'):
                self._vwap_history = {}
            if ativo not in self._vwap_history:
                self._vwap_history[ativo] = []
            vwap_val = v_data.get('vwap', 0.0)
            if vwap_val > 0:
                self._vwap_history[ativo].append(vwap_val)
                if len(self._vwap_history[ativo]) > 3000:
                    self._vwap_history[ativo] = self._vwap_history[ativo][-2500:]
                
                hist = self._vwap_history[ativo]
                if len(hist) >= 600:
                    row['vwap_inclinacao_1m'] = (vwap_val - hist[-600]) / max(hist[-600], 1.0)
                else:
                    row['vwap_inclinacao_1m'] = 0.0
                if len(hist) >= 3000:
                    row['vwap_inclinacao_5m'] = (vwap_val - hist[-3000]) / max(hist[-3000], 1.0)
                else:
                    row['vwap_inclinacao_5m'] = 0.0

        # 4. Intermarket (WIN x WDO)
        # P0-A23: ts_ms do evento — janelas relativas ao evento, nunca wall clock.
        row.update(self.inter.calcular(ts_ms))
        
        row.update(self.session_time.snapshot(ts_ms))

        # 5. Interações Sugeridas (Seção 16) — SINCRONIZADAS COM BATCH v950
        # Todas as 13 interações do batch devem ser calculadas no live também.
        # Batch calcula em: ml/features_contexto_avancado.py:adicionar_interacoes_micro_contexto
        
        aggr = row.get('aggr_imb', 0.0)
        cvd = row.get('cvd_total', 0.0)
        imb5 = row.get('imb_L5', 0.0)
        vol = row.get('_vol_pts', 0.0)
        
        dist_vwap = row.get('dist_vwap_pts', 0.0)
        dist_ajuste = row.get('dist_ajuste_oficial_pts', 0.0)
        acima_vwap = row.get('acima_vwap', 0.0)
        acima_ajuste = row.get('acima_ajuste_oficial', 0.0)
        pos_range = row.get('posicao_range_dia', 0.0)
        
        # aggr_imb × contexto
        row['aggr_x_dist_vwap'] = aggr * dist_vwap
        row['aggr_x_dist_ajuste_oficial'] = aggr * dist_ajuste
        row['aggr_x_acima_vwap'] = aggr * acima_vwap
        row['aggr_x_acima_ajuste_oficial'] = aggr * acima_ajuste
        row['aggr_x_posicao_range_dia'] = aggr * pos_range
        
        # cvd_total × contexto
        row['cvd_x_dist_vwap'] = cvd * dist_vwap
        row['cvd_x_dist_ajuste_oficial'] = cvd * dist_ajuste
        row['cvd_x_acima_vwap'] = cvd * acima_vwap
        row['cvd_x_acima_ajuste_oficial'] = cvd * acima_ajuste
        
        # imbalance × contexto
        row['imb_x_dist_vwap'] = imb5 * dist_vwap
        row['imb_x_dist_ajuste_oficial'] = imb5 * dist_ajuste
        
        # volume × contexto
        row['vol_x_acima_vwap'] = vol * acima_vwap
        row['vol_x_acima_ajuste_oficial'] = vol * acima_ajuste

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
                    
                    # v12.0: Atualizar regime pos_vs_ajuste
                    if ativo in self.regime:
                        vol_ref = max(row.get('_vol_pts', 1.0), 1e-9)
                        self.regime[ativo]._volta_dia = preco
                        # Atualizar regime com dados do snapshot
                        aggr = row.get('aggr_imb', 0.0)
                        cvd = row.get('cvd_total', 0.0)
                        vwap_val = self.vwaps[ativo].vwap if ativo in self.vwaps else None
                        self.regime[ativo].update(ts_ms, preco, row.get('_vol_pts', 0.0), 
                                                aggr, cvd, vwap_val)
                        row.update(self.regime[ativo].snapshot())
                        row['regime_pos_vs_ajuste'] = row.get('dist_ajuste_oficial_norm', 0.0)

        # 6. Features de ATR (v12.0)
        if ativo in self._atr_values:
            if preco > 0:
                prev_preco = self._atr_prev.get(ativo)
                if prev_preco is not None:
                    true_range = abs(preco - prev_preco)
                    self._atr_values[ativo] = self._atr_alpha * true_range + (1 - self._atr_alpha) * self._atr_values[ativo]
                else:
                    self._atr_values[ativo] = 0.0
                self._atr_prev[ativo] = preco
                row['atr_14'] = self._atr_values[ativo]
                row['atr_14_norm'] = self._atr_values[ativo] / max(preco, 1.0)
            else:
                row['atr_14'] = self._atr_values.get(ativo, 0.0)
                row['atr_14_norm'] = self._atr_values.get(ativo, 0.0) / max(preco, 1.0) if preco > 0 else 0.0
        else:
            row['atr_14'] = 0.0
            row['atr_14_norm'] = 0.0

        # 7. Features de Regime (v12.0) — calcular se não foram atualizadas
        if ativo in self.regime and 'regime_realiz_vol' not in row:
            row.update(self.regime[ativo].snapshot())

        # v11.11 + P0-A29 (v15.24): extracao com CONTRATO de cobertura.
        # Ausente != invalida != zero legitimo:
        #   - feature obrigatoria ausente          -> fail-safe (0.5 neutro)
        #   - feature presente mas nao numerica    -> fail-safe (idem)
        #   - feature opcional ausente com default -> default documentado
        #   - zero legitimo (presente)             -> zero
        # Nunca fabricar 0.0 para feature ausente (informacao falsa p/ o
        # modelo). Motivo registrado em self.ultimo_error, distingui
        # AUSENTE/INVALIDA/SEM_DEFAULT, e o log e throttled por assinatura.
        problemas = None
        if self.manifest:
            _vals, problemas = self.manifest.montar_vetor(row)
            if not problemas:
                vals = _vals
        else:
            # Fallback sem manifest: TODA feature da lista do .pkl e
            # obrigatoria (mesma semantica do required=True padrao do
            # manifest). ANTES: row.get(c, 0.0) zerava ausente em silencio.
            ausentes = [c for c in self.features if c not in row]
            invalidas = [c for c in self.features if c in row
                         and not _valor_numerico(row[c])]
            if ausentes or invalidas:
                problemas = ([f'AUSENTE:{c}' for c in ausentes]
                             + [f'INVALIDA:{c}' for c in invalidas])
            else:
                vals = [float(row[c]) for c in self.features]
        if problemas:
            motivo = ';'.join(problemas[:10])
            self.fallos += 1
            self.ultimo_fallo_ts = time.time()
            self.ultimo_error = f'COBERTURA: {motivo}'
            # throttling: loga na 1a ocorrencia da assinatura e a cada 200
            if motivo != self._ultimo_erro_cobertura:
                log.error('[MANIFEST] Cobertura incompleta: %s (fallos=%d) — '
                          'sinal neutro (0.5), sem zero fake', motivo, self.fallos)
                self._ultimo_erro_cobertura = motivo
            elif self.fallos % 200 == 0:
                log.error('[MANIFEST] Cobertura incompleta (repetida %dx): %s',
                          self.fallos, motivo)
            # P0-A30: sinaliza MODEL_ERROR — quem consome decide explicitamente
            self.status[ativo] = 'MODEL_ERROR'
            return 0.5  # fail-safe: neutro (prob NAO confiavel)

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
            # P0-A30: sinaliza MODEL_ERROR — prob NAO confiavel
            self.status[ativo] = 'MODEL_ERROR'
            return 0.5
        
        # v12.2: ECE fallback — se ECE alto, retorna probabilidade neutra
        if hasattr(self, '_ece') and self._ece > 0.15:
            log.warning('[ML] ECE alto (%.4f), usando fallback neutro', self._ece)
            # P0-A30: status proprio (neutro POLITICO, nao erro de modelo)
            self.status[ativo] = 'ECE_ALTO'
            return 0.5
        
        self.status[ativo] = 'OK'
        # P0-A30 (v15.25): registra a prob valida aqui tambem (invariante
        # prob/status consistente para qualquer chamador; _consumir apenas
        # reatribui o mesmo valor no caminho de evento).
        self.prob[ativo] = float(p)
        self.ultimo_ok_ts = time.time()
        return p

    def obter_estado(self, ativo):
        """P0-A30 (v15.25): (probabilidade | None, status).

        Fonte de verdade para quem consome o ML:
          - status 'MODEL_ERROR' -> prob = None (a inferencia FALHOU; o 0.5
            guardado em prob[] e de fallback e NAO pode ser tratado como
            probabilidade valida);
          - demais status -> prob numerica (0.5 pode ser neutro legitimo
            quando 'OK', ou neutro POLITICO quando 'ECE_ALTO').
        """
        st = self.status.get(ativo, 'NAO_INFERIDO')
        if st == 'MODEL_ERROR':
            return None, st
        p = self.prob.get(ativo)
        if p is None or not isinstance(p, (int, float)) or math.isnan(p):
            return 0.5, st
        return float(p), st

    def get_raw_signal(self, ativo):
        """Retorna apenas a probabilidade bruta para o RiskManager (legacy).

        P0-A30: NAO distingue erro de neutro legitimo — prefira
        obter_estado(ativo) que retorna None quando a inferencia falhou.
        """
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
        
        # v12.0: Estado do regime
        regime_estado = {}
        for a, rt in self.regime.items():
            regime_estado[a] = rt.snapshot()
        
        return {
            'ativos': list(self.prob.keys()),
            'prob': dict(self.prob),
            # P0-A30 (v15.25): status por ativo para monitoramento — 0.5 de
            # fallback (MODEL_ERROR) nao e probabilidade valida
            'status': dict(self.status),
            'fallos': self.fallos,
            'ultimo_fallo_ts': self.ultimo_fallo_ts,
            'ultimo_ok_ts': self.ultimo_ok_ts,
            'ultimo_error': self.ultimo_error,
            'n_features_modelo': len(self.features),
            'top_features': self.importancias,
            'features_modelo': list(self.features),
            'vwap_estado': vwap_estado,
            'ajuste_anterior_oficial': dict(self.ajuste_anterior_oficial),
            'regime_estado': regime_estado,
        }
