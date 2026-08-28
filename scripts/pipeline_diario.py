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
ATIVO = 'WINV26'
PYTHON = sys.executable


def dia_util_anterior(hoje=None):
    hoje = hoje or date.today()
    d = hoje - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def run(modulo, args_list, log_file, passo, desc, dry_run=False):
    """Executa um módulo via subprocess e aborta se falhar."""
    cmd = [PYTHON, modulo] + args_list
    desc_str = f'[{passo}/6] {desc}'
    print(f'\n{desc_str}')
    print(f'  $ {" ".join(cmd)}')
    if dry_run:
        print(f'  (dry-run — não executa)')
        return
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
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

    # Acumulação: processa TODOS os dias do mês atual (features + labels +
    # parquet), não só um dia — dataset_final.parquet passa a conter o mês.
    hoje = date.today()
    periodo = f'1-{hoje.day}'
    feat_file = f'dataset_100ms_{ATIVO}_{periodo}.jsonl'
    labels_file = f'labels_{ATIVO}_{periodo}.jsonl'

    print(f'Pipeline diário — {dia}')
    print(f'SAVE_DIR: {save_dir}')
    print(f'Acumulação: período do mês atual {periodo} (features + labels + parquet)')
    if args.dry_run:
        print('MODO: --dry-run (nenhuma execução real)\n')

    # 1. Relatório de qualidade
    print(f'\n[1/6] Relatório de qualidade ({dia})')
    if not args.dry_run:
        from relatorio_diario import gerar_relatorio, validar_dia
        # Validação prévia (exit 1 se o dia base estiver com problema)
        info = validar_dia(save_dir, dia)
        if info['problemas']:
            print(f'  ⚠️  Problemas no dia base {dia}:')
            for p in info['problemas']:
                print(f'     - {p}')
            print('  O pipeline continua (o gate do retreino validará os dias de treino).')
        info, out = gerar_relatorio(save_dir, dia)
        print(f'  Relatório: {out}')
    else:
        print(f'  relatorio_diario.gerar_relatorio({save_dir}, {dia})')

    # 2. Features 100ms (batch_processor) — mês inteiro
    if not args.skip_batch:
        run('ml/batch_processor.py', ['--periodo', periodo, '--ativo', ATIVO],
            log_file, 2, 'Features 100ms (batch_processor)', dry_run=args.dry_run)
    else:
        print(f'\n[2/6] Features 100ms — pulado (--skip-batch)')

    # 3. Labels — v9.13: usa labeler_VECTORIZADO (o labeler.py tinha o bug do
    # purge rearmado a cada linha neutra, gerando parquets ~100% neutros).
    if not args.skip_batch:
        feat_path = Path(save_dir) / feat_file
        if not args.dry_run and (not feat_path.exists() or feat_path.stat().st_size == 0):
            print(f'\n[3/6] Features vazias/ausentes: {feat_path}')
            print('  (nada para rotular — o pipeline abortado para não gerar parquet vazio)')
            sys.exit(3)
        run('ml/labeler_vectorizado.py',
            ['--input', str(feat_path), '--output', str(Path(save_dir) / labels_file),
             '--tp', '100', '--sl', '50', '--max-holding', '30', '--purge', '10'],
            log_file, 3, 'Labels (labeler_vectorizado)', dry_run=args.dry_run)
    else:
        print(f'\n[3/6] Labels — pulado (--skip-batch)')

    # 4. Dataset final (parquet)
    if not args.skip_batch:
        out_parquet = str(Path(save_dir) / 'dataset_final.parquet')
        run('ml/dataset_builder.py',
            ['--features', str(Path(save_dir) / feat_file),
             '--labels', str(Path(save_dir) / labels_file),
             '--output', out_parquet],
            log_file, 4, 'Dataset final (dataset_builder)', dry_run=args.dry_run)
    else:
        print(f'\n[4/6] Dataset final — pulado (--skip-batch)')

    # 4.5 (v9.32) Integração das camadas de contexto (ajuste oficial B3 + VWAP
    # intraday + features de regime + interações micro x contexto).
    # Gera dataset_final_completo.parquet, ajuste_diario_<YYYYMM>.csv e
    # vwap_<YYYYMM>.parquet, que alimentam o scorer ao vivo e o dashboard.
    if not args.skip_batch:
        mes_str = f'{hoje.year}{hoje.month:02d}'
        run('ml/integrar_base.py',
            ['--mes', mes_str, '--ativo', ATIVO,
             ('--ativo', CONFIG.get('ativo_contexto')) if CONFIG.get('ativo_contexto') else '--ativo'],
            log_file, 4.5, 'Integrar contexto avançado (v9.32)', dry_run=args.dry_run)
    else:
        print(f'\n[4.5/6] Integrar contexto — pulado (--skip-batch)')

    # 5. Gate de dados — TODOS os dias úteis do mês (protege o parquet)
    print(f'\n[5/6] Gate de qualidade')
    from relatorio_diario import ultimos_dias_uteis
    # nº de dias úteis do mês até hoje = quantos dias o parquet contém
    inicio_mes = date(hoje.year, hoje.month, 1)
    n_uteis = sum(1 for i in range((hoje - inicio_mes).days + 1)
                  if (inicio_mes + timedelta(days=i)).weekday() < 5)
    dias_gate = ultimos_dias_uteis(n_uteis)
    print(f'  Dias validados ({n_uteis} úteis do mês): {dias_gate}')
    if not args.dry_run:
        # gate_qualidade movido de retreinar_sem_leak (arquivo removido)
        from relatorio_diario import validar_dia as _validar_dia
        def gate_qualidade(save_dir, dias):
            ok = True
            for dia in dias:
                dia = dia.strip()
                if not dia:
                    continue
                info = _validar_dia(save_dir, dia)
                if info['problemas']:
                    ok = False
                    print(f'[GATE] Dia {dia}: PROBLEMAS')
                    for p in info['problemas']:
                        print(f'  - {p}')
                else:
                    print(f'[GATE] Dia {dia}: OK')
            if not ok:
                import sys; sys.exit(2)
        gate_qualidade(save_dir, dias_gate.split(','))
        # v9.13: gate de LABELS — se o parquet sair ~100% neutro, aborta
        # (senão o retreino noturno refaz o modelo com dados podres em silêncio)
        out_parquet = Path(save_dir) / 'dataset_final.parquet'
        if not args.skip_batch and out_parquet.exists():
            import pandas as pd
            dfc = pd.read_parquet(out_parquet, columns=['label'])
            nz = int((dfc['label'].fillna(0) != 0).sum())
            pct = 100.0 * nz / max(len(dfc), 1)
            print(f'  Labels não-zero: {nz:,} ({pct:.2f}%)')
            if pct < 1.0:
                print(f'  [GATE] Parquet com {pct:.2f}% de labels não-zero — ABORTANDO (evita retreino cego)')
                sys.exit(5)
    else:
        print(f'  retreinar_lgbm_limpo.gate_qualidade({save_dir}, {dias_gate})')

    # 6. Retreino
    print(f'\n[6/6] Retreino do modelo')
    # v9.32: usar dataset enriquecido (com VWAP, ajuste, regime) se existir
    retrain_args = ['--gate-dias', dias_gate, '--save-dir', save_dir, '--ativo', ATIVO,
                    '--usar-complemento']
    run('ml/retreinar_lgbm_limpo.py',
        retrain_args,
        log_file, 6, 'Retreino (retreinar_lgbm_limpo)', dry_run=args.dry_run)

    print(f'\n{"="*60}')
    print(f'Pipeline {dia} concluído com sucesso!')
    print(f'Log: {log_file}')
    print(f'{"="*60}')


if __name__ == '__main__':
    main()