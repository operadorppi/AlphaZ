# Motor RT Alphaz — Documentacao

Sistema de trading algoritmico para B3 (WIN/WDO) via ProfitChart RTD.

## Estrutura desta documentacao

| Documento | Conteudo |
|-----------|----------|
| [ESTADO_ATUAL.md](ESTADO_ATUAL.md) | Status do sistema |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Diagrama e fluxo de dados |
| [COMPONENTS.md](COMPONENTS.md) | Referencia funcao por funcao |
| [DATA_CONTRACTS.md](DATA_CONTRACTS.md) | Interfaces entre modulos |
| [CONFIGURATION.md](CONFIGURATION.md) | config.json, env vars |
| [RUNTIME.md](RUNTIME.md) | Task Scheduler, watchdog |
| [DATA_PIPELINE.md](DATA_PIPELINE.md) | Pipeline offline |
| [MACHINE_LEARNING.md](MACHINE_LEARNING.md) | Walk-forward, features |
| [VALIDATION.md](VALIDATION.md) | Leakage, causalidade |
| [TESTING.md](TESTING.md) | Suite de testes |
| [API.md](API.md) | Endpoints HTTP |
| [OPERATIONS.md](OPERATIONS.md) | Operacao diaria |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Problemas conhecidos |
| [DECISIONS.md](DECISIONS.md) | Decisoes arquiteturais |
| [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) | Pendencias |
| [CHANGELOG.md](CHANGELOG.md) | Historico de versoes |
| [RECOVERY_NOTES.md](RECOVERY_NOTES.md) | Bugs corrigidos |

## Quick Start

cd C:\Freebuff
python motor_rt_alphaz.py
Dashboard: http://127.0.0.1:5001/
Testes: python -m pytest testes/ -v
