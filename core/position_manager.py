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
        limiar = limiar_confirmacao if limiar_confirmacao is not None else self.config.get('limiar_confirmacao', 0.55)
        sinal_valido = (lado != 0 and abs(self.confianca_ewma) >= limiar)

        if self.posicao is not None:
            # v10.9 (Fase 9): Lógica de Piramidação (Averaging Up)
            pos = self.posicao
            ps_config = self.config.get('position_sizing', {})
            max_qty = ps_config.get('max_position_size', 5)
            conf_piramide = self.config.get('confianca_piramidacao', 0.85)
            
            pnl_atual = (preco - pos['preco_medio']) if pos['lado'] == 'C' else (pos['preco_medio'] - preco)
            
            # Condições para piramidar:
            # 1. Sinal na mesma direção
            # 2. Confiança extrema (> 0.85)
            # 3. Posição atual já está no lucro (mínimo 50 pts)
            # 4. Ainda não atingiu o limite de contratos
            mesmo_lado = (lado > 0 and pos['lado'] == 'C') or (lado < 0 and pos['lado'] == 'V')
            
            if mesmo_lado and abs(self.confianca_ewma) >= conf_piramide and pnl_atual >= 50 and pos['quantidade'] < max_qty:
                qtd_add = 1
                nova_qtd = pos['quantidade'] + qtd_add
                # Novo preço médio
                pos['preco_medio'] = ((pos['preco_medio'] * pos['quantidade']) + (preco * qtd_add)) / nova_qtd
                pos['quantidade'] = nova_qtd
                pos['motivos'].append(f"PIRAMIDE_CONF_{self.confianca_ewma:.2f}")
                
                # Ao piramidar, subimos o stop para o breakeven do novo preço médio para garantir risco zero na adição
                pos['stop_preco'] = pos['preco_medio']
                log.info(f"[EXEC] Piramidação: {pos['ativo']} adicionado +{qtd_add} @ {preco}. Nova Qtd: {nova_qtd}")
                if self.persistence:
                    self.persistence.salvar_checkpoint(self.posicao)

            resultado = self.checar_saidas(preco, max_holding_s=max_holding_s)
            sl_offset = abs(pos.get('stop_preco', preco) - pos['preco_medio']) if pos.get('stop_preco') else pos['tp']
            return resultado if resultado is not None else Action(
                tipo='MANTER', lado=pos['lado'], preco=preco,
                tp=pos['tp'], sl=sl_offset,
                motivo=''
            )

        if sinal_valido and preco > 0 and self._sinal_streak >= 2:
            # v10.21: Injeção de RiskDecision para desacoplamento. Se não injetado, consulta o manager local.
            if not decision:
                res_recentes = self.learning.resultados if self.learning else []
                decision = self.risk.pode_abrir(signal, res_recentes)

            if not decision or not decision.permitido:
                motivo = decision.motivo if decision else "RISK_DECISION_MISSING"
                return Action(tipo='REJEITADO', lado='', preco=preco, tp=0.0, sl=0.0, motivo=motivo)

            # v10.12 (Fase 9): Registro de Slippage
            # Comparamos o preço de execução com o preço de referência do sinal
            self.risk.registrar_execucao(ativo, signal.preco_ref, preco) 

            l = 'C' if lado > 0 else 'V'
            stop_preco = (preco - decision.sl) if l == 'C' else (preco + decision.sl)

            if self.learning:
                self.learning.previsoes.append({
                    'idx': len(self.learning.previsoes), 'ativo': ativo, 'lado': l,
                    'entrada': preco, 'tp': decision.tp, 'sl': decision.sl,
                    'aberta_em': time.time()
                })

            quantidade = decision.size or 1

            self.posicao = {
                'ativo': ativo, 'lado': l, 'entrada': preco, 'preco_medio': preco,
                'stop_preco': stop_preco, 'tp': decision.tp,
                'aberta_em': time.time(), 'motivos': list(signal.motivos), 'contrib': list(signal.contrib),
                'prev_idx': len(self.learning.previsoes) - 1 if self.learning else 0,
                'mfe': 0.0, 'mae': 0.0, 'breakeven_ativado': False,
                'regime_abertura': regime or 'indefinido',
                'quantidade': quantidade
            }
            self.risk.trades_dia += 1
            if self.persistence:
                self.persistence.salvar_checkpoint(self.posicao)
            return Action(tipo='ABRIR', lado=l, preco=preco, tp=decision.tp, sl=decision.sl, motivo=';'.join(signal.motivos))

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

        # Captura offsets antes de limpar a posição
        sl_abs = pos.get('stop_preco', 0.0)
        sl_offset = abs(sl_abs - pos['preco_medio']) if sl_abs > 0 else pos['tp']
        tp_val = pos['tp']

        self.risk.registrar_resultado(leveraged_pnl, acertou, pos.get('ativo', self.ativo_principal))

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
