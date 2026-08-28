# -*- coding: utf-8 -*-
"""
features/patterns.py — PadroesMemoria (spoof, stop-hunt, absorção).

Aprende padrões repetitivos ao longo do dia e entre sessões.
- Spoof: corretora coloca volume grande e retira rápido sem executar
- Stop-hunt: rompimento de extremo recente seguido de reversão rápida
- Absorvedor: corretora defendendo nível persistentemente
- Perfil horário: agressividade por corretora por hora do dia
"""

import time
import threading
import json
from collections import deque, defaultdict
from datetime import date, datetime
from pathlib import Path


class PadroesMemoria:
    """Aprende padrões repetitivos ao longo do dia e entre sessões."""

    def __init__(self, base_dir, config=None):
        self.base_dir = base_dir
        self.config = config or {}
        self.lock = threading.Lock()

        self.perfil = defaultdict(lambda: {
            'spoofs': 0, 'absorcoes': 0, 'stop_hunts': 0,
            'liderancas_varejo': 0, 'liderancas_inst': 0,
            'ultima_spoof': 0, 'ultima_abs': 0,
            'horas_ativas': defaultdict(float),
            'consistencia_padrao': 0.0,
        })
        self.niveis_stop = {}
        self.hunts_recentes = deque(maxlen=200)
        self.extremos_preco = deque(maxlen=300)
        self._book_anterior = {}
        self._breakout = {}
        self.ultima_atualizacao = time.time()
        self._carregar()

    def _carregar(self):
        p = Path(self.base_dir) / 'padroes_memoria.json'
        if not p.exists():
            return
        try:
            import logging
            log = logging.getLogger(__name__)
            st = json.loads(p.read_text(encoding='utf-8'))
            for b, dados in st.get('perfil', {}).items():
                self.perfil[b].update({
                    k: v for k, v in dados.items()
                    if k in ('spoofs', 'absorcoes', 'stop_hunts',
                             'liderancas_varejo', 'liderancas_inst',
                             'consistencia_padrao')
                })
                if isinstance(dados.get('horas_ativas'), dict):
                    self.perfil[b]['horas_ativas'] = defaultdict(
                        float, {int(h): float(v) for h, v in dados['horas_ativas'].items()})
            if st.get('data') == date.today().isoformat():
                self.niveis_stop = st.get('niveis_stop', {})
            log.info(f"[PADROES] carregado: {len(self.perfil)} corretoras, "
                     f"{len(self.niveis_stop)} níveis de stop")
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[PADROES] falha ao carregar: {e}")

    def salvar(self):
        import logging
        log = logging.getLogger(__name__)
        with self.lock:
            try:
                out = Path(self.base_dir)
                out.mkdir(parents=True, exist_ok=True)
                st = {
                    'perfil': {b: {
                        'spoofs': d['spoofs'], 'absorcoes': d['absorcoes'],
                        'stop_hunts': d['stop_hunts'],
                        'liderancas_varejo': d['liderancas_varejo'],
                        'liderancas_inst': d['liderancas_inst'],
                        'consistencia_padrao': d['consistencia_padrao'],
                        'horas_ativas': dict(d['horas_ativas']),
                    } for b, d in self.perfil.items()},
                    'niveis_stop': self.niveis_stop,
                    'data': date.today().isoformat(),
                    'salvo_em': datetime.now().isoformat(timespec='seconds'),
                }
                (out / 'padroes_memoria.json').write_text(
                    json.dumps(st, ensure_ascii=False, indent=1), encoding='utf-8')
            except Exception as e:
                log.warning(f"[PADROES] falha ao salvar: {e}")

    def aplicar_decay(self):
        agora = time.time()
        dt_horas = (agora - self.ultima_atualizacao) / 3600
        if dt_horas < 0.1:
            return
        self.ultima_atualizacao = agora
        decay_horas = self.config.get('padroes_decay_horas', 0.9)
        with self.lock:
            fator = decay_horas ** dt_horas
            for b in list(self.perfil):
                self.perfil[b]['consistencia_padrao'] *= fator
            for nivel in list(self.niveis_stop):
                self.niveis_stop[nivel]['forca'] *= fator
                if self.niveis_stop[nivel]['forca'] < 0.1:
                    del self.niveis_stop[nivel]

    def detectar_spoof(self, ativo, snap_atual, ts_agora):
        snap_ant = self._book_anterior.get(ativo, {})
        self._book_anterior[ativo] = {
            b: {'bid_vol': s.get('bid_vol_top3', 0), 'ask_vol': s.get('ask_vol_top3', 0), 'ts': ts_agora}
            for b, s in snap_atual.items()
        }
        spoofs_detectados = []
        if not snap_ant:
            return spoofs_detectados
        spoof_vol_min = self.config.get('spoof_vol_min', 100)
        spoof_retirada_pct = self.config.get('spoof_retirada_pct', 0.3)
        for broker, s_atual in snap_atual.items():
            s_ant = snap_ant.get(broker, {'bid_vol': 0, 'ask_vol': 0})
            for lado, campo in (('bid', 'bid_vol'), ('ask', 'ask_vol')):
                vol_ant = s_ant.get(campo, 0)
                vol_atual = s_atual.get(campo, 0)
                if vol_ant > spoof_vol_min and vol_atual < vol_ant * spoof_retirada_pct:
                    with self.lock:
                        p = self.perfil[broker]
                        p['spoofs'] += 1
                        p['ultima_spoof'] = ts_agora
                        p['consistencia_padrao'] = 0.7 * p['consistencia_padrao'] + 0.3 * 1.0
                        spoofs_detectados.append({
                            'broker': broker, 'lado': lado,
                            'vol_retirada': vol_ant - vol_atual,
                            'spoofs_total': p['spoofs']
                        })
        return spoofs_detectados

    def registrar_extremo(self, ativo, preco, ts_agora):
        self.extremos_preco.append((ts_agora, preco))
        while self.extremos_preco and ts_agora - self.extremos_preco[0][0] > 900:
            self.extremos_preco.popleft()

    def detectar_stop_hunt(self, ativo, preco, aggr_imb, ts_agora, hist_preco):
        if len(hist_preco) < 30:
            return None
        antigos = [p for t, p in self.extremos_preco if t < ts_agora]
        if len(antigos) < 10:
            return None
        topo = max(antigos)
        fundo = min(antigos)
        pend = self._breakout.get(ativo)
        janela_s = self.config.get('stop_hunt_janela_s', 30)
        if pend is None or ts_agora - pend['ts'] > janela_s:
            if preco > topo and aggr_imb > 0.3:
                self._breakout[ativo] = {'tipo': 'topo', 'nivel': topo,
                                         'preco_break': preco, 'ts': ts_agora}
                return None
            if preco < fundo and aggr_imb < -0.3:
                self._breakout[ativo] = {'tipo': 'fundo', 'nivel': fundo,
                                         'preco_break': preco, 'ts': ts_agora}
                return None
            return None
        lim = self.config.get('stop_hunt_reversao_pts', 10)
        if pend['tipo'] == 'topo' and preco <= pend['preco_break'] - lim:
            self._breakout.pop(ativo, None)
            nivel = int(round(pend['nivel'] / 5) * 5)
            self._registrar_stop_hunt(nivel, 'topo', preco, ts_agora)
            return {'nivel': nivel, 'tipo': 'topo', 'preco_hunt': preco}
        if pend['tipo'] == 'fundo' and preco >= pend['preco_break'] + lim:
            self._breakout.pop(ativo, None)
            nivel = int(round(pend['nivel'] / 5) * 5)
            self._registrar_stop_hunt(nivel, 'fundo', preco, ts_agora)
            return {'nivel': nivel, 'tipo': 'fundo', 'preco_hunt': preco}
        return None

    def _registrar_stop_hunt(self, nivel, tipo, preco, ts_agora):
        import logging
        log = logging.getLogger(__name__)
        with self.lock:
            if nivel not in self.niveis_stop:
                self.niveis_stop[nivel] = {
                    'tipo': tipo, 'vezes_testado': 0, 'reverteu': 0,
                    'ultimo_teste': ts_agora, 'forca': 0.5
                }
            n = self.niveis_stop[nivel]
            n['vezes_testado'] += 1
            n['reverteu'] += 1
            n['ultimo_teste'] = ts_agora
            n['forca'] = min(1.0, n['forca'] + 0.2)
            self.hunts_recentes.append({
                'ts': ts_agora, 'nivel': nivel, 'tipo': tipo, 'preco': preco
            })
            log.info(f"[PADROES] stop-hunt: {tipo} @ {nivel} "
                     f"(testado {n['vezes_testado']}x, força {n['forca']:.2f})")

    def assinatura_liquidez(self, broker):
        with self.lock:
            p = self.perfil.get(broker)
            if not p:
                return 0.0
            score = min(1.0, (p['spoofs'] / 10) * 0.5 + p['consistencia_padrao'] * 0.5)
            return score

    def corretora_no_horario(self, broker, hora):
        with self.lock:
            p = self.perfil.get(broker)
            if not p:
                return 0.0
            vol_hora = p['horas_ativas'].get(hora, 0.0)
            vol_total = sum(p['horas_ativas'].values()) or 1.0
            media = vol_total / 8
            return vol_hora / media if media > 0 else 0.0

    def registrar_agressao(self, broker, qtd, lado, ts_agora):
        hora = datetime.fromtimestamp(ts_agora).hour
        with self.lock:
            p = self.perfil[broker]
            delta = qtd if lado == 'C' else -qtd
            p['horas_ativas'][hora] += abs(delta)
            from .utils import classificar_corretora
            if classificar_corretora(broker) == 'varejo':
                p['liderancas_varejo'] += 1
            else:
                p['liderancas_inst'] += 1

    def nivel_stop_perto(self, preco, tolerancia_pts=15):
        nivel_arred = int(round(preco / 5) * 5)
        with self.lock:
            for delta in range(-tolerancia_pts, tolerancia_pts + 1, 5):
                n = nivel_arred + delta
                if n in self.niveis_stop:
                    dados = self.niveis_stop[n]
                    if dados['forca'] > 0.3:
                        return {'nivel': n, **dados}
        return None

    def get_resumo(self):
        with self.lock:
            top_spoof = sorted(
                [(b, d['spoofs'], d['consistencia_padrao'])
                 for b, d in self.perfil.items() if d['spoofs'] > 0],
                key=lambda x: -x[1])[:10]
            return {
                'top_spoofers': [{'broker': b, 'spoofs': s, 'conf': round(c, 2)}
                                 for b, s, c in top_spoof],
                'niveis_stop': [
                    {'nivel': n, **d} for n, d in self.niveis_stop.items()
                    if d['forca'] > 0.3
                ],
                'hunts_ultimos_10min': [
                    h for h in self.hunts_recentes
                    if time.time() - h['ts'] < 600
                ],
                'total_corretoras_perfil': len(self.perfil),
            }
