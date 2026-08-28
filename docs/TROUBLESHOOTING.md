# Troubleshooting

## Critico
1. Labels WIN/WDO misturados -> CORRIGIDO v939
2. Modelo dados corrompidos -> PENDENTE retreino
3. config_models.py vazio -> Restaurar

## Alto
4. Motor nao conecta RTD -> Abrir ProfitChart
5. Pipeline 18:36 -> path errado -> CORRIGIDO
6. Watchdog mata sadio -> Aumentar intervalo

## Diagnostico
tasklist | grep -i python
curl http://127.0.0.1:5001/api/rtd_health
tail -20 motor_stdout.log
