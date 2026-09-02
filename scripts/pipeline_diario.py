#!/usr/bin/env python3
"""
pipeline_diario.py — Pipeline diário completo (v9.11).

Orquestra: relatório → features 100ms → labels → dataset parquet → gate → retreino.

Uso:
  python pipeline_diario.py                    # ontem (dia útil)
  python pipeline_diario.py --dia 20260821     # dia específico
  python pipeline_diario.py --dry-run          # só mostra o que faria
  python pipeline_diario.py --skip-batch       # só relatório + gate + retreino
"""
import argparse
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

SAVE_DIR_DEFAULT = r'D:\MarketData\mimo'
ATIVOS = ['WINV26', 'INDV26', 'WDOU26', 'DOLU26']
ATIVO = 'WINV26'  # principal (para compatibilidade)
ATIVO_STR = '_'.join(ATIVOS)
PYTHON = sys.executable


def dia_util_anterior(hoje=None):
    hoje = hoje or date.today()
    d = hoje - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def dia_util_para_periodo(dia):
    """Converte um dia útil para o formato de período (dia inicio-dia fim).
    
    Para pipeline diário, processa apenas o dia especificado.
    Exemplo: '20260828' -> '28-28'
    """
    dia_num = int(dia[-2:])  # Extrai o dia do mês
    return f'{dia_num}-{dia_num}'


def run(modulo, args_list, log_file, passo, desc, dry_run=False):
    """Executa um módulo via subprocess e aborta se falhar."""
    # Sempre usa a raiz do projeto (onde este script esta)
    _root = Path(__file__).resolve().parent.parent
    modulo_path = _root / modulo
    
    # Adiciona PYTHONPATH para garantir que pacotes como 'ml' sejam encontrados
    env = os.environ.copy()
    env['PYTHONPATH'] = str(_root) + os.pathsep + str(_root / 'scripts') + os.pathsep + env.get('PYTHONPATH', '')
    
    cmd = [PYTHON, str(modulo_path)] + args_list
    desc_str = f'[{passo}/7] {desc}'
    print(f'\n{desc_str}')
    print(f'  $ {" ".join(cmd)}')
    if dry_run:
        print(f'  (dry-run — não executa)')
        return
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_root), env=env)
        with open(log_file, 'a', encoding='utf-8') as lf:
            lf.write(f'\n{"="*60}\n{desc_str}\n{"="*60}\n')
            lf.write(r.stdout)
            if r.stderr:
                lf.write(f'\n--- STDERR ---\n{r.stderr}')
        if r.returncode != 0:
            print(f'  [FALHA] exit {r.returncode} — ver {log_file}')
            print(f'  Últimas linhas do log:')
            with open(log_file, encoding='utf-8') as lf:
                lines = lf.readlines()
            for l in lines[-5:]:
                print(f'    {l.strip()}')
            sys.exit(passo)
        print(f'  OK')
    except FileNotFoundError:
        print(f'  [FALHA] Módulo não encontrado: {modulo}')
        sys.exit(passo)


def main():
    ap = argparse.ArgumentParser(description='Pipeline diário de dados + ML')
    ap.add_argument('--dia', default=None, help='YYYYMMDD (default: ontem)')
    ap.add_argument('--save-dir', default=None)
    ap.add_argument('--dry-run', action='store_true', help='Simula sem executar')
    ap.add_argument('--skip-batch', action='store_true',
                    help='Pula geração de features/labels/dataset (já existe)')
    args = ap.parse_args()

    save_dir = args.save_dir or os.environ.get('SINAL_RT_DIR') or SAVE_DIR_DEFAULT
    dia = args.dia or dia_util_anterior().strftime('%Y%m%d')
    log_file = Path(save_dir) / 'pipeline.log'

    # Pipeline diário: processa APENAS o dia especificado
    hoje = date.today()
    periodo = dia_util_para_periodo(dia)
    feat_file = f'dataset_100ms_{ATIVO_STR}_{dia}.jsonl'
    labels_file = f'labels_{ATIVO_STR}_{dia}.jsonl'

    print(f'Pipeline diário — {dia}')
    print(f'SAVE_DIR: {save_dir}')
    print(f'Processando: período {periodo} (apenas dia {dia})')
    if args.dry_run:
        print('MODO: --dry-run (nenhuma execução real)\n')

    # 0. Converter JSONL brutos em Parquet por ativo
    print(f'\n[0/7] Conversão JSONL → Parquet ({dia})')
    if not args.dry_run:
        run('scripts/converter_brutos_parquet.py', ['--dia', dia, '--save-dir', save_dir],
            log_file, 0, 'Conversão JSONL → Parquet', dry_run=args.dry_run)
    else:
        print(f'  converter_brutos_parquet.py --dia {dia}')

    # 1. Relatório de qualidade
    print(f'\n[1/7] Relatório de qualidade ({dia})')
    if not args.dry_run:
        from relatorio_diario import gerar_relatorio, validar_dia
        # Validação prévia (exit 1 se o dia base estiver com problema)
        info = validar_dia(save_dir, dia)
        if info['problemas']:
            print(f'  [!] Problemas no dia base {dia}:')
            for p in info['problemas']:
                print(f'     - {p}')
            print('  O pipeline continua (o gate do retreino validará os dias de treino).')
        info, out = gerar_relatorio(save_dir, dia)
        print(f'  Relatório: {out}')
    else:
        print(f'  relatorio_diario.gerar_relatorio({save_dir}, {dia})')

    # 2. Features 100ms (batch_processor) — mês inteiro
    if not args.skip_batch:
        run('ml/batch_processor.py', ['--periodo', periodo, '--ativo', ','.join(ATIVOS)],
            log_file, 2, 'Features 100ms (batch_processor)', dry_run=args.dry_run)
    else:
        print(f'\n[2/7] Features 100ms — pulado (--skip-batch)')

    # 3. Labels — v9.13: usa labeler_VECTORIZADO (o labeler.py tinha o bug do
    # purge rearmado a cada linha neutra, gerando parquets ~100% neutros).
    if not args.skip_batch:
        feat_path = Path(save_dir) / feat_file
        if not args.dry_run and (not feat_path.exists() or feat_path.stat().st_size == 0):
            print(f'\n[3/7] Features vazias/ausentes: {feat_path}')
            print('  (nada para rotular — o pipeline abortado para não gerar parquet vazio)')
            sys.exit(3)
        run('ml/labeler_vectorizado.py',
            ['--input', str(feat_path), '--output', str(Path(save_dir) / labels_file),
             '--tp', '100', '--sl', '50', '--max-holding', '30', '--purge', '10'],
            log_file, 3, 'Labels (labeler_vectorizado)', dry_run=args.dry_run)
    else:
        print(f'\n[3/7] Labels — pulado (--skip-batch)')

    # 4. Dataset final (parquet)
    if not args.skip_batch:
        out_parquet = str(Path(save_dir) / 'dataset_final.parquet')
        run('ml/dataset_builder.py',
            ['--features', str(Path(save_dir) / feat_file),
             '--labels', str(Path(save_dir) / labels_file),
             '--output', out_parquet],
            log_file, 4, 'Dataset final (dataset_builder)', dry_run=args.dry_run)
    else:
        print(f'\n[4/7] Dataset final — pulado (--skip-batch)')

    # 4.5 (v9.32) Integração das camadas de contexto (ajuste oficial B3 + VWAP
    # intraday + features de regime + interações micro x contexto).
    # Gera dataset_final_completo.parquet, ajuste_diario_<YYYYMM>.csv e
    # vwap_<YYYYMM>.parquet, que alimentam o scorer ao vivo e o dashboard.
    if not args.skip_batch:
        mes_str = f'{hoje.year}{hoje.month:02d}'
        run('ml/integrar_base.py',
                ['--mes', mes_str, '--ativo'] + ATIVOS,
                log_file, 4.5, 'Integrar contexto avançado (v9.32)', dry_run=args.dry_run)
    else:
        print(f'\n[4.5/7] Integrar contexto — pulado (--skip-batch)')

    # 5. Gate de dados — Dias úteis do mês até hoje (protege o parquet)
    print(f'\n[5/7] Gate de qualidade')
    from relatorio_diario import ultimos_dias_uteis
    from glob import glob
    # Só validar dias que REALMENTE têm dados brutos
    # v14: buscar diretórios Hive (RAW/data_type=TT/date=YYYYMMDD/)
    #       fallback: JSONL legado (raw_negocios_ms_*.jsonl)
    dias_com_dados = set()
    raw_root = Path(save_dir) / 'RAW'
    tt_root = raw_root / 'data_type=TT'
    if tt_root.exists():
        for date_dir in tt_root.iterdir():
            if date_dir.is_dir() and date_dir.name.startswith('date='):
                dia_str = date_dir.name.replace('date=', '')
                if len(dia_str) == 8 and dia_str.isdigit():
                    # Verificar se tem pelo menos 1 arquivo Parquet dentro
                    tem_dados = any(date_dir.rglob('*.parquet'))
                    if tem_dados:
                        dias_com_dados.add(dia_str)
    if not dias_com_dados:
        # Fallback: JSONL legado
        arquivos_neg = glob(str(Path(save_dir) / 'raw_negocios_ms_*.jsonl'))
        for af in arquivos_neg:
            nome = Path(af).stem
            for parte in nome.split('_'):
                if len(parte) == 8 and parte.isdigit():
                    dias_com_dados.add(parte)
                    break
    # Dias do gate = interseção entre dias úteis do mês e dias com dados
    inicio_mes = date(hoje.year, hoje.month, 1)
    todos_uteis = [d for i in range((hoje - inicio_mes).days + 1)
                   for d in [((inicio_mes + timedelta(days=i)).strftime('%Y%m%d'))]
                   if (inicio_mes + timedelta(days=i)).weekday() < 5]
    dias_gate = [d for d in todos_uteis if d in dias_com_dados]
    print(f'  Dias úteis do mês: {len(todos_uteis)} | Com dados: {len(dias_com_dados)} | Gate: {len(dias_gate)}')
    print(f'  Dias validados: {", ".join(dias_gate)}')
    if not args.dry_run:
        from relatorio_diario import validar_dia as _validar_dia
        def gate_qualidade(save_dir, dias):
            erros_criticos = 0
            warnings = 0
            for dia in dias:
                dia = dia.strip()
                if not dia:
                    continue
                info = _validar_dia(save_dir, dia)
                if info['problemas']:
                    # span curto é warning, outros são erros
                    critico = [p for p in info['problemas'] if 'span' not in p.lower()]
                    aviso = [p for p in info['problemas'] if 'span' in p.lower()]
                    if critico:
                        erros_criticos += len(critico)
                        print(f'[GATE] Dia {dia}: ERRO')
                        for p in critico:
                            print(f'  - {p}')
                    if aviso:
                        warnings += len(aviso)
                        print(f'[GATE] Dia {dia}: OK (aviso: {aviso[0]})')
                else:
                    print(f'[GATE] Dia {dia}: OK')
            print(f'  Resumo: {erros_criticos} erros, {warnings} avisos')
            return erros_criticos == 0
        if not gate_qualidade(save_dir, dias_gate):
            print('  [GATE] Erros críticos — abortando retreino')
            sys.exit(2)
        # v9.13: gate de LABELS — se o parquet sair ~100% neutro, aborta
        # (senão o retreino noturno refaz o modelo com dados podres em silêncio)
        out_parquet = Path(save_dir) / 'dataset_final.parquet'
        if not args.skip_batch and out_parquet.exists():
            import pandas as pd
            dfc = pd.read_parquet(out_parquet, columns=['label'])
            nz = int((dfc['label'].fillna(0) != 0).sum())
            pct = 100.0 * nz / max(len(dfc), 1)
            print(f'  Labels não-zero: {nz:,} ({pct:.2f}%)')
            if pct < 0.01 and nz < 1:
                print(f'  [GATE] Parquet com {pct:.2f}% de labels não-zero — ABORTANDO (evita retreino cego)')
                sys.exit(5)
            elif pct < 1.0:
                print(f'  [GATE] Labels baixos ({pct:.2f}%) — continuando (poucos dados de treino)')
    else:
        print(f'  retreinar_lgbm_limpo.gate_qualidade({save_dir}, {dias_gate})')

    # 6. Retreino
    print(f'\n[6/7] Retreino do modelo')
    # v9.32: usar dataset enriquecido (com VWAP, ajuste, regime) se existir
    retrain_args = ['--gate-dias', ','.join(dias_gate), '--save-dir', save_dir, '--ativo', ATIVO]
    run('ml/retreinar_lgbm_limpo.py',
        retrain_args,
        log_file, 6, 'Retreino (retreinar_lgbm_limpo)', dry_run=args.dry_run)

    print(f'\n{"="*60}')
    print(f'Pipeline {dia} concluído com sucesso!')
    print(f'Log: {log_file}')
    print(f'{"="*60}')


if __name__ == '__main__':
    main()
