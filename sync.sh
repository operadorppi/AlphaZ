#!/bin/bash
# Script simples para sincronizar mudanças com o GitHub
git add .
git commit -m "Update: $(date +'%Y-%m-%d %H:%M:%S')"
git push origin main
echo "Sincronização concluída com sucesso!"