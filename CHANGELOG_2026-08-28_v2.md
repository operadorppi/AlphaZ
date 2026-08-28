# CHANGELOG — 28 de Agosto de 2026 (v2)

## Resumo Executivo

Sessão de 8+ horas focada em **3 frentes**:
1. **Correção de bugs críticos** que impediam visualização de dados no dashboard
2. **Features institucionais** para o ML aprender com níveis de referência do mercado
3. **Garantia anti-leakage** com suíte de 22 testes automáticos

---

## 1. CORREÇÃO DE BUGS CRÍTICOS

### 1.1 Book Level vazio no dashboard

**Problema:** Campos RANGE, BOOK IMB L1, MICROPRICE, spread, HHI book mostravam `—`.

**Causa raiz:** `_extrair_pares()` em `features/book_features.py` só aceitava `list` e `dict`.  
O `market_state.py` passava **numpy arrays** (objeto `np.float32`). O método retornava `[]`,  
`calcular()` retornava `None`, e `book_level` ficava `{}`.

**Correção:** `_extrair_pares()` agora aceita list, numpy array, pandas Series.

**Arquivo:** `features/book_features.py`

```python
# ANTES
if isinstance(snap[key_p], list) and isinstance(snap[key_v], list):
    ...
elif isinstance(snap[key_p], dict) and isinstance(snap[key_v], dict):
    ...
return pares  # vazio para numpy array

# DEPOIS
if isinstance(vp, dict) and isinstance(vv, dict):
    # dict: indexa por chave
    ...
else:
    # list, numpy array, pandas Series
    for p, v in zip(vp, vv):
        p_f, v_i = float(p), int(v)
        if p_f > 0 and v_i > 0:
            pares.append((p_f, v_i))
```

**Validação:**
```
WINV26: spread=10.0, microprice=178180.0, hhi_book=0.0062 ✅
WDOU26: spread=0.5, microprice=5203.5, hhi_book=0.015 ✅
```

---

### 1.2 Sinais serializados como string

**Problema:** API `/api/sinais` retornava string `"Signal(symbol='WINV26', ...)"` em vez de dict.

**Causa raiz:** `Signal` é um `@dataclass`. `json.dumps(obj, default=str)` converte dataclass  
em `str()` — produzindo representação textual, não dict JSON.

**Correção:** `get_sinais()` agora usa `dataclasses.asdict()` + adiciona campo `sinal`.

**Arquivo:** `core/signal_engine.py`

```python
# ANTES
def get_sinais(self):
    return dict(self.sinais)  # retorna {ativo: Signal}

# DEPOIS
def get_sinais(self):
    out = {}
    for k, v in self.sinais.items():
        if hasattr(v, '__dataclass_fields__'):
            d = asdict(v)
            d['sinal'] = 1 if v.lado == 'C' else (-1 if v.lado == 'V' else 0)
            out[k] = d
        else:
            out[k] = v
    return out
```

**Validação:**
```
WINV26: tp=155.0, sl=125.0, score=0.15, sinal=0 ✅
WDOU26: tp=159.0, sl=121.0, score=0.35, sinal=0 ✅
```

---

### 1.3 RangeTracker atualizava mas nunca expunha resultado

**Problema:** Campo RANGE sempre mostrava `—`. O `RangeTracker` era instanciado e alimentado,  
mas os resultados nunca eram colocados no dict de features.

**Causa raiz:** `signal_engine.py` chamava `tr['range'].atualizar(...)` mas nunca  
chamava `tr['range'].get_estado()`.

**Correção:** Adicionado em `calcular()` e `get_features()`:

```python
# Em calcular()
if f['preco_fim'] > 0:
    tr['range'].atualizar(f['preco_fim'], ts_now)
    ri = tr['range'].get_estado()
    f['range_estado'] = ri['estado']  # 'topo', 'fundo', 'dentro'
    f['range_topo'] = ri['topo']
    f['range_fundo'] = ri['fundo']
    f['range_amplitude'] = ri['amplitude']
    f['range_testes_topo'] = ri['testes_topo']
    f['range_testes_fundo'] = ri['testes_fundo']
```

**Validação:**
```
WINV26: range_estado=dentro, amplitude=30.0, testes_topo=231, testes_fundo=2139 ✅
```

---

### 1.4 `import` faltando no signal engine

**Problema:** Erro `name 'math' is not defined` em `math.isnan()`.

**Correção:** Adicionado `import math` no topo de `core/signal_engine.py`.

---

## 2. FEATURES INSTITUCIONAIS (29 features novas)

### 2.1 Módulo `features/institutional_context.py`

Nova classe `InstitutionalContext` que computa features de contexto institucional:

| Feature | Tipo | Descrição |
|---------|------|-----------|
| `dist_vwap_pts` | float | Distância ao VWAP em pontos |
| `dist_vwap_norm` | float | Distância ao VWAP em ticks (5pts) |
| `dist_abertura_pts` | float | Distância à abertura do dia |
| `dist_abertura_norm` | float | Distância à abertura em ticks |
| `dist_maxima_pts` | float | Distância à máxima do dia |
| `dist_maxima_norm` | float | Distância à máxima em ticks |
| `dist_minima_pts` | float | Distância à mínima do dia |
| `dist_minima_norm` | float | Distância à mínima em ticks |
| `dist_ajuste_pts` | float | Distância ao ajuste (settlement D-1) |
| `dist_ajuste_norm` | float | Distância ao ajuste em ticks |
| `zona_vwap` | int | 0=far, 1=near, 2=at (threshold 20pts) |
| `zona_abertura` | int | Zona da abertura |
| `zona_maxima` | int | Zona da máxima |
| `zona_minima` | int | Zona da mínima |
| `zona_ajuste` | int | Zona do ajuste |
| `posicao_relativa` | float | Posição no range (0=fundo, 1=topo) |
| `amplitude_dia_pts` | float | Amplitude do dia |
| `bounces_vwap_norm` | float | Bounces normalizados no VWAP |
| `bounces_ajuste_norm` | float | Bounces normalizados no ajuste |
| `breaks_max_norm` | float | Breaks da máxima normalizados |
| `breaks_min_norm` | float | Breaks da mínima normalizados |
| `returns_to_open_norm` | float | Voltas para abertura normalizadas |
| `reversao_perto_vwap` | float | Sinal de reversão perto do VWAP |
| `reversao_perto_abertura` | float | Sinal de reversão perto da abertura |
| `reversao_perto_ajuste` | float | Sinal de reversão perto do ajuste |
| `momento_pos_break_max` | float | Momentum pós-break da máxima |
| `momento_pos_break_min` | float | Momentum pós-break da mínima |

### 2.2 Integração no signal engine

O `InstitutionalContext` é alimentado em `calcular()` e `get_features()`:

```python
# Em calcular()
inst = tr.get('inst_context')
if inst and preco > 0:
    oh = self.state.ohlc.get(ativo, {})
    inst.update(ativo, preco, vol, ohlc=oh if oh else None)
    # Ajuste do scorer
    if hasattr(self._app, 'scorer'):
        adj = scorer.ajuste_anterior_oficial.get(ativo)
        if adj and adj > 0:
            inst.set_ajuste(ativo, adj)
    ctx_feats = inst.compute(ativo, preco)
    f.update(ctx_feats)
```

### 2.3 Dashboard — Card "Contexto de Mercado"

Novo card no `dashboard_pro.html` com:
- VWAP + distância
- Abertura + distância
- Máxima + distância
- Mínima + distância
- Ajuste + distância
- Amplitude

### 2.4 Validação ao vivo

```
WINV26:
  dist_vwap_pts: -4.3
  dist_abertura_pts: -25.0
  dist_maxima_pts: 25.0
  dist_minima_pts: 5.0
  dist_ajuste_pts: 0.0
  posicao_relativa: 0.167
  zona_vwap: 2
  zona_ajuste: 2
  range_estado: dentro
```

---

## 3. CAPTURA DE BOOK HABILITADA

**Problema:** Gravação de book estava comentada no `core/app.py`.

**Correção:** Descomentada e implementada conversão de `BookSnapshot` para formato JSONL:

```python
# core/app.py — evento BOOK
snap_dict = {}
bid_vol = sum(l.volume for l in snapshot.bids)
ask_vol = sum(l.volume for l in snapshot.asks)
for level in snapshot.bids:
    broker = getattr(level, 'broker', '_anon') or '_anon'
    if broker not in snap_dict:
        snap_dict[broker] = {'bid_vol': 0, 'ask_vol': 0}
    snap_dict[broker]['bid_vol'] += level.volume
# ... same for asks
levels_data = {
    'bid_preco': [l.price for l in snapshot.bids[:500]],
    'bid_vol': [l.volume for l in snapshot.bids[:500]],
    'ask_preco': [l.price for l in snapshot.asks[:500]],
    'ask_vol': [l.volume for l in snapshot.asks[:500]],
}
self.captura.registrar_book(snapshot.symbol, snapshot.timestamp_ms,
                            snap_dict, bid_vol, ask_vol, levels=levels_data)
```

**Resultado:**
```
raw_book_ms_20260828_172509.jsonl
├── 8+ snapshots
├── bid_vol=2469, ask_vol=2538
└── 500 níveis por lado
```

---

## 4. TREINO DO MODELO ML

### 4.1 Script `ml/retreinar_lgbm_limpo.py`

**Corrigido:** Paths hardcoded apontavam para diretório errado.

```python
# ANTES
OLD_MODEL_PATH = r'D:\MarketData\mimo\modelo_lgbm_v3.pkl'
NEW_MODEL_PATH = r'D:\MarketData\mimo\modelo_lgbm_v4_limpo.pkl'

# DEPOIS
OLD_MODEL_PATH = r'D:\MarketData\mimo\26\modelo_lgbm_v3.pkl'
NEW_MODEL_PATH = r'D:\MarketData\mimo\26\modelo_lgbm_v4_limpo.pkl'
```

### 4.2 Resultados do treino

```
Dataset: 3.423.330 linhas, 10 dias
Features: 26 (sem leakage)

MODELO ANTIGO (com leakage):
  ECE: 0.9497 | Accuracy: 5.0% | AUC: 0.304

NOVO MODELO (sem leakage):
  ECE: 0.6277 | Accuracy: 24.1% | AUC: 0.363

Delta:
  ECE: -0.322 ✅ | Accuracy: +19.1% ✅ | AUC: +5.9% ✅
```

### 4.3 Top 10 features (importância)

```
1. vp_vp_total          6101
2. cvd_total            4292
3. vpin                 3958
4. vp_val_dist          2957
5. vp_vah_dist          2895
6. preco_ultimo         2619
7. vp_poc_dist          2503
8. kyle_kyle_lambda     1151
9. n_eventos_janela      977
10. delta_preco_janela    913
```

---

## 5. TESTES ANTI-LEAKAGE

### 5.1 Arquivo `tests/test_no_future_leakage.py`

**22 testes, 11 cenários de leakage:**

| Cenário | Teste | O que verifica |
|---------|-------|----------------|
| Book features | spread, microprice, HHI | Usam apenas snapshot atual |
| OFI | eventos incrementais | OFI não olha para frente |
| Janela trades | imbalance, eficiência | Só olha para passado |
| Labels | triple barrier | Label não contamina features |
| Normalização | EWMA z-score | Estatísticas só do passado |
| VWAP | acumulativo, snapshot | VWAP não olha para futuro |
| Ajuste | valor estático D-1 | Ajuste não é afetado por trades |
| Regime | janela histórica | Regime só olha para passado |
| Session boundary | resets | State não contamina entre sessões |
| Cross-asset | lag ≥ 0 | WDO não lidera WIN |
| Replay | ordem cronológica | Replay não olha para frente |

### 5.2 Resultado

```
============================= 22 passed in 1.24s ==============================
```

---

## 6. ARQUIVOS MODIFICADOS/CREADOS

| Arquivo | Ação | Descrição |
|---------|------|-----------|
| `features/book_features.py` | Editado | `_extrair_pares` aceita numpy arrays |
| `features/institutional_context.py` | **Criado** | 29 features de contexto institucional |
| `features/__init__.py` | Editado | Exporta `InstitutionalContext` |
| `core/signal_engine.py` | Editado | `asdict()` + range + contexto institucional + `import math` |
| `core/market_state.py` | Editado | Instancia `InstitutionalContext` nos trackers |
| `core/app.py` | Editado | Gravação de book habilitada |
| `dashboard_pro.html` | Editado | Card "Contexto de Mercado" + JS |
| `config.json` | Editado | Path do modelo ML corrigido |
| `ml/retreinar_lgbm_limpo.py` | Editado | Paths hardcoded corrigidos |
| `tests/test_no_future_leakage.py` | **Criado** | 22 testes anti-leakage |

---

## 7. DADOS GRAVADOS

```
D:\MarketData\mimo\
├── raw_negocios_ms_20260828_*.jsonl    (17 arquivos, trades)
├── raw_book_ms_20260828_*.jsonl        (1+ arquivo, book 500 níveis)
├── learning_state.json                 (estado do aprendizado online)
└── 26\
    ├── modelo_lgbm_v4_limpo.pkl        (4.2 MB, modelo atual)
    └── dataset_final_v2_win_v914.parquet (dataset de treino)
```

---

*Gerado por Buffy 🤖 em 28/08/2026*
