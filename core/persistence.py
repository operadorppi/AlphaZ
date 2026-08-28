# -*- coding: utf-8 -*-
"""
core/persistence.py — Gravação de trades, decisões e checkpoints.

Extrai de Analise:
  - _garantir_fp, _gravar_trade, _gravar_decisao
  - _rotacionar, _flush_trades, _flush_decisoes
  - _carregar_posicao_checkpoint, _salvar_posicao_checkpoint
  - salvar_sessao (delega para os flush + checkpoint)
"""

import json
import os
import threading
import time
import logging
from pathlib import Path
from datetime import datetime

log = logging.getLogger(__name__)


class Persistence:
    """Gravação de trades e decisões em JSONL com rotação por tamanho."""

    def __init__(self, base_dir, session_ts=None):
        self.base_dir = base_dir
        self.session_ts = session_ts or datetime.now().strftime('%Y%m%d_%H%M%S')
        self._fp = None
        self._fp_dec = None
        self._io_lock = threading.Lock()
        self._buf_trades = []
        self._buf_decisoes = []
        self._parte = {'_fp': 1, '_fp_dec': 1}
        self._fsync_counter_trades = 0
        self._fsync_counter_decisoes = 0
        self._fsync_a_cada = 20
        self.max_bytes_trade_file = 100 * 1024 * 1024  # 100MB
        self._posicao_checkpoint_path = Path(base_dir) / 'posicao_atual.json'

    def garantir_fp(self):
        """Garante que os file handles estão abertos."""
        with self._io_lock:
            if self._fp is None:
                try:
                    out = Path(self.base_dir)
                    out.mkdir(parents=True, exist_ok=True)
                    self._fp = open(out / f'negocios_{self.session_ts}.jsonl', 'a', encoding='utf-8')
                except Exception as e:
                    log.warning(f"[IO] falha ao abrir negocios jsonl: {e}")
            if self._fp_dec is None:
                try:
                    out = Path(self.base_dir)
                    out.mkdir(parents=True, exist_ok=True)
                    self._fp_dec = open(out / f'decisoes_{self.session_ts}.jsonl', 'a', encoding='utf-8')
                except Exception as e:
                    log.warning(f"[IO] falha ao abrir decisoes jsonl: {e}")
            return self._fp, self._fp_dec

    def gravar_trade(self, neg):
        self.garantir_fp()
        self._buf_trades.append(json.dumps(neg, ensure_ascii=False))
        if len(self._buf_trades) >= 200:
            self._flush_trades()

    def gravar_decisao(self, dec):
        self.garantir_fp()
        self._buf_decisoes.append(json.dumps(dec, ensure_ascii=False, default=str))
        if len(self._buf_decisoes) >= 50:
            self._flush_decisoes()

    def _rotacionar(self, attr, prefix):
        fp = getattr(self, attr)
        if fp is not None:
            try:
                fp.close()
            except Exception:
                pass
        self._parte[attr] += 1
        out = Path(self.base_dir)
        nome = out / f'{prefix}_{self.session_ts}_p{self._parte[attr]:02d}.jsonl'
        setattr(self, attr, open(nome, 'a', encoding='utf-8'))

    def _flush_trades(self):
        with self._io_lock:
            if self._buf_trades and self._fp is not None:
                try:
                    if self._fp.tell() >= self.max_bytes_trade_file:
                        self._rotacionar('_fp', 'negocios')
                    self._fp.write('\n'.join(self._buf_trades) + '\n')
                    self._fp.flush()
                    self._fsync_counter_trades += 1
                    if self._fsync_counter_trades >= self._fsync_a_cada:
                        os.fsync(self._fp.fileno())
                        self._fsync_counter_trades = 0
                except Exception as e:
                    log.warning(f"[IO] falha ao gravar trades: {e}")
                self._buf_trades.clear()

    def _flush_decisoes(self):
        with self._io_lock:
            if self._buf_decisoes and self._fp_dec is not None:
                try:
                    if self._fp_dec.tell() >= self.max_bytes_trade_file:
                        self._rotacionar('_fp_dec', 'decisoes')
                    self._fp_dec.write('\n'.join(self._buf_decisoes) + '\n')
                    self._fp_dec.flush()
                    self._fsync_counter_decisoes += 1
                    if self._fsync_counter_decisoes >= self._fsync_a_cada:
                        os.fsync(self._fp_dec.fileno())
                        self._fsync_counter_decisoes = 0
                except Exception as e:
                    log.warning(f"[IO] falha ao gravar decisoes: {e}")
                self._buf_decisoes.clear()

    def flush(self):
        """Força flush de todos os buffers."""
        self._flush_trades()
        self._flush_decisoes()

    # ---- Checkpoint de posição ----

    def carregar_checkpoint(self):
        """Restaura posição do último checkpoint. Retorna dict ou None."""
        if self._posicao_checkpoint_path.exists():
            try:
                data = json.loads(self._posicao_checkpoint_path.read_text(encoding='utf-8'))
                aberta_em = data.get('aberta_em', 0)
                if aberta_em and (time.time() - aberta_em) > 12 * 3600:
                    log.info('[POS] Checkpoint stale (>12h) — ignorando')
                    self._posicao_checkpoint_path.unlink(missing_ok=True)
                    return None
                if data.get('aberta'):
                    pos = {
                        'ativo': data['ativo'], 'lado': data['lado'],
                        'entrada': data['entrada'], 'preco_medio': data['entrada'],
                        'stop_preco': data.get('stop_preco'), 'tp': data.get('tp'),
                        'aberta_em': data.get('aberta_em', time.time()),
                        'motivos': data.get('motivos', []), 'contrib': data.get('contrib', []),
                        'mfe': data.get('mfe', 0.0), 'mae': data.get('mae', 0.0),
                        'breakeven_ativado': data.get('breakeven_ativado', False),
                        'quantidade': data.get('quantidade', 1)
                    }
                    log.warning("Posição recuperada do checkpoint")
                    return pos
            except Exception as e:
                log.warning(f"[POS] falha ao carregar checkpoint: {e}")
        return None

    def salvar_checkpoint(self, posicao):
        """Salva posição atual em disco. posicao=None apaga o checkpoint."""
        try:
            if posicao is None:
                if self._posicao_checkpoint_path.exists():
                    self._posicao_checkpoint_path.unlink()
            else:
                pos = posicao
                data = {
                    'aberta': True, 'ativo': pos['ativo'], 'lado': pos['lado'],
                    'entrada': pos['entrada'], 'stop_preco': pos.get('stop_preco'),
                    'tp': pos.get('tp'), 'aberta_em': pos.get('aberta_em'),
                    'motivos': pos.get('motivos', []), 'contrib': pos.get('contrib', []),
                    'mfe': pos.get('mfe', 0.0), 'mae': pos.get('mae', 0.0),
                    'breakeven_ativado': pos.get('breakeven_ativado', False),
                    'quantidade': pos.get('quantidade', 1)
                }
                self._posicao_checkpoint_path.write_text(
                    json.dumps(data, ensure_ascii=False), encoding='utf-8')
        except Exception as e:
            log.warning(f"[POS] falha ao salvar checkpoint: {e}")

    def close(self):
        """Fecha todos os file handles."""
        with self._io_lock:
            for fp in (self._fp, self._fp_dec):
                if fp is not None:
                    try:
                        fp.close()
                    except Exception:
                        pass
            self._fp = None
            self._fp_dec = None

    def salvar_sessao(self, final=False, salvar_aprendizado=None,
                     padroes=None):
        """Flush tudo + checkpoint + aprendizado + padrões."""
        self.flush()
        if salvar_aprendizado:
            salvar_aprendizado(self.base_dir)
        if padroes:
            padroes.aplicar_decay()
            padroes.salvar()
        if final:
            self.close()
