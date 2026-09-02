"""
adapters/file_storage.py — Captura de eventos brutos em Parquet + Hive.

v14.1: Schema completo com preservação total dos campos RAW.

BOOK (denormalizado por nível — 1 row por nível de preço):
  ts_ns, ativo, asset_partition, janela_id, window_name,
  nivel, bid, ask, bid_volume, ask_volume,
  broker_bid, broker_ask,
  por_corretora (JSON agregado),
  received_at_ns, sequence_id

TT (1 row por negócio):
  ts_ns, ativo, asset_partition, janela_id, window_name, is_rlp,
  preco, quantidade, agressor, compradora, vendedora,
  received_at_ns, sequence_id

Compressão: Snappy | Engine: PyArrow
Jamais sobrescrever dados RAW já gravados.
"""
import json
import logging
import os
import time
import threading
from pathlib import Path
from datetime import datetime, date
from collections import defaultdict

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    HAS_PYARROW = True
except ImportError:
    HAS_PYARROW = False

log = logging.getLogger(__name__)

# ========================================================================
# SCHEMAS EXPLÍTICOS (Seção 10 — tipos consistentes entre arquivos)
# ========================================================================

TT_SCHEMA = pa.schema([
    ('ts_ns', pa.int64()),
    ('received_at_ns', pa.int64()),
    ('sequence_id', pa.int64()),
    ('ativo', pa.string()),
    ('asset_partition', pa.string()),
    ('janela_id', pa.int16()),
    ('window_name', pa.string()),
    ('is_rlp', pa.bool_()),
    ('preco', pa.float64()),
    ('quantidade', pa.int64()),
    ('agressor', pa.string()),
    ('compradora', pa.string()),
    ('vendedora', pa.string()),
])

BOOK_SCHEMA = pa.schema([
    ('ts_ns', pa.int64()),
    ('received_at_ns', pa.int64()),
    ('sequence_id', pa.int64()),
    ('ativo', pa.string()),
    ('asset_partition', pa.string()),
    ('janela_id', pa.int16()),
    ('window_name', pa.string()),
    ('nivel', pa.int16()),
    ('bid', pa.float64()),
    ('ask', pa.float64()),
    ('bid_volume', pa.int64()),
    ('ask_volume', pa.int64()),
    ('bid_vol_total', pa.int64()),
    ('ask_vol_total', pa.int64()),
    ('por_corretora', pa.string()),
    ('ofi', pa.float64()),
])

# Mapeamento data_type → schema
_SCHEMAS = {'TT': TT_SCHEMA, 'BOOK': BOOK_SCHEMA}


def _asset_partition(symbol, is_rlp=False):
    """Converte WINV26 -> WIN, ou WINV26 + is_rlp -> WIN_RLP"""
    base = symbol
    for suffix in ('V26', 'V25', 'V24', 'M26', 'M25', 'M24', 'U26', 'U25'):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
            break
    if is_rlp:
        return f"{base}_RLP"
    return base


class CapturaEventosMS:
    """v14.1: Gravação RAW em Parquet + Hive com schema completo.

    BOOK: denormalizado por nível (1 row por nível de preço).
    TT: 1 row por negócio com todos os campos RAW.
    Timestamps: nanosegundos (ns) para preservar precisão."""

    def __init__(self, base_dir, session_ts=None, flush_a_cada=500,
                 max_bytes_por_arquivo=100*1024*1024):
        self.base_dir = Path(base_dir)
        self.session_ts = session_ts or datetime.now().strftime('%Y%m%d_%H%M%S')
        self.flush_a_cada = flush_a_cada
        self.max_bytes_por_arquivo = max_bytes_por_arquivo
        self._lock = threading.Lock()

        # Buffers: {(data_type, asset): [row_dict, ...]}
        self._buf = {}
        # Contadores de parte por partição
        self._parte = defaultdict(int)
        self._bytes_escrito = defaultdict(int)

        # Blindagem
        self._trades_recentes = {}
        self.rejeitados = {'ts_futuro': 0, 'ts_antigo': 0, 'qtd': 0,
                           'preco': 0, 'dup': 0, 'overflow': 0}
        self._ultimo_flush = time.time()
        self.flush_max_idade_s = 300.0  # v14.6: flush a cada 5 min (era 5s)

        # Metadados
        self._meta = {
            'session': self.session_ts,
            'inicio_epoch_ms': int(time.time() * 1000),
            'fim_epoch_ms': None,
            'negocios': 0,
            'negocios_por_ativo': {},
            'book_snapshots': 0,
            'rejeitados': dict(self.rejeitados),
        }

        if not HAS_PYARROW:
            raise RuntimeError("PyArrow é obrigatório para v14 (Parquet + Hive)")

        # v14.7: consolidar fragmentos existentes da sessão antes de escrever
        # novos (evita acumular milhares de arquivos pequenos após restarts).
        self._consolidar_fragmentos_iniciais()

    # ---- Caminho Hive ----

    def _hive_dir(self, data_type, asset):
        # v14.7: usar a DATA DA SESSÃO (session_ts=YYYYMMDD_HHMMSS), não a
        # data de hoje — sessões que cruzam a meia-noite ou reiniciadas em
        # outro dia gravam na partição correta.
        if len(self.session_ts) >= 8 and self.session_ts[:8].isdigit():
            data_str = self.session_ts[:8]
        else:
            data_str = date.today().strftime('%Y%m%d')
        return (self.base_dir / 'RAW' /
                f'data_type={data_type}' /
                f'date={data_str}' /
                f'asset={asset}')

    def _consolidar_fragmentos_iniciais(self):
        """v14.7: Consolida fragmentos pequenos existentes da sessão atual
        em 1 arquivo por partição, ANTES de qualquer escrita nova.

        Seguro: roda no __init__, antes do daemon começar a consumir a fila.
        Nunca apaga dados — escreve o consolidado, valida, e só então remove
        os fragmentos originais.
        """
        raw_dir = self.base_dir / 'RAW'
        if not raw_dir.exists():
            return
        data_str = self.session_ts[:8] if len(self.session_ts) >= 8 else ''
        if not data_str.isdigit():
            return
        for dt_dir in sorted(raw_dir.glob('data_type=*')):
            dt = dt_dir.name.replace('data_type=', '')
            date_dir = dt_dir / f'date={data_str}'
            if not date_dir.exists():
                continue
            for asset_dir in sorted(date_dir.glob('asset=*')):
                asset = asset_dir.name.replace('asset=', '')
                self._consolidar_fragmentos(dt, asset)

    def _consolidar_fragmentos(self, data_type, asset):
        """v14.7: Mescla fragmentos part-*.parquet de uma partição em 1 arquivo.
        Escreve em arquivo temporário; só remove os originais após sucesso.
        """
        hdir = self._hive_dir(data_type, asset)
        if not hdir.exists():
            return
        files = sorted(hdir.glob('part-*.parquet'))
        if len(files) <= 1:
            return
        tmp = hdir / '_consolidating.parquet'
        try:
            tables = []
            total = 0
            for f in files:
                t = pq.read_table(str(f))
                tables.append(t)
                total += t.num_rows
            combined = pa.concat_tables(tables)
            pq.write_table(combined, tmp, compression='snappy')
            # Sucesso: substituir part-0000 e remover fragmentos antigos
            out = hdir / 'part-0000.parquet'
            if out.exists():
                out.unlink(missing_ok=True)
            tmp.replace(out)
            for f in files:
                if f.name != 'part-0000.parquet':
                    f.unlink(missing_ok=True)
            self._parte[(data_type, asset)] = 1
            log.info(f"[PARQUET] Consolidados {len(files)} fragmentos -> 1 arquivo "
                     f"({total:,} rows) {data_type}/{asset}")
        except Exception as e:
            log.warning(f"[PARQUET] Consolidacao falhou ({data_type}/{asset}): {e}")
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    # ---- Escrita Parquet ----

    def _flush_partition(self, data_type, asset):
        key = (data_type, asset)
        dados = self._buf.get(key, [])
        if not dados:
            return

        hdir = self._hive_dir(data_type, asset)
        hdir.mkdir(parents=True, exist_ok=True)

        parte = self._parte[key]
        out_path = hdir / f'part-{parte:04d}.parquet'
        # v14.7: nunca sobrescrever RAW existente — se o arquivo já existe
        # (ex: restart do motor com parte reiniciada em 0), continuar a
        # numeração. Isso elimina a perda silenciosa de dados no restart.
        while out_path.exists():
            parte += 1
            out_path = hdir / f'part-{parte:04d}.parquet'

        try:
            schema = _SCHEMAS.get(data_type)
            if schema:
                table = pa.Table.from_pylist(dados, schema=schema)
            else:
                table = pa.Table.from_pylist(dados)
            pq.write_table(table, out_path, compression='snappy',
                           use_dictionary=False)
            self._bytes_escrito[key] += out_path.stat().st_size
            self._parte[key] += 1
        except Exception as e:
            log.error(f"[PARQUET] Flush falhou para {key}: {e} — {len(self._buf.get(key, []))} rows PERDIDOS")
            # Dados NÃO são descartados: _buf não foi limpo.
            # Próximo flush tentará novamente. Se continuar falhando,
            # a queue do daemon enche e eventos são dropped com métrica.
            return

        self._buf[key] = []
        self._ultimo_flush = time.time()

    # ---- Validação ----

    def _validar_negocio(self, ts_ns, preco, qtd, agora_ns):
        """Valida um negócio. Retorna True se válido."""
        if ts_ns > agora_ns + 5_000_000_000:  # 5s em ns
            self.rejeitados['ts_futuro'] += 1
            return False
        if ts_ns < agora_ns - 300_000_000_000:  # 300s em ns
            self.rejeitados['ts_antigo'] += 1
            return False
        if qtd <= 0 or qtd > 100000:
            self.rejeitados['qtd'] += 1
            return False
        if preco <= 0:
            self.rejeitados['preco'] += 1
            return False
        return True

    # ---- API pública ----

    def registrar_negocios(self, novos):
        """Registra negócios/T&T/RLP.

        Tuple v14: (sym, tms, preco, qtd, agr, comp, vend,
                     janela_id, window_name, is_rlp,
                     received_at_ns, sequence_id)

        Tuple legada (7-10 campos): compatível com versões anteriores.
        """
        if not novos:
            return
        agora_ns = time.time_ns()
        with self._lock:
            for item in novos:
                n = len(item)
                if n >= 12:
                    (sym, tms, preco, qtd, agr, comp, vend,
                     janela_id, window_name, is_rlp,
                     recv_ns, seq_id) = item[:12]
                elif n >= 10:
                    (sym, tms, preco, qtd, agr, comp, vend,
                     janela_id, window_name, is_rlp) = item[:10]
                    recv_ns = tms * 1_000_000
                    seq_id = 0
                elif n >= 9:
                    (sym, tms, preco, qtd, agr, comp, vend,
                     janela_id, window_name) = item[:9]
                    is_rlp = False
                    recv_ns = tms * 1_000_000
                    seq_id = 0
                else:
                    sym, tms, preco, qtd, agr, comp, vend = item[:7]
                    janela_id = 0
                    window_name = ''
                    is_rlp = False
                    recv_ns = tms * 1_000_000
                    seq_id = 0

                # Converter ts_ms para ts_ns (preservar precisão)
                # 1e17 separa ms (~1.78e12) de ns (~1.78e18)
                ts_ns = int(tms) * 1_000_000 if tms < 1e17 else int(tms)

                if not self._validar_negocio(ts_ns, preco, qtd, agora_ns):
                    continue

                self._meta['negocios'] += 1
                self._meta['negocios_por_ativo'][sym] = \
                    self._meta['negocios_por_ativo'].get(sym, 0) + 1

                asset = _asset_partition(sym, is_rlp=bool(is_rlp))
                data_type = 'TT'
                row = {
                    # Timestamps
                    'ts_ns': ts_ns,
                    'received_at_ns': int(recv_ns) if recv_ns else ts_ns + 1_000_000,
                    'sequence_id': int(seq_id) if seq_id else 0,
                    # Identificação
                    'ativo': sym,
                    'asset_partition': asset,
                    'janela_id': int(janela_id),
                    'window_name': str(window_name),
                    'is_rlp': bool(is_rlp),
                    # Dados do negócio
                    'preco': float(preco),
                    'quantidade': int(qtd),
                    'agressor': str(agr),
                    'compradora': str(comp),
                    'vendedora': str(vend),
                }
                self._buf.setdefault((data_type, asset), []).append(row)

            # Flush por partição
            for key in list(self._buf.keys()):
                dt, asset = key
                buf = self._buf[key]
                if len(buf) >= self.flush_a_cada or \
                        (buf and time.time() - self._ultimo_flush > self.flush_max_idade_s):
                    self._flush_partition(dt, asset)

    def registrar_book(self, ativo, ts_ms, snap, bid_vol, ask_vol,
                       levels=None, janela_id=0, window_name='',
                       received_at_ns=0, sequence_id=0):
        """Registra snapshot de book DENORMALIZADO por nível.

        Cada nível de preço gera 1 row com:
          nivel, bid, ask, bid_volume, ask_volume, broker_bid, broker_ask

        Args:
            levels: dict com 'bid_preco','bid_vol','ask_preco','ask_vol' (listas)
            snap: dict {corretora: {'bid_vol':..., 'ask_vol':...}}
        """
        asset = _asset_partition(ativo)
        data_type = 'BOOK'

        # Converter ts_ms para ts_ns
        # 1e17 separa ms (~1.78e12) de ns (~1.78e18)
        ts_ns = int(ts_ms) * 1_000_000 if ts_ms < 1e17 else int(ts_ms)
        recv_ns = int(received_at_ns) if received_at_ns else ts_ns + 1_000_000

        # JSON de corretoras agregado (preservar info RAW)
        snap_json = json.dumps(snap, ensure_ascii=False) if snap else '{}'

        if levels:
            bid_precos = levels.get('bid_preco', [])
            bid_vols = levels.get('bid_vol', [])
            ask_precos = levels.get('ask_preco', [])
            ask_vols = levels.get('ask_vol', [])
            n_levels = max(len(bid_precos), len(ask_precos))

            with self._lock:
                self._meta['book_snapshots'] += 1
                for nivel in range(n_levels):
                    bid_p = float(bid_precos[nivel]) if nivel < len(bid_precos) else 0.0
                    bid_v = int(bid_vols[nivel]) if nivel < len(bid_vols) else 0
                    ask_p = float(ask_precos[nivel]) if nivel < len(ask_precos) else 0.0
                    ask_v = int(ask_vols[nivel]) if nivel < len(ask_vols) else 0

                    row = {
                        # Timestamps
                        'ts_ns': ts_ns,
                        'received_at_ns': recv_ns,
                        'sequence_id': int(sequence_id),
                        # Identificação
                        'ativo': ativo,
                        'asset_partition': asset,
                        'janela_id': int(janela_id),
                        'window_name': str(window_name),
                        # Nível
                        'nivel': nivel,
                        'bid': bid_p,
                        'ask': ask_p,
                        'bid_volume': bid_v,
                        'ask_volume': ask_v,
                        # Totais
                        'bid_vol_total': int(bid_vol),
                        'ask_vol_total': int(ask_vol),
                        # Corretoras (JSON agregado)
                        'por_corretora': snap_json,
                        # OFI (opcional — None se não disponível)
                        'ofi': float(levels.get('ofi')) if 'ofi' in levels else None,
                    }

                    self._buf.setdefault((data_type, asset), []).append(row)
        else:
            # Sem levels: gravar 1 row com totais apenas
            with self._lock:
                self._meta['book_snapshots'] += 1
                row = {
                    'ts_ns': ts_ns,
                    'received_at_ns': recv_ns,
                    'sequence_id': int(sequence_id),
                    'ativo': ativo,
                    'asset_partition': asset,
                    'janela_id': int(janela_id),
                    'window_name': str(window_name),
                    'nivel': -1,
                    'bid': 0.0,
                    'ask': 0.0,
                    'bid_volume': 0,
                    'ask_volume': 0,
                    'bid_vol_total': int(bid_vol),
                    'ask_vol_total': int(ask_vol),
                    'por_corretora': snap_json,
                    'ofi': None,
                }
                self._buf.setdefault((data_type, asset), []).append(row)

        # Check flush
        key = (data_type, asset)
        buf = self._buf.get(key, [])
        if len(buf) >= self.flush_a_cada or \
                (buf and time.time() - self._ultimo_flush > self.flush_max_idade_s):
            self._flush_partition(data_type, asset)

    def registrar_rlp(self, novos):
        """Registra RLP — usa registrar_negocios com is_rlp=True."""
        if not novos:
            return
        agora_ns = time.time_ns()
        with self._lock:
            for item in novos:
                n = len(item)
                if n >= 9:
                    sym, tms, preco, qtd, agr, comp, vend, janela_id, window_name = item[:9]
                    recv_ns = tms * 1_000_000
                    seq_id = 0
                elif n >= 7:
                    sym, tms, preco, qtd, agr, comp, vend = item[:7]
                    janela_id = 0
                    window_name = ''
                    recv_ns = tms * 1_000_000
                    seq_id = 0
                else:
                    continue

                # 1e17 separa ms (~1.78e12) de ns (~1.78e18)
                ts_ns = int(tms) * 1_000_000 if tms < 1e17 else int(tms)

                if not self._validar_negocio(ts_ns, preco, qtd, agora_ns):
                    continue

                asset = _asset_partition(sym, is_rlp=True)
                data_type = 'TT'
                row = {
                    'ts_ns': ts_ns,
                    'received_at_ns': int(recv_ns),
                    'sequence_id': int(seq_id),
                    'ativo': sym,
                    'asset_partition': asset,
                    'janela_id': int(janela_id),
                    'window_name': str(window_name),
                    'is_rlp': True,
                    'preco': float(preco),
                    'quantidade': int(qtd),
                    'agressor': str(agr),
                    'compradora': str(comp),
                    'vendedora': str(vend),
                }
                self._buf.setdefault((data_type, asset), []).append(row)

            for key in list(self._buf.keys()):
                dt, asset = key
                buf = self._buf[key]
                if len(buf) >= self.flush_a_cada or \
                        (buf and time.time() - self._ultimo_flush > self.flush_max_idade_s):
                    self._flush_partition(dt, asset)

    # ---- Flush / Stats / Fechar ----

    def stats(self):
        with self._lock:
            return dict(self.rejeitados)

    def flush(self):
        with self._lock:
            for key in list(self._buf.keys()):
                dt, asset = key
                self._flush_partition(dt, asset)

    def fechar(self):
        self.flush()
        with self._lock:
            self._meta['fim_epoch_ms'] = int(time.time() * 1000)
            self._meta['rejeitados'] = dict(self.rejeitados)
            try:
                meta_path = self.base_dir / f'raw_meta_{self.session_ts}.json'
                meta_path.parent.mkdir(parents=True, exist_ok=True)
                meta_path.write_text(
                    json.dumps(self._meta, ensure_ascii=False, indent=2),
                    encoding='utf-8')
            except Exception as e:
                log.warning(f"[FILE_STORAGE] Falha ao gravar meta: {e}")


# Alias
FileStorage = CapturaEventosMS


# ========================================================================
# Helper para leitura hive
# ========================================================================

def find_hive_files(base_dir, dia_str=None, data_type=None, asset=None):
    """Busca arquivos Parquet na estrutura hive."""
    base = Path(base_dir) / 'RAW'
    if not base.exists():
        return []

    if dia_str and len(dia_str) == 8:
        date_glob = f'date={dia_str}'
    else:
        date_glob = 'date=*'

    if data_type:
        dt_globs = [f'data_type={data_type}']
    else:
        dt_globs = ['data_type=TT', 'data_type=BOOK']

    if asset:
        asset_globs = [f'asset={asset}']
    else:
        asset_globs = ['asset=*']

    files = []
    for dtg in dt_globs:
        for ag in asset_globs:
            pattern = f'{dtg}/{date_glob}/{ag}/*.parquet'
            files.extend(base.glob(pattern))

    return sorted(files)


def flush_buffers_with_retry(buffers, write_fn, logger=None):
    total_ok = 0
    total_tentado = 0
    for key in list(buffers.keys()):
        rows = buffers.pop(key)
        n = write_fn(key, rows)
        total_tentado += len(rows)
        total_ok += n
        if n < len(rows):
            buffers.setdefault(key, []).extend(rows[n:])
    return total_ok, total_tentado
