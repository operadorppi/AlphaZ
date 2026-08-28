# Decisoes Arquiteturais

D1: Motor em arquivo unico (4155 linhas) - state sharing
D2: features_lib canonico live+batch - evita divergencia
D3: Labeler vectorizado NumPy - 180x mais rapido
D4: Walk-forward com cache - re-run em segundos
D5: Dashboard HTML puro - zero dependencia
D6: Captura em JSONL - append-only, tolerante
D7: Sem dedup (v9.39) - RTD nunca envia duplicados
D8: Threshold score 0.3 - mais amostras

D9: Migração para Arquitetura em Camadas (v10.0)
  - Motivo: Monolito de 4k linhas impedia testes unitários e causava dependências circulares.
  - Trade-off: Introduz complexidade no carregamento de configurações (shadow imports) e overhead de IPC.
  - Alternativa descartada: Microserviços (Docker). Descartado devido à latência crítica da interface COM/RTD no Windows.
  - Decisão: Divisão em core (negócio), features (cálculo), adapters (I/O).
D10: TP/SL Adaptativo e Position Sizing Dinâmico (v10.3)
  - Motivo: Melhorar a relação R:R em diferentes regimes de volatilidade.
D11: Abstração MarketDataSource e Contratos Tipados (v10.4)
  - Motivo: Isolar o Core do Windows/COM para permitir replay determinístico em Linux.
  - Decisão: Substituição de tuplas e dicts por dataclasses `MarketEvent`. O `App` agora consome uma stream agnóstica.
  - Decisão: TP/SL variam conforme `vol_1min_bps`. Position sizing escala linearmente com a confiança do sinal acima do threshold.
