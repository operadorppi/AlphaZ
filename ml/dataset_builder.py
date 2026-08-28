#!/usr/bin/env python3
"""
dataset_builder.py — Junta features 100ms + labels + asof join (WIN×WDO)
e exporta como Parquet pronto para treino de ML.

Pipeline final:
  features_100ms.jsonl ───┐
                          ├──▶ DatasetBuilder ──▶ dataset.parquet
  labels.jsonl ───────────┘

Se houver dados de WIN e WDO, também faz o asof join para incluir
features do contexto (WDO) alinhadas temporalmente.

Uso:
  python dataset_builder.py --features features_100ms.jsonl --labels labels.jsonl
  python dataset_builder.py --dia 20 --ativo WINV26
"""
import sys, os, json, argparse
from pathlib import Path
from collections import defaultdict
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from features_lib import asof_join_linhas, idade_ms
from treino_lib import flatten_snapshot
from features_contexto_preco import adicionar_contexto_preco
from features_expansao import adicionar_expansao
from features_contexto_avancado import (
    adicionar_ajuste_oficial,
    adicionar_vwap_causal,
)

SAVE_DIR = os.environ.get("SINAL_RT_DIR", r"D:\MarketData\mimo")


def carregar_jsonl(arquivo):
    """Carrega arquivo JSONL em lista de dicts."""
    dados = []
    with open(arquivo, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    dados.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return dados


def _carregar_ref_diario(path):
    """Carrega tabela de referência diária (D-1) opcional para o contexto
    de preço. Espera colunas de contexto (fechamento_anterior, ajuste_anterior,
    maxima_anterior, minima_anterior, faixa_anterior) indexadas por
    (ativo, _dia) — exatamente o formato de calcular_referencia_diaria()."""
    import pandas as pd
    p = str(path)
    if p.endswith('.jsonl'):
        d = pd.DataFrame(carregar_jsonl(path))
    else:
        d = pd.read_parquet(path)
    if 'ativo' in d.columns and '_dia' in d.columns:
        return d.set_index(['ativo', '_dia'])
    return d


def _carregar_ajuste_oficial(path):
    """Carrega tabela de ajuste oficial B3 (saida de
    calcular_ajuste_diario.calcular_ajuste_multi_dias).
    Colunas esperadas: data_pregao, contrato, ajuste, [abertura, ...]
    """
    import pandas as pd
    p = str(path).lower()
    if p.endswith('.csv'):
        return pd.read_csv(path)
    if p.endswith('.jsonl'):
        return pd.DataFrame(carregar_jsonl(path))
    return pd.read_parquet(path)


def _carregar_vwap(path):
    """Carrega tabela de VWAP por timestamp de negocio (saida de
    calcular_vwap_diaria.calcular_vwap).
    Colunas: timestamp_brt, simbolo, preco, quantidade, _dia, pv_acumulado,
    volume_acumulado, vwap, dist_vwap_pts.
    """
    import pandas as pd
    p = str(path)
    if p.endswith('.jsonl'):
        return pd.DataFrame(carregar_jsonl(p))
    return pd.read_parquet(p)


# flatten_snapshot importado de treino_lib (única implementação)


def merge_features_labels(features, labels):
    """Junta features e labels por ts_ms."""
    labels_dict = {(l['ts_ms'], l.get('ativo', '')): l for l in labels}
    merged = []

    for feat in features:
        ts = feat.get('ts_ms', 0)
        ativo = feat.get('ativo', '')
        label_info = labels_dict.get((ts, ativo), {})
        
        row = flatten_snapshot(feat)
        row['label'] = label_info.get('label', 0)
        row['tp_atingido'] = label_info.get('tp_atingido', False)
        row['sl_atingido'] = label_info.get('sl_atingido', False)
        row['preco_saida'] = label_info.get('preco_saida', 0)
        row['retorno_pts'] = label_info.get('retorno_pts', 0)
        row['duracao_label_ms'] = label_info.get('duracao_ms', 0)
        
        merged.append(row)
    
    return merged


# Colunas de label adicionadas pelo merge (defaults para snapshots sem label)
_LABEL_COLS = ['label', 'tp_atingido', 'sl_atingido', 'preco_saida',
               'retorno_pts', 'duracao_label_ms']


def merge_features_labels_chunked(arquivo_features, labels_df, chunk_size=500_000):
    """Junta features (lendo JSONL em chunks) com labels (DataFrame) por ts_ms.
    Retorna DataFrame final — eficiente em memória para datasets grandes."""
    import pandas as pd
    # v9.13: labels vazio (0 linhas) não pode quebrar o merge com KeyError —
    # produz parquet 100% neutro e o GATE de %labels do pipeline aborta depois.
    if labels_df is None or labels_df.empty or 'ts_ms' not in labels_df.columns:
        label_cols = ['ts_ms', 'label', 'tp_atingido', 'sl_atingido', 'preco_saida',
                      'retorno_pts', 'duracao_label_ms']
        labels_df = pd.DataFrame(columns=label_cols)
    partes = []
    chunk = []
    n_total = 0
    with open(arquivo_features, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            chunk.append(json.loads(line))
            if len(chunk) >= chunk_size:
                partes.append(_merge_chunk(chunk, labels_df))
                chunk = []
                n_total += chunk_size
                print(f'  {n_total:,} snapshots processados', end='\r')
    if chunk:
        partes.append(_merge_chunk(chunk, labels_df))
        n_total += len(chunk)
    print(f'  {n_total:,} snapshots processados')
    if not partes:
        return None
    df = pd.concat(partes, ignore_index=True)
    # v9.13: garante colunas de label mesmo sem correspondência E zera NaN's
    # (merge left sem match deixava label=NaN que conta como `label != 0` nas
    # métricas — silencioso e enviesado). Default por TIPO: numérico 0,
    # booleano False (fillna(False) em coluna float quebraria o parquet).
    _DEFAULTS = {'label': 0, 'preco_saida': 0, 'retorno_pts': 0,
                 'duracao_label_ms': 0, 'tp_atingido': False, 'sl_atingido': False}
    for col in _LABEL_COLS:
        if col not in df.columns:
            df[col] = _DEFAULTS[col]
        elif col in ('label', 'preco_saida', 'retorno_pts', 'duracao_label_ms'):
            df[col] = df[col].fillna(0)
        elif col in ('tp_atingido', 'sl_atingido'):
            df[col] = df[col].fillna(False)
    return df


def _merge_chunk(chunk, labels_df):
    """Achata um chunk de features e faz merge com labels.
    Chave: (ts_ms, ativo) — snapshots WIN e WDO compartilham os MESMOS
    timestamps de corte, então o merge por ts_ms sozinho duplicaria linhas."""
    import pandas as pd
    rows = [flatten_snapshot(feat) for feat in chunk]
    df = pd.DataFrame(rows)
    if not df.empty:
        chave = ['ts_ms', 'ativo'] if 'ativo' in labels_df.columns else 'ts_ms'
        df = df.merge(labels_df, on=chave, how='left')
    return df


def asof_join_features(features_win, features_wdo, tolerancia_ms=100):
    """Faz asof join entre features WIN e WDO, adicionando
    features do WDO como colunas *_ctx e ctx_idade_ms."""
    return asof_join_linhas(features_win, features_wdo, tolerancia_ms)


def salvar_parquet(dados, output_path):
    """Salva como Parquet (requer pandas/pyarrow)."""
    try:
        import pandas as pd
        df = pd.DataFrame(dados)
        df.to_parquet(output_path, index=False)
        print(f'Parquet salvo: {output_path}')
        print(f'  Linhas: {len(df)}')
        print(f'  Colunas: {len(df.columns)}')
        print(f'  Tamanho: {os.path.getsize(output_path) / 1024:.1f} KB')
        return df
    except ImportError:
        print('pandas/pyarrow não instalado. Salvando como JSONL...')
        salvar_jsonl(dados, output_path.replace('.parquet', '.jsonl'))
        return None


def salvar_jsonl(dados, output_path):
    """Salva como JSONL (fallback sem pandas)."""
    with open(output_path, 'w', encoding='utf-8') as f:
        for row in dados:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
    print(f'JSONL salvo: {output_path} ({len(dados)} linhas)')


def main():
    parser = argparse.ArgumentParser(description='Dataset Builder')
    parser.add_argument('--features', help='Arquivo de features 100ms')
    parser.add_argument('--labels', help='Arquivo de labels')
    parser.add_argument('--features-ctx', help='Features do contexto (WDO)')
    parser.add_argument('--output', help='Caminho de saída')
    parser.add_argument('--formato', choices=['parquet', 'jsonl'], default='parquet')
    parser.add_argument('--join-tolerancia', type=int, default=100,
                        help='Tolerância do asof join em ms')
    parser.add_argument('--contexto', dest='contexto', action='store_true',
                        default=True,
                        help='Adiciona features de contexto de preço (default: on)')
    parser.add_argument('--no-contexto', dest='contexto', action='store_false',
                        help='Desliga features de contexto de preço')
    parser.add_argument('--ref-diario', default=None,
                        help='Parquet/JSONL com referência diária D-1 (opcional)')
    parser.add_argument('--ajuste-oficial', default=None,
                        help='CSV/Parquet/JSONL com ajuste oficial B3 '
                             '(saida de calcular_ajuste_diario.calcular_ajuste_multi_dias)')
    parser.add_argument('--vwap-por-negocio', default=None,
                        help='Parquet com VWAP por timestamp de negocio '
                             '(saida de calcular_vwap_diaria.calcular_vwap)')
    parser.add_argument('--no-vwap', dest='usar_vwap', action='store_false',
                        default=True,
                        help='Desliga a injecao de features de VWAP')
    args = parser.parse_args()
    
    if not args.features or not args.labels:
        print('Especifique --features e --labels')
        return
    
    print('Carregando labels...')
    labels = carregar_jsonl(args.labels)
    print(f'  {len(labels)} labels')
    import pandas as pd
    labels_df = pd.DataFrame(labels)
    
    # Asof join se tiver features de contexto (carrega só os snapshots WIN)
    if args.features_ctx:
        print('Carregando features do contexto...')
        features_ctx = carregar_jsonl(args.features_ctx)
        print(f'  {len(features_ctx)} snapshots contexto')
    
    # Merge features + labels (chunked — eficiente em memória)
    print('Juntando features + labels (chunked)...')
    df = merge_features_labels_chunked(args.features, labels_df)

    # Contexto de preço (features_contexto_preco) — CAUSAL, sem look-ahead.
    # Mantém TODAS as colunas existentes; adiciona as novas. O _dia auxiliar
    # é descartado para não virar feature acidental.
    if args.contexto:
        print('Adicionando contexto de preço (causal)...')
        ref = None
        if args.ref_diario:
            ref = _carregar_ref_diario(args.ref_diario)
        df = adicionar_contexto_preco(df, ref_diario=ref)

        # v9.37: features de expansao (vol multi-TF, retornos, POC, volume, tempo)
        print('Adicionando features de expansao...')
        df = adicionar_expansao(df)

        # Camada avançada: ajuste oficial B3 (substitui o proxy de fechamento)
        if args.ajuste_oficial:
            print(f'  Injetando ajuste oficial de {args.ajuste_oficial}...')
            aj_df = _carregar_ajuste_oficial(args.ajuste_oficial)
            df = adicionar_ajuste_oficial(df, aj_df, usar_proxy_se_ausente=True)
            if '_dia' in df.columns:
                df = df.drop(columns=['_dia'])
            print(f'    +colunas de ajuste oficial')

        # Camada avançada: VWAP intraday causal
        if args.vwap_por_negocio and args.usar_vwap:
            print(f'  Injetando VWAP de {args.vwap_por_negocio}...')
            vwap_df = _carregar_vwap(args.vwap_por_negocio)
            df = adicionar_vwap_causal(df, vwap_df)
            print(f'    +colunas de VWAP')

        if '_dia' in df.columns:
            df = df.drop(columns=['_dia'])
        print(f'  Colunas agora: {len(df.columns)}')

    # Estatísticas
    print(f'\n=== Dataset Final ===')
    print(f'  Linhas: {len(df)}')
    n_com_label = int((df['label'] != 0).sum()) if 'label' in df.columns else 0
    print(f'  Com label: {n_com_label} ({n_com_label/len(df)*100:.1f}%)')
    print(f'  Colunas: {len(df.columns)}')
    
    # Salvar
    output = args.output or str(Path(SAVE_DIR) / f'dataset_final.{args.formato}')
    if args.formato == 'parquet':
        try:
            df.to_parquet(output, index=False)
            print(f'Parquet salvo: {output}')
            print(f'  Linhas: {len(df)}')
            print(f'  Colunas: {len(df.columns)}')
            print(f'  Tamanho: {os.path.getsize(output) / 1024:.1f} KB')
        except ImportError:
            print('pandas/pyarrow não instalado. Salvando como JSONL...')
            salvar_jsonl(df.to_dict('records'), output.replace('.parquet', '.jsonl'))
    else:
        salvar_jsonl(df.to_dict('records'), output)


if __name__ == '__main__':
    main()
