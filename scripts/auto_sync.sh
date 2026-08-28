#!/bin/bash
# Script para sincronizar alterações automaticamente com o GitHub

INTERVALO=30 # Verificação a cada 30 segundos (mais equilibrado)

echo "Monitor de sincronização iniciado (Cloud -> GitHub)..."

while true; do
  # Verifica mudanças excluindo logs, temporários, compactados e cache do python
  if [[ -n $(git status -s | grep -vE ".log|.tmp|.tar.gz|__pycache__") ]]; then
    echo "$(date +'%H:%M:%S') - Alterações detectadas! Sincronizando..."
    git add .
    git commit -m "Auto-sync: $(date +'%Y-%m-%d %H:%M:%S')"
    git push origin main || echo "Erro ao enviar para o GitHub. Verificando na próxima rodada..."
  fi
  sleep $INTERVALO
done