# -*- coding: utf-8 -*-
"""
testes/test_event_ordering.py — Testes do detector de ordenamento temporal (Fase 3).

Testa:
  1. Evento atrasado (lag > limiar)
  2. Evento fora de ordem (timestamp < último do ativo)
  3. Timestamp duplicado
  4. Salto temporal anormal
  5. Sequência regressiva (N eventos seguidos no passado)
  6. Métricas acumuladas
  7. Independência por ativo
  8. Política de descarte/reordenação explícita
"""

import pytest
from core.event_ordering import EventOrderingDetector, OrderingResult


def make_receive_ns(ts_ms):
    """Converte epoch ms para epoch ns (simula recebimento)."""
    return ts_ms * 1_000_000


class TestEventoAtrasado:
    """1. Evento atrasado (lag > limiar)."""

    def test_evento_atrasado_detectado(self):
        """Se lag > 500ms, evento é classificado como atrasado."""
        det = EventOrderingDetector(late_threshold_ms=500)
        # Evento às 10:00:00.000, recebido às 10:00:00.800 (800ms depois)
        event_ts = 1_700_000_000_000  # epoch ms arbitrário
        receive_ns = (event_ts + 800) * 1_000_000

        result = det.check('WINV26', event_ts, receive_ns)

        assert result.is_late is True
        assert result.lag_ms == 800
        assert det.get_stats()['events_late'] == 1

    def test_evento_sem_atraso_nao_classificado(self):
        """Se lag < 500ms, evento NÃO é atrasado."""
        det = EventOrderingDetector(late_threshold_ms=500)
        event_ts = 1_700_000_000_000
        receive_ns = (event_ts + 100) * 1_000_000  # 100ms depois

        result = det.check('WINV26', event_ts, receive_ns)

        assert result.is_late is False
        assert det.get_stats()['events_late'] == 0

    def test_max_event_lag_ms_atualizado(self):
        """max_event_lag_ms reflete o maior lag observado."""
        det = EventOrderingDetector(late_threshold_ms=500)

        # Evento 1: lag 100ms
        det.check('WINV26', 1_000, (1_000 + 100) * 1_000_000)
        # Evento 2: lag 900ms
        det.check('WINV26', 2_000, (2_000 + 900) * 1_000_000)

        stats = det.get_stats()
        assert stats['max_event_lag_ms'] == 900


class TestForaDeOrdem:
    """2. Evento fora de ordem (timestamp < último do ativo)."""

    def test_fora_de_ordem_detectado(self):
        """Se timestamp < último, é fora de ordem."""
        det = EventOrderingDetector()
        # Evento 1: ts=1000
        det.check('WINV26', 1000, make_receive_ns(1000))
        # Evento 2: ts=900 (voltou 100ms)
        result = det.check('WINV26', 900, make_receive_ns(900))

        assert result.is_out_of_order is True
        assert result.gap_ms == -100  # 900 - 1000
        assert det.get_stats()['events_out_of_order'] == 1

    def test_fora_de_ordem_isolado_e_aceito(self):
        """Evento fora de ordem isolado é aceito (não descartado)."""
        det = EventOrderingDetector()
        det.check('WINV26', 1000, make_receive_ns(1000))
        result = det.check('WINV26', 990, make_receive_ns(990))

        assert result.is_out_of_order is True
        assert result.action == "ACCEPT"
        assert result.reason == "out_of_order_accepted"


class TestTimestampDuplicado:
    """3. Timestamp duplicado (mesmo event_ts_ms já visto)."""

    def test_duplicado_detectado_e_rejeitado(self):
        """Mesmo timestamp do mesmo ativo é rejeitado."""
        det = EventOrderingDetector()
        det.check('WINV26', 1000, make_receive_ns(1000))
        result = det.check('WINV26', 1000, make_receive_ns(1000))

        assert result.is_duplicate is True
        assert result.action == "REJECT"
        assert result.reason == "duplicate_timestamp"
        assert det.get_stats()['events_duplicate'] == 1

    def test_mesmo_ts_em_ativos_diferentes_nao_e_duplicado(self):
        """Mesmo timestamp em ativos diferentes não é duplicado."""
        det = EventOrderingDetector()
        det.check('WINV26', 1000, make_receive_ns(1000))
        result = det.check('WDOU26', 1000, make_receive_ns(1000))

        assert result.is_duplicate is False
        assert result.action == "ACCEPT"


class TestSaltoTemporal:
    """4. Salto temporal anormal."""

    def test_salto_frente_detectado(self):
        """Gap > 60s para frente é salto temporal."""
        det = EventOrderingDetector(forward_jump_threshold_ms=60_000)
        det.check('WINV26', 1000, make_receive_ns(1000))
        # Evento 65s depois
        result = det.check('WINV26', 1000 + 65_000, make_receive_ns(1000 + 65_000))

        assert result.is_forward_jump is True
        assert result.gap_ms == 65_000
        assert det.get_stats()['events_forward_jump'] == 1

    def test_gap_normal_nao_e_salto(self):
        """Gap < 60s não é salto."""
        det = EventOrderingDetector(forward_jump_threshold_ms=60_000)
        det.check('WINV26', 1000, make_receive_ns(1000))
        result = det.check('WINV26', 1000 + 5_000, make_receive_ns(1000 + 5_000))

        assert result.is_forward_jump is False


class TestSequenciaRegressiva:
    """5. Sequência regressiva (N eventos seguidos no passado)."""

    def test_sequencia_regressiva_detectada(self):
        """3+ eventos seguidos no passado = sequência regressiva."""
        det = EventOrderingDetector(backward_sequence_threshold=3)

        det.check('WINV26', 1000, make_receive_ns(1000))
        # 3 eventos seguidos no passado
        det.check('WINV26', 990, make_receive_ns(990))
        det.check('WINV26', 980, make_receive_ns(980))
        result = det.check('WINV26', 970, make_receive_ns(970))

        assert result.is_backward_sequence is True
        assert det.get_stats()['events_backward_sequence'] == 1

    def test_sequencia_interrompida_por_evento_normal(self):
        """Se um evento em ordem chega, o contador de regressivos reseta."""
        det = EventOrderingDetector(backward_sequence_threshold=3)

        det.check('WINV26', 1000, make_receive_ns(1000))
        det.check('WINV26', 990, make_receive_ns(990))  # 1 backward
        det.check('WINV26', 1005, make_receive_ns(1005))  # em ordem — reset
        result = det.check('WINV26', 995, make_receive_ns(995))  # 1 backward (resetou)

        assert result.is_backward_sequence is False


class TestMetricas:
    """6. Métricas acumuladas."""

    def test_stats_completas(self):
        """get_stats retorna todas as métricas esperadas."""
        det = EventOrderingDetector()
        stats = det.get_stats()

        for key in ['events_total', 'events_accepted', 'events_out_of_order',
                     'events_duplicate', 'events_timestamp_invalid',
                     'events_late', 'max_event_lag_ms']:
            assert key in stats, f"Métrica faltando: {key}"

    def test_dashboard_stats(self):
        """get_stats_for_dashboard retorna métricas formatadas."""
        det = EventOrderingDetector()
        det.check('WINV26', 1000, make_receive_ns(1000))

        stats = det.get_stats_for_dashboard()
        assert 'events_total' in stats
        assert 'events_out_of_order' in stats
        assert 'events_duplicate' in stats
        assert 'events_late' in stats
        assert 'max_event_lag_ms' in stats

    def test_contador_total_incrementa(self):
        """events_total incrementa a cada check."""
        det = EventOrderingDetector()
        det.check('WINV26', 1000, make_receive_ns(1000))
        det.check('WINV26', 1001, make_receive_ns(1001))
        det.check('WINV26', 1002, make_receive_ns(1002))

        assert det.get_stats()['events_total'] == 3
        assert det.get_stats()['events_accepted'] == 3


class TestIndependenciaAtivo:
    """7. Independência por ativo."""

    def test_dedup_independente_por_ativo(self):
        """Dedup de timestamp é independente por ativo."""
        det = EventOrderingDetector()
        det.check('WINV26', 1000, make_receive_ns(1000))
        result = det.check('WDOU26', 1000, make_receive_ns(1000))

        assert result.is_duplicate is False
        assert result.action == "ACCEPT"

    def test_ordem_independente_por_ativo(self):
        """Fora de ordem em um ativo não afeta outro."""
        det = EventOrderingDetector()
        det.check('WINV26', 1000, make_receive_ns(1000))
        det.check('WINV26', 990, make_receive_ns(990))  # WIN fora de ordem

        # WDO começa normal
        result = det.check('WDOU26', 5000, make_receive_ns(5000))
        assert result.is_out_of_order is False
        assert result.action == "ACCEPT"


class TestPoliticaDescarte:
    """8. Política de descarte/reordenação explícita."""

    def test_duplicada_rejeitada(self):
        """Duplicatas são REJEITADAS (não processadas)."""
        det = EventOrderingDetector()
        det.check('WINV26', 1000, make_receive_ns(1000))
        result = det.check('WINV26', 1000, make_receive_ns(1000))

        assert result.action == "REJECT"

    def test_fora_de_ordem_aceito(self):
        """Eventos fora de ordem isolados são ACEITOS (não descartados)."""
        det = EventOrderingDetector()
        det.check('WINV26', 1000, make_receive_ns(1000))
        result = det.check('WINV26', 990, make_receive_ns(990))

        assert result.action == "ACCEPT"
        assert result.reason == "out_of_order_accepted"

    def test_sequencia_regressiva_log_only(self):
        """Sequência regressiva é LOG_ONLY (registrada, não rejeitada)."""
        det = EventOrderingDetector(backward_sequence_threshold=3)
        det.check('WINV26', 1000, make_receive_ns(1000))
        det.check('WINV26', 990, make_receive_ns(990))
        det.check('WINV26', 980, make_receive_ns(980))
        result = det.check('WINV26', 970, make_receive_ns(970))

        assert result.action == "LOG_ONLY"

    def test_salto_temporal_log_only(self):
        """Salto temporal é LOG_ONLY (registrado, não rejeitado)."""
        det = EventOrderingDetector(forward_jump_threshold_ms=60_000)
        det.check('WINV26', 1000, make_receive_ns(1000))
        result = det.check('WINV26', 1000 + 65_000, make_receive_ns(1000 + 65_000))

        assert result.action == "LOG_ONLY"

    def test_timestamp_invalido_rejeitado(self):
        """Timestamp zero é rejeitado."""
        det = EventOrderingDetector()
        result = det.check('WINV26', 0, make_receive_ns(1000))

        assert result.action == "REJECT"
        assert result.reason == "timestamp_invalid_zero"
        assert det.get_stats()['events_timestamp_invalid'] == 1


class TestReset:
    """Reset do detector."""

    def test_reset_limpa_estado(self):
        """Reset limpa estado e métricas."""
        det = EventOrderingDetector()
        det.check('WINV26', 1000, make_receive_ns(1000))
        det.check('WINV26', 1000, make_receive_ns(1000))  # duplicata

        assert det.get_stats()['events_total'] == 2

        det.reset()

        stats = det.get_stats()
        assert stats['events_total'] == 0
        assert stats['events_duplicate'] == 0

    def test_reset_permite_reprocessar_duplicatas(self):
        """Apos reset, mesmo timestamp não é mais duplicata."""
        det = EventOrderingDetector()
        det.check('WINV26', 1000, make_receive_ns(1000))
        result = det.check('WINV26', 1000, make_receive_ns(1000))
        assert result.is_duplicate is True

        det.reset()

        result = det.check('WINV26', 1000, make_receive_ns(1000))
        assert result.is_duplicate is False
        assert result.action == "ACCEPT"
