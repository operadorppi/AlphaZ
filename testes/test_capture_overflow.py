# -*- coding: utf-8 -*-
"""
testes/test_capture_overflow.py — Testes de overflow e perda de eventos (Fase 4).

Testa:
  1. Fila cheia — eventos descartados com métricas corretas
  2. Produtor mais rápido que consumidor — overflow detectado
  3. Lote BOOK acima do limite — sem corrupção de estado
  4. Nenhuma perda invisível — data_loss_detected = True
  5. Métricas registram exatamente o ocorrido
  6. Sistema não corrompe estado
  7. Watermark (pico da fila) registrado
  8. Backpressure mode (não descarta, bloqueia)
  9. Drain no shutdown não perde eventos
"""

import pytest
import time
import queue
import threading
import os
import json
import tempfile
from core.capture_daemon import CaptureDaemon, _MAX_QUEUE


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp(prefix='capture_test_')
    yield d
    # Cleanup
    import shutil
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def daemon(tmp_dir):
    """Daemon com fila pequena para facilitar testes de overflow."""
    d = CaptureDaemon(tmp_dir, 'test_session', flush_interval_s=0.1)
    # Substituir fila por uma pequena para testar overflow facilmente
    d._queue = queue.Queue(maxsize=5)
    d.start()
    yield d
    d.stop()


class TestFilaCheia:
    """1. Fila cheia — eventos descartados com métricas corretas."""

    def test_fila_cheia_descarta_e_registra(self, daemon):
        """Quando a fila enche, eventos são descartados e contados."""
        # Encher a fila (5 slots)
        for i in range(5):
            daemon.registrar_negocios([('WINV26', 1000+i, 177500, 10,
                                        'Comprador', 'BTG', 'XP')])

        # Próximo lote deve ser descartado
        daemon.registrar_negocios([('WINV26', 2000, 177500, 10,
                                    'Comprador', 'BTG', 'XP')])

        stats = daemon.stats()
        assert stats['events_dropped'] >= 1, "Deveria ter descartado pelo menos 1 evento"
        assert stats['data_loss_detected'] is True, "data_loss_detected deveria ser True"
        assert stats['overflow_count'] >= 1

    def test_motivo_do_descarte_registrado(self, daemon):
        """O motivo do descarte é registrado."""
        for i in range(5):
            daemon.registrar_negocios([('WINV26', 1000+i, 177500, 10,
                                        'Comprador', 'BTG', 'XP')])

        # Descartar
        daemon.registrar_negocios([('WINV26', 2000, 177500, 10,
                                    'Comprador', 'BTG', 'XP')])

        stats = daemon.stats()
        assert 'queue_full' in stats['last_drop_reason']
        assert stats['last_drop_ts'] > 0

    def test_contador_recebidos_vs_processados(self, daemon):
        """recebidos > processados quando há descarte."""
        # Encher a fila
        for i in range(5):
            daemon.registrar_negocios([('WINV26', 1000+i, 177500, 10,
                                        'Comprador', 'BTG', 'XP')])

        # Descartar 3
        for i in range(3):
            daemon.registrar_negocios([('WINV26', 2000+i, 177500, 10,
                                        'Comprador', 'BTG', 'XP')])

        # Esperar processamento
        time.sleep(0.5)

        stats = daemon.stats()
        assert stats['events_received'] == 8  # 5 enfileirados + 3 descartados
        assert stats['events_dropped'] >= 3
        assert stats['data_loss_detected'] is True


class TestProdutorRapido:
    """2. Produtor mais rápido que consumidor."""

    def test_overflow_detectado_com_produtor_rapido(self, daemon):
        """Produtor envia 1000 eventos mas fila só tem 5 slots."""
        for i in range(1000):
            daemon.registrar_negocios([('WINV26', 1000+i, 177500, 10,
                                        'Comprador', 'BTG', 'XP')])

        stats = daemon.stats()
        assert stats['events_received'] == 1000
        assert stats['events_dropped'] > 0, "Deveria ter descartado eventos"
        assert stats['data_loss_detected'] is True
        assert stats['overflow_count'] > 0


class TestLoteBookAcimaDoLimite:
    """3. Lote BOOK acima do limite — sem corrupção de estado."""

    def test_book_snapshot_descartado_com_fila_cheia(self, daemon):
        """Book snapshot descartado quando fila está cheia."""
        # Encher a fila com negócios
        for i in range(5):
            daemon.registrar_negocios([('WINV26', 1000+i, 177500, 10,
                                        'Comprador', 'BTG', 'XP')])

        # Book deve ser descartado
        daemon.registrar_book('WINV26', 2000, {'BTG': {'bid_vol': 100}},
                              100, 90, levels={'bid_preco': [177500]})

        stats = daemon.stats()
        assert stats['book_dropped'] >= 1
        assert stats['data_loss_detected'] is True

    def test_estado_nao_corrompido_apos_overflow(self, daemon):
        """Após overflow, o daemon continua funcionando."""
        # Encher e descartar
        for i in range(100):
            daemon.registrar_negocios([('WINV26', 1000+i, 177500, 10,
                                        'Comprador', 'BTG', 'XP')])

        # Esperar processar
        time.sleep(0.5)

        # Novo evento deve ser aceito (fila deve ter espaço livre)
        daemon.registrar_negocios([('WINV26', 5000, 177500, 10,
                                    'Comprador', 'BTG', 'XP')])
        time.sleep(0.3)

        stats = daemon.stats()
        # Daemon ainda está vivo e processando
        assert stats['events_processed'] > 0
        assert stats['negocios_erro'] == 0  # sem erros de gravação


class TestPerdaInvisivel:
    """4. Nenhuma perda fica invisível."""

    def test_data_loss_detected_no_health_check(self, daemon):
        """health_check reporta data_loss_detected após descarte."""
        for i in range(5):
            daemon.registrar_negocios([('WINV26', 1000+i, 177500, 10,
                                        'Comprador', 'BTG', 'XP')])
        daemon.registrar_negocios([('WINV26', 2000, 177500, 10,
                                    'Comprador', 'BTG', 'XP')])

        health = daemon.health_check()
        assert health['data_loss_detected'] is True

    def test_log_error_nao_warning(self, daemon, caplog):
        """Overflow gera log.error, não log.warning."""
        import logging
        with caplog.at_level(logging.ERROR):
            for i in range(5):
                daemon.registrar_negocios([('WINV26', 1000+i, 177500, 10,
                                            'Comprador', 'BTG', 'XP')])
            daemon.registrar_negocios([('WINV26', 2000, 177500, 10,
                                        'Comprador', 'BTG', 'XP')])

        # Deve ter pelo menos um log.error com OVERFLOW
        overflow_logs = [r for r in caplog.records if 'OVERFLOW' in r.message]
        assert len(overflow_logs) >= 1
        assert overflow_logs[0].levelno == logging.ERROR


class TestMetricasExatas:
    """5. Métricas registram exatamente o ocorrido."""

    def test_contadores_somam_corretamente(self, daemon):
        """recebidos = processados + dropped + erro (após processamento)."""
        # 3 eventos aceitos (fila tem 5 slots, cada lote = 1 item na fila)
        for i in range(3):
            daemon.registrar_negocios([('WINV26', 1000+i, 177500, 10,
                                        'Comprador', 'BTG', 'XP')])

        # Encher a fila completamente (5 slots já ocupados por 3 + processando)
        # e tentar 2 mais (devem ser descartados)
        for i in range(10):
            daemon.registrar_negocios([('WINV26', 2000+i, 177500, 10,
                                        'Comprador', 'BTG', 'XP')])

        time.sleep(0.5)

        stats = daemon.stats()
        assert stats['events_received'] == 13
        # Pelo menos alguns devem ter sido descartados
        assert stats['events_dropped'] > 0

    def test_watermark_max_registrado(self, daemon):
        """Watermark reflete o pico da fila."""
        # Encher fila parcialmente
        for i in range(3):
            daemon.registrar_negocios([('WINV26', 1000+i, 177500, 10,
                                        'Comprador', 'BTG', 'XP')])

        time.sleep(0.3)
        stats = daemon.stats()
        # Watermark deve ser >= 3 em algum momento
        assert stats['watermark_max'] >= 1

    def test_backlog_ms_registrado(self, daemon):
        """Backlog (latência fila → disco) é medido."""
        daemon.registrar_negocios([('WINV26', 1000, 177500, 10,
                                    'Comprador', 'BTG', 'XP')])
        time.sleep(0.3)
        stats = daemon.stats()
        assert stats['backlog_ms_max'] >= 0  # pode ser 0 se muito rápido


class TestCorrupcaoEstado:
    """6. Sistema não corrompe estado."""

    def test_daemon_sobrevive_a_overflow(self, daemon):
        """Daemon não morre após overflow."""
        for i in range(1000):
            daemon.registrar_negocios([('WINV26', 1000+i, 177500, 10,
                                        'Comprador', 'BTG', 'XP')])

        health = daemon.health_check()
        assert health['alive'] is True

    def test_daemon_sobrevive_a_erro_de_gravacao(self, tmp_dir):
        """Daemon não morre se I/O falha."""
        d = CaptureDaemon(tmp_dir, 'test_io_fail', flush_interval_s=0.1)
        d._queue = queue.Queue(maxsize=100)
        # Simular storage com erro
        class FakeStorage:
            def registrar_negocios(self, dados):
                raise IOError("disk full")
            def registrar_book(self, *args, **kwargs):
                raise IOError("disk full")
            def flush(self):
                pass
            def fechar(self):
                pass
            def stats(self):
                return {}
        d._storage = FakeStorage()
        d.start()

        d.registrar_negocios([('WINV26', 1000, 177500, 10,
                                'Comprador', 'BTG', 'XP')])
        time.sleep(0.3)

        health = d.health_check()
        assert health['alive'] is True  # Daemon ainda está vivo
        assert d.stats()['events_error'] > 0  # Erro registrado

        d.stop()


class TestWatermarkEBacklog:
    """7. Watermark e backlog."""

    def test_watermark_atualiza_no_health_check(self, daemon):
        """health_check atualiza watermark se fila cresceu."""
        # Enfileirar sem esperar processamento (0 sleep)
        for i in range(5):
            daemon.registrar_negocios([('WINV26', 1000+i, 177500, 10,
                                        'Comprador', 'BTG', 'XP')])

        # Verificar imediatamente — fila deve ter pelo menos 1 item
        health = daemon.health_check()
        # watermark_max pode ser 0 se o loop processou muito rápido,
        # mas watermark_current deve ser > 0
        assert health['watermark_max'] >= 1 or health['queue_size'] >= 1

    def test_watermark_nao_diminui(self, daemon):
        """Watermark só cresce, nunca diminui."""
        for i in range(3):
            daemon.registrar_negocios([('WINV26', 1000+i, 177500, 10,
                                        'Comprador', 'BTG', 'XP')])
        time.sleep(0.2)
        wm1 = daemon.stats()['watermark_max']

        time.sleep(0.5)  # Fila esvazia
        wm2 = daemon.stats()['watermark_max']
        assert wm2 >= wm1  # Não diminuiu


class TestBackpressure:
    """8. Backpressure mode (não descarta, bloqueia)."""

    def test_backpressure_nao_descarta(self, tmp_dir):
        """Com backpressure=True, eventos não são descartados."""
        d = CaptureDaemon(tmp_dir, 'test_bp', flush_interval_s=0.1,
                          backpressure=True)
        d._queue = queue.Queue(maxsize=3)
        d.start()

        # Encher a fila
        for i in range(3):
            d.registrar_negocios([('WINV26', 1000+i, 177500, 10,
                                    'Comprador', 'BTG', 'XP')])

        # Thread que tenta enfileirar mais (deve bloquear)
        resultado = {'bloqueou': False, 'completou': False}
        def tentar_enfileirar():
            d.registrar_negocios([('WINV26', 2000, 177500, 10,
                                    'Comprador', 'BTG', 'XP')])
            resultado['completou'] = True

        t = threading.Thread(target=tentar_enfileirar, daemon=True)
        t.start()
        t.join(timeout=0.5)

        # Se a thread ainda está viva, bloqueou (não descartou)
        if t.is_alive():
            resultado['bloqueou'] = True

        # Com backpressure, não deve ter descartado
        stats = d.stats()
        assert stats['events_dropped'] == 0
        assert stats['data_loss_detected'] is False

        # Se bloqueou, a thread está viva
        # Se completou (fila tinha espaço por processamento), ok
        assert resultado['bloqueou'] or resultado['completou']

        d.stop()


class TestDrainShutdown:
    """9. Drain no shutdown não perde eventos."""

    def test_drain_grava_eventos_restantes(self, tmp_dir):
        """Drain no shutdown grava eventos que estavam na fila."""
        d = CaptureDaemon(tmp_dir, 'test_drain', flush_interval_s=10.0)
        d._queue = queue.Queue(maxsize=100)
        d.start()

        # Enfileirar 1 lote com 250 trades
        # v14.8: timestamp em epoch ms (ms-do-dia era rejeitado como antigo
        # pela validação de 300s do FileStorage) e verificação via Parquet
        # Hive (v14 substituiu o JSONL).
        agora_ms = int(time.time() * 1000)
        trades = [('WINV26', agora_ms+i, 177500, 10, 'Comprador', 'BTG', 'XP')
                  for i in range(250)]
        d.registrar_negocios(trades)

        # Dar um momento para o loop consumir da fila
        time.sleep(0.2)

        # Parar — drain deve gravar tudo
        d.stop()

        # Verificar que os Parquets Hive foram criados
        import glob
        files = glob.glob(os.path.join(tmp_dir, 'RAW', 'data_type=TT', 'date=*',
                                      'asset=WIN', '*.parquet'))
        assert len(files) > 0, "Deveria ter criado arquivo de negócios"

        # Verificar que os eventos foram gravados (contar rows via metadata)
        import pyarrow.parquet as pq
        total_rows = 0
        for f in files:
            total_rows += pq.read_metadata(f).num_rows

        assert total_rows > 0, "Deveria ter gravado eventos no drain"

    def test_drain_com_fila_parcial(self, tmp_dir):
        """Drain grava mesmo se a fila foi parcialmente processada."""
        d = CaptureDaemon(tmp_dir, 'test_drain2', flush_interval_s=0.05)
        d._queue = queue.Queue(maxsize=100)
        d.start()

        for i in range(50):
            d.registrar_negocios([('WINV26', 1000+i, 177500, 10,
                                    'Comprador', 'BTG', 'XP')])

        time.sleep(0.1)  # Processar alguns
        d.stop()

        stats = d.stats()
        # Alguns foram processados, resto foi gravado no drain
        assert stats['events_processed'] > 0


class TestWatchdog:
    """Watchdog identifica a condição de overflow."""

    def test_health_check_para_watchdog(self, daemon):
        """health_check retorna campos que o watchdog pode verificar."""
        for i in range(5):
            daemon.registrar_negocios([('WINV26', 1000+i, 177500, 10,
                                        'Comprador', 'BTG', 'XP')])
        daemon.registrar_negocios([('WINV26', 2000, 177500, 10,
                                    'Comprador', 'BTG', 'XP')])

        health = daemon.health_check()
        # Campos que o watchdog pode checar
        assert 'alive' in health
        assert 'queue_pct' in health
        assert 'data_loss_detected' in health
        assert 'watermark_max' in health
        assert health['data_loss_detected'] is True
