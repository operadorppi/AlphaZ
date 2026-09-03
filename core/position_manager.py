# -*- coding: utf-8 -*-
"""
core/position_manager.py — Gestão de posições abertas.

Extrai de Analise:
  - gerenciar_posicao (linha 2755)
  - _checar_saidas (linha 2671)
  - _fechar_posicao (linha 2825)
  - _suavizar_sinal (linha 2625)
  - verificar_saidas_tempo_real (linha 2615)
"""

import time
import logging
from datetime import datetime

from core.risk_manager import custo_execucao, horario_permite_abrir
from core.contracts import Action, Signal, RiskDecision

log = logging.getLogger(__name__)


class PositionManager:
    """Gere posições abertas. Não decide sinais nem risco."""

    def __init__(self, risk_manager, persistence=None, learning=None,
                 config=None, ativo_principal='WINV26'):
        self.risk = risk_manager
        self.persistence = persistence
        self.learning = learning
        self.config = config or {}
        self.ativo_principal = ativo_principal
        self.posicao = None

        # Estado de sinal
        self.sinal_confirmado = 0
        self._score_confirmado = 0.0
        self._sinal_streak = 0
        self._sinal_anterior_bruto = 0
        self._lado_anterior = 0
        self.sinal_contador = 0
        self.confianca_ewma = 0.0
        # v12.4 (Fase 6): Cooldown removido — RiskEngine._check_cooldown é a fonte de verdade

    def suavizar(self, lado_bruto, confirmacao_necessaria=None):
        """Suavização de sinal com confirmação por N segmentos."""
        if lado_bruto == 0:
            return self.sinal_confirmado
        if lado_bruto == self._lado_anterior:
            self.sinal_contador += 1
        else:
            self._lado_anterior = lado_bruto
            self.sinal_contador = 1

        conf_alvo = confirmacao_necessaria or self.config.get('confirmacao_necessaria', 3)
        if self.sinal_contador >= conf_alvo:
            self.sinal_confirmado = lado_bruto
            self._score_confirmado = getattr(self, '_score_anterior', 0.0)

        if lado_bruto == self.sinal_confirmado:
            return lado_bruto
        return self.sinal_confirmado if self.sinal_confirmado != 0 else 0

    def gerenciar(self, ativo, signal: Signal, preco, decision: RiskDecision = None,
                  regime=None, limiar_confirmacao=None,
                  cooldown_entre_trades_s=None, max_holding_s=None):
        """Decide abrir/manter/fechar baseado no sinal + estado atual."""
        if ativo != self.ativo_principal:
            return Action(tipo='CONTEXT_ONLY', lado='', preco=preco, tp=0.0, sl=0.0, motivo='')

        sinal_int = 1 if signal.lado == 'C' else (-1 if signal.lado == 'V' else 0)
        
        # v10.21: Sincronização do streak de sinal para validação de estabilidade
        if sinal_int != 0 and sinal_int == self._sinal_anterior_bruto:
            self._sinal_streak += 1
        else:
            self._sinal_streak = 1 if sinal_int != 0 else 0
        self._sinal_anterior_bruto = sinal_int

        lado = self.suavizar(sinal_int,
                             confirmacao_necessaria=self.config.get('confirmacao_necessaria', 3))
        # v12.4 (Fase 6): Risco é decisão do RiskEngine, não do PositionManager.
        # A confiança/streak ainda são necessários para suavização do sinal,
        # mas a validação de "posso abrir?" vem do RiskDecision injetado.

        if self.posicao is not None:
            # v10.9 (Fase 9): Lógica de Piramidação (Averaging Up)
            # v12.3 (Fase 5): Máquina de estados clara — VALIDAR → APROVADA/REJEITADA
            pos = self.posicao
            ps_config = self.config.get('position_sizing', {})
            max_qty = ps_config.get('max_position_size', 5)
            conf_piramide = self.config.get('confianca_piramidacao', 0.85)
            ml_threshold_piram = self.config.get('ml_threshold_piramidacao', 0.65)
            pnl_min_piram = self.config.get('pnl_min_piramidacao', 50)

            pnl_atual = (preco - pos['preco_medio']) if pos['lado'] == 'C' else (pos['preco_medio'] - preco)
            mesmo_lado = (lado > 0 and pos['lado'] == 'C') or (lado < 0 and pos['lado'] == 'V')
            ml_piramida = pos.get('ml_prob', 0.5)

            # === ESTADO 1: PIRAMIDAÇÃO_SOLICITADA ===
            piramide_solicitada = (
                mesmo_lado
                and abs(self.confianca_ewma) >= conf_piramide
                and pnl_atual >= pnl_min_piram
                and pos['quantidade'] < max_qty
            )

            if piramide_solicitada:
                # === ESTADO 2: VALIDAR ===
                # Validação ML: o modelo precisa confirmar a piramidação
                ml_aprovou = ml_piramida >= ml_threshold_piram

                if ml_aprovou:
                    # === ESTADO 3a: APROVADA → EXECUTAR ===
                    qtd_add = 1
                    nova_qtd = pos['quantidade'] + qtd_add
                    novo_preco_medio = (
                        (pos['preco_medio'] * pos['quantidade']) + (preco * qtd_add)
                    ) / nova_qtd

                    # ATUALIZAR POSIÇÃO (só aqui, nunca antes)
                    pos['preco_medio'] = novo_preco_medio
                    pos['quantidade'] = nova_qtd
                    # Stop sobe para breakeven do novo preço médio
                    pos['stop_preco'] = novo_preco_medio
                    pos['motivos'].append(
                        f"PIRAMIDE_CONF_{self.confianca_ewma:.2f}_ML_{ml_piramida:.2f}"
                    )
                    log.info(
                        f"[EXEC] Piramidação aprovada: {pos['ativo']} "
                        f"+{qtd_add} @ {preco}. Nova Qtd: {nova_qtd}, "
                        f"PM: {novo_preco_medio:.1f}"
                    )
                    if self.persistence:
                        self.persistence.salvar_checkpoint(self.posicao)
                else:
                    # === ESTADO 3b: REJEITADA → NÃO ALTERAR POSIÇÃO ===
                    # Stop e preço médio NÃO mudam
                    pos['motivos'].append(
                        f"PIRAMIDE_REJEITADA_ML_{ml_piramida:.2f}"
                    )
                    log.info(
                        f"[EXEC] Piramidação rejeitada (ML={ml_piramida:.2f} < "
                        f"{ml_threshold_piram}). Posição inalterada."
                    )

            resultado = self.checar_saidas(preco, max_holding_s=max_holding_s)
            sl_offset = abs(pos.get('stop_preco', preco) - pos['preco_medio']) if pos.get('stop_preco') else pos['tp']
            return resultado if resultado is not None else Action(
                tipo='MANTER', lado=pos['lado'], preco=preco,
                tp=pos['tp'], sl=sl_offset,
                motivo=''
            )

        # v12.4 (Fase 6): Cooldown e validação de risco são do RiskEngine.
        # PositionManager apenas executa a RiskDecision recebida.

        if lado != 0 and preco > 0 and self._sinal_streak >= 2:
            # RiskDecision DEVE ser injetada pelo chamador (app.py → risk_engine.avaliar())
            if not decision:
                return Action(tipo='REJEITADO', lado='', preco=preco, tp=0.0, sl=0.0,
                              motivo='SEM_RISK_DECISION')

            if not decision.permitido:
                return Action(tipo='REJEITADO', lado='', preco=preco, tp=0.0, sl=0.0,
                              motivo=decision.motivo)

            l = 'C' if lado > 0 else 'V'
            stop_preco = (preco - decision.sl) if l == 'C' else (preco + decision.sl)

            if self.learning:
                self.learning.previsoes.append({
                    'idx': len(self.learning.previsoes), 'ativo': ativo, 'lado': l,
                    'entrada': preco, 'tp': decision.tp, 'sl': decision.sl,
                    'aberta_em': time.time()
                })

            ml_prob = getattr(signal, 'ml_prob', 0.5)
            quantidade = decision.size or 1

            self.posicao = {
                'ativo': ativo, 'lado': l, 'entrada': preco, 'preco_medio': preco,
                'stop_preco': stop_preco, 'tp': decision.tp,
                'aberta_em': time.time(), 'motivos': list(signal.motivos), 'contrib': list(signal.contrib),
                'prev_idx': len(self.learning.previsoes) - 1 if self.learning else 0,
                'mfe': 0.0, 'mae': 0.0, 'breakeven_ativado': False,
                'regime_abertura': regime or 'indefinido',
                'quantidade': quantidade,
                'ml_prob': ml_prob
            }
            if self.persistence:
                self.persistence.salvar_checkpoint(self.posicao)
            return Action(tipo='ABRIR', lado=l, preco=preco, tp=decision.tp, sl=decision.sl,
                          motivo=';'.join(signal.motivos))

        return Action(tipo='AGUARDE', lado='', preco=preco, tp=0.0, sl=0.0, motivo='')

    def checar_saidas(self, preco, max_holding_s=None):
        """Verifica TP/SL/reversão em tempo real."""
        pos = self.posicao
        if pos is None:
            return None
        lado = pos['lado']
        raw_pnl = (preco - pos['preco_medio']) if lado == 'C' else (pos['preco_medio'] - preco)
        leveraged_pnl = raw_pnl * pos.get('quantidade', 1)
        pos['mfe'] = max(pos.get('mfe', 0), raw_pnl)
        pos['mae'] = min(pos.get('mae', 0), raw_pnl)

        agora = datetime.now()
        fechamento = self.config.get('horario_fechamento', (16, 30))
        if isinstance(fechamento, (list, tuple)):
            fech_t = fechamento[0] * 60 + fechamento[1]
        else:
            fech_t = fechamento
        if self.config.get('desligar_horarios_ruins', True) and (agora.hour, agora.minute) >= (fech_t // 60, fech_t % 60):
            return self._fechar(preco, 'FECHAMENTO_HORARIO')

        max_hold = max_holding_s if max_holding_s is not None else self.config.get('tempo_max_posicao_s', 300)
        if max_hold > 0 and time.time() - pos['aberta_em'] >= max_hold:
            return self._fechar(preco, 'TIMEOUT')

        # REVERSAO
        if self.config.get('reversao_fecha', True) and self.sinal_confirmado != 0:
            holding = time.time() - pos['aberta_em']
            min_hold_rev = self.config.get('min_holding_reversao_s', 90)
            conf_min_rev = self.config.get('confianca_min_reversao', 0.75)
            if holding >= min_hold_rev:
                sinal_lado = 1 if self.sinal_confirmado > 0 else -1
                pos_lado = 1 if pos['lado'] == 'C' else -1
                if sinal_lado != pos_lado and abs(self.confianca_ewma) >= conf_min_rev and abs(self._score_confirmado) > 0.4:
                    return self._fechar(preco, 'REVERSAO')

        # v10.8: Lógica de Trailing Stop baseada em MFE
        if self.config.get('usar_trailing_mfe', True):
            mfe = pos.get('mfe', 0.0)
            tp = pos.get('tp', 1.0)
            mfe_pct = mfe / tp if tp > 0 else 0
            
            novo_stop = None
            # Degrau 3: Se atingiu 90% do TP, trava 75% do lucro do MFE
            if mfe_pct >= 0.90:
                trava_pontos = mfe * 0.75
                novo_stop = pos['preco_medio'] + trava_pontos if lado == 'C' else pos['preco_medio'] - trava_pontos
            # Degrau 2: Se atingiu 75% do TP, trava 40% do lucro do MFE
            elif mfe_pct >= 0.75:
                trava_pontos = mfe * 0.40
                novo_stop = pos['preco_medio'] + trava_pontos if lado == 'C' else pos['preco_medio'] - trava_pontos
            # Degrau 1: Se atingiu 50% do TP, move para Breakeven
            elif mfe_pct >= 0.50:
                novo_stop = pos['preco_medio']

            if novo_stop is not None:
                if lado == 'C':
                    if novo_stop > pos.get('stop_preco', -9e18):
                        pos['stop_preco'] = novo_stop
                else:
                    if novo_stop < pos.get('stop_preco', 9e18):
                        pos['stop_preco'] = novo_stop

        # TP
        if (lado == 'C' and preco >= pos['entrada'] + pos['tp']) or (lado == 'V' and preco <= pos['entrada'] - pos['tp']):
            return self._fechar(preco, 'TP')

        # SL
        if pos.get('stop_preco') and pos['stop_preco'] > 0:
            if lado == 'C' and preco <= pos['stop_preco']:
                return self._fechar(preco, 'SL')
            if lado == 'V' and preco >= pos['stop_preco']:
                return self._fechar(preco, 'SL')

        return None

    def _fechar(self, preco, motivo):
        """Fecha posição e registra resultado."""
        pos = self.posicao
        if pos is None:
            return Action(tipo='SEM_POSICAO', lado='', preco=preco, tp=0.0, sl=0.0, motivo='')
        lado = pos['lado']
        raw_pnl = (preco - pos['preco_medio']) if lado == 'C' else (pos['preco_medio'] - preco)
        leveraged_pnl = raw_pnl * pos.get('quantidade', 1)
        acertou = leveraged_pnl > 0

        if self.learning:
            self.learning.resultados.append({
                'idx': pos.get('prev_idx', 0), 'preco_antes': pos['entrada'],
                'preco_depois': preco, 'acertou': acertou, 'delta': leveraged_pnl,
                'lado': lado, 'mfe': pos.get('mfe', 0), 'mae': pos.get('mae', 0), 'motivo': motivo,
                'ts': datetime.now().isoformat(timespec='seconds'),
                'fechada_em': time.time(),
            })
            self.learning.aprender_mfe_mae(pos.get('contrib', []), acertou,
                                          pos.get('mfe', 0), pos.get('mae', 0),
                                          regime_abertura=pos.get('regime_abertura'))
        
        # v11.10: Alimentar calibrador ML com resultado do trade
        if hasattr(self, '_calibration') and self._calibration:
            ml_prob = pos.get('ml_prob', 0.5)
            regime = pos.get('regime_abertura', 'lateral')
            outcome = 1 if acertou else 0
            self._calibration.update(ml_prob, outcome, regime=regime)

        # Captura offsets antes de limpar a posição
        sl_abs = pos.get('stop_preco', 0.0)
        sl_offset = abs(sl_abs - pos['preco_medio']) if sl_abs > 0 else pos['tp']
        tp_val = pos['tp']

        # v12.4 (Fase 6): RiskEngine.registrar_resultado (com ativo) ou RiskManager (compat)
        try:
            self.risk.registrar_resultado(leveraged_pnl, acertou, pos.get('ativo', self.ativo_principal))
        except TypeError:
            # RiskEngine.registrar_resultado tem assinatura diferente
            self.risk.registrar_resultado(leveraged_pnl, acertou)

        self.posicao = None
        self.confianca_ewma = 0.0
        self.sinal_confirmado = 0
        self._lado_anterior = 0
        self.sinal_contador = 0
        if self.persistence:
            self.persistence.salvar_checkpoint(None)
        
        return Action(tipo='FECHAR', lado=lado, preco=preco, 
                      tp=tp_val, sl=sl_offset, 
                      motivo=motivo, pnl=leveraged_pnl)

    def get_posicao(self, ultimo_preco_fn=None):
        """Retorna posição atual com PnL em tempo real."""
        if self.posicao is None:
            return None
        pos = self.posicao
        preco = ultimo_preco_fn(pos['ativo']) if ultimo_preco_fn else 0
        raw_pnl = (preco - pos['preco_medio']) if pos['lado'] == 'C' else (pos['preco_medio'] - preco)
        leveraged_pnl = raw_pnl * pos.get('quantidade', 1)
        sl_abs = pos.get('stop_preco', 0)
        sl_offset = abs(sl_abs - pos['preco_medio']) if sl_abs > 0 else pos['tp']
        return {
            'lado': pos['lado'], 'entrada': pos['entrada'], 'preco_atual': preco,
            'pnl': leveraged_pnl, 'tp': pos['tp'], 'sl': sl_offset,
            'mfe': pos.get('mfe', 0), 'mae': pos.get('mae', 0),
            'duracao_s': time.time() - pos['aberta_em'],
        }
