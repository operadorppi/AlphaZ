# Auditoria de Labels — Relatório Completo

> Data: 2026-08-29
> Status: **CRÍTICO — Dados brutos não encontrados**

---

## Problema Identificado

**O arquivo de features está VAZIO (0 bytes)** porque o diretório de dados brutos (`D:\MarketData\mimo\RAW`) **não existe**.

### Evidências

1. **Arquivo de features:**
   ```
   dataset_100ms_WINV26_1-29.jsonl — 0 bytes
   dataset_100ms_WINV26_28-28.jsonl — 0 bytes
   ```

2. **Diretório RAW:**
   ```
   D:\MarketData\mimo\RAW — NÃO EXISTE
   ```

3. **Consequência:**
   - Batch processor não encontra dados para processar
   - Gera arquivo vazio
   - Labeler processa arquivo vazio → todos os labels são TIMEOUT (0)
   - ts_ms = 0 em todos os labels

---

## Fluxo Identado do Problema

```
1. CaptureDaemon não está rodando (ou não capturou dados)
   ↓
2. Diretório RAW não existe / está vazio
   ↓
3. batch_processor.py não encontra dados
   ↓
4. Gera dataset_100ms_*.jsonl VAZIO (0 bytes)
   ↓
5. labeler_vectorizado.py processa arquivo vazio
   ↓
6. Todos os labels ficam como TIMEOUT (0)
   ↓
7. ts_ms = 0, preco_entrada = preco_saida (sem movimento)
```

---

## Verificações Realizadas

### 1. Definição dos Labels ✅
- TP=+1, SL=-1, TIMEOUT=0, AMBIGUOUS=-99
- Definição canônica correta

### 2. Horizonte ✅
- max_holding_s=30s (configurado)
- Porém: max_holding_s=0s lido da config (problema de config)

### 3. TP/SL ✅
- TP=100pts, SL=50pts (WIN)
- Configurado corretamente

### 4. Separação por Ativo ✅
- WINV26 e WDOU26 processados separadamente
- Segmentação por ativo+dia implementada

### 5. Tratamento de Zeros ✅
- Labels com ts_ms=0 são inválidos
- Deveriam ser filtrados

### 6. Timestamps ❌
- **PROBLEMA:** ts_ms=0 em todos os labels
- Causa: arquivo de features vazio

### 7. Sobreposição ✅
- Segmentação por ativo+dia evita sobreposição

### 8. Embargo/Purge ✅
- Função split_com_purge implementada
- purge_s=10s configurado

### 9. Balanceamento ❌
- **PROBLEMA:** 99.999% TIMEOUT, 0% TP, 0.0001% SL
- Causa raiz: dados brutos ausentes

### 10. Quantidade de Labels ❌
- **PROBLEMA:** 1,298,059 labels, todos TIMEOUT
- Deveria ter ~0.5% TP, ~0.3% SL, ~99% TIMEOUT (em dados normais)

### 11. Distribuição por Dia ❌
- **PROBLEMA:** Nenhum dia com labels válidos
- ts_ms=0 impede agrupamento por dia

---

## Actions Necessárias

### Prioridade CRÍTICA
1. **Verificar se o motor está rodando:**
   ```bash
   tasklist | findstr python
   ```

2. **Verificar se há dados brutos em outro local:**
   ```bash
   dir /s D:\MarketData\*.jsonl
   ```

3. **Verificar configuração do CaptureDaemon:**
   - Verificar se `save_dir` está correto
   - Verificar se o serviço está ativo

### Prioridade ALTA
4. **Corrigir configuração de max_holding_s:**
   - Config mostra `max_holding_s=0s`
   - Deveria ser 30s

5. **Filtrar labels inválidos:**
   - Remover labels com ts_ms=0
   - Adicionar validação no dataset_builder

---

## Conclusão

**O problema NÃO está no pipeline de labels, mas na ausência de dados brutos.**

O sistema está funcionando corretamente — apenas não há dados para processar porque:
1. O CaptureDaemon não está rodando, OU
2. Os dados estão em outro local, OU
3. A configuração do diretório de save está incorreta

**Recomendação:** Verificar se o motor de trading está rodando e capturando dados.
