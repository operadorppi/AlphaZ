import pandas as pd
from pathlib import Path

# Verificar o arquivo de features
feat_file = Path(r'D:\MarketData\mimo\dataset_100ms_WINV26_1-29.jsonl')
if feat_file.exists():
    # Ler primeira linha
    with open(feat_file, 'r') as f:
        first_line = f.readline()
    import json
    data = json.loads(first_line)
    print('Colunas no dataset_100ms:')
    for k in sorted(data.keys()):
        print(f'  {k}')
else:
    print('Arquivo não encontrado')
