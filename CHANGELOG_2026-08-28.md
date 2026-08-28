# CHANGELOG — 28 de Agosto de 2026

## Resumo

Sessão focada em **3 bugs de dashboard** que impediam visualização de dados, mais  
**1 bug de serialização** que impedia o funcionamento do ML ao vivo. Todos os fixes  
respeitaram a arquitetura desacoplada (features → signal engine → API → dashboard).

---

## Bugs Corrigidos

### 1. 📊 Book Level vazio no dashboard (`—` em todos os campos)

**Sintoma:** RANGE, BOOK IMB L1, MICROPRICE, spread, HHI book — todos mostravam `—`.

**Causa raiz:** `_extrair_pares()` em `features/book_features.py` só aceitava `list` e `dict`.  
O `market_state.py` passava **numpy arrays** (objeto `np.float32`). O método retornava `[]`,  
`calcular()` retornava `None`, e `book_level` ficava `{}`.

**Arquivo:** `features/book_features.py`  
**Método:** `_extrair_pares()`

**Antes:**
```python
if isinstance(snap[key_p], list) and isinstance(snap[key_v], list):
    ...
elif isinstance(snap[key_p], dict) and isinstance(snap[key_v], dict):
    ...
return pares  # ← vazio para numpy array
```

**Depois:**
```python
if isinstance(vp, dict) and isinstance(vv, dict):
    # dict: indexa por chave
    ...
else:
    # list, numpy array, pandas Series — todos suportam zip
    for p, v in zip(vp, vv):
        p_f, v_i = float(p), int(v)
        if p_f > 0 and v_i > 0:
            pares.append((p_f, v_i))
```

**Impacto:** Book level agora calcula corretamente. Dashboard mostra spread, microprice,  
imbalance, HHI, velocity — todas as features de microestrutura.

---

### 2. 📡 Sinais serializados como string (TP/SL/RISCO/R:R vazios)

**Sintoma:** API `/api/sinais` retornava string `"Signal(symbol='WINV26', ...)"` em vez de dict  
com campos `tp`, `sl`, `score`, `confianca`, `sinal`. Dashboard não conseguia ler.

**Causa raiz:** `Signal` é um `@dataclass`. `json.dumps(obj, default=str)` converte dataclass  
em `str()` — produzindo a representação textual, não um dict JSON.

**Arquivo:** `core/signal_engine.py`  
**Método:** `get_sinais()`

**Antes:**
```python
def get_sinais(self):
    return dict(self.sinais)  # ← retorna {ativo: Signal}
```

**Depois:**
```python
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

**Impacto:** Dashboard agora recebe `{"tp": 195, "sl": 155, "score": 0.15, "sinal": 0, ...}`.  
TP, SL, RISCO, R:R, confiança, score — tudo aparece corretamente.

---

### 3. 📈 RangeTracker atualizava mas nunca expunha resultado

**Sintoma:** Campo RANGE sempre mostrava `—`. O `RangeTracker` era instanciado e alimentado  
(`atualizar()` chamado a cada trade), mas os resultados (`estado`, `topo`, `fundo`)  
nunca eram colocados no dict de features.

**Causa raiz:** `signal_engine.py` chamava `tr['range'].atualizar(...)` mas nunca  
chamava `tr['range'].get_estado()` para extrair o resultado.

**Arquivo:** `core/signal_engine.py`  
**Método:** `calcular()`

**Adição:**
```python
if f['preco_fim'] > 0:
    tr['range'].atualizar(f['preco_fim'], ts_now)
    ri = tr['range'].get_estado()       # ← NOVO
    f['range_estado'] = ri['estado']    # 'topo', 'fundo', 'dentro'
    f['range_topo'] = ri['topo']
    f['range_fundo'] = ri['fundo']
    f['range_amplitude'] = ri['amplitude']
    f['range_testes_topo'] = ri['testes_topo']
    f['range_testes_fundo'] = ri['testes_fundo']
```

**Impacto:** Dashboard agora mostra RANGE com cor (âmbar=topo, ciano=fundo, verde=dentro)  
e detalhes (amplitude, testes topo/fundo).

---

## Arquivos Modificados

| Arquivo | Mudança |
|---------|---------|
| `features/book_features.py` | `_extrair_pares()` aceita numpy arrays |
| `core/signal_engine.py` | `get_sinais()` retorna dicts via `asdict()` + campo `sinal` |
| `core/signal_engine.py` | RangeTracker agora alimenta `range_estado` no dict de features |

## Arquivos NÃO Modificados (respeitando arquitetura)

- `adapters/dashboard_api.py` — camada HTTP intacta
- `dashboard_pro.html` — camada de apresentação intacta
- `core/market_state.py` — quem chama as features intacto
- `features_lib.py` — shim de compatibilidade intacto

## Validação

- ✅ `python -m py_compile features/book_features.py` → OK
- ✅ `python -m py_compile core/signal_engine.py` → OK
- ✅ Dashboard mostra book_level com dados reais
- ✅ API `/api/sinais` retorna dicts com tp, sl, score, sinal
- ✅ RANGE mostra estado com cor

---

*Gerado por Buffy 🤖 em 28/08/2026*
