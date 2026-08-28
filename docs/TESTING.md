# Testes

## Como Rodar

python -m pytest testes/ -v

## Status (26/08/2026)

test_features.py: 71 passed, 1 skipped
test_contexto_preco.py: 16 passed
test_contexto_avancado.py: 7 passed
test_scorer.py: 4 passed, 2 skipped
Total: 98 passed, 3 skipped (9.80s)

## Cobertura por Modulo

### features_lib.py
TestEwma (4) | TestHHI (5) | TestEntropia (4) | TestIdadeMs (3)
TestVPIN (2) | TestOFI (5) | TestKyleLambda (4) | TestEWMAZScore (3)
TestJanelaFeatures (5) | TestBookLevel (4+) | TestGeradorJanelas (3+)
TestVolumeProfile (3+) | TestCVD (4) | TestVolNova (3) | TestSessao (5)
TestCapturaDedup (2) | TestCapturaRotacao (1) | TestCapturaMeta (1)

### test_contexto_preco.py
test_preco_context_basico | test_abertura_causal | test_maxima_minima_causal
test_fechamento_anterior | test_ajuste_anterior | test_distancias_normalizadas
test_reset_diario | test_divisao_por_zero | test_sem_futuro
test_preserva_existentes | test_auditoria_leakage

### test_contexto_avancado.py
test_ajuste_media_ponderada | test_ajuste_causalidade_D1
test_vwap_reset_diario | test_vwap_causal_sem_futuro
test_leakage_A_negocio_futuro | test_leakage_B_maxima_futura
test_leakage_C_vwap_final | test_leakage_D_poc_via_vwap
test_leakage_E_volume_futuro | test_auditoria_leakage_avancado

### test_scorer.py
test_predicao_normal | test_falha_registrada | test_estado_salud
test_decisao_threshold

## Testes Antigos (nao executados)

test_b3_staleness: interfaces mudaram
test_book_writer: mocks desatualizados
test_com_watchdog: interfaces mudaram
test_config_flat: estrutura mudou
test_r2_aprendizado: API antiga
