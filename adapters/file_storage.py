"""
adapters/file_storage.py — Captura de eventos brutos (JSONL com timestamp ms).

Adapter para gravação de dados brutos (negócios + book snapshots)
em disco para replay offline. Alias: CapturaEventosMS = FileStorage.

Formato gravado (JSONL, um objeto por linha):
  raw_negocios_ms_<session>.jsonl  — um por negócio
  raw_book_ms_<session>.jsonl      — um por snapshot de book (250ms)

Este módulo NÃO decide trade, NÃO calcula score. Só grava dados brutos.
"""""
import json
import os
import time
import threading
from pathlib import Path
from datetime import datetime


class CapturaEventosMS:
    """Buffer thread-safe que grava negócios e snapshots de book brutos
    em disco com timestamp de milissegundo, para replay offline pela
    Feature Engine."""

    def __init__(self, base_dir, session_ts=None, flush_a_cada=200,
                 max_bytes_por_arquivo=100*1024*1024):
        self.base_dir = Path(base_dir)
        self.session_ts = session_ts or datetime.now().strftime('%Y%m%d_%H%M%S')
        self.flush_a_cada = flush_a_cada
        self.max_bytes_por_arquivo = max_bytes_por_arquivo
        self._lock = threading.Lock()
        self._buf_neg = []
        self._buf_book = []
        self._fp_neg = None
        self._fp_book = None
        # v8: blindagem da captura
        self._trades_recentes = {}   # hash -> ts de chegada (dedup)
        self.rejeitados = {'ts_futuro': 0, 'ts_antigo': 0, 'qtd': 0,
                           'preco': 0, 'dup': 0, 'overflow': 0}
        self._ultimo_flush = time.time()
        self.flush_max_idade_s = 5.0   # flusha por idade mesmo sem encher o buffer
        # v9.9: rotação por tamanho (parte 0 = nome sem sufixo)
        self._parte = {'neg': 0, 'book': 0}
        self._bytes_arquivo = {'neg': 0, 'book': 0}
        # v9.10: metadados da sessão (gravados no fechar como raw_meta_*.json)
        self._meta = {
            'session': self.session_ts,
            'inicio_epoch_ms': int(time.time() * 1000),
            'fim_epoch_ms': None,
            'negocios': 0,
            'negocios_por_ativo': {},
            'book_snapshots': 0,
            'rejeitados': dict(self.rejeitados),
        }
        # Cache do offset epoch ↔ time-of-day (recalculado a cada flush)
        self._offset_epoch_tod = None

    def _abrir(self, tipo):
        """Abre o próximo arquivo da rotação. tipo='neg' ou 'book'.
        Parte 0 = nome sem sufixo (backward compat); demais com _pN."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        sufixo = f'_p{self._parte[tipo]:02d}' if self._parte[tipo] > 0 else ''
        self._parte[tipo] += 1
        if tipo == 'neg':
            nome = f'raw_negocios_ms_{self.session_ts}{sufixo}.jsonl'
        else:
            nome = f'raw_book_ms_{self.session_ts}{sufixo}.jsonl'
        self._bytes_arquivo[tipo] = 0
        return open(self.base_dir / nome, 'a', encoding='utf-8')

    def _rotacionar(self, tipo, fp_attr):
        """Fecha o arquivo atual e abre a próxima parte."""
        fp = getattr(self, fp_attr)
        if fp is not None:
            try:
                fp.close()
            except Exception:
                pass
            setattr(self, fp_attr, None)
        setattr(self, fp_attr, self._abrir(tipo))

    @staticmethod
    def _tod_ms(dt=None):
        """Retorna time-of-day em ms (ex.: 09:30:00.000 → 34_200_000)."""
        dt = dt or datetime.now()
        return ((dt.hour * 3600 + dt.minute * 60 + dt.second) * 1000) + dt.microsecond // 1000

    def registrar_negocios(self, novos):
        """novos: lista de tuplas (sym, tms, preco, qtd, agr, comp, vend) —
        exatamente o formato que processar_dados() já produz. Nenhuma
        transformação além de serializar."""
        if not novos:
            return
        agora_epoch = int(time.time() * 1000)
        agora_tod = self._tod_ms()
        # offset = epoch - tod; converte tms (T&T time-of-day) para epoch ms
        # para manter relógio consistente com registros de book (já em epoch ms)
        offset = agora_epoch - agora_tod
        with self._lock:
            for sym, tms, preco, qtd, agr, comp, vend in novos:
                tms_epoch = offset + tms  # T&T tod → epoch ms
                # blindagem 1: timestamp (rejeita replay / clock ruim)
                if tms_epoch > agora_epoch + 5000:
                    self.rejeitados['ts_futuro'] += 1
                    continue
                if tms_epoch < agora_epoch - 300000:   # >5min no passado = replay
                    self.rejeitados['ts_antigo'] += 1
                    continue
                # blindagem 2: quantidade e preço
                if qtd <= 0 or qtd > 100000:
                    self.rejeitados['qtd'] += 1
                    continue
                if preco <= 0:
                    self.rejeitados['preco'] += 1
                    continue
                # blindagem 3: dedup removido - RTD nunca envia duplicado
                # rajadas (bursts) tinham assinatura igual e eram descartadas
                self._meta['negocios'] += 1
                self._meta['negocios_por_ativo'][sym] = self._meta['negocios_por_ativo'].get(sym, 0) + 1
                self._buf_neg.append(json.dumps({
                    'ts_ms': tms_epoch, 'ativo': sym, 'preco': preco, 'qtd': qtd,
                    'agressor': agr, 'compradora': comp, 'vendedora': vend,
                }, ensure_ascii=False))
            # poda do dedup (manter só últimos 60s)
            if len(self._trades_recentes) > 20000:
                corte = agora_epoch - 60000
                self._trades_recentes = {k: v for k, v in self._trades_recentes.items() if v >= corte}
            # proteção de RAM
            if len(self._buf_neg) > 100000:
                self.rejeitados['overflow'] += len(self._buf_neg) - 100000
                del self._buf_neg[:len(self._buf_neg) - 100000]
            # flush por tamanho ou por idade
            if len(self._buf_neg) >= self.flush_a_cada or \
                    (self._buf_neg and time.time() - self._ultimo_flush > self.flush_max_idade_s):
                self._flush_neg()

    def registrar_book(self, ativo, ts_ms, snap, bid_vol, ask_vol, levels=None):
        """snap: dict {corretora: {...}}; levels (opcional): dict com
        'bid_preco','bid_vol','ask_preco','ask_vol' (listas por nível) —
        indispensável para o BookLevelFeatures funcionar no batch."""
        with self._lock:
            reg = {
                'ts_ms': ts_ms, 'ativo': ativo, 'bid_vol': bid_vol, 'ask_vol': ask_vol,
                'por_corretora': snap,
            }
            if levels:
                reg['levels'] = levels
            self._meta['book_snapshots'] += 1
            self._buf_book.append(json.dumps(reg, ensure_ascii=False, default=str))
            if len(self._buf_book) >= self.flush_a_cada or \
                    (self._buf_book and time.time() - self._ultimo_flush > self.flush_max_idade_s):
                self._flush_book()

    def _flush_neg(self):
        if not self._buf_neg:
            return
        if self._fp_neg is None:
            self._fp_neg = self._abrir('neg')
        elif self._bytes_arquivo['neg'] >= self.max_bytes_por_arquivo:
            # v9.9: rotação por tamanho
            self._rotacionar('neg', '_fp_neg')
        try:
            payload = '\n'.join(self._buf_neg) + '\n'
            self._fp_neg.write(payload)
            self._fp_neg.flush()
            os.fsync(self._fp_neg.fileno())   # garante que chegou ao disco
            self._bytes_arquivo['neg'] += len(payload.encode('utf-8'))
        except Exception:
            # NÃO descarta o buffer: reabre o arquivo e tenta de novo depois
            try:
                self._fp_neg.close()
            except Exception:
                pass
            self._fp_neg = None
            return
        self._buf_neg.clear()
        self._ultimo_flush = time.time()

    def _flush_book(self):
        if not self._buf_book:
            return
        if self._fp_book is None:
            self._fp_book = self._abrir('book')
        elif self._bytes_arquivo['book'] >= self.max_bytes_por_arquivo:
            # v9.9: rotação por tamanho
            self._rotacionar('book', '_fp_book')
        try:
            payload = '\n'.join(self._buf_book) + '\n'
            self._fp_book.write(payload)
            self._fp_book.flush()
            os.fsync(self._fp_book.fileno())
            self._bytes_arquivo['book'] += len(payload.encode('utf-8'))
        except Exception:
            try:
                self._fp_book.close()
            except Exception:
                pass
            self._fp_book = None
            return
        self._buf_book.clear()
        self._ultimo_flush = time.time()

    def stats(self):
        """Contadores de rejeição para auditoria (dashboard/log)."""
        with self._lock:
            return dict(self.rejeitados)

    def flush(self):
        with self._lock:
            self._flush_neg()
            self._flush_book()

    def fechar(self):
        self.flush()
        with self._lock:
            # v9.10: grava metadados da sessão (gate de qualidade do retreino)
            self._meta['fim_epoch_ms'] = int(time.time() * 1000)
            self._meta['rejeitados'] = dict(self.rejeitados)
            try:
                (self.base_dir / f'raw_meta_{self.session_ts}.json').write_text(
                    json.dumps(self._meta, ensure_ascii=False, indent=2), encoding='utf-8')
            except Exception:
                pass
            for attr in ('_fp_neg', '_fp_book'):
                fp = getattr(self, attr)
                if fp is not None:
                    try: fp.close()
                    except Exception: pass
                    setattr(self, attr, None)


# Alias para o novo nome da camada adapter
FileStorage = CapturaEventosMS


# ========================================================================
# Flush transacional com retry (B4, v10.1.1)
# ========================================================================

def flush_buffers_with_retry(buffers, write_fn, logger=None):
    """Faz flush dos buffers para disco com retry em falha (B4).

    Antes (motor_web.py thread_escritora):
        pendentes = list(buffers.items())
        buffers.clear()           # ← rows removidas ANTES de gravar
        for key, rows in pendentes:
            total_ok += _append_hour_file(key, rows)
        # Se write falhou, rows foram PERDIDAS silenciosamente.

    Depois (adapters.file_storage):
        Para cada buffer, grava e só remove as rows que foram escritas.
        Rows que falharam voltam ao buffer para retry no próximo ciclo.
        Nenhum dado é descartado.

    Args:
        buffers: dict {key: [rows...]} — modificado in-place
        write_fn: callable(key, rows) -> int (número de rows escritas)
        logger: logger opcional para mensagens de debug

    Returns:
        (total_ok, total_tentado)
    """
    total_ok = 0
    total_tentado = 0
    for key in list(buffers.keys()):
        rows = buffers.pop(key)
        n = write_fn(key, rows)
        total_tentado += len(rows)
        total_ok += n
        if n < len(rows):
            # B4: re-enfileira rows que falharam para retry no próximo flush
            buffers.setdefault(key, []).extend(rows[n:])
    return total_ok, total_tentado
