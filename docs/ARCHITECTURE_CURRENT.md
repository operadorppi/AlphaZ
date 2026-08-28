# Arquitetura Atual — Motor RT Alphaz (v10.2)

O sistema opera em um modelo de orquestração centralizada com acoplamento forte entre infraestrutura Windows e lógica de trading.

## Diagrama de Blocos Atual

```text
[ ProfitChart RTD ] 
       |
       | (COM/win32)
       v
[ motor_web.py ] <---- [ adapters/profit_rtd.py ] (Shim)
       |
       | (Queue / Dataframes)
       v
[ core/app.py (Orquestrador Monolítico) ]
       |-- [ core/market_state.py ] (Estado + Trackers)
       |-- [ core/signal_engine.py ] (Heurística + ML Scorer)
       |-- [ core/position_manager.py ] (Execução de Saídas)
       |-- [ core/risk_manager.py ] (Filtros Simples)
       |-- [ scorer.py ] (Invocação de Modelos .pkl)
       |-- [ adapters/dashboard_api.py ] (Servidor HTTP)
       |-- [ adapters/file_storage.py ] (Escrita JSONL)
```

## Problemas Críticos Identificados

1.  **Dependência de Plataforma:** O Domínio (`core/`) importa bibliotecas Windows, impedindo testes de replay em ambiente Linux/Cloud Shell.
2.  **Fluxo de Dados Fragmentado:** O cálculo de features ocorre tanto no `SignalEngine` (live) quanto no `features_lib.py` (batch), com risco de divergência.
3.  **Configuração Distribuída:** Constantes mágicas e defaults estão espalhados entre `config.json`, `config/defaults.py` e `core/app.py`.
4.  **Estado Global Oculto:** O uso de `ERROS_GLOBAIS` e `PESOS_INICIAIS` como variáveis de módulo dificulta a testabilidade paralela.
5.  **Leakage Estatístico:** O mecanismo de split temporal no `treino_lib.py` não garante a barreira de embargo necessária para Triple-Barrier.

## Inventário de Shims
- `motor_rt_alphaz.py`: Redireciona para `core/app.py`.
- `features_lib.py`: Agrega módulos de `features/`.
- `captura_eventos_ms.py`: Redireciona para `adapters/file_storage.py`.
- `adapters/profit_rtd.py`: Importa dinamicamente a raiz para expor `motor_web.py`.