# -*- coding: utf-8 -*-
"""
features/institutional_context.py — Contexto Institucional.

Features que capturam o comportamento do preço em relação a níveis
de referência institucional: VWAP, abertura, máxima, mínima, ajuste.

Insights de mercado:
- Preço tende a reagir (bounce/rejeição) perto de VWAP
- Preço tende a acelerar após romper máxima/mínima do dia
- Preço tende a voltar para a abertura (mean reversion)
- Preço tende a gravitar em direção ao ajuste anterior
- Zonas perto dos níveis são mais voláteis
"""

import numpy as np
from .utils import ewma_update


class InstitutionalContext:
    """Features de contexto institucional: distância e comportamento
    em relação a VWAP, abertura, máxima e mínima do dia."""
    
    def __init__(self):
        # Estado por ativo
        self._state = {}
        self._ajuste_oficial = {}  # contrato -> valor do ajuste anterior
    
    def _get_state(self, ativo):
        if ativo not in self._state:
            self._state[ativo] = {
                'vwap': 0.0,
                'vwap_pv': 0.0,
                'vwap_vol': 0.0,
                'abertura': 0.0,
                'maxima': 0.0,
                'minima': 0.0,
                'ajuste': 0.0,
                'preco_anterior': 0.0,
                'dist_vwap_anterior': 0.0,
                'dist_ajuste_anterior': 0.0,
                'bounces_vwap': 0,
                'bounces_ajuste': 0,
                'breaks_max': 0,
                'breaks_min': 0,
                'returns_to_open': 0,
                'last_direction': 0,  # 1=up, -1=down, 0=flat
            }
        return self._state[ativo]
    
    def set_ajuste(self, ativo, valor):
        """Define o ajuste oficial (settlement) do dia anterior."""
        s = self._get_state(ativo)
        s['ajuste'] = valor
        self._ajuste_oficial[ativo] = valor
    
    def update(self, ativo, preco, vol, ohlc=None):
        """Atualiza estado com novo trade.
        
        Args:
            ativo: Símbolo (ex: 'WINV26')
            preco: Preço atual
            vol: Volume do trade
            ohlc: dict com 'abertura', 'maxima', 'minima' do dia (opcional)
        """
        s = self._get_state(ativo)
        
        # Atualizar VWAP
        if preco > 0 and vol > 0:
            s['vwap_pv'] += preco * vol
            s['vwap_vol'] += vol
            if s['vwap_vol'] > 0:
                s['vwap'] = s['vwap_pv'] / s['vwap_vol']
        
        # Atualizar OHLC
        if ohlc:
            if ohlc.get('abertura', 0) > 0:
                s['abertura'] = ohlc['abertura']
            if ohlc.get('maxima', 0) > 0:
                s['maxima'] = ohlc['maxima']
            if ohlc.get('minima', 0) > 0:
                s['minima'] = ohlc['minima']
        
        # Detectar comportamento perto dos níveis
        if s['preco_anterior'] > 0 and preco > 0:
            dist_vwap = preco - s['vwap'] if s['vwap'] > 0 else 0
            dist_vwap_ant = s['dist_vwap_anterior']
            
            # Bounce no VWAP (cruzou e voltou)
            if dist_vwap_ant != 0 and dist_vwap != 0:
                if (dist_vwap_ant > 0 and dist_vwap < 0) or \
                   (dist_vwap_ant < 0 and dist_vwap > 0):
                    s['bounces_vwap'] += 1
            
            # Bounce no ajuste
            if s['ajuste'] > 0:
                dist_adj = preco - s['ajuste']
                dist_adj_ant = s['dist_ajuste_anterior']
                if dist_adj_ant != 0 and dist_adj != 0:
                    if (dist_adj_ant > 0 and dist_adj < 0) or \
                       (dist_adj_ant < 0 and dist_adj > 0):
                        s['bounces_ajuste'] += 1
                s['dist_ajuste_anterior'] = dist_adj
            
            # Break da máxima
            if s['maxima'] > 0:
                if preco >= s['maxima'] and s['preco_anterior'] < s['maxima']:
                    s['breaks_max'] += 1
            
            # Break da mínima
            if s['minima'] > 0:
                if preco <= s['minima'] and s['preco_anterior'] > s['minima']:
                    s['breaks_min'] += 1
            
            # Volta para abertura
            if s['abertura'] > 0:
                dist_abertura = preco - s['abertura']
                dist_abertura_ant = s['preco_anterior'] - s['abertura']
                if dist_abertura_ant != 0 and dist_abertura != 0:
                    if (dist_abertura_ant > 0 and dist_abertura < 0) or \
                       (dist_abertura_ant < 0 and dist_abertura > 0):
                        s['returns_to_open'] += 1
            
            # Direção atual
            if preco > s['preco_anterior']:
                s['last_direction'] = 1
            elif preco < s['preco_anterior']:
                s['last_direction'] = -1
            else:
                s['last_direction'] = 0
            
            s['dist_vwap_anterior'] = dist_vwap
        
        s['preco_anterior'] = preco
    
    def compute(self, ativo, preco):
        """Computa features de contexto institucional.
        
        Returns:
            dict com features prontas para o modelo
        """
        s = self._get_state(ativo)
        
        features = {}
        
        # Valores brutos (para dashboard e ML)
        features['vwap'] = round(s['vwap'], 1) if s['vwap'] > 0 else 0.0
        features['ajuste_oficial'] = s['ajuste'] if s['ajuste'] > 0 else None
        
        # Distâncias absolutas
        features['dist_vwap_pts'] = round(preco - s['vwap'], 1) if s['vwap'] > 0 else 0.0
        features['dist_abertura_pts'] = round(preco - s['abertura'], 1) if s['abertura'] > 0 else 0.0
        features['dist_maxima_pts'] = round(s['maxima'] - preco, 1) if s['maxima'] > 0 else 0.0
        features['dist_minima_pts'] = round(preco - s['minima'], 1) if s['minima'] > 0 else 0.0
        features['dist_ajuste_pts'] = round(preco - s['ajuste'], 1) if s['ajuste'] > 0 else 0.0
        
        # Distâncias normalizadas (em ticks de 5 pontos)
        tick = 5.0
        features['dist_vwap_norm'] = round(features['dist_vwap_pts'] / tick, 2)
        features['dist_abertura_norm'] = round(features['dist_abertura_pts'] / tick, 2)
        features['dist_maxima_norm'] = round(features['dist_maxima_pts'] / tick, 2)
        features['dist_minima_norm'] = round(features['dist_minima_pts'] / tick, 2)
        features['dist_ajuste_norm'] = round(features['dist_ajuste_pts'] / tick, 2)
        
        # Zonas (0=far, 1=near, 2=at)
        zone_threshold = 20.0  # 20 pontos = 4 ticks
        features['zona_vwap'] = self._compute_zone(features['dist_vwap_pts'], zone_threshold)
        features['zona_abertura'] = self._compute_zone(features['dist_abertura_pts'], zone_threshold)
        features['zona_maxima'] = self._compute_zone(features['dist_maxima_pts'], zone_threshold)
        features['zona_minima'] = self._compute_zone(features['dist_minima_pts'], zone_threshold)
        features['zona_ajuste'] = self._compute_zone(features['dist_ajuste_pts'], zone_threshold)
        
        # Posição relativa (0-1, onde 0=minima, 1=maxima)
        # Nome canônico: posicao_range_dia (padrão batch v950)
        # Alias: posicao_relativa (compatibilidade com código legado)
        if s['maxima'] > s['minima'] and s['maxima'] > 0:
            pos_range = round(
                (preco - s['minima']) / (s['maxima'] - s['minima']), 3
            )
            features['posicao_range_dia'] = pos_range
            features['posicao_relativa'] = pos_range  # alias para compatibilidade
        else:
            features['posicao_range_dia'] = 0.5
            features['posicao_relativa'] = 0.5  # alias para compatibilidade
        
        # Amplitude do dia
        features['amplitude_dia_pts'] = round(s['maxima'] - s['minima'], 1) if s['maxima'] > 0 and s['minima'] > 0 else 0.0
        
        # Contadores de comportamento (normalizados)
        features['bounces_vwap_norm'] = min(s['bounces_vwap'] / 10.0, 1.0)
        features['bounces_ajuste_norm'] = min(s['bounces_ajuste'] / 10.0, 1.0)
        features['breaks_max_norm'] = min(s['breaks_max'] / 5.0, 1.0)
        features['breaks_min_norm'] = min(s['breaks_min'] / 5.0, 1.0)
        features['returns_to_open_norm'] = min(s['returns_to_open'] / 10.0, 1.0)
        
        # Sinais de reversão perto de níveis
        features['reversao_perto_vwap'] = 1.0 if abs(features['dist_vwap_pts']) < 15 and s['last_direction'] != 0 else 0.0
        features['reversao_perto_abertura'] = 1.0 if abs(features['dist_abertura_pts']) < 15 and s['last_direction'] != 0 else 0.0
        features['reversao_perto_ajuste'] = 1.0 if abs(features['dist_ajuste_pts']) < 15 and s['last_direction'] != 0 else 0.0
        
        # Momentum pós-break
        features['momento_pos_break_max'] = 1.0 if features['dist_maxima_pts'] < 0 and s['last_direction'] == 1 else 0.0
        features['momento_pos_break_min'] = 1.0 if features['dist_minima_pts'] < 0 and s['last_direction'] == -1 else 0.0
        
        return features
    
    def _compute_zone(self, dist, threshold):
        """Computa zona baseada na distância.
        
        Returns:
            0 = longe, 1 = perto, 2 = no nível
        """
        abs_dist = abs(dist)
        if abs_dist < 5:  # no nível
            return 2
        elif abs_dist < threshold:  # perto
            return 1
        else:  # longe
            return 0
    
    def snapshot(self, ativo):
        """Retorna estado atual para debug/logging."""
        s = self._get_state(ativo)
        return {
            'vwap': s['vwap'],
            'abertura': s['abertura'],
            'maxima': s['maxima'],
            'minima': s['minima'],
            'bounces_vwap': s['bounces_vwap'],
            'breaks_max': s['breaks_max'],
            'breaks_min': s['breaks_min'],
        }
    
    def reset_diario(self):
        """Reset para novo dia de negociação."""
        for ativo in self._state:
            s = self._state[ativo]
            s['vwap_pv'] = 0.0
            s['vwap_vol'] = 0.0
            s['vwap'] = 0.0
            s['abertura'] = 0.0
            s['maxima'] = 0.0
            s['minima'] = 0.0
            s['bounces_vwap'] = 0
            s['bounces_ajuste'] = 0
            s['breaks_max'] = 0
            s['breaks_min'] = 0
            s['returns_to_open'] = 0
