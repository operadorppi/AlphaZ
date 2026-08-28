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
# Inicializa o git na pasta do projeto
git init
# Adiciona o endereço do seu GitHub
git remote add origin https://github.com/operadorppi/alphaz.git
# Define o nome da ramificação principal como 'main'
git branch -M main
# Prepara todos os arquivos (respeitando o .gitignore)
git add .
# Cria o primeiro "pacote" de alterações
git commit -m "Primeiro envio: Motor AlphaZ v10"
# Envia para o GitHub (vai pedir seu usuário e o Token/Senha)
git push -u origin main
ssh-keygen -t ed25519 -C "daytradenofluxo@gmail.com"
cat ~/.ssh/id_ed25519.pub
ssh -T git@github.com
git config --global user.email "daytradenofluxo@gmail.com"
git config --global user.name "operadorppi"
cd /home/daytradenofluxo/
# Inicializa o repositório local
git init
# Cria a branch principal
git branch -M main
# Adiciona o repositório remoto (AlphaZ)
# Nota: Se já existir, use 'git remote remove origin' antes
git remote add origin git@github.com:operadorppi/AlphaZ.git
git remote set-url origin git@github.com:operadorppi/AlphaZ.git
git add .
git commit -m "Sincronização Ponto Zero"
git push -u origin main
git push -u origin main --force
sh-keygen -t ed25519
chmod +x /home/daytradenofluxo/scripts/auto_sync.sh
./home/daytradenofluxo/scripts/auto_sync.sh &
tar -czvf alphaz_v10_update.tar.gz motor_web.py scripts/auto_sync.sh core/ features/ adapters/ config.py
cat ~/.ssh/id_ed25519.pub
ssh -T git@github.com
ps aux | grep auto_sync.sh
chmod +x /home/daytradenofluxo/scripts/auto_sync.sh
nohup /home/daytradenofluxo/scripts/auto_sync.sh &
