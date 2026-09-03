# -*- coding: utf-8 -*-
"""
core/risk_engine.py — Risk Engine v2 (Fase 12).

14 mecanismos de proteção:
1.  Daily Loss Limit       - Stop diário de perda
2.  Max Exposure           - Exposição máxima agregada
3.  Max Position           - Tamanho máximo da posição
4.  Max Trades             - Número máximo de trades/dia
5.  Cooldown               - Intervalo mínimo entre trades
6.  Consecutive Loss       - Proteção contra sequência de perdas
7.  Stale Data             - Dados obsoletos (RTD desconectado)
8.  Spread Protection      - Spread muito largo
9.  Volatility Protection  - Volatilidade extrema
10. Model Unavailable      - Modelo ML indisponível
11. Confidence Protection  - Confiança abaixo do mínimo
12. Session Protection     - Fora do horário de operação
13. Kill Switch            - Trava manual de emergência
14. Circuit Breaker        - Breaker proporcional

Toda decisão gera RiskDecision com:
- allowed, reason, size, tp, sl, risk_score
"""

import time
import logging
from datetime import datetime, time as dt_time
from typing import Optional, Dict, Any, List
from core.contracts import Signal, RiskDecision

log = logging.getLogger('risk_engine')


class RiskEngine:
    """Risk Engine completo com 14 proteções."""
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        
        # ============================================================
        # LIMITES CONFIGURÁVEIS (lidos do config.json)
        # ============================================================
        tc = self.config.get('trading', {})
        cb = self.config.get('circuit_breaker', {})
        ps = self.config.get('position_sizing', {})
        
        # 1. Daily Loss Limit
        self.max_drawdown_dia = cb.get('nivel3_pnl', -500)
        
        # 2. Max Exposure (por ativo, em R$)
        default_exposure = {'WIN': 200000, 'IND': 2000000, 'WDO': 200000000, 'DOL': 5000000000}
        cfg_exp = self.config.get('max_exposure_brl', default_exposure)
        if isinstance(cfg_exp, dict):
            self.max_exposure_brl = {**default_exposure, **cfg_exp}
        else:
            self.max_exposure_brl = {k: cfg_exp for k in default_exposure}
        
        # 2b. Lotes mínimos por ativo
        default_lotes = {'WIN': 1, 'IND': 5, 'WDO': 1, 'DOL': 5}
        cfg_lotes = self.config.get('lotes_minimos', default_lotes)
        self.lotes_minimos = {**default_lotes, **cfg_lotes} if isinstance(cfg_lotes, dict) else default_lotes
        
        # 2c. Valor do tick por ativo (R$ por ponto)
        default_ticks = {'WIN': 0.20, 'IND': 1.00, 'WDO': 5.00, 'DOL': 125.00}
        cfg_ticks = self.config.get('tick_values', default_ticks)
        self.tick_values = {**default_ticks, **cfg_ticks} if isinstance(cfg_ticks, dict) else default_ticks
        
        # 2d. Fórmula nominal por ativo
        # divisor: RTD entrega DOL/WDO em pontos (5123 = R$5,123), dividir por 1000
        default_nominal = {
            'WIN': {'tipo': 'pontos', 'multiplier': 0.20, 'divisor': 1},
            'IND': {'tipo': 'pontos', 'multiplier': 5, 'divisor': 1},
            'WDO': {'tipo': 'dolar', 'nominal_usd': 10000, 'divisor': 1000},
            'DOL': {'tipo': 'dolar', 'nominal_usd': 250000, 'divisor': 1000},
        }
        cfg_nominal = self.config.get('nominal_formula', default_nominal)
        self.nominal_formula = {**default_nominal, **cfg_nominal} if isinstance(cfg_nominal, dict) else default_nominal
        
        # 3. Max Position
        self.max_position_size = ps.get('max_position_size', 5)
        
        # 4. Max Trades
        self.max_trades_dia = tc.get('max_trades_dia', 15)
        
        # 5. Cooldown
        self.cooldown_s = tc.get('cooldown_entre_trades_s', 45)
        
        # 6. Consecutive Loss
        self.max_perdas_consecutivas = cb.get('nivel1_perdas', 3)
        
        # 7. Stale Data
        self.max_stale_s = self.config.get('max_stale_data_s', 30)
        
        # 8. Spread Protection
        self.max_spread_pts = self.config.get('max_spread_pts', {'WIN': 30, 'WDO': 3})
        
        # 9. Volatility Protection
        self.max_vol_bps = self.config.get('max_volatility_bps', 100)
        
        # 10. Model Unavailable (tolerância)
        self.tolerancia_sem_ml_s = self.config.get('tolerancia_sem_ml_s', 300)
        
        # 11. Confidence Protection
        self.min_confianca = tc.get('limiar_confirmacao', 0.50)
        
        # 12. Session Protection (lido do config.json)
        horarios = self.config.get('horarios', {})
        abertura = horarios.get('abertura_fim', [10, 0])
        fechamento = horarios.get('fechamento', [16, 30])
        almoco_ini = horarios.get('almoco_inicio', [12, 0])
        almoco_fim = horarios.get('almoco_fim', [13, 30])
        self.hora_abre = f'{abertura[0]:02d}:{abertura[1]:02d}'
        self.hora_fecha = f'{fechamento[0]:02d}:{fechamento[1]:02d}'
        self.hora_almoco_ini = f'{almoco_ini[0]:02d}:{almoco_ini[1]:02d}'
        self.hora_almoco_fim = f'{almoco_fim[0]:02d}:{almoco_fim[1]:02d}'
        
        # 13. Kill Switch
        self.kill_switch_ativo = False
        
        # 14. Circuit Breaker
        self.circuit_breaker_nivel = 0  # 0=normal, 1=cauta, 2=forte, 3=bloqueado
        self.cb_nivel1_pnl = cb.get('nivel1_pnl', -100)
        self.cb_nivel2_pnl = cb.get('nivel2_pnl', -300)
        self.cb_nivel3_pnl = cb.get('nivel3_pnl', -500)
        self.cb_nivel1_perdas = cb.get('nivel1_perdas', 3)
        self.cb_nivel2_perdas = cb.get('nivel2_perdas', 5)
        self.cb_nivel3_perdas = cb.get('nivel3_perdas', 7)
        self.cb_recovery_s = cb.get('recovery_s', 1800)
        self._cb_ultimo_reset = 0
        
        # ============================================================
        # ESTADO
        # ============================================================
        self.pnl_dia = 0.0
        self.lucro_acumulado = 0.0
        self.trades_dia = 0
        self.perdas_consecutivas = 0
        self.ultimo_trade_ts = 0.0
        self.slippage_total = 0.0
        
        # Exposição atual
        self.exposure_atual = 0.0  # soma de |tp + sl| das posições abertas
        
        # Dados de mercado (atualizados externamente)
        self._ultimo_preco_ts = 0.0
        self._spread_atual = 0.0
        self._vol_bps = 0.0
        self._ml_disponivel = False
        self._ml_ultimo_update = 0.0
        self._confianca_ewma = 0.0
        
        # Historico para auditoria
        self.historico_decisoes = []
    
    # ============================================================
    # MÉTODO PRINCIPAL
    # ============================================================
    
    def avaliar(self, signal: Signal, resultados_recentes=None) -> RiskDecision:
        """Avalia sinal e retorna RiskDecision com 14 proteções.
        
        Returns:
            RiskDecision com allowed, reason, size, tp, sl, risk_score
        """
        ativo = signal.symbol
        agora = time.time() * 1000
        
        # Coletar estado de mercado
        mercado = self._coletar_estado_mercado()
        
        # Executar todas as proteções
        protecoes = {}
        
        # 13. Kill Switch (primeiro — mais rápido)
        protecoes['kill_switch'] = self._check_kill_switch()
        if not protecoes['kill_switch']['allowed']:
            return self._criar_decisao(ativo, agora, signal, protecoes, False, 'KILL_SWITCH')
        
        # 15. Sanidade de Alvos (TP/SL dentro de limites)
        protecoes['sanidade_alvos'] = self._check_sanidade_alvos(ativo, signal.tp, signal.sl)
        if not protecoes['sanidade_alvos']['allowed']:
            return self._criar_decisao(ativo, agora, signal, protecoes, False, protecoes['sanidade_alvos']['detail'])
        
        # 14. Circuit Breaker
        protecoes['circuit_breaker'] = self._check_circuit_breaker()
        if not protecoes['circuit_breaker']['allowed']:
            return self._criar_decisao(ativo, agora, signal, protecoes, False, 'CIRCUIT_BREAKER')
        
        # 1. Daily Loss Limit
        protecoes['daily_loss'] = self._check_daily_loss()
        if not protecoes['daily_loss']['allowed']:
            return self._criar_decisao(ativo, agora, signal, protecoes, False, 'DAILY_LOSS_LIMIT')
        
        # 4. Max Trades
        protecoes['max_trades'] = self._check_max_trades()
        if not protecoes['max_trades']['allowed']:
            return self._criar_decisao(ativo, agora, signal, protecoes, False, 'MAX_TRADES')
        
        # 6. Consecutive Loss
        protecoes['consecutive_loss'] = self._check_consecutive_loss()
        if not protecoes['consecutive_loss']['allowed']:
            return self._criar_decisao(ativo, agora, signal, protecoes, False, 'CONSECUTIVE_LOSS')
        
        # 5. Cooldown
        protecoes['cooldown'] = self._check_cooldown()
        if not protecoes['cooldown']['allowed']:
            return self._criar_decisao(ativo, agora, signal, protecoes, False, 'COOLDOWN')
        
        # 12. Session Protection
        protecoes['session'] = self._check_session()
        if not protecoes['session']['allowed']:
            return self._criar_decisao(ativo, agora, signal, protecoes, False, 'FORA_HORARIO')
        
        # 7. Stale Data
        protecoes['stale_data'] = self._check_stale_data()
        if not protecoes['stale_data']['allowed']:
            return self._criar_decisao(ativo, agora, signal, protecoes, False, 'STALE_DATA')
        
        # 8. Spread Protection
        protecoes['spread'] = self._check_spread(ativo)
        if not protecoes['spread']['allowed']:
            return self._criar_decisao(ativo, agora, signal, protecoes, False, 'SPREAD_EXCESSIVO')
        
        # 9. Volatility Protection
        protecoes['volatility'] = self._check_volatility()
        if not protecoes['volatility']['allowed']:
            return self._criar_decisao(ativo, agora, signal, protecoes, False, 'VOLATILIDADE_EXTERMA')
        
        # 11. Confidence Protection
        protecoes['confidence'] = self._check_confidence(signal)
        if not protecoes['confidence']['allowed']:
            return self._criar_decisao(ativo, agora, signal, protecoes, False, 'CONFIANCA_BAIXA')
        
        # 2. Max Exposure
        protecoes['exposure'] = self._check_exposure(signal)
        if not protecoes['exposure']['allowed']:
            return self._criar_decisao(ativo, agora, signal, protecoes, False, 'MAX_EXPOSURE')
        
        # 3. Max Position
        protecoes['position'] = self._check_position(signal)
        
        # 10. Model Unavailable (warning, não bloqueia)
        protecoes['model'] = self._check_model_availability()
        
        # ============================================================
        # APROVADO — Calcular sizing
        # ============================================================
        size = protecoes['position'].get('size', 1)
        tp = signal.tp
        sl = signal.sl
        
        # Risk score normalizado (0-1)
        risk_score = self._calcular_risk_score(protecoes)
        
        # Risk level
        risk_level = 'normal'
        if self.circuit_breaker_nivel >= 1:
            risk_level = 'cauta'
        if self.circuit_breaker_nivel >= 2:
            risk_level = 'bloqueado'
        
        # Se caution mode, reduzir size
        if self.circuit_breaker_nivel == 1:
            size = max(1, size // 2)
        
        decision = self._criar_decisao(
            ativo, agora, signal, protecoes, True, 'OK',
            size=size, tp=tp, sl=sl, risk_score=risk_score,
            risk_level=risk_level
        )
        
        # Registrar
        self.trades_dia += 1
        self.ultimo_trade_ts = time.time()
        
        return decision
    
    def registrar_resultado(self, pnl: float, acertou: bool, ativo: str = ''):
        """Registra resultado de um trade fechado."""
        self.pnl_dia += pnl
        self.lucro_acumulado += pnl
        
        if acertou:
            self.perdas_consecutivas = 0
        else:
            self.perdas_consecutivas += 1
        
        # Atualizar circuit breaker
        self._atualizar_circuit_breaker()
    
    def registrar_execucao(self, ativo: str, sinal_preco: float, exec_preco: float):
        """Registra slippage e ativa circuit breaker se crítico."""
        if sinal_preco <= 0 or exec_preco <= 0:
            return
        
        tick_size = 5.0 if 'WIN' in ativo.upper() or 'IND' in ativo.upper() else 0.5
        slip_pts = abs(exec_preco - sinal_preco)
        slip_ticks = slip_pts / tick_size
        
        self.slippage_total += slip_pts
        max_slip_ticks = self.config.get('max_slippage_ticks', 3)
        
        if slip_ticks > max_slip_ticks:
            log.warning(f"[RISK ENGINE] Slippage: {slip_ticks:.1f} ticks em {ativo}")
            if slip_ticks > max_slip_ticks * 2:
                self.circuit_breaker_nivel = max(self.circuit_breaker_nivel, 3)
                log.error("[RISK ENGINE] Slippage crítico! CB nível 3.")
    
    def atualizar_mercado(self, **kwargs):
        """Atualiza estado de mercado (chamado pelo motor)."""
        if 'preco_ts' in kwargs:
            self._ultimo_preco_ts = kwargs['preco_ts']
        if 'spread' in kwargs:
            self._spread_atual = kwargs['spread']
        if 'vol_bps' in kwargs:
            self._vol_bps = kwargs['vol_bps']
        if 'ml_disponivel' in kwargs:
            self._ml_disponivel = kwargs['ml_disponivel']
            if kwargs['ml_disponivel']:
                self._ml_ultimo_update = time.time()
        if 'confianca' in kwargs:
            self._confianca_ewma = kwargs['confianca']
        if 'exposure' in kwargs:
            self.exposure_atual = kwargs['exposure']
    
    def ativar_kill_switch(self):
        """Ativa kill switch manual."""
        self.kill_switch_ativo = True
        log.critical("[RISK ENGINE] KILL SWITCH ATIVADO")
    
    def desativar_kill_switch(self):
        """Desativa kill switch."""
        self.kill_switch_ativo = False
        log.info("[RISK ENGINE] KILL SWITCH DESATIVADO")
    
    def reset_diario(self):
        """Reset para novo dia."""
        self.pnl_dia = 0.0
        self.trades_dia = 0
        self.perdas_consecutivas = 0
        self.circuit_breaker_nivel = 0
        self.slippage_total = 0.0
        self.exposure_atual = 0.0
        log.info("[RISK ENGINE] Reset diário executado")
    
    def _check_sanidade_alvos(self, ativo: str, tp: float, sl: float) -> Dict:
        """15. Sanidade de Alvos — TP/SL dentro de limites operacionais."""
        if tp <= 0 or sl <= 0:
            return {'allowed': False, 'detail': 'ALVOS_NULOS'}
        
        sym = ativo.upper()
        is_win = 'WIN' in sym or 'IND' in sym
        is_wdo = 'WDO' in sym or 'DOL' in sym
        
        if is_win:
            if not (30 <= tp <= 2500):
                return {'allowed': False, 'detail': 'TP_INSANO_WIN'}
            if not (30 <= sl <= 1500):
                return {'allowed': False, 'detail': 'SL_INSANO_WIN'}
        elif is_wdo:
            if not (1.0 <= tp <= 200.0):
                return {'allowed': False, 'detail': 'TP_INSANO_WDO'}
            if not (1.0 <= sl <= 100.0):
                return {'allowed': False, 'detail': 'SL_INSANO_WDO'}
        
        max_ratio = self.config.get('trading', {}).get('max_sl_tp_ratio', 3.0)
        if sl / tp > max_ratio:
            return {'allowed': False, 'detail': 'RISCO_RETORNO_INSANO'}
        
        return {'allowed': True, 'detail': 'alvos_ok'}
    
    def get_estado(self) -> Dict[str, Any]:
        """Retorna estado atual para dashboard/logging."""
        return {
            'pnl_dia': round(self.pnl_dia, 1),
            'trades_dia': self.trades_dia,
            'perdas_consecutivas': self.perdas_consecutivas,
            'circuit_breaker_nivel': self.circuit_breaker_nivel,
            'kill_switch': self.kill_switch_ativo,
            'exposure_atual': round(self.exposure_atual, 1),
            'cooldown_restante': max(0, self.cooldown_s - (time.time() - self.ultimo_trade_ts)),
        }
    
    # ============================================================
    # 14 PROTEÇÕES
    # ============================================================
    
    def _check_kill_switch(self) -> Dict:
        """13. Kill Switch — trava manual de emergência."""
        return {
            'allowed': not self.kill_switch_ativo,
            'detail': 'kill_switch_ativo' if self.kill_switch_ativo else 'ok'
        }
    
    def _check_circuit_breaker(self) -> Dict:
        """14. Circuit Breaker — breaker proporcional."""
        bloqueado = self.circuit_breaker_nivel >= 3
        return {
            'allowed': not bloqueado,
            'nivel': self.circuit_breaker_nivel,
            'detail': f'nivel={self.circuit_breaker_nivel}' + (' BLOQUEADO' if bloqueado else '')
        }
    
    def _check_daily_loss(self) -> Dict:
        """1. Daily Loss Limit — stop diário."""
        excedido = self.pnl_dia <= self.max_drawdown_dia
        return {
            'allowed': not excedido,
            'pnl_dia': round(self.pnl_dia, 1),
            'limite': self.max_drawdown_dia,
            'detail': f'pnl={self.pnl_dia:.0f} limite={self.max_drawdown_dia}'
        }
    
    def _check_max_trades(self) -> Dict:
        """4. Max Trades — limite diário."""
        excedido = self.trades_dia >= self.max_trades_dia
        return {
            'allowed': not excedido,
            'trades': self.trades_dia,
            'limite': self.max_trades_dia,
            'detail': f'trades={self.trades_dia}/{self.max_trades_dia}'
        }
    
    def _check_consecutive_loss(self) -> Dict:
        """6. Consecutive Loss — proteção contra sequência."""
        excedido = self.perdas_consecutivas >= self.max_perdas_consecutivas
        return {
            'allowed': not excedido,
            'perdas': self.perdas_consecutivas,
            'limite': self.max_perdas_consecutivas,
            'detail': f'perdas_consecutivas={self.perdas_consecutivas}/{self.max_perdas_consecutivas}'
        }
    
    def _check_cooldown(self) -> Dict:
        """5. Cooldown — intervalo entre trades."""
        elapsed = time.time() - self.ultimo_trade_ts
        restante = max(0, self.cooldown_s - elapsed)
        bloqueado = restante > 0
        return {
            'allowed': not bloqueado,
            'cooldown_restante': round(restante, 1),
            'detail': f'restante={restante:.1f}s'
        }
    
    def _check_session(self) -> Dict:
        """12. Session Protection — horário de operação."""
        agora = datetime.now().time()
        
        try:
            h_abre = datetime.strptime(self.hora_abre, '%H:%M').time()
            h_fecha = datetime.strptime(self.hora_fecha, '%H:%M').time()
        except ValueError:
            return {'allowed': True, 'detail': 'config_horarios_invalida'}
        
        if agora < h_abre or agora > h_fecha:
            return {'allowed': False, 'detail': f'fora_horario ({agora})'}
        
        # Almoço (opcional — desabilitar com hora >= 24)
        try:
            h_alm_ini = datetime.strptime(self.hora_almoco_ini, '%H:%M').time()
            h_alm_fim = datetime.strptime(self.hora_almoco_fim, '%H:%M').time()
            if h_alm_ini.hour < 24 and h_alm_fim.hour < 24:
                if h_alm_ini <= agora <= h_alm_fim:
                    return {'allowed': False, 'detail': f'horario_almoco ({agora})'}
        except ValueError:
            pass  # Almoço desabilitado
        
        return {'allowed': True, 'detail': 'dentro_horario'}
    
    def _check_stale_data(self) -> Dict:
        """7. Stale Data — dados obsoletos."""
        if self._ultimo_preco_ts <= 0:
            return {'allowed': True, 'detail': 'sem_dados_iniciais'}
        
        elapsed = time.time() - (self._ultimo_preco_ts / 1000)
        stale = elapsed > self.max_stale_s
        return {
            'allowed': not stale,
            'elapsed_s': round(elapsed, 1),
            'limite_s': self.max_stale_s,
            'detail': f'stale={elapsed:.0f}s limite={self.max_stale_s}s'
        }
    
    def _check_spread(self, ativo: str) -> Dict:
        """8. Spread Protection — spread muito largo."""
        if self._spread_atual <= 0:
            return {'allowed': True, 'detail': 'sem_dados_spread'}
        
        max_spread = self.max_spread_pts.get('WIN' if 'WIN' in ativo.upper() else 'WDO', 30)
        excedido = self._spread_atual > max_spread
        return {
            'allowed': not excedido,
            'spread': round(self._spread_atual, 1),
            'limite': max_spread,
            'detail': f'spread={self._spread_atual:.1f} limite={max_spread}'
        }
    
    def _check_volatility(self) -> Dict:
        """9. Volatility Protection — volatilidade extrema."""
        if self._vol_bps <= 0:
            return {'allowed': True, 'detail': 'sem_dados_vol'}
        
        excedido = self._vol_bps > self.max_vol_bps
        return {
            'allowed': not excedido,
            'vol_bps': round(self._vol_bps, 2),
            'limite': self.max_vol_bps,
            'detail': f'vol={self._vol_bps:.1f}bps limite={self.max_vol_bps}bps'
        }
    
    def _check_model_availability(self) -> Dict:
        """10. Model Unavailable — modelo ML indisponível (FASE 8 P1).
        
        Usa mlgate/ para avaliação padronizada da disponibilidade do ML.
        """
        from mlgate import MlAvailability, evaluate_gate, PRODUCTION_POLICY, DEVELOPMENT_POLICY
        
        # Determinar se ML está disponível
        if self._ml_disponivel:
            ml_status = MlAvailability.up()
        else:
            elapsed = time.time() - self._ml_ultimo_update
            ml_status = MlAvailability.down(f"ML indisponivel por {elapsed:.0f}s")
        
        # Determinar política baseada no ambiente
        ambiente = self.config.get('environment', 'DEVELOPMENT')
        if ambiente == 'PRODUCTION':
            policy = PRODUCTION_POLICY
        else:
            policy = DEVELOPMENT_POLICY
        
        # Avaliar gate (heuristic_decision: fallback quando ML indisponível)
        decision = evaluate_gate(
            ml_status, policy,
            heuristic_decision=lambda: True  # heurística aprova por padrão
        )
        
        return {
            'allowed': decision.allowed,
            'ml_available': decision.ml_available,
            'ml_unavailable_reason': decision.ml_unavailable_reason,
            'decision_source': decision.decision_source,
            'detail': decision.note,
        }
    
    def _check_confidence(self, signal: Signal) -> Dict:
        """11. Confidence Protection — confiança abaixo do mínimo."""
        abaixo = signal.confianca < self.min_confianca
        return {
            'allowed': not abaixo,
            'confianca': round(signal.confianca, 3),
            'limite': self.min_confianca,
            'detail': f'conf={signal.confianca:.3f} min={self.min_confianca}'
        }
    
    def _calcular_exposure_nominal(self, signal: Signal) -> float:
        """Calcula exposição nominal em R$.
        
        Fórmulas:
        - Índice (WIN/IND): E = multiplier × cotação_ibov / divisor
        - Dólar (WDO/DOL): E = nominal_usd × cotação_dólar / divisor
        
        RTD entrega DOL/WDO em pontos (5123 = R$5,123), divisor=1000.
        """
        n = signal.quantidade
        p = signal.preco_ref
        
        if n <= 0 or p <= 0:
            return 0.0
        
        sym = signal.symbol.replace('V26', '').replace('Q26', '').replace('U26', '')
        formula = self.nominal_formula.get(sym, {'tipo': 'pontos', 'multiplier': 0.20, 'divisor': 1})
        divisor = formula.get('divisor', 1)
        
        if formula['tipo'] == 'dolar':
            return formula['nominal_usd'] * p / divisor
        else:
            return formula['multiplier'] * p / divisor
    
    def _check_exposure(self, signal: Signal) -> Dict:
        """2. Max Exposure — exposição por ativo (E = N * P * V em R$)."""
        exposure_nova = self._calcular_exposure_nominal(signal)
        nova_exposure = self.exposure_atual + exposure_nova
        # Limite por ativo
        sym = signal.symbol.replace('V26', '').replace('Q26', '').replace('U26', '')
        limite = self.max_exposure_brl.get(sym, 100000)
        excedido = nova_exposure > limite
        return {
            'allowed': not excedido,
            'atual': round(self.exposure_atual, 1),
            'nova': round(nova_exposure, 1),
            'limite': limite,
            'detail': f'{sym}: R${nova_exposure:,.0f}/R${limite:,.0f} (N={signal.quantidade}, P={signal.preco_ref:.0f}, V=R${signal.valor_ponto})'
        }
    
    def _check_position(self, signal: Signal) -> Dict:
        """3. Max Position — tamanho máximo."""
        # Calcular size baseado no risco
        target_risk = self.config.get('position_sizing', {}).get('target_risk_per_trade', 100)
        if signal.sl > 0:
            size = max(1, min(self.max_position_size, round(target_risk / signal.sl)))
        else:
            size = 1
        
        return {
            'allowed': True,
            'size': size,
            'max': self.max_position_size,
            'detail': f'size={size} max={self.max_position_size}'
        }
    
    # ============================================================
    # HELPERS
    # ============================================================
    
    def _coletar_estado_mercado(self) -> Dict:
        """Coleta estado de mercado para decisões."""
        return {
            'pnl_dia': self.pnl_dia,
            'trades_dia': self.trades_dia,
            'perdas': self.perdas_consecutivas,
            'cb_nivel': self.circuit_breaker_nivel,
        }
    
    def _calcular_risk_score(self, protecoes: Dict) -> float:
        """Calcula risk score normalizado (0-1, onde 1 = mais arriscado)."""
        score = 0.0
        
        # CB contribui mais
        score += self.circuit_breaker_nivel * 0.25
        
        # PnL contribui
        if self.max_drawdown_dia < 0:
            score += min(0.3, abs(self.pnl_dia / self.max_drawdown_dia) * 0.3)
        
        # Perdas consecutivas
        score += min(0.2, self.perdas_consecutivas / self.max_perdas_consecutivas * 0.2)
        
        # Trades hoje
        score += min(0.15, self.trades_dia / self.max_trades_dia * 0.15)
        
        # Confiança baixa = risco alto
        if self._confianca_ewma < self.min_confianca:
            score += 0.1
        
        return min(1.0, score)
    
    def _criar_decisao(self, ativo, ts_ms, signal, protecoes, allowed, motivo,
                       size=0, tp=0, sl=0, risk_score=0, risk_level='normal') -> RiskDecision:
        """Cria RiskDecision padronizada."""
        decision = RiskDecision(
            symbol=ativo,
            timestamp_ms=int(ts_ms),
            permitido=allowed,
            motivo=motivo,
            size=size,
            tp=tp or signal.tp,
            sl=sl or signal.sl,
            risk_score=round(risk_score, 3),
            risk_level=risk_level,
            risk_components=protecoes,
        )
        
        # Log
        if allowed:
            log.info(f"[RISK ENGINE] APROVADO {ativo} size={size} tp={tp} sl={sl} "
                     f"risk={risk_score:.2f} nivel={risk_level}")
        else:
            log.warning(f"[RISK ENGINE] BLOQUEADO {ativo} motivo={motivo}")
        
        # Historico
        self.historico_decisoes.append({
            'ts_ms': ts_ms,
            'ativo': ativo,
            'allowed': allowed,
            'motivo': motivo,
            'risk_score': risk_score,
        })
        
        return decision
    
    def _atualizar_circuit_breaker(self):
        """Atualiza circuit breaker baseado no PnL e perdas."""
        agora = time.time()
        
        # Auto-recovery após cooldown
        if self.circuit_breaker_nivel > 0 and self.circuit_breaker_nivel < 3:
            if agora - self._cb_ultimo_reset > self.cb_recovery_s:
                self.circuit_breaker_nivel = max(0, self.circuit_breaker_nivel - 1)
                self._cb_ultimo_reset = agora
                log.info(f"[RISK ENGINE] CB recovery: nivel={self.circuit_breaker_nivel}")
        
        # Determinar nível
        novo_nivel = 0
        
        if self.perdas_consecutivas >= self.cb_nivel3_perdas or \
           self.pnl_dia <= self.cb_nivel3_pnl:
            novo_nivel = 3  # Bloqueado
        elif self.perdas_consecutivas >= self.cb_nivel2_perdas or \
             self.pnl_dia <= self.cb_nivel2_pnl:
            novo_nivel = 2  # Forte
        elif self.perdas_consecutivas >= self.cb_nivel1_perdas or \
             self.pnl_dia <= self.cb_nivel1_pnl:
            novo_nivel = 1  # Cauta
        
        if novo_nivel > self.circuit_breaker_nivel:
            self.circuit_breaker_nivel = novo_nivel
            self._cb_ultimo_reset = agora
            log.warning(f"[RISK ENGINE] CB ativado: nivel={novo_nivel} "
                       f"(perdas={self.perdas_consecutivas} pnl={self.pnl_dia:.0f})")
