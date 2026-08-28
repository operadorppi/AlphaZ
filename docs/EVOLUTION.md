# Evolução Arquitetural — B3 Trading RTD (v10.x)

Este documento descreve a trajetória de evolução do projeto, saindo de uma estrutura monolítica para um sistema quantitativo desacoplado, escalável e validado.

## 1. A Grande Refatoração (v10.0)
**De Monolito para Camadas de Domínio**

Anteriormente, o sistema concentrava toda a lógica (Captura, Estado, Sinal, Execução e UI) nos arquivos `motor_rt_alphaz.py` e `motor_web.py`. A v10.0 isolou essas responsabilidades em camadas de domínio:

- **Core**: O coração do motor.
  - `market_state.py`: Mantém o estado dos ativos e trackers de microestrutura de forma thread-safe.
  - `signal_engine.py`: Motor de scoring (heurístico + ML) sem conhecimento de risco ou posição.
  - `risk_manager.py`: Gate de segurança (Circuit Breaker, Horários, Stop Diário).
  - `position_manager.py`: Gestão de ordens, Trailing Stops e Breakeven.
- **Adapters**: Interfaces com o mundo externo.
  - `profit_rtd.py`: Abstração da conexão COM/Windows.
  - `dashboard_api.py`: Servidor HTTP independente para telemetria.
  - `file_storage.py`: Persistência transacional em JSONL/Parquet.

## 2. Expansão do Contexto Quant (v10.2)
**De Microestrutura para Contexto de Mercado**

A evolução permitiu que o modelo deixasse de olhar apenas para o fluxo imediato e passasse a enxergar a "geografia" do mercado:

- **Volume Profile & POC**: Implementação de um profile intraday causal que rastreia a migração do Preço de Controle (POC).
- **VWAP Causal**: Cálculo incremental de preço médio ponderado por volume com detecção de aproximação/afastamento.
- **Intermarket (WIN × WDO)**: Camada de correlação rolling e detecção de liderança temporal entre o Índice e o Dólar.
- **Volatilidade Multi-TF**: Monitoramento de regime através de janelas EWMA (100ms a 5min).

## 3. Regra Zero — No Look-Ahead
**Integridade de Dados e Validação**

Para garantir que o modelo não sofra de "vazamento de dados" (data leakage), estabelecemos o protocolo de causalidade estrita:

1. **Causalidade Incremental**: Trackers operam em modo streaming. O snapshot no tempo *t* é computado sem qualquer conhecimento de *t+1*.
2. **Teste de Leakage**: Implementação de `leakage_test.py` que valida as Seções A-E da regra zero, garantindo que eventos futuros (preços absurdos ou volumes massivos) não alterem predições passadas.
3. **Replay Determinístico**: Criação do `replay_engine.py`, que permite reprocessar capturas brutas através de exatamente os mesmos objetos de negócio usados em produção.

## 4. Infraestrutura e Observabilidade
**Robustez Operacional**

- **Watchdog Cross-Platform**: Monitor de processos que suporta tanto Windows (Produção) quanto Linux (Desenvolvimento/Cloud Shell).
- **Deduplicação por Assinatura**: Lógica avançada no `SignalEngine` para preservar negócios fragmentados legítimos enquanto remove duplicatas de processamento COM.
- **Saúde do Scorer**: Telemetria específica para falhas de inferência de ML, evitando "mortes silenciosas" do modelo.

## 5. Próximos Passos

- **Ablation Tests**: Testar sistematicamente a contribuição de cada novo grupo de features (VWAP, POC, Intermarket) contra o baseline.
- **Regime-Specific Weighting**: Evoluir o `Learning` para que os pesos das features se adaptem automaticamente ao regime de volatilidade detectado.

---
*Documentação atualizada em: 2026-08-28*