# Runtime e Automacao

## Task Scheduler
- MotorAlphaz_Iniciar: 08:45 seg-sex
- MotorAlphaz_Parar: 18:35 seg-sex
- MotorAlphaz_Pipeline: 18:36 seg-sex

## Watchdog
- CHECK_INTERVAL: 10s, RESTART_DELAY: 10s, MAX_RESTARTS: 10/hora
- Nao roda fins de semana, protecao multi-instancia

## Ciclo Diario
08:45 Motor liga -> 09:00+ Pregao -> 18:35 Para -> 18:36 Pipeline (6 passos)
