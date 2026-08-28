# Guia de Uso — ReplayEngine.py (v10.1)

## 1. Introdução
O `ReplayEngine.py` é a ferramenta de backtesting determinístico do ecossistema Freebuff. Sua função principal é reprocessar arquivos de captura bruta (`.jsonl`) através das mesmas camadas de lógica utilizadas no ambiente de produção.

Diferente de backtests tradicionais em barras, este motor processa **evento por evento**, garantindo que a latência de cálculo, o estado da microestrutura e a execução do modelo ML sejam validados de forma causal (sem *look-ahead*).

## 2. Pré-requisitos e Estrutura de Pastas
Para que o replay funcione corretamente, os arquivos devem estar organizados da seguinte forma no diretório de dados (definido pela flag `--pasta`):

```text
MarketData/Profit/
├── modelos/
│   └── modelo_final.pkl           # Modelo ML para o Scorer
└── raw_negocios_ms_20260828_120000.jsonl  # Captura bruta da sessão
```

## 3. Como Executar

O script é executado via linha de comando no terminal do Cloud Shell ou Windows.

### Sintaxe
```bash
python replay_engine.py --pasta <CAMINHO_DOS_DADOS> --sessao <TIMESTAMP_DA_SESSAO>
```

### Parâmetros
- `--pasta`: O diretório raiz onde os arquivos `.jsonl` e a pasta `modelos/` estão localizados. (Padrão: `MarketData/Profit`).
- `--sessao`: O sufixo de timestamp do arquivo de negócios. Exemplo: para o arquivo `raw_negocios_ms_20260828_120000.jsonl`, utilize `20260828_120000`.

### Exemplo de Comando
```bash
python replay_engine.py --pasta /home/daytradenofluxo/MarketData/Profit --sessao 20260828_120000
```

## 4. Camadas de Processamento
O ReplayEngine orquestra os dados através das camadas desacopladas conforme a Evolução v10.0:

1.  **MarketState**: Reconstrói o livro de ofertas e o fluxo de ordens.
2.  **ScorerML**: Injeta as features nos trackers e executa a inferência do modelo `.pkl`.
3.  **SignalEngine**: Avalia os critérios heurísticos e combina com a probabilidade do ML.
4.  **RiskManager**: Aplica filtros de horário, circuit breakers e validação de sinal.
5.  **PositionManager**: Simula o preenchimento de ordens, gestão de stops (TP/SL) e trailing stops.

## 5. Interpretação dos Resultados

### Logs em Tempo Real
Durante a execução, você verá logs detalhando as ações tomadas:
- `EVENTO OPERACIONAL: ABRIU`: Indica que um sinal passou pelo filtro de risco e uma posição foi iniciada.
- `EVENTO OPERACIONAL: FECHOU`: Indica encerramento por TP, SL ou inversão, exibindo o PnL da operação.

### Estatísticas Finais
Ao término, o motor utiliza o módulo `core.metrics` para exibir:
- **Profit Factor**: Proporção entre ganho e perda bruta.
- **Accuracy**: Taxa de acerto dos sinais.
- **Sharpe Ratio**: Qualidade do retorno em relação à volatilidade.
- **Expectancy**: Valor esperado por trade.

## 6. Solução de Problemas

| Problema | Causa Provável | Solução |
|----------|----------------|---------|
| `Arquivo não encontrado` | Erro no nome da sessão ou caminho da pasta. | Verifique se o nome do arquivo `.jsonl` segue o padrão `raw_negocios_ms_...` |
| `Scorer desabilitado` | O arquivo `modelo_final.pkl` não foi encontrado. | Certifique-se de que o modelo está na pasta `modelos/` dentro do caminho de dados. |
| `PNL sempre zero` | O RiskManager está bloqueando trades. | Verifique o `config.json` ou as flags de horário e stop diário no `risk_manager.py`. |

## 7. Melhores Práticas
- **Validação de Modelo**: Sempre rode o ReplayEngine após treinar um novo modelo para garantir que a performance "live" condiz com a performance do "walk-forward".
- **Debug de Sinais**: Se um trade não foi aberto quando deveria, verifique os "motivos" impressos nos logs do SignalEngine para entender qual regra de filtragem ou risco barrou a entrada.

---
*Documentação criada em: 28/08/2026*