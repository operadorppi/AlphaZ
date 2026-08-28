# Recovery Notes

## 26/08/2026

### Labels WIN/WDO misturados (CRITICO)
Bug: labels_WINV26_4-17_final.jsonl (1.2GB) continha 50% precos WDO para WINV26
Impacto: retorno_pts mostrava diferenca WIN~WDO (~170K pts) em vez de retorno real
Correcao: Re-rodar labeler_vectorizado.py --ativo WINV26 (v939)

### config_models.py vazio
Bug: config_models.py tinha 0 bytes. Motor crashava na importacao
Correcao: Restaurar de backup

### Task Scheduler path errado
Bug: MotorAlphaz_Pipeline apontava para path inexistente apos reorganizacao
Correcao: schtasks /change com novo path

### mask_valido bug no labeler
Bug: labeler_vectorizado.py usava mask_valido (inexistente)
Correcao: Renomear para mask_valid

## 23/08/2026 - v9.13

### P0-1: Scorer ML morto
Bug: _consumir chamava .get() em tuplas; motor passava 6/7 campos
Impacto: Camada ML NUNCA executava em producao
Correcao: scorer.py desempacota tuplas; motor passa neg[6]

### P0-2: Labeler rearmava purge a cada linha neutra
Bug: Embargo rearma a cada trade neutro -> dataset ~100% neutro
Correcao: Embargo so rearma em trade

### P0-3: Labeler ignorava SL
Bug: SL nunca era avaliado -> labels nao representavam execucao real
Correcao: SL avaliada; janela nao cruza dia/ativo

### P0-4: retreinar hardcoded 150k-250k
Bug: --ativo WDOU26 treinava com 0 linhas (faixa WIN hardcoded)
Correcao: FAIXAS_PRECO por prefixo

### P0-5: Pipeline com labeler quebrado
Bug: Retreino noturno usava parquet podre
Correcao: Usa labeler_vectorizado + gate %labels >= 1%

### P0-6: dataset_builder labels vazios
Bug: KeyError ts_ms quando labels nao existiam
Correcao: DataFrame default com chave; merge com (ts_ms, ativo)

## 21/08/2026 - v9.6

### 5 funcionalidades mortas reativadas
1. CrossAssetEngine: registrar() nunca era chamado
2. CrossAssetEngine relogio: cutoffs errados (epoch vs TOD)
3. Pesos por regime: sempre pesos de lateral
4. Confirmacao por regime: _confirmacao_congelada = 3
5. Stop-hunt: condicao sempre falsa
6. Captura batch: todo trade rejeitado como replay

### v9.8.1: Fix dedup crash
Bug: agora_ms (inexistente) na poda do _trades_recentes
Correcao: agora_ms -> agora_epoch

## 20/08/2026 - v9.9

### Fix _garantir_fp
Bug: _garantir_fp() nunca era chamado -> negocios_*.jsonl nunca criados
Correcao: _gravar_trade e _gravar_decisao chamam _garantir_fp()

### Bugs iniciais (6 fixes)
_normalizar_simbolo nao existia | estado nao definido em alimentar_book
await fora de async | get_learning() inexistente
book_snap_ant nunca setado | Cooldown contava da abertura
