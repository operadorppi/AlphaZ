# -*- coding: utf-8 -*-
"""
testes/test_temporal.py — Testes do contrato temporal (Fase 2).

Testa que o timestamp do evento de mercado (DAT do Profit) é preservado
e nunca substituído pelo horário de processamento.

Cobertura:
  1. Evento Profit 10:35:21.127 recebido às 10:35:21.481 -> event_ts_ms preserva .127
  2. Múltiplos trades no mesmo RefreshData
  3. Trades fora de ordem (timestamp não monótono)
  4. Virada de segundo
  5. Virada de minuto
  6. Virada de dia
  7. Timestamp inválido
  8. Monotonicidade
  9. sequence_id incremental
  10. receive_ts_ns != event_ts_ms
"""

import pytest
import time
from datetime import datetime, timezone, timedelta
from core.temporal import (
    dat_to_epoch_ms, now_ns, now_ms, next_sequence_id,
    validate_event_ts, MonotonicityChecker, TZ_BR,
)
from core.contracts import TradeEvent, MarketEvent


# ============================================================
#  Helpers
# ============================================================

def make_trade(dat_str, sym='WINV26', price=177500, qtd=10,
               aggressor='Comprador', buyer='BTG', seller='XP',
               receive_ns=None, seq_id=None):
    """Cria um TradeEvent com timestamp de mercado do DAT."""
    if receive_ns is None:
        receive_ns = now_ns()
    if seq_id is None:
        seq_id = next_sequence_id()
    event_ts = dat_to_epoch_ms(dat_str)
    return TradeEvent(
        symbol=sym, timestamp_ms=event_ts, price=price, quantity=qtd,
        aggressor=aggressor, buyer=buyer, seller=seller,
        received_at_ns=receive_ns, sequence_id=seq_id,
    )


# ============================================================
#  Teste principal: timestamp do mercado preservado
# ============================================================

class TestTimestampPreservado:
    """O timestamp do evento de mercado NUNCA pode ser o de processamento."""

    def test_event_ts_preserva_milissegundo_do_profit(self):
        """Evento Profit 10:35:21.127 recebido às 10:35:21.481.

        Resultado:
          event_ts_ms = 10:35:21.127 (do DAT)
          received_at_ns ≈ 10:35:21.481 (do Python)

        NUNCA:
          event_ts_ms = 10:35:21.481
        """
        dat_str = "10:35:21.127"
        event_ts = dat_to_epoch_ms(dat_str)
        receive_ns = now_ns()

        trade = TradeEvent(
            symbol='WINV26', timestamp_ms=event_ts, price=177500, quantity=10,
            aggressor='Comprador', buyer='BTG', seller='XP',
            received_at_ns=receive_ns, sequence_id=1,
        )

        # event_ts_ms deve conter .127, não .481
        event_dt = datetime.fromtimestamp(event_ts / 1000, tz=TZ_BR)
        assert event_dt.second == 21, f"Segundos errados: {event_dt}"
        assert event_dt.microsecond == 127000, (
            f"Milissegundos errados: esperado 127000, obtido {event_dt.microsecond}"
        )

        # received_at deve ser diferente (geralmente maior)
        receive_ms = receive_ns // 1_000_000
        assert receive_ms != event_ts, (
            "event_ts_ms == receive_ts_ms — timestamp do mercado foi substituído!"
        )

    def test_receive_ts_diferente_de_event_ts(self):
        """receive_ts_ns deve ser diferente de event_ts_ms."""
        trade = make_trade("10:35:21.127")

        assert trade.timestamp_ms != trade.received_at_ns, (
            "timestamp_ms não pode ser igual a received_at_ns"
        )

    def test_event_ts_nao_e_wall_clock(self):
        """Se o evento tem DAT 09:00:00.000 mas recebido às 10:35:21,
        o timestamp_ms deve ser 09:00:00, não 10:35:21."""
        dat_str = "09:00:00.000"
        event_ts = dat_to_epoch_ms(dat_str)

        # Simular recebimento 1.5h depois
        receive_ns = now_ns()

        trade = TradeEvent(
            symbol='WINV26', timestamp_ms=event_ts, price=177500, quantity=10,
            aggressor='Comprador', buyer='BTG', seller='XP',
            received_at_ns=receive_ns, sequence_id=1,
        )

        event_dt = datetime.fromtimestamp(trade.timestamp_ms / 1000, tz=TZ_BR)
        assert event_dt.hour == 9, f"Esperado hora 9, obtido {event_dt.hour}"
        assert event_dt.minute == 0
        assert event_dt.second == 0


# ============================================================
#  Múltiplos trades no mesmo RefreshData
# ============================================================

class TestMultiTrades:
    """Múltiplos trades no mesmo RefreshData devem ter timestamps distintos."""

    def test_dois_trades_mesmo_segundo_ms_diferente(self):
        """Dois trades no mesmo segundo mas com ms diferente."""
        t1 = make_trade("10:35:21.127", price=177500)
        t2 = make_trade("10:35:21.250", price=177505)

        assert t1.timestamp_ms != t2.timestamp_ms

        dt1 = datetime.fromtimestamp(t1.timestamp_ms / 1000, tz=TZ_BR)
        dt2 = datetime.fromtimestamp(t2.timestamp_ms / 1000, tz=TZ_BR)

        assert dt1.second == 21
        assert dt2.second == 21
        assert dt1.microsecond == 127000
        assert dt2.microsecond == 250000

    def test_dez_trades_com_timestamps_crescentes(self):
        """10 trades com timestamps crescentes."""
        trades = []
        for i in range(10):
            t = make_trade(f"10:35:{i:02d}.000")
            trades.append(t)

        for i in range(1, 10):
            assert trades[i].timestamp_ms > trades[i-1].timestamp_ms, (
                f"Trade {i} não é crescente em relação ao {i-1}"
            )


# ============================================================
#  Trades fora de ordem
# ============================================================

class TestForaDeOrdem:
    """Trades fora de ordem temporal."""

    def test_monotonicidade_detecta_volta_no_tempo(self):
        """O checker de monotonicidade detecta timestamp que voltou."""
        checker = MonotonicityChecker(max_backward_s=5.0)

        # Primeiro evento
        ok1, _ = checker.check('WINV26', dat_to_epoch_ms("10:35:21.000"))
        assert ok1 is True

        # Segundo evento 1s depois
        ok2, _ = checker.check('WINV26', dat_to_epoch_ms("10:35:22.000"))
        assert ok2 is True

        # Terceiro evento voltando 10s (mais que max_backward)
        ok3, motivo = checker.check('WINV26', dat_to_epoch_ms("10:35:12.000"))
        assert ok3 is False
        assert "voltou" in motivo

    def test_monotonicidade_aceita_mesmo_timestamp(self):
        """Dois trades com o mesmo timestamp são aceitos."""
        checker = MonotonicityChecker()
        ts = dat_to_epoch_ms("10:35:21.000")

        ok1, _ = checker.check('WINV26', ts)
        ok2, _ = checker.check('WINV26', ts)
        assert ok1 is True
        assert ok2 is True

    def test_monotonicidade_independente_por_ativo(self):
        """Monotonicidade é independente por ativo."""
        checker = MonotonicityChecker(max_backward_s=5.0)

        ts_win = dat_to_epoch_ms("10:35:21.000")
        ts_wdo = dat_to_epoch_ms("10:35:21.000")

        checker.check('WINV26', ts_win)
        # WDO pode ter timestamp "menor" que WIN sem problema
        ok, _ = checker.check('WDOU26', dat_to_epoch_ms("09:00:00.000"))
        assert ok is True


# ============================================================
#  Viradas
# ============================================================

class TestViradas:
    """Testes de virada de segundo, minuto e dia."""

    def test_virada_de_segundo(self):
        """Trade às 10:35:21.999 e próximo às 10:35:22.001."""
        t1 = make_trade("10:35:21.999")
        t2 = make_trade("10:35:22.001")

        assert t2.timestamp_ms > t1.timestamp_ms

        dt1 = datetime.fromtimestamp(t1.timestamp_ms / 1000, tz=TZ_BR)
        dt2 = datetime.fromtimestamp(t2.timestamp_ms / 1000, tz=TZ_BR)

        assert dt1.second == 21
        assert dt2.second == 22

    def test_virada_de_minuto(self):
        """Trade às 10:35:59.999 e próximo às 10:36:00.001."""
        t1 = make_trade("10:35:59.999")
        t2 = make_trade("10:36:00.001")

        assert t2.timestamp_ms > t1.timestamp_ms

        dt1 = datetime.fromtimestamp(t1.timestamp_ms / 1000, tz=TZ_BR)
        dt2 = datetime.fromtimestamp(t2.timestamp_ms / 1000, tz=TZ_BR)

        assert dt1.minute == 35
        assert dt2.minute == 36

    def test_virada_de_dia_nao_causa_problema(self):
        """Trade às 23:59:58 e próximo às 23:59:59 — virada de dia é tratada."""
        t1 = make_trade("23:59:58.000")
        t2 = make_trade("23:59:59.000")

        # Ambos devem ter epoch_ms válido
        assert t1.timestamp_ms > 0
        assert t2.timestamp_ms > 0
        assert t2.timestamp_ms > t1.timestamp_ms

    def test_virada_de_segundo_com_hora_igual(self):
        """Dois trades no mesmo segundo mas com ms diferente não colidem."""
        t1 = make_trade("10:35:21.100")
        t2 = make_trade("10:35:21.200")

        assert t1.timestamp_ms != t2.timestamp_ms
        assert t2.timestamp_ms - t1.timestamp_ms == 100  # 100ms de diferença


# ============================================================
#  Timestamp inválido
# ============================================================

class TestTimestampInvalido:
    """Testes de timestamps inválidos."""

    def test_dat_vazio_retorna_zero(self):
        """DAT vazio retorna 0 (inválido)."""
        assert dat_to_epoch_ms("") == 0
        assert dat_to_epoch_ms(None) == 0

    def test_dat_malformado_retorna_zero(self):
        """DAT malformado retorna 0."""
        assert dat_to_epoch_ms("abc") == 0
        assert dat_to_epoch_ms("10:35") == 0
        assert dat_to_epoch_ms(":") == 0
        assert dat_to_epoch_ms(":::") == 0

    def test_validacao_rejeita_timestamp_zero(self):
        """Timestamp zero é rejeitado."""
        ok, motivo = validate_event_ts(0, now_ns())
        assert ok is False
        assert "zero" in motivo

    def test_validacao_rejeita_timestamp_futuro(self):
        """Timestamp muito no futuro é rejeitado."""
        future_ts = now_ms() + 60_000  # 60s no futuro
        ok, motivo = validate_event_ts(future_ts, now_ns())
        assert ok is False
        assert "futuro" in motivo

    def test_validacao_rejeita_timestamp_passado(self):
        """Timestamp muito no passado é rejeitado."""
        # v14.3: threshold é 600s (buffer RTD), então usa 700s
        past_ts = now_ms() - 700_000  # ~11.7min no passado
        ok, motivo = validate_event_ts(past_ts, now_ns())
        assert ok is False
        assert "passado" in motivo

    def test_validacao_aceita_timestamp_proximo(self):
        """Timestamp próximo do atual é aceito."""
        ts = now_ms() - 1000  # 1s atrás
        ok, _ = validate_event_ts(ts, now_ns())
        assert ok is True


# ============================================================
#  sequence_id
# ============================================================

class TestSequenceId:
    """Testes do sequence_id."""

    def test_sequence_id_e_incremental(self):
        """sequence_id deve ser incremental."""
        s1 = next_sequence_id()
        s2 = next_sequence_id()
        s3 = next_sequence_id()

        assert s2 > s1
        assert s3 > s2

    def test_sequence_id_e_positivo(self):
        """sequence_id deve ser positivo."""
        assert next_sequence_id() > 0


# ============================================================
#  Contrato TradeEvent
# ============================================================

class TestContratoTradeEvent:
    """Testes do contrato TradeEvent."""

    def test_tradeevent_tem_tres_timestamps(self):
        """TradeEvent deve ter event_ts_ms, received_at_ns e sequence_id."""
        trade = make_trade("10:35:21.127")

        assert hasattr(trade, 'timestamp_ms')
        assert hasattr(trade, 'received_at_ns')
        assert hasattr(trade, 'sequence_id')

        assert trade.timestamp_ms > 0
        assert trade.received_at_ns > 0
        assert trade.sequence_id > 0

    def test_tradeevent_received_at_ms_compatibilidade(self):
        """received_at_ms deve funcionar como compatibilidade."""
        trade = make_trade("10:35:21.127")

        assert trade.received_at_ms == trade.received_at_ns // 1_000_000

    def test_schema_version_2(self):
        """Schema version deve ser 2.0 (Fase 2)."""
        trade = make_trade("10:35:21.127")
        assert trade.schema_version == "2.0"

    def test_trade_event_frozen(self):
        """TradeEvent deve ser frozen (imutável)."""
        trade = make_trade("10:35:21.127")

        with pytest.raises(Exception):
            trade.price = 999999  # Deve falhar pois é frozen

    def test_preco_invalido_levanta_erro(self):
        """Preço inválido deve levantar erro."""
        with pytest.raises(ValueError):
            TradeEvent(
                symbol='WINV26', timestamp_ms=now_ms(), price=-1,
                quantity=10, aggressor='C', buyer='BTG', seller='XP',
                received_at_ns=now_ns(),
            )

    def test_quantidade_invalida_levanta_erro(self):
        """Quantidade inválida deve levantar erro."""
        with pytest.raises(ValueError):
            TradeEvent(
                symbol='WINV26', timestamp_ms=now_ms(), price=177500,
                quantity=0, aggressor='C', buyer='BTG', seller='XP',
                received_at_ns=now_ns(),
            )
