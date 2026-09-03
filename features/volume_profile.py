# -*- coding: utf-8 -*-
"""
features/volume_profile.py — VolumeProfileTracker (POC, VAH/VAL).

P0-A27 (v15.22): identidade temporal EXPLÍCITA no tracker.
ANTES o tracker nao conhecia a data e dependia de o chamador lembrar de
chamar reset()/reset_diario() na virada de sessao. No ScorerML a ordem era:
  vps.atualizar() ... depois ... _atualizar_ajuste_para_dia() (reset)
Ou seja, a 1a linha de um dia novo entrava no perfil do dia ANTERIOR
(contaminando o POC/VAH/VAL daquele instante) e o reset posterior apagava
o 1o trade do dia novo. AGORA o tracker faz rollover interno por dia de
Brasilia (core.temporal.dia_de_ts_br, fonte unica do P0-A22): basta passar
ts_ms no atualizar() — nenhum chamador precisa mais lembrar de resetar.
"""

from core.temporal import dia_de_ts_br


class VolumeProfileTracker:
    """Volume Profile do dia: acumula volume por nivel de preco.

    Um perfil = uma sessao/dia BRT. Na primeira atualizacao de um dia
    diferente do ultimo registrado, o perfil anterior e descartado
    automaticamente (rollover interno). `reset()` tambem zera a identidade
    de dia, entao a proxima atualizacao comeca um perfil novo.
    """

    def __init__(self, tick=5, value_area=0.70):
        self.tick = tick
        self.value_area = value_area
        self.volumes = {}
        self.delta = {}
        self._ultimo_dia = None  # P0-A27: identidade de sessao (dia BRT)

    def atualizar(self, ts_ms, preco, qtd, agressor):
        """Acumula volume/delta no nivel de preco do dia de `ts_ms`.

        P0-A27 (v15.22): `ts_ms` agora e OBRIGATORIO e define a sessao.
        Se o dia BRT de `ts_ms` mudou desde a ultima atualizacao, o perfil
        anterior e descartado ANTES de acumular — o 1o trade do dia novo
        nunca entra no perfil do dia anterior nem e perdido.
        """
        if ts_ms is None or preco <= 0 or qtd <= 0:
            return
        dia = dia_de_ts_br(ts_ms)
        if self._ultimo_dia is not None and dia != self._ultimo_dia:
            self._reset_perfil()  # virada de sessao: nao carrega o dia anterior
        self._ultimo_dia = dia
        nivel = int(preco / self.tick + 0.5) * self.tick
        self.volumes[nivel] = self.volumes.get(nivel, 0) + qtd
        ag = (agressor or '').lower()
        d = qtd if ag in ('compra', 'comprador') else (-qtd if ag in ('venda', 'vendedor') else 0)
        self.delta[nivel] = self.delta.get(nivel, 0) + d

    def _reset_perfil(self):
        """Zera o perfil acumulado (volumes/delta)."""
        self.volumes = {}
        self.delta = {}

    def reset(self):
        """Reset completo: perfil E identidade de dia (proxima sessao nova)."""
        self._reset_perfil()
        self._ultimo_dia = None

    def reset_diario(self):
        """Compat (v12.2): mesmo que reset(). Rollover diario agora e interno."""
        self.reset()

    def calcular(self, preco_atual):
        if not self.volumes or preco_atual <= 0:
            return {'poc_dist': 0, 'vah_dist': 0, 'val_dist': 0,
                    'poc_acima': 0, 'vp_total': 0}
        total = sum(self.volumes.values())
        poc = max(self.volumes, key=self.volumes.get)
        niveis = sorted(self.volumes.keys())
        idx = niveis.index(poc)
        va_vol = self.volumes[poc]
        lo = hi = idx
        while va_vol < self.value_area * total and (lo > 0 or hi < len(niveis) - 1):
            vol_lo = self.volumes[niveis[lo - 1]] if lo > 0 else -1
            vol_hi = self.volumes[niveis[hi + 1]] if hi < len(niveis) - 1 else -1
            if vol_lo >= vol_hi:
                lo -= 1; va_vol += self.volumes[niveis[lo]]
            else:
                hi += 1; va_vol += self.volumes[niveis[hi]]
        return {
            'poc_dist': round(poc - preco_atual, 1),
            'vah_dist': round(niveis[hi] - preco_atual, 1),
            'val_dist': round(niveis[lo] - preco_atual, 1),
            'poc_acima': 1 if poc > preco_atual else 0,
            'vp_total': total,
        }
