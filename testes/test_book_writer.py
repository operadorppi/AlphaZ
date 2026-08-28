import sys, os
_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in [_base, os.path.join(_base, "ml"), os.path.join(_base, "scripts")]:
    if os.path.isdir(_d): sys.path.insert(0, _d)
"""
test_book_writer.py — Testes B4: escrita BOOK transacional

ANTES da correcao (thread_escritora): o flush fazia:
    pendentes = list(buffers.items())
    buffers.clear()          # <-- linhas removidas do buffer ANTES de gravar
    for key, rows in pendentes:
        total_ok += _append_hour_file(key, rows)
Se write_parquet_part falhasse, as linhas eram PERDIDAS silenciosamente
(contadas em falhas_gravacao, mas nunca re-enfileiradas nem retentadas).

DEPOIS da correcao: as linhas so saem do buffer APOS confirmacao de
gravacao. Em falha, voltam para o buffer e sao retentadas no proximo flush.
Nenhum dado e descartado.

Os testes abaixo validam:
  1. Falha na 1a tentativa -> dados NAO se perdem: 2a tentativa grava
  2. Falha persistente -> writer continua retentando (nao descarta)
"""
import sys
import os
import time
import signal
import queue
import threading
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import motor_web as mw


def _make_linha_book(tms=1724000000000, simbolo="WINV26"):
    """Linha minima de snapshot BOOK (time_ms + simbolo bastam p/ o writer)."""
    return {
        "capture_sequence": 1,
        "snapshot_id": 1,
        "time_ms": tms,
        "timestamp_recebimento_python": tms,
        "simbolo": simbolo,
        "keepalive": False,
    }


class TestBookWriterTransacional:
    """Testes da escritora BOOK com escrita transacional (B4)."""

    def _setup(self, monkeypatch, write_impl):
        """Patches comuns: writer rapido (flush 0.1s), stats no-op, signal no-op."""
        calls = {'n': 0, 'ultimo_df': None}
        monkeypatch.setattr(mw, 'write_parquet_part', lambda pasta, hora, df, schema: write_impl(calls, df))
        monkeypatch.setattr(mw, '_registrar_book', lambda *a, **k: None)
        monkeypatch.setattr(mw, '_registrar_tt', lambda *a, **k: None)
        monkeypatch.setattr(mw, '_registrar_stat', lambda *a, **k: None)
        monkeypatch.setattr(mw, '_live_inc', lambda *a, **k: None)
        monkeypatch.setattr(mw, 'INTERVALO_SALVAMENTO_S', 0.1)
        monkeypatch.setattr(mw, 'logger', MagicMock())
        # thread_escritora chama signal.signal no corpo — vira no-op no teste
        monkeypatch.setattr(signal, 'signal', lambda *a, **k: None)
        return calls

    def test_falha_na_primeira_gravacao_nao_perde_dados(self, monkeypatch, tmp_path):
        """1a gravacao falha -> dados voltam ao buffer -> 2a gravacao salva."""
        def write_impl(calls, df):
            calls['n'] += 1
            calls['ultimo_df'] = df
            if calls['n'] == 1:
                return False  # 1a tentativa falha
            return True       # 2a tentativa (retry) salva

        calls = self._setup(monkeypatch, write_impl)

        fila = queue.Queue()
        shutdown_event = threading.Event()
        fila.put([_make_linha_book()])

        t = threading.Thread(
            target=mw.thread_escritora,
            args=(fila, 'TESTE_BOOK', 'W', None, str(tmp_path), shutdown_event, None, 0, None),
            daemon=True,
        )
        t.start()

        # Aguarda o retry acontecer (2 tentativas de gravacao)
        deadline = time.time() + 8.0
        while time.time() < deadline and calls['n'] < 2:
            time.sleep(0.05)

        assert calls['n'] >= 2, (
            f"Esperava >=2 tentativas de gravacao (retry apos falha), obtidas {calls['n']}"
        )

        # Encerra o writer
        shutdown_event.set()
        t.join(timeout=8.0)

        assert not t.is_alive(), "Writer deveria ter encerrado apos shutdown"
        # A 2a tentativa recebeu os dados (nada foi descartado)
        assert calls['ultimo_df'] is not None and len(calls['ultimo_df']) >= 1, (
            "A tentativa de retry deveria ter recebido os dados preservados"
        )

    def test_falha_persistente_continua_retentando(self, monkeypatch, tmp_path):
        """Falha persistente -> writer continua retentando (nunca descarta)."""
        def write_impl(calls, df):
            calls['n'] += 1
            calls['ultimo_df'] = df
            return bool(calls.get('succeed', False))  # falha ate succeed=True

        calls = self._setup(monkeypatch, write_impl)

        fila = queue.Queue()
        shutdown_event = threading.Event()
        fila.put([_make_linha_book()])

        t = threading.Thread(
            target=mw.thread_escritora,
            args=(fila, 'TESTE_BOOK', 'W', None, str(tmp_path), shutdown_event, None, 0, None),
            daemon=True,
        )
        t.start()

        # Com falha persistente, o writer deve continuar retentando:
        # varias tentativas de gravacao ao longo do tempo (old code parava em 1)
        deadline = time.time() + 8.0
        while time.time() < deadline and calls['n'] < 3:
            time.sleep(0.05)

        assert calls['n'] >= 3, (
            f"Com falha persistente o writer deveria retentar (>=3 tentativas), "
            f"obtidas {calls['n']}"
        )

        # Agora a gravacao passa a funcionar e o writer drena e encerra
        calls['succeed'] = True
        shutdown_event.set()
        t.join(timeout=8.0)

        assert not t.is_alive(), "Writer deveria ter encerrado apos drenar"
        assert calls['ultimo_df'] is not None and len(calls['ultimo_df']) >= 1, (
            "Os dados deveriam ter sido gravados ao final"
        )

    def test_gravacao_ok_nao_gera_retry(self, monkeypatch, tmp_path):
        """Sem falha, writer grava em 1 tentativa e nao re-emite."""
        def write_impl(calls, df):
            calls['n'] += 1
            calls['ultimo_df'] = df
            return True  # sempre sucesso

        calls = self._setup(monkeypatch, write_impl)

        fila = queue.Queue()
        shutdown_event = threading.Event()
        fila.put([_make_linha_book()])

        t = threading.Thread(
            target=mw.thread_escritora,
            args=(fila, 'TESTE_BOOK', 'W', None, str(tmp_path), shutdown_event, None, 0, None),
            daemon=True,
        )
        t.start()

        # Aguarda a gravacao inicial
        deadline = time.time() + 8.0
        while time.time() < deadline and calls['n'] < 1:
            time.sleep(0.05)
        assert calls['n'] >= 1, "Deveria ter gravado ao menos 1x"

        # Pequena janela sem novas gravacoes (sem retry espurio)
        time.sleep(0.5)
        assert calls['n'] == 1, (
            f"Sem falha nao deveria haver retry, obtidas {calls['n']} gravacoes"
        )

        shutdown_event.set()
        t.join(timeout=8.0)
        assert not t.is_alive(), "Writer deveria ter encerrado apos shutdown"