# -*- coding: utf-8 -*-
"""
core/risk_manager.py — Gestão de Risco e Política de Execução.
"""

import logging
from datetime import datetime
from core.contracts import Signal, RiskDecision

log = logging.getLogger('risk')

def custo_execucao(ativo, config=None):
    """Retorna o custo estimado de corretagem+emolumentos em pontos."""
    if not config: config = {}
    custos = config.get('trading', {}).get('custo_execucao', {'WIN': 5.0, 'WDO': 1.0})
    for k, v in custos.items():
        if ativo.upper().startswith(k):
            return v
    return 5.0

def horario_permite_abrir(config=None):
    """Valida se a janela de tempo permite novas entradas.

    v15.35: lê a MESMA fonte de `horarios` do config.json usada pelo
    RiskEngine._check_session() (FASE 6 — unificação de risco). Pregão B3 de
    futuros: 09:00–18:30 contínuo, sem pausa de almoço. Antes o default era
    09:05–17:30 com almoço 12:00–13:00, que bloqueava FORA_HORARIO às 09:56
    mesmo com o mercado aberto.
    """
    if not config: config = {}
    agora = datetime.now().time()

    horarios = config.get('horarios', {})
    h_abre = horarios.get('abertura_fim', [9, 0])
    h_fecha = horarios.get('fechamento', [18, 30])
    h_abre_t = datetime.strptime(f'{h_abre[0]:02d}:{h_abre[1]:02d}', '%H:%M').time()
    h_fecha_t = datetime.strptime(f'{h_fecha[0]:02d}:{h_fecha[1]:02d}', '%H:%M').time()

    if config.get('desligar_horarios_ruins', True):
        if agora < h_abre_t or agora > h_fecha_t:
            return False
        # Pausa opcional no almoço (desabilitada com hora >= 24)
        h_alm_ini = horarios.get('almoco_inicio', [24, 0])
        h_alm_fim = horarios.get('almoco_fim', [24, 0])
        if h_alm_ini[0] < 24 and h_alm_fim[0] < 24:
            h_alm_ini_t = datetime.strptime(f'{h_alm_ini[0]:02d}:{h_alm_ini[1]:02d}', '%H:%M').time()
            h_alm_fim_t = datetime.strptime(f'{h_alm_fim[0]:02d}:{h_alm_fim[1]:02d}', '%H:%M').time()
            if h_alm_ini_t <= agora <= h_alm_fim_t:
                return False
    return True

class RiskManager:
    def __init__(self, config=None):
        self.config = config or {}
        self.threshold = self.config.get('ml_threshold', 0.60)
        self.max_posicao = self.config.get('position_sizing', {}).get('max_position_size', 5)
        self.stop_diario = self.config.get('trading', {}).get('max_drawdown_dia', -1000)
        self.base_size = self.config.get('base_size', 1)
        
        # Estado
        self.lucro_acumulado = 0.0
        self.pnl_dia = 0.0
        self.trades_dia = 0
        self.perdas_consecutivas = 0
        self.circuit_breaker_nivel = 0

        # v10.12 (Fase 9): Slippage Circuit Breaker
        self.slippage_total_pontos = 0.0
        self.trades_com_slippage = 0
        self.max_slippage_ticks = self.config.get('max_slippage_ticks', 3)
        self.circuit_breaker_slippage = False
        self.kill_switch_ativo = False # v10.18: Kill Switch Emergencial

    def pode_abrir(self, signal: Signal, resultados_recentes=None) -> RiskDecision:
        """Gatekeeper central para novas operações."""
        ativo = signal.symbol
        
        def create_decision(permitido: bool, motivo: str, size: int = 0) -> RiskDecision:
            return RiskDecision(
                symbol=ativo,
                timestamp_ms=signal.timestamp_ms,
                permitido=permitido,
                motivo=motivo,
                size=size,
                tp=signal.tp,
                sl=signal.sl,
                risk_score=signal.confianca
            )

        if self.kill_switch_ativo:
            log.critical("[RISK] Operação negada: KILL SWITCH ATIVO.")
            return create_decision(False, "KILL_SWITCH")
            
        if self.circuit_breaker_slippage:
            return create_decision(False, "CB_SLIPPAGE")
        if self.pnl_dia <= self.stop_diario:
            return create_decision(False, "STOP_DIARIO")
        if self.perdas_consecutivas >= self.config.get('max_perdas_consecutivas', 4):
            return create_decision(False, "MAX_PERDAS_SEQ")
        if not horario_permite_abrir(self.config):
            return create_decision(False, "FORA_HORARIO")

        # v10.16: Validação de sanidade de alvos (TP/SL)
        sane, msg = self.validar_sanidade_alvos(ativo, signal.tp, signal.sl, resultados_recentes)
        if not sane:
            return create_decision(False, msg)

        # v10.20: Position sizing centralizado no RiskManager
        ps_config = self.config.get('position_sizing', {})
        target_risk = ps_config.get('target_risk_per_trade', 100)
        
        if signal.sl > 0:
            size = max(1, min(self.max_posicao, round(target_risk / signal.sl)))
        else:
            size = 1

        return create_decision(True, "OK", size=size)

    def validar_sanidade_alvos(self, ativo, tp, sl, resultados_recentes=None):
        """Verifica se os alvos de TP e SL estão dentro de limites operacionais seguros."""
        if tp <= 0 or sl <= 0:
            return False, "ALVOS_NULOS"

        sym = ativo.upper()
        is_win = 'WIN' in sym or 'IND' in sym
        is_wdo = 'WDO' in sym or 'DOL' in sym

        # Limites WIN (pontos)
        if is_win:
            if not (30 <= tp <= 2500): return False, "TP_INSANO_WIN"
            if not (30 <= sl <= 1500): return False, "SL_INSANO_WIN"
        # Limites WDO (pontos)
        elif is_wdo:
            if not (1.0 <= tp <= 200.0): return False, "TP_INSANO_WDO"
            if not (1.0 <= sl <= 100.0): return False, "SL_INSANO_WDO"

        # Razão Risco:Retorno (evita stops desproporcionais/estratégias de esperança)
        max_ratio = self.config.get('trading', {}).get('max_sl_tp_ratio', 3.0)
        if sl / tp > max_ratio:
            return False, "RISCO_RETORNO_INSANO"

        return True, "OK"

    def registrar_execucao(self, ativo, sinal_preco, exec_preco):
        """Registra a slippage e ativa o CB se o custo exceder 3 ticks (ou o configurado)."""
        if sinal_preco <= 0 or exec_preco <= 0: return
        
        # WIN=5pts, WDO=0.5pts
        tick_size = 5.0 if 'WIN' in ativo.upper() or 'IND' in ativo.upper() else 0.5
        slip_pts = abs(exec_preco - sinal_preco)
        slip_ticks = slip_pts / tick_size
        
        self.slippage_total_pontos += slip_pts
        self.trades_com_slippage += 1
        
        if slip_ticks > self.max_slippage_ticks:
            log.warning(f"[RISK] Slippage detectada: {slip_ticks:.1f} ticks no ativo {ativo}")
            # Circuit Breaker agressivo: se um único trade escorregar > 6 ticks (2x limite), para tudo.
            if slip_ticks > self.max_slippage_ticks * 2:
                self.circuit_breaker_slippage = True
                log.error("[RISK] Slippage Crítica! Circuit Breaker ATIVADO.")

    def registrar_resultado(self, pnl, acertou, ativo):
        """Atualiza estado após o trade."""
        self.pnl_dia += pnl
        self.lucro_acumulado += pnl
        if acertou:
            self.perdas_consecutivas = 0
        else:
            self.perdas_consecutivas += 1

    def avaliar_sinal(self, signal: Signal, resultados_recentes=None) -> RiskDecision:
        """Transforma sinal bruto em decisão operacional tipada."""
        if not signal.lado:
            return RiskDecision(
                symbol=signal.symbol,
                timestamp_ms=signal.timestamp_ms,
                permitido=False,
                motivo="NEUTRO"
            )

        return self.pode_abrir(signal, resultados_recentes=resultados_recentes)

    def calcular_barreiras_dinamicas(self, ativo, vol_p, vol_bps, regime, confianca):
        """Calcula TP/SL adaptativos baseados em volatilidade histórica, regime e confiança (v10.14)."""
        # 1. Ajuste adaptativo via range_vol_bps vs baseline de 20bps.
        vol_adj_mult = max(0.8, min(2.5, vol_bps / 20.0)) if vol_bps > 0 else 1.0

        estrategias = self.config.get('estrategias', {})
        estrategia = estrategias.get(regime, estrategias.get('lateral', {}))
        
        # 2. Multiplicadores de estratégia escalados pela volatilidade
        tp_mult = estrategia.get('tp_mult', 1.0) * vol_adj_mult
        sl_mult = estrategia.get('sl_mult', 1.0) * vol_adj_mult

        # 3. Cálculo inicial baseado na amplitude média (vol_p)
        tp = round(vol_p * 0.6 * tp_mult / 5) * 5
        sl = round(vol_p * 0.4 * sl_mult / 5) * 5
        
        min_sl = int(150 * sl_mult)
        min_tp = int(200 * tp_mult)

        # 4. Sizing por confiança (reduz alvos em sinais fracos, expande em fortes)
        if confianca >= 0.8:
            conf_tp_mult, conf_sl_mult = 1.2, 0.85
        elif confianca >= 0.5:
            conf_tp_mult, conf_sl_mult = 1.0, 1.0
        else:
            conf_tp_mult, conf_sl_mult = 0.8, 1.15

        tp = round(tp * conf_tp_mult / 5) * 5
        sl = round(sl * conf_sl_mult / 5) * 5
        
        # 5. Segurança: Pisos mínimos
        if sl < min_sl: sl = min_sl
        if tp < min_tp: tp = min_tp

        # 6. Compensação de Custos
        custo = custo_execucao(ativo, self.config)
        tp -= custo
        sl += custo

        return round(tp, 1), round(sl, 1)

    def ativar_kill_switch(self):
        """Trava o robô imediatamente."""
        self.kill_switch_ativo = True
        log.critical("[RISK] KILL SWITCH ATIVADO MANUALMENTE.")