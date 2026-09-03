import json
from pathlib import Path

feat_file = Path(r'D:\MarketData\mimo\dataset_100ms_WINV26_1-29.jsonl')
print(f'Arquivo: {feat_file}')
print(f'Existe: {feat_file.exists()}')
print(f'Tamanho: {feat_file.stat().st_size / 1024 / 1024:.2f} MB')

if feat_file.exists():
    with open(feat_file, 'r') as f:
        first_line = f.readline()
    print(f'\nPrimeira linha (primeiros 500 chars):')
    print(first_line[:500])
    
    # Tentar parsear
    try:
        data = json.loads(first_line)
        print(f'\nColunas encontradas:')
        for k in sorted(data.keys()):
            print(f'  {k}: {data[k]}')
    except Exception as e:
        print(f'\nErro ao parsear: {e}')
