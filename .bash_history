pwd
zip -r alphaz_v10_evolution.zip core/ ml/ features/ adapters/ *.py config.json docs/
zip -r freebuff_v10.zip core/ features/ adapters/ scripts/ *.py config.json dashboard_pro.html -x "**/__pycache__/*" "MarketData/*"
zip -r freebuff_v10_final.zip core/ features/ adapters/ scripts/ *.py config.json -x "**/__pycache__/*" "MarketData/*" "venv/*"
cat <<EOF > /home/daytradenofluxo/.gitignore
# Caches do Python
**/__pycache__/
*.py[cod]
*\$py.class

# Dados de Mercado (NUNCA sincronizar via Git)
MarketData/
dados/
*.jsonl
*.parquet
*.log

# Ambiente Virtual
venv/
.env

# Arquivos do VS Code / Editor
.codeoss/
EOF

cat <<EOF > /home/daytradenofluxo/.gitignore
# Caches do Python
**/__pycache__/
*.py[cod]
*\$py.class

# Dados de Mercado (NUNCA sincronizar via Git)
Marketr
.codeoss/
EOF

git config --global user.name "matheus"
git config --global user.email "daytradenofluxo@gmail.com"
git config --global user.name "operadorppi"
git config --global user.email "daytradenofluxo.com"
git config --global user.name "operadorppi"
git config --global user.email "daytradenofluxo.com"
