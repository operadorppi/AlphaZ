# feature_cache.py
# Cache de features computadas (v9.38)
import os, hashlib, time
import pandas as pd

CACHE_DIR = os.path.join(os.environ.get('SINAL_RT_DIR', 'D:/MarketData/mimo'), 'feature_cache')


def _cache_key(extra_deps=None):
    """Hash dos arquivos de codigo que afetam as features."""
    files = ['features_contexto_preco.py', 'features_expansao.py', 'features_lib.py']
    if extra_deps:
        files.extend(extra_deps)
    h = hashlib.md5()
    for f in files:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), f)
        if os.path.exists(p):
            with open(p, 'rb') as fh:
                h.update(fh.read())
    return h.hexdigest()[:12]


def load_or_compute(df_path, compute_fn, name='features', force=False):
    """Carrega features do cache ou computa e salva."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = _cache_key()
    cache_file = os.path.join(CACHE_DIR, name + '_' + key + '.parquet')
    meta_file = os.path.join(CACHE_DIR, name + '_' + key + '.meta')

    if not force and os.path.exists(cache_file):
        t0 = time.time()
        df = pd.read_parquet(cache_file)
        dt = time.time() - t0
        print(f'  [CACHE] {name} carregado do cache ({dt:.1f}s, {len(df)} linhas)')
        return df

    t0 = time.time()
    df = pd.read_parquet(df_path) if isinstance(df_path, str) else df_path
    df = compute_fn(df)
    dt = time.time() - t0
    print(f'  [CACHE] {name} computado ({dt:.1f}s, {len(df)} linhas)')

    for c in df.select_dtypes(include=['float64']).columns:
        if df[c].max() < 3.4e38 and df[c].min() > -3.4e38:
            df[c] = df[c].astype('float32')
    df.to_parquet(cache_file, index=False)
    with open(meta_file, 'w') as fh:
        fh.write('rows=' + str(len(df)) + chr(10))
        fh.write('cols=' + str(len(df.columns)) + chr(10))
        fh.write('key=' + key + chr(10))
    print(f'  [CACHE] {name} salvo em {cache_file}')
    return df


def invalidate():
    """Remove todos os caches."""
    if os.path.exists(CACHE_DIR):
        import shutil
        shutil.rmtree(CACHE_DIR)
        print('  [CACHE] Invalidado')
