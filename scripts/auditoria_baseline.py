# -*- coding: utf-8 -*-
"""
scripts/auditoria_baseline.py — Certificado do estado atual do repositório.

Uso:
    python scripts/auditoria_baseline.py

Imprime um "fingerprint" do estado atual para que qualquer auditor
(humano, IA ou ferramenta) possa VERIFICAR se está lendo código atual
antes de reportar qualquer achado.

Regra de ouro:
    Se o conteúdo que o auditor está analisando não bater com os hashes
    abaixo, ele está olhando uma cópia VELHA (snapshot antigo, export,
    pasted excerpt ou git history pré-sync). Abortar e reler o working tree.

Referências:
    - Sync master com o projeto real:  d995d36 (2026-09-02/03)
    - HEAD na última entrega conhecida: 50cb841 (v15.2, 2026-09-02)
"""

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Marcos: qualquer HEAD anterior a estes commits é considerado defasado.
SYNC_COMMIT = "d995d36"   # master sincronizado com o projeto real
LAST_KNOWN = "50cb841"    # v15.2 — motor imortal + watchdog pré-mercado

ARQUIVOS_CHAVE = [
    "core/market_state.py",
    "features/book_features.py",
    "features/cross_asset.py",
    "features/feature_engine.py",
    "core/app.py",
    "core/signal_engine.py",
    "core/capture_daemon.py",
    "adapters/profit_rtd.py",
    "adapters/file_storage.py",
    "config/__init__.py",
    "config/loader.py",
    "config.json",
    "watchdog.py",
    "replay_engine.py",
    "ml/scorer.py",
]


def sha256_curto(path: Path, n=16) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()[:n]


def git(*args: str) -> str:
    try:
        r = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )
        return r.stdout.strip()
    except Exception as e:  # pragma: no cover
        return f"(git indisponível: {e})"


def main() -> int:
    line = "=" * 66
    print(line)
    print("CERTIFICADO DO ESTADO ATUAL DO REPOSITÓRIO")
    print(line)

    head = git("rev-parse", "HEAD")
    data = git("log", "-1", "--format=%h %ci %s")
    print(f"HEAD  : {head}")
    print(f"LOG   : {data}")

    # Verifica se o HEAD contém o sync (d995d36)
    try:
        r = subprocess.run(
            ["git", "merge-base", "--is-ancestor", SYNC_COMMIT, "HEAD"],
            cwd=ROOT, capture_output=True,
        )
        sincronizado = r.returncode == 0
    except Exception:
        sincronizado = False

    if sincronizado:
        print(f"SYNC  : OK — HEAD contém {SYNC_COMMIT} (master == projeto real)")
    else:
        print(f"SYNC  : ⚠️  FALHA — HEAD NÃO contém {SYNC_COMMIT}. "
              f"O repositório está defasado (pré-sync).")

    dirty = git("status", "--short")
    if dirty:
        print("TREE  : SUJO — arquivos ainda NÃO commitados (abaixo).")
        for linha in dirty.splitlines()[:20]:
            print(f"        {linha}")
    else:
        print("TREE  : LIMPO — working tree == HEAD")

    print("-" * 66)
    print("HASHES DOS ARQUIVOS CANÔNICOS (SHA-256, 16 primeiros chars):")
    print("Qualquer divergência com o que o auditor está lendo = cópia velha.")
    print("-" * 66)
    for rel in ARQUIVOS_CHAVE:
        p = ROOT / rel
        if p.exists():
            print(f"  {rel:42s} {sha256_curto(p)}")
        else:
            print(f"  {rel:42s} (não encontrado no working tree)")
    print("-" * 66)
    print("REGRAS PARA O AUDITOR:")
    print("  1. Ler SEMPRE o working tree em C:\\Freebuff (ou master APÓS push).")
    print("  2. Nunca analisar git history anterior a %s." % SYNC_COMMIT)
    print("  3. Nunca analisar exports/pastes de código — reler do disco.")
    print("  4. Ignorar docs/archive/ (cópias legacy intencionais).")
    print("  5. Antes de reportar um P0, conferir o hash do arquivo acima.")
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
