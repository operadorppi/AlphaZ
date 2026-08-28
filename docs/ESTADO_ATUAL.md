# Estado Atual

Versao: v10.2 (28/08/2026)

| Componente | Status |
|-----------|--------|
| Motor | v10.2 — Estável, saneamento de testes críticos concluído |
| Entry point | `run_motor.py` → `core.app.App` |
| Legado | `motor_rt_alphaz.py` → shim 25 linhas (arquivado em docs/archive/) |
| RTD | Funcional (500 niveis, watchdog COM em `adapters/com_watchdog.py`) |
| Features live | 121 ML + contexto ao vivo |
| Dashboard | Funcional (5001) |
| ML | Funcional (RF, 129 features) |
| Watchdog | Funcional (motor + `adapters/com_watchdog.py`) |
| Task Scheduler | Funcional (08:45 start, 18:35 stop) |
| Pipeline | Corrigido (18:35 seg-sex) |
| Dataset | v950 (165 cols, 1.3GB, 3.4M linhas) |
| Modelo | AUC 0.779, acc 75.4% (129 features) |
| Leakage | Corrigido (volume_relativo, range_percentil, regime_persistencia) |
| Testes | 154 passed, 3 skipped, 0 failed |
| Config | `config/defaults.py` — ConfigCompleto + aninhado + flat (R3) |
| Book writer | Retry em falha (B4 corrigido) |
| Pendencia | Modelo .pkl nao salvo - retreinar antes de ligar motor |
