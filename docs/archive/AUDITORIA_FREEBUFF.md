# Auditoria Completa — Freebuff (Motor RT Alphaz)

**Data:** 25/08/2026
**Escopo:** Sistema de trading algorítmico B3 (WIN/WDO) via ProfitChart RTD + pipeline ML/backtest + validação.
**Diretório auditado:** `C:\Freebuff`
**Método:** Leitura do código-fonte (motor, RTD, pipeline ML), logs de execução reais, documentos e JSONs de backtest; checagem de compilação; auditoria de segurança/arquivos.

> ⚠️ **ATENÇÃO:** Este documento descreve vulnerabilidades de um sistema que opera (ou se propõe a operar) dinheiro real em futuros B3. A auditoria não é um atestado de segurança — é uma lista de riscos objetivos encontrados.

---

## 🔴 CRÍTICO — Impede a operação

### C1. `motor_rt_alphaz.py` NÃO COMPILA (IndentationError na linha 2728)

O arquivo principal do motor tem um erro de indentação que impede a execução total:

```
linha 2727:        # Manter posição aberta se nenhuma condição de saída foi atendida
linha 2728: return None          ← dedentado para a coluna 0 (fora do método)
linha 2730:    def gerenciar_posicao(...)
```

`python -m py_compile motor_rt_alphaz.py` → **`IndentationError: unexpected indent (line 2730)`**.

- Quebra o método `_checar_saidas` (linha 2671) e tudo o que vier depois dentro da classe.
- **Efeito:** o motor com o código atual **não inicia**. O watchdog e o auto_start (`MotorAlphaz_Iniciar`) não conseguem subir o motor.
- O log `motor_stdout.log` mostra uma inicialização OK às 19:33 — **anterior** à última modificação do arquivo (25/08 20:11), ou seja, a última edição introduziu a quebra.
- **Ação recomendada:** recuar `return None` para dentro do método (4 espaços de indentação).

### C2. `KeyError: 'position_sizing'` — o motor jamais abre posição

`gerenciar_posicao` (linhas 2769-2770) acessa `CONFIG["position_sizing"]["target_risk_per_trade"]` e `CONFIG["position_sizing"]["max_position_size"]`, mas:

- O dicionário **não tem** a chave `position_sizing` no default do `CONFIG` (linhas 73-164).
- `_carregar_config_externa` (linhas 175-240) é quem lê `config.json`, e **só copia chaves específicas** (`trading`, `circuit_breaker`, `web`, etc.) — **`position_sizing` nunca é carregado**.
- `config.json` **tem** a seção `position_sizing` — mas ela é ignorada pelo motor.

**Efeito:** no primeiro sinal válido de abertura de posição, `CONFIG["position_sizing"]` lança `KeyError`, que é capturado no `_loop` (linha 3958) e logado como `[LOOP] Erro nao tratado` → **a posição NUNCA abre**. O motor calcula scores mas não executa trades (quando o C1 estivesse corrigido).

### C3. Estratégia validada como NÃO rentável — edge não confirmado

O JSON `walk_forward_v914_limpo.json` (7 folds, out-of-sample) mostra **resultado inconsistente e majoritariamente negativo** no threshold configurado (0.6):

| Fold | Expectancy @0.6 (pts/trade) | Profit Factor | nº trades @0.6 |
|------|------|------|------|
| 1 | **-16.63** | 0.155 | 20.980 |
| 2 | **-11.04** | 0.284 | 93.260 |
| 3 | +7.38 | 1.444 | 1.090 |
| 4 | +11.16 | 1.597 | 5.110 |
| 5 | **-4.64** | 0.801 | 10.720 |
| 6 | **-4.93** | 0.730 | 10.890 |
| 7 | +42.50 | 6.135 | 580 |

- Folds com **muita** negativa têm **10-160x mais trades**; os folds "lucrativos" têm amostras minúsculas (580–5.110) e provavelmente são sorte/regime.
- `RELATORIO_VALIDACAO.md`: modelo bruto superestimava a probabilidade de TP **10x** (ECE 0.41, Brier 0.26 — muito acima dos critérios de 0.10/0.18).
- **Interpretação honesta:** o edge do modelo não é robusto; a expectativa por trade no threshold configurado é aproximadamente nula a negativa, com variância enorme. Rodar com capital real nessa estratégia é de alto risco.

---

## 🟠 ALTO

### A1. Métricas correlacionadas de risco não usam a classe 0 (timeout)

`labeler_vectorizado.py` **descarta todos os labels `0` (timeout)** (linha 302: `mask = (labels != 0) ...`), salvando só TP/SL. O modelo treina como binário "TP ou SL", **sem nunca ter visto o caso majoritário "nem TP nem SL no holding"**. Isso causa:
- Probabilidade de TP sistematicamente inflada (registrado: superestimação 10x).
- O `scorer` ao vivo aplica threshold em `P(TP)` mas o modelo não modela o cenário neutro → calibração ruim e sinais ruins.

### A2. Bug de purge/embargo em `treino_lib.split_com_purge` + teste fraco

Em `split_com_purge` (linhas 67-76), o **embargo de 30s não é aplicado ao início do teste** — o teste começa em `ts_corte` (com purge de 5s), não em `ts_corte + embargo`. Com labels de horizonte até 30s, há **sobreposição das janelas de label entre treino e teste = leakage temporal**.

O teste `test_split_com_purge` (test_features.py:323-337) **valida apenas `gap >= 4.0s`** (nunca exige os 30s de embargo), então **passa vacuamente** e mascara o bug. A assinatura diz "sem leakage", mas não é garantida.

### A3. Motor em **loop de hang/reconexão do COM** na produção

`motor_stdout.log` (linhas 17:45-17:47) mostra um **ciclo infinito**: `conecta → descobre → assina → reconecta → [COM-WATCHDOG] Hang 16-18s detectado → reconecta`... O motor alterna entre `[COM-WATCHDOG] Hang detectado pela thread` e `Reconexão bem-sucedida` sem nunca capturar dados de mercado. Contagem de ~12.097 linhas de erro no `motor_stdout.log`.
- A proteção anti-hang (`_com_watchdog`, 15s) dispara e força reconexão repetida — sinal de RTD/COM instável ou de que `comtypes.PumpEvents` não está sendo bombeado de forma eficaz no ciclo.
- **Efeito:** em produção o motor não capturou dados neste período; fidelidade de dados (a base do ML/retreino) fica comprometida.

### A4. `pipeline_diario.py` passo 6 chama arquivo inexistente

O orquestrador automático (rodado às 18:35 via Task Scheduler) faz `run('retreinar_sem_leak.py', [])` na etapa 6 — mas **`retreinar_sem_leak.py` não existe** no diretório (foi removido; só o `retreinar_lgbm_limpo.py` existe). Resultado: **o pipeline automático de retreino falha todos os dias na etapa 6** (`sys.exit(6)`), e ainda **sem passar `--gate-dias`** (o gate de qualidade não protege o retreino).

### A5. `auto_start.bat` mata TODOS os processos Python às 18:30

A tarefa `MotorAlphaz_Parar` roda `taskkill /f /im python.exe` — mata **qualquer** processo python da máquina (inclusive análises, dashboards auxiliares, outros scripts). Na arquitetura de "máquina B de análise" documentada em `run.md`, isso derrubaria os consumidores e concorreria com o retreino.

---

## 🟡 MÉDIO

1. **Breakeven/trailing usam P&L alavancado contra TP bruto** — `_checar_saidas` (linhas 2700 e 2705) compara `pnl = raw_pnl × quantidade` contra `pos['tp']` (pontos brutos). Com `position_sizing` (até 10 contratos), breakeven/trailing disparam **cedo demais** (ex.: com 10 contratos, breakeven ativa a 5% do TP). Correto seria comparar `raw_pnl` (sem alavancagem) ao `tp`, ou `leveraged_pnl` a `tp × quantidade`.
2. **`_suavizar_sinal` pode devolver sinal confirmado obsoleto em neutro** — com `lado_bruto == 0` retorna `self.sinal_confirmado`; só é neutro por causa da trava `_sinal_streak >= 2`. Fragilidade de lógica, não bug ativo.
3. **Referências de documentação para arquivos removidos** — `DOCUMENTACAO.md`/`run.md` citam `retreinar_sem_leak.py`, `labeler.py`, `treinar_modelo.py`, `dashboard_analise.py`, `smoke_test_v96.py`, `auto_retreinar.bat`, `agendar_pipeline.bat`, `iniciar_watchdog.bat`, `verificar_tt.py`, `colapsar_tt.py` — **todos ausentes** do diretório vivo (existem no `backup.zip`). Divergência significativa entre doc e código real.
4. **Arquivos-orfãos vazios com nomes de versão** — `0.3:`, `0.5:`, `=` (0 bytes, criados juntos 25/08 10:17). Não são ADS com payload — são resíduos de redirecionamento de saída acidental (nomes parecem fragmentos de versão/operação). Limpeza recomendada; não são malware.
5. **Dois `.bat` de pipeline concorrentes e divergentes** — `auto_start.bat` agenda `pipeline_after_market.bat` (fluxo antigo, hardcoded `--tp 100 --sl 50`, chama `walk_forward_v914_limpo.py`), enquanto `pipeline_diario.py` (v9.11) tem outro fluxo. Não está claro qual roda de verdade, e os dois não se comunicam com o `config.json` (tp/sl hardcode).
6. **Watchdog em modo monitor é inócuo** — em `_modo_monitor` (linha 329), `self.motor_proc` é `None` (nunca iniciado), então o "monitor" não detecta/quebra nada — só loga «Motor morreu» que nunca ocorre. Documentado como "monitora", mas não monitora nada relevante.
7. **Hardcodes mágicos** — vários TP/SL/helders fixos nos `.bat` e em `pipeline_after_market.bat`/`pipeline_diario.py` (ex.: `--tp 100 --sl 50 --max-holding 30`), **não** lidos de `config.json`, apesar de a doc afirmar que o `config.py` centraliza esses números.

---

## 🟢 BAIXO / POÇO

- `auto_start.bat` linha 9: `set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1}"` funciona, mas é frágil/mágico (remove o último char `\`).
- `scorer.decisao` usa `threshold` default 0.65 enquanto o motor cfg usa 0.6 — inconsistência menor de default (se o motor não passar o threshold).
- `labeler_vectorizado` chamado de "vectorizado" mas o core (scan forward) é **loop Python O(N × holding_ticks)** com `holding_ticks=300` — em 3.4M linhas ≈ 10⁹ iterações; lento. Não compromete a corretude, mas o nome é enganoso e pode levar dias para datasets grandes.
- `_checkpoint` / `salvar_aprendizado` gravam o estado de aprendizado com `default=str` — algumas chaves podem virar string e quebrar reload se forem não-serializáveis.

---

## ✅ O que está correto (destacar)

- Arquitetura **single-source de features** (`features_lib.py` usada pelo live e batch) — boa prática, evita divergência live/batch.
- **Sanity check de preço** (`_preco_plausivel`): faixa por ativo + salto %, com reset diário — sólido.
- **rlóck reentrante** (`RLock`) e separação de `_io_lock` para escrita — evita deadlock e corrupção de arquivo.
- **Observabilidade do scorer** (v9.19) — falhas contadas e loggadas, não mais silenciosas.
- **Gate de qualidade** de captura no `relatorio_diario.validar_dia` e a checagem de % de labels não-zero no pipeline — boas salvaguardas contra dados ruins.
- `scorer.py` lê `blob['features']` do modelo salvo, garantindo consistência treino×live das colunas.

---

## Recomendações prioritárias (ordem)

1. **Corrigir C1** (indentação) — o motor não roda. 
2. **Corrigir C2** (`position_sizing`) — carregar `position_sizing` no `CONFIG` ou usar defaults; rever a lógica de risco/breakeven (item M1 usa P&L alavancado contra TP bruto).
3. **Decisão de risco**: antes de operar capital real, validar edge com mais folds/amostras e incluir a **classe 0 (timeout)** no treino; re-fazer o walk-forward com embargo correto (corrigir A2).
4. **Corrigir A4** (`pipeline_diario.py` passo 6) — apontar para `retreinar_lgbm_limpo.py` e passar `--gate-dias`.
5. **Corrigir A5** — restringir `taskkill` ao processo do motor (não `python.exe` global).
6. **Investigar A3** (hang do COM/RTD) — é o que impede a coleta de dados em produção.
7. Alinhar `auto_start.bat`/`pipeline_after_market.bat` ao `pipeline_diario.py` e ao `config.json`.

---

*Documento gerado por auditoria de código. Nenhuma correção foi aplicada ao projeto — apenas diagnóstico.*