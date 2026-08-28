#!/bin/bash
# Script para sincronizar alterações automaticamente com o GitHub

INTERVALO=30 # Verificação a cada 30 segundos (mais equilibrado)

# Configura identidade Git (obrigatório para commit funcionar)
if [ -z "$(git config user.email)" ]; then
    git config --global user.email "operadorppi@gmail.com"
    git config --global user.name "AlphaZ AutoSync"
fi

echo "Monitor de sincronização iniciado (Cloud -> GitHub)..."
echo "Acompanhe os logs com: tail -f /home/daytradenofluxo/nohup.out"

while true; do
  # Verifica mudanças excluindo logs, temporários, compactados e cache do python
  if [[ -n $(git status -s | grep -vE ".log|.tmp|.tar.gz|__pycache__") ]]; then
  # Verifica mudanças excluindo logs, temporários, compactados, cache e o nohup.out
  CHANGES=$(git status -s | grep -vE ".log|.tmp|.tar.gz|__pycache__|nohup.out")

  if [[ -n "$CHANGES" ]]; then
    echo "$(date +'%H:%M:%S') - Alterações detectadas! Sincronizando..."
    git add .
    # Verifica se realmente há algo novo para commitar (evita commits vazios)
    if ! git diff --cached --quiet; then
      git commit -m "Auto-sync: $(date +'%Y-%m-%d %H:%M:%S')"
      # Tenta enviar. Se falhar (ex: você mudou algo no PC), tenta baixar e reordenar (rebase) antes de enviar de novo
      git push origin main || (git pull --rebase origin main && git push origin main) || \
      echo "Erro crítico de sincronização. Verifique conflitos manuais."
      echo "$(date +'%H:%M:%S') - ERRO: Falha no push. Verifique conflitos ou permissões SSH."
    fi
  fi
  sleep $INTERVALO
done