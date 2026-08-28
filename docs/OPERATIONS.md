# Operacao Diaria

## Rotina
08:45 Motor liga | 09:00+ Pregao | 18:35 Para | 18:36 Pipeline

## Checklist
Antes: motor? RTD? Dashboard?
Durante: dados? score? trades?
Apos: pipeline? dataset? modelo?

## Comandos
tasklist | grep -i python
taskkill /IM python.exe /F
python motor_rt_alphaz.py
curl http://127.0.0.1:5001/health
python -m pytest testes/ -v
