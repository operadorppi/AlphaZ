# Run doc — Dashboard B3 Trading RTD

Projeto de captura de dados RTD do ProfitChart (arquivo único `motor_web.py` + `dashboard.html` externo + `verificar_tt.py` para auditoria). O servidor web embutido serve o dashboard em `http://127.0.0.1:5000`.

## Reproduzir artefatos

Não há artefatos gerados por build nem arquivos `.env` necessários. O único requisito é o servidor COM RTD do ProfitChart estar rodando na máquina (o motor conecta nele via comtypes).

Dependências Python (todas instaladas no ambiente): `comtypes`, `pandas`, `pyarrow`, `duckdb`, `flask` (opcional). Instalar se faltar:

```bash
pip install comtypes pandas pyarrow duckdb
```

## Como rodar o servidor

O dashboard e o motor de captura são a mesma aplicação. Para subir apenas o servidor com captura completa:

```bash
python motor_web.py                # porta 5000, abre navegador
python motor_web.py --no-browser   # sem abrir navegador
python motor_web.py --web-port 5001
```

O motor conecta nas janelas RTD do ProfitChart, descobre os 6 ativos (IND, WIN, DOL, WDO, DI1F28, DI1F29) e serve:

**Nota de fidedignidade (pregao):** o motor assina o contador total de negocios
(`NEG`) de cada ativo como fonte de verdade independente. A cada janela de 5s
compara `delta_NEG` com os negocios detectados — perdas reais aparecem na coluna
"Fidedignidade NEG vs captura" do dashboard e no log como `[FIDEDIGNIDADE]`.
Limite real da janela T&T do Profit: 500 linhas (`LINHAS_TT=500`), testado
empiricamente (linha 500 retorna "Linha Invalida") — o ProfitChart nao permite
configurar mais de 500. O motor le ~24 mil topicos (nao 45 mil) e conecta em
~10s. Log `[CICLO]` a cada 2s: frequencia real de refresh, custo do COM e
processamento; dashboard mostra na linha "ciclo: N Hz · refresh Xms...".

**Multi-janela T&T (RLP):** o motor descobre TODAS as janelas T&T por ativo
(`tt_janelas`). Para WIN e WDO com janela extra de RLP, abra a janela T&T
extra no ProfitChart para o mesmo ativo — o motor a detecta automaticamente
(MAX_JANELAS_RTD=12). A contagem por assinatura usa MAX entre janelas: o
mesmo negocio na janela normal E na RLP conta 1x (nao duplica), e microlotes
identicos sao capturados.

**Pasta separada para RLP:** cada negocio carrega `origem_janela` ("negocios"
ou "rlp"). Negocios RLP sao gravados em `sym=WINV26_RLP` / `sym=WDOU26_RLP`
(pasta separada), com a coluna `origem_janela` no Parquet. O `verificar_tt.py`
ja le as duas pastas (glob `sym=*`).

**Latencia por negocio (`latencia_us`):** cada linha T&T grava
`latencia_us` = (timestamp_recebimento_python - time_ms) em microssegundos —
fidedignidade de TEMPO (nao so de quantidade). O dashboard mostra a latencia
p95/por ativo, e o `verificar_tt.py` reporta media/p95/max usando a coluna
persistida (fallback: recalcula das duas colunas de ms).

**Arquivos de 1h para ML:** o flush continua a cada 60s (perda maxima de 1min
em crash), e uma thread automatica (`ConsolidarHora`) junta os `.part_` da
hora fechada em `{hora}.parquet` unico — 1 arquivo por hora por ativo. Cobre
TODAS as pastas `sym=*`, inclusive `*_RLP` (WINV26_RLP, WDOU26_RLP), com o
mesmo padrao `{pasta}/{hora}.parquet` do ML. A cada ~5min a thread tambem
vara horas mais antigas com `.part_` pendentes e re-consolida (reparo de
orfaos: partes escritas depois do flush daquela hora — ex.: reinicio no meio
da hora — antes ficavam pendentes para sempre). NA INICIALIZACAO, antes de
comecar a capturar, o motor roda `_consolidar_pendentes_inicial()`: consolida
horas atrasadas de hoje/ontem/anteontem (TT + BOOK, incluindo RLP), entao o
dataset nunca comeca o dia com buracos de hora fechada mesmo se a maquina
ficou desligada. Manual:
`python motor_web.py --consolidar-tt` (e `--consolidar-book`).

**Colapso de blocos de balcao (DOL):** a janela T&T do ProfitChart expande
negocios de balcao institucionais (ex.: DOL) em N linhas — uma por contraparte,
repetindo a MESMA quantidade total do bloco em cada linha. Sem correcao, o
volume gravado fica ~7,8x o real e a contagem 3,5x o NEG (dia 14: 77.612 linhas
vs NEG 21.969). A escritora TT colapsa grupos (ms, preco, vendedora, agressor)
com quantidade identica repetida >=2x em 1 linha (`ATIVOS_COLAPSO_BLOCO =
{"DOLU26"}`), gravando `n_contrapartes` (1 = normal; N = bloco colapsado) e
incrementando `tt_blocos_colapsados` (visivel no dashboard). Resultado dia 14:
24.515 negocios, R$ 76 bi (real: R$ 65 bi). Aplicacao retroativa em parquet
antigos:

```bash
python colapsar_tt.py --dia 20260814            # corrige (faz backup em _backup_colapso)
python colapsar_tt.py --dia 20260814 --dry-run  # so mostra o que faria
```

**Otimizacao do hot path (stats em memoria):** `_registrar_stat` antes lia +
escrevia o JSON de stats no disco A CADA chamada (~15ms). Medido no pregao:
~12 chamadas/ciclo em rajada = ~180ms de I/O por ciclo (gargalo que derrubava
o ciclo a 1-2 Hz e perdia negocios — WIN 7,4% no dia 14). Agora os contadores
acumulam em memoria e persistem a cada 5s (`STATS_FLUSH_S`), com flush forcado
no drain final. Ganho medido: 120 chamadas de 1.583ms -> 4,8ms (~330x).

- `GET /` → `dashboard.html` (externo, editável sem tocar no .py)
- `GET /api/status` → payload JSON com contadores, latência, filas por ativo
- `GET /api/historico?minutos=N` → série persistida em disco (`_historico_dashboard.json`)

Para parar: `Ctrl+C` (faz drain final das escritoras). As escritoras se auto-encerram se o processo pai morrer (watchdog anti-órfã).

## Início automático no pregão

`iniciar_motor.bat` sobe o motor na abertura: (1) abre o ProfitChart se não
estiver rodando (espera até 60s), (2) verifica se o motor já está na porta
5000 e sai sem duplicar, (3) inicia `python motor_web.py` (a consolidação
inicial de horas atrasadas roda no boot do motor). Log em `motor_auto.log`.

Tarefa agendada do Windows (`MotorRTD_Abertura`), **segunda a sexta às 08:40**
(antes do leilão 08:55) — criada com `schtasks /SC WEEKLY /D
MON,TUE,WED,THU,FRI`, rodando mesmo na bateria, com recuperação de horário
perdido se a máquina dormir. Requer sessão interativa
(logon do usuário); o ProfitChart precisa estar configurado com as janelas RTD.

Testado nos dois caminhos: com motor já rodando (detecta porta 5000 e sai,
`exit /b 0`) e com motor parado (sobe o motor, dashboard em 5000).

Obs.: ao testar o `.bat` via Git Bash, use `cmd //c "C:\Freebuff\iniciar_motor.bat"`
(barra dupla — o msys corrompe `/c`); e não se assuste com o wrapper "travar":
o Git Bash espera a janela `/min` do `start` fechar, mas o batch completa (veja
`motor_auto.log`).

Auditoria dos Parquet gravados em `D:\MarketData\Profit\RAW`:

```bash
python verificar_tt.py              # resumo do dia
python verificar_tt.py WINV26 15    # últimos 15 negócios de um ativo
```

O `verificar_tt.py` aplica o mesmo colapso de blocos na leitura do DOL, entao
a contagem exibida ja reflete os negocios reais da B3 (comparavel com o NEG).

**Auditoria completa do dia (`auditar_dia.py`):** um comando que valida tudo
para o ML — contagem T&T (com colapso do DOL e RLP somado ao pai), fidelidade
NEG vs gravados, arquivos de 1h (cobertura + partes órfãs, inclui RLP) e
volume financeiro vs B3. Exit code 0/1/2 (OK/atenção/crítico).

```bash
python auditar_dia.py                                             # dia de hoje
python auditar_dia.py --dia 20260817                              # dia específico
python auditar_dia.py --dia 20260817 \
    --neg WINV26=... DOLU26=... WDOU26=... DI1F28=... DI1F29=... \
    --b3  WDOU26=... DOLU26=... WINV26=...
```

Os valores `--neg` (contador de negócios) e `--b3` (volume em R$ bi) são
lidos na tela do ProfitChart no fim do pregão. Com `--neg-do-motor`, o NEG é
lido sozinho do payload do motor em `/api/status` (nenhum contador digitado)
— e a comparação soma o RLP ao principal (o NEG conta todos os negócios).
Multiplicadores B3 usados: WIN 0,20 · IND 1,00 · WDO 10,00 · DOL 50,00
(R$/ponto); DI1 1,00 (aprox. nominal). Validação no dia 14: WDO total
(main+RLP) = 99,2% do volume real (141,15 vs 142,33 bi); fidelidade NEG
WDO 99,4% OK, WIN 92,6% (a perda real de 7,4% nas rajadas) — o RLP é
essencial para fechar a conta.

**Auditoria automática às 18:35:** o motor tem uma thread (`Auditoria18h35`)
que, em dias úteis, dispara `auditar_dia.py --dia <hoje> --neg-do-motor` em
processo separado entre 18:35 e 19:10 (uma vez por dia, só se o dia tem
dados). Relatório completo em `D:\MarketData\Profit\auditoria_<dia>.log`.
A checagem de horas respeita a hora atual: partes da hora em andamento são
"em andamento" (não órfãs), evitando falso CRÍTICO às 18:35.

**Features para ML (`features_book.py` + `validar_imbalance.py` +
`FEATURES.md`):** camada DERIVADA de microestrutura — nunca altera os brutos.
Método: uma feature por vez, validada e documentada. Feature #1: família de
imbalance (simples 60 níveis, regiões 1–5/6–10/11–20/21–30/31–40/41–60,
ponderado por distância 1/sqrt(nível)) em um relógio mestre (grid 1s).
Módulo 2 (`validar_imbalance.py`): validação temporal com as-of merge no ms
(horizontes 100ms–2s, caminhos mid->mid e mid->preço T&T). Achados dia 14:
sinal significativo em >=1s (mean-reversion), correlações curtas por preço
T&T são CONTEMPORÂNEAS (baseline −0,127/−0,136 idêntico ao "forward"),
gargalo = frequência do book (~400ms), candidato real de curto prazo = WDO
imbalance_1_5 -> trade(t+500) (+0,061); assimetria estrutural no nível 1–5
(WDO mediana −0,27) — normalizar por ativo antes do ML. Detalhes em
`FEATURES.md` (inclui os resultados do dia 14 para testes fora da amostra).

```bash
python features_book.py --dia 20260814 --sym WINV26 --saida feat_win.csv
python validar_imbalance.py --dia 20260814   # validacao as-of (ms)
python features_tt.py --dia 20260814 --syms WINV26 WDOU26 --valida  # Modulo 3
python interacao_book_tt.py --dia 20260814 --syms WINV26 WDOU26     # Modulo 4
python liquidez_absorcao.py --dia 20260814 --sym WINV26             # Modulo 5
python liquidez_absorcao.py --dia 20260814 --sym WDOU26 --agr-ms 2000 --esc-ticks 1
```

**Revalidacao fora da amostra (`rodar_pipeline.py`):** roda as metricas-chave
dos Modulos 1-5 por ativo, CONGELA o dia 14 como linha de base
(`pipeline_baseline.json`, so regrava com `--force`) e compara qualquer dia
novo, marcando metrica a metrica SOBREVIVEU/ALTEROU (veredito por limiar por
tipo de metrica). Relatorio markdown em `relatorio_pipeline_<dia>_vs_<ref>.md`.

```bash
python rodar_pipeline.py --lista                        # dias congelados
python rodar_pipeline.py --dia 20260817                 # compara com a base
python rodar_pipeline.py --dia 20260817 --syms WINV26 WDOU26 DOLU26
```

**Modulo 5 (`liquidez_absorcao.py`)** — sequencia agressao -> consumo -> reposicao -> preco. WIN: absorcao compradora reverte -1,08 pts/2s (vs -0,64 movimento, +0,10 quieto) — real e distinguivel de baixa volatilidade, mais forte em atividade alta. WDO nao mostra absorcao em 0,5-2s (86% janelas quietas; exigir calibracao por ativo).

**Modulo 4 (`interacao_book_tt.py`)** — interacao condicional BOOK x T&T
(quadrantes contexto x ataque): SEM ganho de interacao para a direcao do
fluxo (BOOK marginal ~0; T&T carrega tudo), o SINAL do delta_1s e o que
importa (normalizacao por volume nao muda nada), efeito persiste dentro de
tercis de atividade (nao e mercado ativo), e o ALINHAMENTO importa para os
retornos (quadrantes alinhados revertem em 0,5-1s).

**Modulo 3 (`features_tt.py`)** — dinamica de agressao do T&T (intensidade,
velocidade 100ms-1s, aceleracao, persistencia de sequencia) com 2 testes
as-of: PRECO (bounce) e FLUXO (direcao do proximo negocio — o obrigatorio).
Achado dia 14: o fluxo continua mesmo quando o preco nao anda (WIN delta_1s
-> prox negocio +0,145; hit-rate +17,9pp) e WIN/WDO tem estruturas diferentes
(WIN responde a volume, WDO a sequencia com mean-reversion real).

**Operacoes vs base (`analisar_operacoes.py`)** — cruza o relatorio de
performance do ProfitChart (2 CSVs: operacoes completas + fills individuais,
cp1252, numeros BR) com o T&T do dia: reconstroi o P&L pelos precos medios de
execucao (validado: |dif| media R$ 0,21 em 48 ops), atribui ENTRADA/MEDIA/SAIDA
por op e anota cada fill/op com o fluxo (net agressor) 60/30/10s antes,
variacao de preco e alinhamento. Saidas: `operacoes_anotadas.csv` (uma linha
por op) e `fills_anotados.csv` (uma linha por fill, com ms recuperado).

**Milissegundo — 2 caminhos:** os CSVs exportados so tem datas HH:MM (os
pontos em "171.160,00" sao separador de MILHAR dos precos, nao milesimos),
mas a TELA do ProfitChart mostra HH:MM:SS.mmm.

1. **Ancora por preco (automatica):** usa o PRECO MEDIO de execucao + lado
   (agressor) como ancora no T&T dentro do minuto da ordem — primeiro negocio
   que fecha a quantidade. Validada contra os ms reais da tela (184 amostras
   do dia 14): |erro| medio 411 ms, p50 132 ms, 96% dentro de 1s
   (`validar_ancora.py`). Aproximacao — o fill exato do broker pode diferir.
2. **Ms EXATO (clipboard):** o grid do ProfitChart mostra os ms — copie as
   linhas (Ctrl+A, Ctrl+C) e rode `python capturar_fills.py` para salvar
   `fills_copia.csv` com os ms reais; depois
   `python analisar_operacoes.py --fills-exatos fills_copia.csv`.

Fluxo: `capturar_fills.py` (clipboard -> CSV com ms) -> `analisar_operacoes.py
--fills-exatos` (re-anota com os ms reais).

Achado dia 14 (n pequeno, indicativo): ops com media custaram **-1123 vs +44
sem media** (robusto — nao depende do timing). O alinhamento de fluxo na
entrada ficou FRACO com a ancora em ms (corr net30ms vs P&L: -0,08 compras /
-0,04 vendas) — a separacao forte vista antes (vendas alinhadas +785) era
ARTEFATO de ancorar no inicio do minuto; com o ms exato, o efeito sobrevive
so em direcao (entrar em pressao compradora ainda levemente pior nos 2 lados)
e dentro do ruido. Replay por op (ex. op1/op18/op23) mostra as medias em
rajadas sub-segundo com o fluxo virando contra no ultimo add.

```bash
# com ancora por preco (automatico)
python analisar_operacoes.py --ordens1 D:\Downloads\ordens1.csv --ordens D:\Downloads\ordens.csv --dia 20260814
# com ms exatos do clipboard (depois de capturar_fills.py)
python capturar_fills.py
python analisar_operacoes.py --ordens1 D:\Downloads\ordens1.csv --ordens D:\Downloads\ordens.csv --dia 20260814 --fills-exatos fills_copia.csv
```

**Checklist operacional (`checklist_operacional.md`)** — regras de ENTRADA e
RISCO derivadas dos dados de sexta (n=48): nao perseguir movimento feito,
book 1-5 a favor, nunca aumentar na perda com fluxo contra (sem medias, o
pior dia vira positivo: -1079 -> +353), saida quando o sinal morre, 1o tick
contra = custo de entrada. Revalidar em 17/08 antes de confiar.

**Features as-of p/ decisao (`features_entrada.py`)** — grid continuo de
features calculadas SOMENTE com dados em ou antes de t (zero look-ahead):
fluxo/net 1-30s, vol total, n, delta do preco, velocidade, aceleracao,
run_direcao (persistencia), book as-of (imbalance 1_5/1_60/pond, spread,
liquidez), RLP 1-5s, e a EFICIENCIA do fluxo (|delta|/vol_total e delta/net).
Labels FUTUROS vao em colunas `label_*` (retorno 1-30s, direcao do proximo
negocio) — para estudo/treino, nunca para a decisao.

```bash
python features_entrada.py --sym WINV26 --valida    # Fase 3: feature antes do resultado?
python features_entrada.py --sym WINV26 --saida grid_win.csv   # base de treino
```

Fase 3 dia 14 (30k amostras/ativo): a eficiencia/delta do passado PREDIZEM o
retorno futuro — preco que ja andou forte na direcao do fluxo REVERTE em
1-10s (WIN -2,7 pts/3s no tercil alto; corr WDO -0,125); preco travado com
fluxo forte continua depois (+0,86 WIN). CONTROLE: o poder vem do DELTA de
preco (mean-reversion), a razao delta/vol nao agrega alem disso nos dados de
sexta. Isso CORRIGE a hipotese da autopsia ("absorcao -> fuja" nao se
sustenta; o que se sustenta e "movimento ja feito -> nao persiga").

**Autopsia por operacao (`autopsia_operacoes.py`)** — tabela temporal por
operacao em 3 momentos: ANTES (fluxo/agressao/persistencia -30s a -1s),
ENTRADA (book 60 niveis as-of: imbalance_1_5/1_60/pond, spread, liquidez) e
DEPOIS (reacao do preco 1-20s, continuacao do fluxo, RLP 1-5s), cruzada com
P&L real. Mesma base que vira treino supervisionado quando houver centenas
de operacoes. Saida: `autopsia_operacoes.csv` + resumo com tercis por feature
(incluindo versao ALINHADA x lado — positivo = a favor da operacao).

Achados dia 14 (n=48, INDICATIVO): entrar a favor do fluxo de 10s rendeu
+57 de P&L medio vs -2 contra; sequencia de agressores a favor +45 vs -52;
book 1-5 CONTRA a operacao -88; agressao bruta alta na entrada = pior nos 2
lados; preco que anda a favor em 1s = +45; RLP ativo logo apos a entrada
associa-se a lucro (+25 no tercil alto de rlp_vol_3s).

```bash
python autopsia_operacoes.py                          # ancora por preco
python autopsia_operacoes.py --fills-exatos fills_copia.csv   # ms exatos
```

## Regra congelada — exaustão de venda (descoberta 14/08, NÃO recalibrar)

Parâmetros fixos (todos vindos do dia 14, sem recalcular no OOS):
- corretora Ideal, venda agressiva >= 652 contratos em 5s (P95 dia 14)
- WIN parado: |dpx_5s| <= 10 pts
- IND em queda: último print <= 5s de idade e dIND < -3 pts
- reposição do bid baixa: vol bid após (<=3s) / antes < 1.0
- horizonte 10s; sinal = reversão para alta
- auxiliar (NÃO altera a conclusão): pool varejo (Ideal,Genial,XP,Santander,BTG,Agora) >= 2474/5s

Resultado dia 14: regra 67% vs base 54% (Δ +13pp, n=51) | pool 62% vs 53% (n=39)

OOS segunda: `python regra_exaustao_venda.py` (roda dia 14 + dia 17 e imprime o veredito).
Regra de ouro: se der 55-60% vs controle ~45-50% = generalizou; se ~controle, não.
NUNCA alterar parâmetros após ver o OOS.

## Book com profundidade real (NIVEIS_BOOK=500) — 15/08

A janela de book do ProfitChart mostra ORDENS (nao niveis): 60 linhas ~ 2-3
ticks; 500 linhas ~ 25 ticks. Sondado no replay dia 14: linha 0 @ 171.620 ate
linha 499 @ 171.495, cada linha = corretora + volume + preco. Vira a base para
mapear os alvos OCO do varejo (que ficam a dezenas de ticks da entrada).

ALTERADO no motor (15/08):
- `NIVEIS_BOOK` configuravel via env `MOTOR_NIVEIS_BOOK` (default 500).
- `iniciar_motor.bat` ja exporta `MOTOR_NIVEIS_BOOK=500`.
- Guardrail no log: apos ~20 snapshots, avisa se a profundidade observada ficar
  < 60% do configurado (janela ao vivo sem linhas suficientes).
- Validado: motor conecta 46.020 topicos (18k book + 28k T&T) em ~50s,
  refresh ~2-4ms/ciclo a 60Hz, schema com 3.006 colunas. Smoke test limpo.
- 17/08 (noite): HOT PATH — auditoria com CSV do Profit mostrou o gap do WIN
  (92,9% total; perda ~100% nos segundos >500 neg/s: ciclo cai para ~700ms =
  545ms refresh + 163ms processo, janela de 500 linhas rola entre polls).
  Fix aplicado em motor_web.py: (1) HORC/HORV assinados so no nivel 0 do book
  (eram 6.000 topicos mortos -> -11% pares/refresh); (2) fast path int/float
  no fnum()/sstr() (4,9x mais rapido; ~28k chamadas/ciclo no rebuild T&T).
  Equivalencia testada (fnum/sstr) e py_compile OK. NAO mexi na logica de
  dedup (MAX/RLP/microlote) — risco zero no dia de pregao.

REQUISITO ANTES DO PREGAO: configurar as janelas de BOOK AO VIVO dos 6 ativos
com 500 linhas no ProfitChart (como a janela de replay que abrimos). Sem isso,
as linhas extras retornam vazio = topicos mortos (custo de conexao sem ganho) e
o guardrail avisa no log. Para voltar ao comportamento antigo:
`set MOTOR_NIVEIS_BOOK=60` (ou remova a linha do .bat).

Efeito colateral esperado: arquivos de book ~6x maiores (3.000 colunas por
snapshot) — disco tem espaco; compressao parquet ajuda nas colunas vazias.

## CHECKLIST PREGAO SEGUNDA (17/08) — book 500 linhas

1. Abrir ProfitChart com as janelas salvos (T&T dos 6 ativos + RLP de WIN/WDO).
2. Para CADA um dos 6 ativos (INDV26, WINV26, DOLU26, WDOU26, DI1F28, DI1F29),
   abrir/ajustar a janela de BOOK (Ofertas) com 500 linhas e o simbolo correto
   no titulo da janela (o motor pareia por simbolo INFO/ATV, nunca por ordem).
3. Garantir que NAO ha nenhuma janela de replay aberta no ProfitChart (replay
   descartado em 16/08) — qualquer janela de replay conflita com a janela ao
   vivo do mesmo simbolo na descoberta automatica. Todas as janelas devem
   estar em MODO AO VIVO (pregao real).
4. Antes das 08:40, conferir no dashboard (http://127.0.0.1:5000/) que os 6
   ativos mostram book/tt capturando (leilao 08:55 cai na janela).
5. Nos primeiros ~2 min de captura, ver o log: a linha "profundidade observada
   X/500 OK" para cada ativo confirma que a janela tem as linhas configuradas.
   Se aparecer "X/500 linhas. Configure a janela..." = janela sem 500 linhas.
6. Se a captura do T&T sofrer com 500 linhas (ciclo_hz caindo ou latencia p95
   subindo no dashboard), reverter no .bat: MOTOR_NIVEIS_BOOK=60 e reiniciar.

## HOT PATH 17/08 — o que verificar na terca (18/08)

Objetivo: fechar o gap do WIN nos segundos de rajada (era ~7%: 92,9% total).
A perda acontece quando a janela T&T de 500 linhas rola mais rapido que o poll
(medido: ciclo 1 Hz a 48k pares nas rajadas). O fix cortou topicos mortos e
acelerou o parse — NAO alterou a logica de dedup.

Verificar no dashboard/log durante o pregao, nas horas de abertura (9-10h):
1. [CICLO] nas rajadas: refresh + process devem somar MENOS que ~700ms do dia
   17 (alvo: 550-620ms). Se o ciclo_hz nas rajadas subir de ~1 Hz para 1,5+,
   o fix funcionou.
2. [FIDEDIGNIDADE] NEG vs detectados: a linha de perda nas janelas deve sumir
   ou cair bem abaixo dos ~350k/dia do dia 17.
3. Fim do dia: exportar o CSV do Profit e rodar
   `python auditar_export_csv.py --csv WINFUT_F_0_Trade_18-08-2026.csv --dia 20260818 --sym WINV26`
   — a cobertura dos segundos >500 neg/s deve subir de 91,2% para mais.

Se o ganho for insuficiente, o proximo passo (NAO aplicado ainda, exige teste
de equivalencia) e a deteccao T&T INCREMENTAL: em vez de rebuildar as contagens
assinatura das 4.000 linhas a cada ciclo, atualizar so as linhas mudadas
(tt_sujas) + resync periodico — cortaria mais ~30-50ms/ciclo.

## ARQUITETURA — SEPARACAO DE MAQUINAS (17/08) — CAPTURA PURA

**DECISAO: esta maquina (ProfitChart + motor) e EXCLUSIVA para gravacao.**

- NUNCA rodar nesta maquina: features ao vivo, modelos, walk-forward,
  meta-labeling, treino, scraping do dashboard, qualquer coisa que
  processe os parquets durante o pregao.
- O hot path ja tem custo medido (refresh COM 545ms + processamento no
  pico = ciclo ~700ms a 48k pares). Cada milissegundo extra de processamento
  aqui = mais rotacao de janela entre polls = mais perda nas rajadas.
- As perdas restantes (WIN ~7% nas rajadas >500 neg/s) sao LIMITE DE
  PROTOCOLO (janela T&T 500 linhas rolando entre polls a ~1Hz). Nao ha
  feature/modelo que corrija isso; so ciclo mais rapido (corte de topicos
  ja feito: 52.020 -> 46.032) ou janela maior.

**O que PODE rodar aqui (custo desprezivel):**
- Motor + dashboard (HTML estatico + /api/status; o dashboard foi reduzido
  a tabelas numericas — sem canvas, sem gauges, sem graficos).
- Consolidacao horaria (thread leve, ~1x/hora).
- Auditoria agendada 18:35 (depois do fechamento).
- iniciar_motor.bat (08:40:40, com guarda de porta).

**Maquina B (analise) — scripts para copiar:**
- features_fluxo.py (base 1s), walkforward_1s.py, treinar_1s.py,
  metarule_1s.py, features_book.py, liquidez_absorcao.py,
  detectar_retirada.py, auditar_export_csv.py, validar_dia_vs_baseline.py,
  converter_csv_historico.py, rodar_pipeline.py, regra_exaustao_venda.py,
  relogio_mestre.py, baseline_historico.py, FEATURES.md, configs_log.jsonl.
- Maquina B precisa de: D:\MarketData\Profit\RAWHISTORICO (CSVs convertidos),
  D:\MarketData\Profit\FEATURES1S (base gerada), D:\MarketData\Profit\BASELINE.
- Transferencia de dados: CSV exportado do ProfitChart (como ja feito) OU
  copia dos parquets RAW de D:\MarketData\Profit\RAW\ano=2026\... apos o
  pregao. NUNCA copiar durante o pregao (I/O no disco do motor pode atrasar
  o flush e causar perda).
- A maquina B pode rodar o modulo de features ao vivo SOBRE COPIA dos
  parquets, nunca sobre o disco de gravacao ativa.

**Regra de ouro:** se a maquina A precisar de um processamento novo,
perguntar primeiro: "isso roda durante o pregao?" Se sim, vai para a B
(copia) ou fica para depois das 18h. A captura nao compete com nada.

## LIVE FEED p/ MAQUINA B — /api/live (17/08)

A maquina A expoe o que ja deduplicou (mesmo stream que grava) via:
  GET http://<ip-A>:5000/api/live?since=<ultimo_time_ms>&book=1

- Resposta: {ts, ultimo_ms, ring_inicio_ms, ring_len, simbolos,
  tt: [[time_ms, a_idx, preco, qtd, agressor(1=C/0=V/2=outro), agente_agr,
        compradora, vendedora, origem_janela], ...], book: [{a, bid:[[p,v,ag]..], ask:[[p,v,ag]..]}]}
- B deve poll a cada 1s com since=<ultimo_ms recebido>. Se since <
  ring_inicio_ms, a B perdeu dados (ring rodou) — recomputar da base do
  parquet do dia (o stream e identico ao gravado) e retomar o poll.
- Custo na A: 1 append/negocio no hot path (~us) + serializacao na thread
  HTTP. Medir ciclo Hz antes/depois no log; se o ciclo piorar, desligar via
  MOTOR_LIVE_FEED=0 (nao implementado ainda — so voltar este commit).
- A B NUNCA escreve na A; so le http://. As features/modelos rodam na B.

## CHECKPOINT — REVISAR DAQUI A 10 PREGOES (marcado em 17/08/2026)

**O que a gente prometeu se lembrar (17/08):**

1. **Separacao de maquinas.** A = captura pura (nunca computar features/modelos
   durante o pregao). B = analise em tempo real consumindo /api/live.
   VERIFICAR: o ciclo Hz do motor degradou com o live feed? (medir no log —
   antes 61-65 Hz; se caiu, desligar o feed).

2. **Book tera ~10 dias** (capturando desde 17/08). AGORA e a hora de entrar as
   features de book no pipeline do modelo — imbalance por regiao, retirada sem
   agressao (bid_pull), reposicao — com o MESMO tratamento: CPCV, OOS, sem
   recalibrar o que foi descoberto antes. Maquina B: features_fluxo.py estendido.

3. **Meta-labeling** (metarule_1s.py) com mais dias: WIN ~20, WDO ~28.
   configs_log.jsonl deve ter dezenas de configs -> PBO/Deflated Sharpe de
   verdade (hoje 3 configs = ilustrativo).

4. **Dia 14 e 17 CONGELADOS** — nada que rodarmos agora recalibra neles.

5. **WIN segue com so 10 dias** — exportar WINFUT de julho (20-30/07, 03/08)
   quando possivel para dobrar os dias do ativo mais importante.

6. **Dashboard da A** reduzido a tabelas numericas (sem canvas/gauges).

7. **Pendencia do hot path:** deteccao T&T incremental (rebuild parcial das
   linhas em vez das 4000 a cada ciclo) — NAO embarcar sem teste de
   equivalencia completo na captura (dedup MAX/RLP/microlote).

8. **A B roda em tempo real "o que acharmos aqui"** — comeca pelas 26 features
   de TT (validadas em 10-19 dias); book entra quando validado; o filtro de
   decisao so depois de PBO/OOS. NUNCA como sinal de trade automatico — e
   filtro de atencao para decisao manual.
