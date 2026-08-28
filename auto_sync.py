#!/usr/bin/env python3
"""
auto_sync.py — Backup automático para GitHub.
Roda em background: detecta mudanças e faz push a cada 5 minutos.

Uso:
    python auto_sync.py          # roda em foreground
    pythonw auto_sync.py         # roda sem janela (Windows)

Para parar: delete o arquivo .auto_sync.pid ou mate o processo.
"""

import os
import sys
import time
import subprocess
from pathlib import Path
from datetime import datetime

REPO_DIR = Path(__file__).resolve().parent
PID_FILE = REPO_DIR / '.auto_sync.pid'
LOG_FILE = REPO_DIR / 'auto_sync.log'
INTERVALO_S = 300  # 5 minutos
MENSAGEM_PREFIX = 'auto-sync'


def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    linha = f'[{ts}] {msg}'
    print(linha)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(linha + '\n')


def ja_rodando():
    """Checa se já existe outra instância rodando."""
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            # No Windows, tenta checar se o PID existe
            result = subprocess.run(
                ['tasklist', '/FI', f'PID eq {pid}'],
                capture_output=True, text=True, creationflags=0x08000000
            )
            if str(pid) in result.stdout:
                return True
        except Exception:
            pass
    return False


def tem_mudancas():
    """Retorna True se há mudanças não commitadas."""
    result = subprocess.run(
        ['git', 'status', '--porcelain'],
        capture_output=True, text=True, cwd=REPO_DIR
    )
    linhas = [l for l in result.stdout.strip().split('\n') if l.strip()]
    # Filtrar arquivos que não devem ser commitados
    ignorados = ['__pycache__', '.pytest_cache', '.freebuff', '.auto_sync.pid']
    relevantes = [l for l in linhas if not any(ign in l for ign in ignorados)]
    return len(relevantes) > 0


def commit_e_push():
    """Faz add + commit + push se houver mudanças."""
    try:
        # Adiciona tudo (respeitando .gitignore)
        subprocess.run(['git', 'add', '-A'], cwd=REPO_DIR, capture_output=True)

        # Verifica se tem algo staged
        result = subprocess.run(
            ['git', 'diff', '--cached', '--stat'],
            capture_output=True, text=True, cwd=REPO_DIR
        )
        if not result.stdout.strip():
            return False  # nada para commitar

        # Monta mensagem com timestamp
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        msg = f'{MENSAGEM_PREFIX}: {ts}'

        # Commit
        subprocess.run(
            ['git', 'commit', '-m', msg],
            cwd=REPO_DIR, capture_output=True, text=True
        )

        # Push
        resultado = subprocess.run(
            ['git', 'push', 'origin', 'master'],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=60
        )
        if resultado.returncode == 0:
            log(f'✅ Push OK ({ts})')
            return True
        else:
            log(f'❌ Push falhou: {resultado.stderr[:200]}')
            return False

    except Exception as e:
        log(f'❌ Erro: {e}')
        return False


def main():
    # Proteção contra múltiplas instâncias
    if ja_rodando():
        print('Auto-sync já está rodando. Saindo.')
        return

    # Salva PID
    PID_FILE.write_text(str(os.getpid()))

    log(f'🚀 Auto-sync iniciado (intervalo: {INTERVALO_S}s)')
    log(f'   Repo: {REPO_DIR}')

    try:
        while True:
            try:
                if tem_mudancas():
                    log('📝 Mudanças detectadas, fazendo push...')
                    commit_e_push()
                else:
                    log('😴 Sem mudanças')
            except Exception as e:
                log(f'❌ Ciclo falhou: {e}')

            time.sleep(INTERVALO_S)
    except KeyboardInterrupt:
        log('🛑 Auto-sync encerrado (Ctrl+C)')
    finally:
        try:
            PID_FILE.unlink()
        except Exception:
            pass


if __name__ == '__main__':
    main()
