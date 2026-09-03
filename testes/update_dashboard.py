#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adiciona novas features ao dashboard HTML."""
from pathlib import Path

html_file = Path('dashboard_pro.html')
content = html_file.read_text(encoding='utf-8')

# Adicionar novos KPIs no HTML
old = '''      <div class="kpi"><div class="l">AMPLITUDE</div><div class="v" id="m_amplitude">—</div><div class="s">pontos</div></div>
    </div>
  </div>
</div>

<div class="grid cols">'''

new = '''      <div class="kpi"><div class="l">AMPLITUDE</div><div class="v" id="m_amplitude">—</div><div class="s">pontos</div></div>
      <div class="kpi"><div class="l">ATR 14</div><div class="v" id="m_atr">—</div><div class="s">norm <span id="m_atr_norm" class="mono">—</span></div></div>
      <div class="kpi"><div class="l">VOL REL</div><div class="v" id="m_vol_rel">—</div><div class="s">acum <span id="m_vol_acum" class="mono">—</span></div></div>
      <div class="kpi"><div class="l">REGIME VOL</div><div class="v" id="m_reg_vol">—</div><div class="s">bps <span id="m_reg_vol_bps" class="mono">—</span></div></div>
      <div class="kpi"><div class="l">VWAP INC 1M</div><div class="v" id="m_vwap_inc1">—</div><div class="s">5m <span id="m_vwap_inc5" class="mono">—</span></div></div>
    </div>
  </div>
</div>

<div class="grid cols">'''

if old in content:
    content = content.replace(old, new)
    print('HTML KPIs added!')
else:
    print('Old text not found')
    # Debug
    idx = content.find('AMPLITUDE')
    if idx >= 0:
        print('Found AMPLITUDE at:', idx)
        print(repr(content[idx:idx+300]))

# Adicionar JavaScript para atualizar novos KPIs
old_js = '''  if(f.amplitude_dia_pts){
    setK('m_amplitude', fmt(f.amplitude_dia_pts,0), null);
  }'''

new_js = '''  if(f.amplitude_dia_pts){
    setK('m_amplitude', fmt(f.amplitude_dia_pts,0), null);
  }
  // v12.2: Novas features de regime e ATR
  if(f.atr_14 != null){
    setK('m_atr', fmt(f.atr_14,0), null);
    setK('m_atr_norm', fmt(f.atr_14_norm,4), null);
  }
  if(f.volume_relativo != null){
    setK('m_vol_rel', fmt(f.volume_relativo,4), null);
    setK('m_vol_acum', fmt(f.volume_acumulado_dia,0), null);
  }
  if(f.regime_realiz_vol != null){
    setK('m_reg_vol', fmt(f.regime_realiz_vol,4), null);
    setK('m_reg_vol_bps', fmt(f.regime_realiz_vol_bps,2), null);
  }
  if(f.vwap_inclinacao_1m != null){
    setK('m_vwap_inc1', fmt(f.vwap_inclinacao_1m,4), null);
    setK('m_vwap_inc5', fmt(f.vwap_inclinacao_5m,4), null);
  }'''

if old_js in content:
    content = content.replace(old_js, new_js)
    print('JavaScript updated!')
else:
    print('JS old text not found')
    # Debug
    idx = content.find('amplitude_dia_pts')
    if idx >= 0:
        print('Found amplitude_dia_pts at:', idx)
        print(repr(content[idx:idx+300]))

html_file.write_text(content, encoding='utf-8')
print('Dashboard updated!')
