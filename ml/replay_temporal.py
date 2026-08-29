#!/usr/bin/env python3
"""
replay_temporal.py -- Replay temporal de microestrutura.

Reconstrói fielmente o estado do mercado em cada ponto do tempo,
preservando a ordem temporal original. Para cada evento significativo,
registra:
  - Contexto disponível naquele instante (FEATURES EM T)
  - Eventos posteriores (timeline)
  - Resultado observado depois (RESULTADO APÓS T)

Princípio: NÃO permite que o motor veja eventos futuros.
Cada snapshot é puramente uma função do passado.

Saída: eventos com timeline T-Nms..T=0..T+Mms, pronto para
comparação e visualização.

Uso:
  python replay_temporal.py --dia 14 --ativo WINV26
  python replay_temporal.py --periodo 13-14 --ativo WINV26
  python replay_temporal.py --arquivo raw_negocios_ms_20260814_HIST.jsonl
"""
import sys
import os
import json
import argparse
import math
from pathlib import Path
from collections import defaultdict, deque
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ml.features_lib import (
    GeradorJanelas, JanelaFeatures, BookLevelFeatures,
    VolumeProfileTracker, KyleLambdaTracker, VPINTracker, EWMAZScore,
    fase_sessao, dias_ate_vencimento, _tod_de_ts
)

SAVE_DIR = os.environ.get("SINAL_RT_DIR", r"D:\MarketData\mimo")


# ============================================================
#   CAMADA 1: Motor de Replay
# ============================================================

class MotorReplay:
    """Replay cronológico de dados brutos, mantendo estado completo
    a cada passo de relógio (100ms).

    Alimenta GeradorJanelas com eventos na ordem exata em que ocorreram,
    e grava o snapshot completo (T&T features + book + VP + Kyle) a cada
    100ms. Cada snapshot é puramente uma função do passado -- zero leakage.
    """

    def __init__(self, instrumentos, janela_ms=100, passo_ms=100):
        self.instrumentos = instrumentos
        self.gerador = GeradorJanelas(
            instrumentos, janela_ms=janela_ms, passo_ms=passo_ms
        )
        # Timeline completa: ts_ms -> {ativo: snapshot}
        self.timeline = {}  # ts_ms -> {ativo: snapshot_dict}
        # Eventos brutos por timestamp
        self.eventos_brutos = defaultdict(list)  # ts_ms -> [evento_dict]
        # Estado acumulado por ativo (preço médio, CVD, etc.)
        self.estado_acum = {a: {
            'precos': [], 'cvd': 0, 'n_eventos': 0,
            'preco_medio': 0.0, 'ultimo_preco': 0.0,
            'volume_total': 0, 'vol_compra': 0, 'vol_venda': 0,
            'extremo_alta': 0.0, 'extremo_baixa': None,
        } for a in instrumentos}
        self.n_processados = 0
        self.n_snapshots = 0

    def processar_dados(self, negocios):
        """Alimenta lista de negócios (ordena por ts_ms antes de processar).
        Cada negócio: {ts_ms, ativo, preco, qtd, agressor, compradora, vendedora}
        """
        negocios.sort(key=lambda x: x['ts_ms'])
        for neg in negocios:
            ativo = neg['ativo']
            ts = neg['ts_ms']
            preco = neg['preco']
            qtd = neg['qtd']
            agr = neg['agressor']
            comp = neg.get('compradora', '')
            vend = neg.get('vendedora', '')

            # Registrar evento bruto
            self.eventos_brutos[ts].append(neg)

            # Atualizar estado acumulado
            est = self.estado_acum.get(ativo)
            if est and preco > 0:
                est['precos'].append(preco)
                est['ultimo_preco'] = preco
                est['n_eventos'] += 1
                est['volume_total'] += qtd
                if agr == 'Comprador':
                    est['vol_compra'] += qtd
                    est['cvd'] += qtd
                elif agr == 'Vendedor':
                    est['vol_venda'] += qtd
                    est['cvd'] -= qtd
                # v9.13: média ponderada correta — antes o numerador usava só
                # vol_compra (preço médio viesado ~4 nos testes com WDO)
                tot_vol = est['vol_compra'] + est['vol_venda']
                est['preco_medio'] = (
                    (est['preco_medio'] * (tot_vol - qtd) + qtd * preco) / tot_vol
                    if tot_vol > 0 else preco
                )
                if preco > est['extremo_alta']:
                    est['extremo_alta'] = preco
                if est['extremo_baixa'] is None or preco < est['extremo_baixa']:
                    est['extremo_baixa'] = preco

            # Alimentar feature engine
            novos = self.gerador.processar_evento(ativo, ts, preco, qtd, agr, comp, vend)

            # Gravar snapshots emitidos
            for ativo_snap, snap in novos:
                ts_snap = snap['ts_ms']
                if ts_snap not in self.timeline:
                    self.timeline[ts_snap] = {}
                # Enriquecer snapshot com estado derivado
                est_snap = self.estado_acum.get(ativo_snap, {})
                snap['preco_medio'] = est_snap.get('preco_medio', 0.0)
                snap['cvd_acum'] = est_snap.get('cvd', 0)
                snap['n_eventos_dia'] = est_snap.get('n_eventos', 0)
                snap['extremo_alta'] = est_snap.get('extremo_alta', 0.0)
                snap['extremo_baixa'] = est_snap.get('extremo_baixa') or 0.0  # v9.13: nunca vaza None/inf p/ JSON
                self.timeline[ts_snap][ativo_snap] = snap
                self.n_snapshots += 1

            self.n_processados += 1
            if self.n_processados % 50000 == 0:
                print(f'  {self.n_processados:,} eventos -> {self.n_snapshots:,} snapshots', end='\r')

        print(f'  {self.n_processados:,} eventos -> {self.n_snapshots:,} snapshots')

    def obter_snapshot(self, ts_ms, ativo):
        """Retorna o snapshot disponível em ts_ms para o ativo.
        Pode retornar None se não houver snapshot naquele exato timestamp.
        """
        if ts_ms in self.timeline and ativo in self.timeline[ts_ms]:
            return self.timeline[ts_ms][ativo]
        return None

    def obter_estado(self, ativo):
        """Retorna o estado acumulado de um ativo."""
        return self.estado_acum.get(ativo, {})


# ============================================================
#   CAMADA 2: Detector de Eventos
# ============================================================

class DetectorEventos:
    """Detecta eventos significativos usando regras simples.
    NÃO usa ML -- só identifica momentos que merecem análise.

    Tipos de evento detectados:
      - agressao: |aggr_imb| > threshold
      - volume_spike: volume na janela > 3x média
      - preco_move: movimento significativo de preço em 1s
      - fluxo_diverge: CVD diverge do preço (divergência)
      - reversal: mudança de direção do aggr_imb
    """

    def __init__(self, ativo, pctl_aggr=0.90, min_vol_spike=3.0,
                 min_preco_move_pts=20):
        self.ativo = ativo
        self.pctl_aggr = pctl_aggr
        self.min_vol_spike = min_vol_spike
        self.min_preco_move_pts = min_preco_move_pts
        # Histórico para cálculo de thresholds
        self._aggr_hist = deque(maxlen=5000)
        self._vol_hist = deque(maxlen=5000)
        self._precos = deque(maxlen=500)
        self._eventos = []
        self._id_counter = 0

    def analisar_snapshot(self, snap):
        """Analisa um snapshot e retorna lista de eventos detectados."""
        eventos = []
        ts = snap.get('ts_ms', 0)
        aggr = snap.get('aggr_imb', 0.0)
        vol = snap.get('vol_total', 0)
        preco = snap.get('preco_ultimo', 0)
        cvd = snap.get('cvd_acum', 0)
        n_evt = snap.get('n_eventos_janela', 0)

        if preco <= 0:
            return eventos

        # Guardar para cálculo de thresholds
        self._aggr_hist.append(abs(aggr))
        self._vol_hist.append(vol)
        self._precos.append((ts, preco))

        # 1. Agressão forte
        if len(self._aggr_hist) > 100:
            pctl = sorted(self._aggr_hist)[int(len(self._aggr_hist) * self.pctl_aggr)]
            if abs(aggr) > pctl and abs(aggr) > 0.2:
                eventos.append({
                    'tipo': 'agressao',
                    'ts_ms': ts,
                    'intensidade': abs(aggr),
                    'lado': 'compra' if aggr > 0 else 'venda',
                    'detalhe': f'aggr_imb={aggr:.3f} (p90={pctl:.3f})',
                })

        # 2. Spike de volume
        if len(self._vol_hist) > 100:
            vol_medio = sum(self._vol_hist) / len(self._vol_hist)
            if vol_medio > 0 and vol > vol_medio * self.min_vol_spike:
                eventos.append({
                    'tipo': 'volume_spike',
                    'ts_ms': ts,
                    'intensidade': vol / vol_medio if vol_medio > 0 else 0,
                    'lado': 'compra' if aggr > 0 else 'venda',
                    'detalhe': f'vol={vol:.0f} ({vol/vol_medio:.1f}x média)',
                })

        # 3. Movimento de preço
        if len(self._precos) >= 10:
            preco_1s_atras = self._precos[-10][1]  # 10 ticks atrás = 1s
            delta = preco - preco_1s_atras
            if abs(delta) >= self.min_preco_move_pts:
                eventos.append({
                    'tipo': 'preco_move',
                    'ts_ms': ts,
                    'intensidade': abs(delta),
                    'lado': 'alta' if delta > 0 else 'baixa',
                    'detalhe': f'delta={delta:+.0f} pts em 1s',
                })

        # 4. Divergência CVD × preço
        if len(self._precos) >= 50:
            preco_5s_atras = self._precos[-50][1]
            delta_p = preco - preco_5s_atras
            # CVD mudou na mesma direção? Se não, divergência
            if abs(delta_p) > 10:
                eventos.append({
                    'tipo': 'divergencia',
                    'ts_ms': ts,
                    'intensidade': abs(delta_p),
                    'lado': 'bull' if delta_p > 0 else 'bear',
                    'detalhe': f'preco={delta_p:+.0f}, cvd={cvd:.0f}',
                })

        # 5. Reversão (mudança de direção do fluxo)
        if len(self._aggr_hist) >= 3:
            recentes = list(self._aggr_hist)[-3:]
            if recentes[-1] > 0.1 and recentes[-2] < -0.1 and recentes[-3] < -0.1:
                eventos.append({
                    'tipo': 'reversao',
                    'ts_ms': ts,
                    'intensidade': abs(aggr),
                    'lado': 'para_compra',
                    'detalhe': f'reversão bear->bull, aggr={aggr:.3f}',
                })
            elif recentes[-1] < -0.1 and recentes[-2] > 0.1 and recentes[-3] > 0.1:
                eventos.append({
                    'tipo': 'reversao',
                    'ts_ms': ts,
                    'intensidade': abs(aggr),
                    'lado': 'para_venda',
                    'detalhe': f'reversão bull->bear, aggr={aggr:.3f}',
                })

        for e in eventos:
            self._id_counter += 1
            e['id'] = self._id_counter
            e['ativo'] = self.ativo

        self._eventos.extend(eventos)
        return eventos

    def eventos_detectados(self):
        return self._eventos


# ============================================================
#   CAMADA 3: Janela Temporal
# ============================================================

class JanelaTemporal:
    """Extrai janela temporal ao redor de cada evento detectado.

    Para cada evento, recorta N ticks antes e M ticks depois do
    snapshot do motor, registra o que era sabido em cada ponto
    (FEATURES EM T) e o que aconteceu depois (RESULTADO).
    """

    def __init__(self, motor, detector, ativo,
                 ticks_antes=20, ticks_depois=30):
        """
        ticks_antes: quantos snapshots antes do evento (20 × 100ms = 2s)
        ticks_depois: quantos snapshots depois (30 × 100ms = 3s)
        """
        self.motor = motor
        self.detector = detector
        self.ativo = ativo
        self.ticks_antes = ticks_antes
        self.ticks_depois = ticks_depois

    def construir_evento(self, evento_detectado):
        """Constrói objeto EventoReplay para um evento detectado."""
        ts_trigger = evento_detectado['ts_ms']
        ts_list = sorted(self.motor.timeline.keys())

        if not ts_list:
            return None

        # Encontrar índice do trigger na timeline
        idx_trigger = None
        for i, ts in enumerate(ts_list):
            if ts >= ts_trigger:
                idx_trigger = i
                break
        if idx_trigger is None:
            return None

        # Recortar janela
        idx_inicio = max(0, idx_trigger - self.ticks_antes)
        idx_fim = min(len(ts_list), idx_trigger + self.ticks_depois + 1)

        # Construir timeline da janela
        timeline = []
        for i in range(idx_inicio, idx_fim):
            ts = ts_list[i]
            dt_ms = ts - ts_trigger
            snap = self.motor.obter_snapshot(ts, self.ativo)

            # Extrair eventos brutos neste timestamp
            evt_brutos = []
            for e in self.motor.eventos_brutos.get(ts, []):
                if e['ativo'] == self.ativo:
                    evt_brutos.append({
                        'preco': e['preco'],
                        'qtd': e['qtd'],
                        'agressor': e['agressor'],
                        'compradora': e.get('compradora', ''),
                        'vendedora': e.get('vendedora', ''),
                    })

            entry = {
                'dt_ms': dt_ms,
                'ts_ms': ts,
                'trigger': (i == idx_trigger),
                'n_eventos_brutos': len(evt_brutos),
                'eventos_brutos': evt_brutos[:5],  # máx 5 por tick
            }

            # Features disponíveis neste instante (FEATURES EM T)
            if snap:
                entry['features'] = _extrair_features(snap)
            else:
                entry['features'] = None

            timeline.append(entry)

        # Calcular resultado (RESULTADO APÓS T)
        resultado = self._calcular_resultado(ts_list, idx_trigger)

        # Estado no trigger
        snap_trigger = self.motor.obter_snapshot(ts_trigger, self.ativo)
        estado_trigger = self.motor.obter_estado(self.ativo)

        evento_replay = {
            'id': evento_detectado['id'],
            'ativo': self.ativo,
            'ts_ms': ts_trigger,
            'tipo': evento_detectado['tipo'],
            'intensidade': evento_detectado['intensidade'],
            'lado': evento_detectado['lado'],
            'detalhe': evento_detectado['detalhe'],
            # Estado no instante do trigger
            'estado_T': {
                'preco': snap_trigger.get('preco_ultimo', 0) if snap_trigger else 0,
                'preco_medio': snap_trigger.get('preco_medio', 0) if snap_trigger else 0,
                'aggr_imb': snap_trigger.get('aggr_imb', 0) if snap_trigger else 0,
                'cvd': snap_trigger.get('cvd_acum', 0) if snap_trigger else 0,
                'vol_total': snap_trigger.get('vol_total', 0) if snap_trigger else 0,
                'vpin': snap_trigger.get('vpin', 0) if snap_trigger else 0,
                'kyle_lambda': snap_trigger.get('kyle', {}).get('kyle_lambda', 0) if snap_trigger else 0,
                'n_eventos_dia': estado_trigger.get('n_eventos', 0),
                'extremo_alta': estado_trigger.get('extremo_alta', 0),
                'extremo_baixa': estado_trigger.get('extremo_baixa', 0),
            },
            # Timeline: features disponíveis em cada ponto
            'timeline': timeline,
            # Resultado: o que aconteceu depois
            'resultado': resultado,
        }

        return evento_replay

    def _calcular_resultado(self, ts_list, idx_trigger):
        """Calcula o resultado observado após o trigger."""
        resultado = {
            'preco_trigger': 0,
            'preco_1s': None,
            'preco_3s': None,
            'max_alta_3s': 0,
            'max_baixa_3s': 0,
            'n_eventos_3s': 0,
            'volume_3s': 0,
            'aggr_medio_3s': 0,
        }

        ts_trigger = ts_list[idx_trigger]
        snap_trigger = self.motor.obter_snapshot(ts_trigger, self.ativo)
        if not snap_trigger:
            return resultado

        resultado['preco_trigger'] = snap_trigger.get('preco_ultimo', 0)

        # Olhar 3s à frente (30 ticks)
        preco_max = resultado['preco_trigger']
        preco_min = resultado['preco_trigger']
        n_evt = 0
        vol_total = 0
        aggr_sum = 0
        n_aggr = 0

        for i in range(idx_trigger + 1, min(idx_trigger + 31, len(ts_list))):
            ts = ts_list[i]
            snap = self.motor.obter_snapshot(ts, self.ativo)
            if snap:
                preco = snap.get('preco_ultimo', 0)
                if preco > 0:
                    preco_max = max(preco_max, preco)
                    preco_min = min(preco_min, preco)
                aggr = snap.get('aggr_imb', 0)
                vol = snap.get('vol_total', 0)
                n_evt += snap.get('n_eventos_janela', 0)
                vol_total += vol
                if vol > 0:
                    aggr_sum += aggr * vol
                    n_aggr += vol

            dt = ts - ts_trigger
            if dt <= 1000 and resultado['preco_1s'] is None:
                resultado['preco_1s'] = snap.get('preco_ultimo', 0) if snap else None
            if dt <= 3000 and resultado['preco_3s'] is None:
                resultado['preco_3s'] = snap.get('preco_ultimo', 0) if snap else None

        resultado['max_alta_3s'] = preco_max - resultado['preco_trigger']
        resultado['max_baixa_3s'] = resultado['preco_trigger'] - preco_min
        resultado['n_eventos_3s'] = n_evt
        resultado['volume_3s'] = vol_total
        resultado['aggr_medio_3s'] = aggr_sum / n_aggr if n_aggr > 0 else 0

        return resultado


def _extrair_features(snap):
    """Extrai dict de features de um snapshot, limpo para serialização."""
    features = {}
    campos = [
        'preco_ultimo', 'preco_medio', 'aggr_imb', 'vol_compra', 'vol_venda',
        'vol_total', 'ewma_imb_curta', 'ewma_imb_media', 'ewma_imb_longa',
        'hhi_compra', 'hhi_venda', 'entropy_compra', 'entropy_venda',
        'vpin', 'delta_preco_janela', 'cvd_total', 'cvd_acum',
        'cvd_div', 'realized_vol_bps', 'range_vol_bps', 'taxa_eventos',
        'n_eventos_janela', 'n_eventos_dia', 'extremo_alta', 'extremo_baixa',
    ]
    for c in campos:
        if c in snap:
            features[c] = snap[c]

    # Book features (se disponível)
    if 'book' in snap and snap['book']:
        b = snap['book']
        for k in ['spread', 'mid', 'microprice', 'imb_L1', 'imb_L5',
                   'imb_L10', 'imb_L30', 'hhi_book', 'ofi',
                   'micro_drift_ewma', 'imb_ponderado', 'slope_bid', 'slope_ask']:
            if k in b:
                features[f'book_{k}'] = b[k]

    # Volume Profile
    if 'vp' in snap and snap['vp']:
        vp = snap['vp']
        for k in ['poc_dist', 'vah_dist', 'val_dist', 'vp_total']:
            if k in vp:
                features[f'vp_{k}'] = vp[k]

    # Kyle's Lambda
    if 'kyle' in snap and snap['kyle']:
        ky = snap['kyle']
        if 'kyle_lambda' in ky:
            features['kyle_lambda'] = ky['kyle_lambda']

    return features


# ============================================================
#   CAMADA 4: Comparador de Eventos
# ============================================================

class ComparadorEventos:
    """Compara eventos por similaridade nas features normalizadas."""

    @staticmethod
    def vetorizar(evento, campos=None):
        """Extrai vetor numérico de um evento para comparação."""
        if campos is None:
            campos = [
                'preco_ultimo', 'aggr_imb', 'vol_total', 'ewma_imb_longa',
                'vpin', 'cvd_acum', 'realized_vol_bps', 'delta_preco_janela',
            ]
        f = evento.get('estado_T', {})
        return [f.get(c, 0.0) for c in campos]

    @staticmethod
    def similaridade(v1, v2):
        """Distância euclidiana normalizada entre dois vetores."""
        if not v1 or not v2 or len(v1) != len(v2):
            return float('inf')
        soma = 0.0
        for a, b in zip(v1, v2):
            d = a - b
            soma += d * d
        return math.sqrt(soma)

    @staticmethod
    def top_similares(evento_alvo, eventos_base, n=10, campos=None):
        """Encontra os N eventos mais similares ao alvo."""
        v_alvo = ComparadorEventos.vetorizar(evento_alvo, campos)
        resultados = []
        for ev in eventos_base:
            if ev['id'] == evento_alvo['id']:
                continue
            v = ComparadorEventos.vetorizar(ev, campos)
            dist = ComparadorEventos.similaridade(v_alvo, v)
            resultados.append((dist, ev))
        resultados.sort(key=lambda x: x[0])
        return resultados[:n]


# ============================================================
#   CAMADA 5: Visualizador de Texto
# ============================================================

def formatar_evento(evento):
    """Formata evento replay como texto legível."""
    e = evento['estado_T']
    r = evento['resultado']
    linhas = []
    linhas.append(f"\n{'='*60}")
    linhas.append(f"EVENTO #{evento['id']}")
    linhas.append(f"{'='*60}")
    linhas.append(f"  Ativo:     {evento['ativo']}")
    linhas.append(f"  Tipo:      {evento['tipo']} ({evento['lado']})")
    linhas.append(f"  Detalhe:   {evento['detalhe']}")
    linhas.append(f"  Preço:     {e['preco']:.0f}")
    linhas.append(f"  Pr.Médio:  {e['preco_medio']:.0f}")
    if e['preco'] > 0 and e['preco_medio'] > 0:
        dist = e['preco'] - e['preco_medio']
        linhas.append(f"  Distância: {dist:+.0f} pts do preço médio")
    linhas.append(f"  CVD:       {e['cvd']:.0f}")
    linhas.append(f"  VPIN:      {e['vpin']:.4f}")
    linhas.append(f"  Kyle lambda:    {e['kyle_lambda']:.6f}")

    linhas.append(f"\n  {'-'*50}")
    linhas.append(f"  SEQUÊNCIA TEMPORAL:")
    linhas.append(f"  {'-'*50}")

    for entry in evento['timeline']:
        dt = entry['dt_ms']
        if entry['trigger']:
            marker = '  >>> T=0'
            if entry['features']:
                preco = entry['features'].get('preco_ultimo', '?')
                aggr = entry['features'].get('aggr_imb', 0)
                marker += f'  preço={preco}  aggr={aggr:+.3f}'
            linhas.append(marker)
        else:
            prefix = 'T+' if dt > 0 else 'T' if dt == 0 else 'T'
            if dt < 0:
                prefix = f'T{dt}ms'
            else:
                prefix = f'T+{dt}ms'

            n_evt = entry['n_eventos_brutos']
            evt_desc = []
            for ev in entry['eventos_brutos'][:3]:
                agr = 'C' if ev['agressor'] == 'Comprador' else 'V'
                evt_desc.append(f"{agr}:{ev['qtd']:.0f}@{ev['preco']:.0f}")

            if entry['features']:
                aggr = entry['features'].get('aggr_imb', 0)
                vol = entry['features'].get('vol_total', 0)
                linhas.append(
                    f"  {prefix:12s} -> {n_evt} evt: {', '.join(evt_desc)}  "
                    f"[aggr={aggr:+.3f} vol={vol:.0f}]"
                )
            else:
                linhas.append(
                    f"  {prefix:12s} -> {n_evt} evt: {', '.join(evt_desc)}"
                )

    linhas.append(f"\n  {'-'*50}")
    linhas.append(f"  RESULTADO (3s após trigger):")
    linhas.append(f"  {'-'*50}")
    linhas.append(f"  Preço trigger:  {r['preco_trigger']:.0f}")
    linhas.append(f"  Preço em 1s:    {r['preco_1s']:.0f}" if r['preco_1s'] else "  Preço em 1s:    -")
    linhas.append(f"  Preço em 3s:    {r['preco_3s']:.0f}" if r['preco_3s'] else "  Preço em 3s:    -")
    linhas.append(f"  Max alta 3s:    {r['max_alta_3s']:+.0f} pts")
    linhas.append(f"  Max baixa 3s:   {r['max_baixa_3s']:+.0f} pts")
    linhas.append(f"  Volume 3s:      {r['volume_3s']:.0f}")
    linhas.append(f"  Eventos 3s:     {r['n_eventos_3s']}")
    linhas.append(f"  Aggr médio 3s:  {r['aggr_medio_3s']:+.3f}")

    # Classificação do resultado
    net = r['max_alta_3s'] - r['max_baixa_3s']
    if net > 20:
        linhas.append(f"\n  * RESULTADO: ALTA (+{net:.0f} pts)")
    elif net < -20:
        linhas.append(f"\n  * RESULTADO: BAIXA ({net:.0f} pts)")
    else:
        linhas.append(f"\n  * RESULTADO: LATERAL ({net:+.0f} pts)")

    return '\n'.join(linhas)


# ============================================================
#   CARREGAMENTO DE DADOS
# ============================================================

def carregar_negocios_dia(ativo, dia, mes=8, ano=2026, save_dir=SAVE_DIR):
    """Carrega negócios de um dia específico."""
    base = Path(save_dir)
    data_str = f'{ano}{mes:02d}{dia:02d}'
    arquivos = sorted(base.glob(f'raw_negocios_ms_*{data_str}*.jsonl'))

    if not arquivos:
        print(f'Nenhum arquivo encontrado para {data_str}')
        return []

    print(f'  Arquivos: {len(arquivos)}')
    negocios = []
    for arq in arquivos:
        with open(arq, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    neg = json.loads(line)
                    if ativo and neg.get('ativo') != ativo:
                        continue
                    negocios.append(neg)
                except json.JSONDecodeError:
                    continue

    negocios.sort(key=lambda x: x['ts_ms'])
    print(f'  Negócios carregados: {len(negocios):,}')
    return negocios


def carregar_negocios_periodo(ativo, dia_inicio, dia_fim, mes=8, ano=2026,
                              save_dir=SAVE_DIR):
    """Carrega negócios de um período de dias."""
    todos = []
    for dia in range(dia_inicio, dia_fim + 1):
        print(f'\n=== Dia {dia}/{mes}/{ano} ===')
        negs = carregar_negocios_dia(ativo, dia, mes, ano, save_dir)
        todos.extend(negs)
    todos.sort(key=lambda x: x['ts_ms'])
    print(f'\nTotal: {len(todos):,} negócios')
    return todos


# ============================================================
#   MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='Replay Temporal de Microestrutura')
    parser.add_argument('--ativo', default='WINV26', help='Ativo principal')
    parser.add_argument('--dia', type=int, help='Dia do mês')
    parser.add_argument('--periodo', help='Range de dias (ex: 13-14)')
    parser.add_argument('--arquivo', help='Arquivo específico')
    parser.add_argument('--output', help='Caminho de saída')
    parser.add_argument('--max-eventos', type=int, default=50,
                        help='Máximo de eventos para mostrar')
    parser.add_argument('--ticks-antes', type=int, default=20,
                        help='Ticks antes do trigger (default: 20 = 2s)')
    parser.add_argument('--ticks-depois', type=int, default=30,
                        help='Ticks depois do trigger (default: 30 = 3s)')
    parser.add_argument('--mostrar', type=int, default=10,
                        help='Número de eventos para mostrar no terminal')
    parser.add_argument('--salvar', action='store_true',
                        help='Salvar eventos em JSON')
    args = parser.parse_args()

    ativo = args.ativo.upper()

    # 1. Carregar dados
    print(f'\n{"="*60}')
    print(f'REPLAY TEMPORAL -- {ativo}')
    print(f'{"="*60}')

    if args.arquivo:
        print(f'\nCarregando arquivo: {args.arquivo}')
        negocios = []
        with open(args.arquivo, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    neg = json.loads(line)
                    if neg.get('ativo') != ativo:
                        continue
                    negocios.append(neg)
                except json.JSONDecodeError:
                    continue
        negocios.sort(key=lambda x: x['ts_ms'])
        print(f'  Negócios: {len(negocios):,}')
    elif args.periodo:
        inicio, fim = map(int, args.periodo.split('-'))
        negocios = carregar_negocios_periodo(ativo, inicio, fim)
    elif args.dia:
        negocios = carregar_negocios_dia(ativo, args.dia)
    else:
        print('Especifique --dia, --periodo ou --arquivo')
        return

    if not negocios:
        print('Sem dados para processar.')
        return

    # 2. Motor de Replay
    print(f'\n--- Motor de Replay ---')
    motor = MotorReplay([ativo])
    motor.processar_dados(negocios)

    # 3. Detector de Eventos
    print(f'\n--- Detector de Eventos ---')
    detector = DetectorEventos(ativo)
    n_evt_detectados = 0
    for ts in sorted(motor.timeline.keys()):
        snap = motor.obter_snapshot(ts, ativo)
        if snap:
            eventos = detector.analisar_snapshot(snap)
            n_evt_detectados += len(eventos)

    eventos = detector.eventos_detectados()
    print(f'  Eventos detectados: {len(eventos)}')
    print(f'  Por tipo:')
    tipos = defaultdict(int)
    for e in eventos:
        tipos[e['tipo']] += 1
    for tipo, cnt in sorted(tipos.items()):
        print(f'    {tipo}: {cnt}')

    if not eventos:
        print('Nenhum evento detectado.')
        return

    # 4. Construir Replay de cada evento
    print(f'\n--- Construindo Replay ---')
    janela = JanelaTemporal(motor, detector, ativo,
                            ticks_antes=args.ticks_antes,
                            ticks_depois=args.ticks_depois)

    eventos_replay = []
    for evt in eventos[:args.max_eventos]:
        replay = janela.construir_evento(evt)
        if replay:
            eventos_replay.append(replay)

    print(f'  Eventos com replay: {len(eventos_replay)}')

    # 5. Mostrar eventos no terminal
    print(f'\n--- Top {args.mostrar} Eventos ---')
    for replay in eventos_replay[:args.mostrar]:
        print(formatar_evento(replay))

    # 6. Salvar em JSON
    if args.salvar or args.output:
        output = args.output or str(
            Path(SAVE_DIR) / f'replay_{ativo}_eventos.json'
        )
        with open(output, 'w', encoding='utf-8') as f:
            json.dump(eventos_replay, f, ensure_ascii=False, indent=2,
                      default=str)
        print(f'\nSalvo: {output} ({len(eventos_replay)} eventos)')

    # 7. Resumo estatístico
    print(f'\n{"="*60}')
    print(f'RESUMO')
    print(f'{"="*60}')
    print(f'  Ativo: {ativo}')
    print(f'  Negócios processados: {motor.n_processados:,}')
    print(f'  Snapshots gerados: {motor.n_snapshots:,}')
    print(f'  Eventos detectados: {len(eventos)}')
    print(f'  Replay construído: {len(eventos_replay)}')

    # Resultados agregados
    if eventos_replay:
        altas = sum(1 for e in eventos_replay
                    if e['resultado']['max_alta_3s'] > e['resultado']['max_baixa_3s'])
        baixas = sum(1 for e in eventos_replay
                     if e['resultado']['max_baixa_3s'] > e['resultado']['max_alta_3s'])
        laterais = len(eventos_replay) - altas - baixas
        print(f'\n  Resultados (3s):')
        print(f'    Altas:    {altas} ({altas/len(eventos_replay)*100:.1f}%)')
        print(f'    Baixas:   {baixas} ({baixas/len(eventos_replay)*100:.1f}%)')
        print(f'    Laterais: {laterais} ({laterais/len(eventos_replay)*100:.1f}%)')

    print(f'\n{"="*60}')


if __name__ == '__main__':
    main()
