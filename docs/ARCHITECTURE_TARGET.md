# Arquitetura Alvo — Engenharia Quantitativa Profissional

O objetivo é o isolamento completo do Domínio (Core) da Infraestrutura, permitindo Replay Determinístico em qualquer plataforma (Linux/Windows).

## Camadas Definidas

1.  **INFRASTRUCTURE:** Relógio (`EventClock`), Logging estruturado, Gerenciamento de Processos, Persistência de arquivos.
2.  **EXECUTION (ADAPTERS):** Isolamento total do COM/RTD. Implementa a interface `MarketDataSource`. 
    - `ProfitRTDAdapter` (Windows/Live)
    - `ReplayAdapter` (Linux/Parquet/JSONL)
3.  **DATA:** Normalização de `TradeEvent` e `BookSnapshot`. Garantia de imutabilidade do RAW.
4.  **DOMAIN (CORE):**
    - `MarketState`: Gestão causal do estado do livro e fluxo.
    - `DecisionJournal`: Registro auditável de cada decisão.
    - `Signal`: Combinação ponderada Heurística + ML.
5.  **FEATURES:** Registro formal (`FeatureRegistry`). Cálculo determinístico baseado em tempo ou evento.
6.  **RISK ENGINE:** Gatekeeper central. Não autoriza ordens se houver violação de limites ou dados obsoletos.
7.  **APPLICATION:** Orquestrador que injeta as dependências (`DI`) e gerencia o lifecycle.
8.  **PRESENTATION:** Dashboard desacoplado via API REST/WebSocket.

## Fluxo de Dados Unificado (Live & Replay)

```text
[ Source ] -> [ MarketEvent ] -> [ MarketState ] -> [ Features ] -> [ Model ] -> [ Risk ] -> [ Decision ]
```

## Garantias Técnicas

- **Determinismo:** O Core processa eventos um a um. Replay de um arquivo RAW deve produzir o mesmo `DecisionJournal` do Live.
- **Isolamento de SO:** O Core deve rodar em Linux sem `win32com`.
- **Contratos Tipados:** Substituição de `dict` por Dataclasses/Pydantic em todas as fronteiras de camada.
- **Causalidade:** Testes de Leakage impedem que dados de `t+n` influenciem o estado em `t`.

## Hierarquia de Testes
1.  **Portáteis (Linux):** Lógica, Features, ML, Replay.
2.  **Plataforma (Windows):** Adapter RTD, Conexão COM.
3.  **Integração:** Live Capture -> Replay -> Comparação de Divergência.