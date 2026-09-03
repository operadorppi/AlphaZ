import pandas as pd
import json
from pathlib import Path

# Verificar o arquivo de labels
labels_file = Path(r'D:\MarketData\mimo\labels_WINV26_1-29.jsonl')
if labels_file.exists():
    with open(labels_file, 'r') as f:
        first_line = f.readline()
    data = json.loads(first_line)
    print('Colunas no labels:')
    for k in sorted(data.keys()):
        print(f'  {k}')
else:
    print('Arquivo de labels não encontrado')

# Verificar o arquivo de features
feat_file = Path(r'D:\MarketData\mimo\dataset_100ms_WINV26_1-29.jsonl')
if feat_file.exists():
    with open(feat_file, 'r') as f:
        first_line = f.readline()
    data = json.loads(first_line)
    print('\nColunas no dataset_100ms:')
    for k in sorted(data.keys()):
        print(f'  {k}')
else:
    print('Arquivo de features não encontrado')
