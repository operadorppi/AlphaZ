# volume_relativo_tracker.py — Volume relativo ao vivo (v9.40)
# Compara volume atual com volume esperado para aquele horario
# Volume esperado: media historica do mesmo horario (causal)
# P0-A22 (v15.16): dia e TOD de Brasilia via funcoes temporais oficiais
# (core.temporal). ANTES, o minuto do dia usava `ts % 86400000` cru (UTC):
# volume de 14h BRT caia no minuto 480 (17h UTC) e NEM ERA contabilizado
# (fora do range 0-405) — feature de volume relativo subestimada.

import time
from collections import defaultdict

from core.temporal import dia_de_ts_br, tod_de_ts_br


class VolumeRelativoTracker:
    """Compara volume acumulado do dia com referencia historica."""

    def __init__(self):
        self._volume_dia = 0.0
        self._volume_por_minuto = defaultdict(float)  # minuto -> total
        self._minuto_atual = -1
        self._historico = defaultdict(list)  # minuto -> [volumes de dias anteriores]
        self._ultimo_dia = None

    def update(self, vol, ts_ms):
        """Atualiza com volume deste tick e timestamp."""
        if vol is None or vol <= 0:
            return

        # Detectar virada de dia (dia civil de Brasilia)
        dia = dia_de_ts_br(ts_ms)
        if self._ultimo_dia is not None and dia != self._ultimo_dia:
            # Salvar volume do dia anterior por minuto
            for m, v in self._volume_por_minuto.items():
                self._historico[m].append(v)
                if len(self._historico[m]) > 20:
                    self._historico[m] = self._historico[m][-20:]
            self._volume_por_minuto.clear()
            self._volume_dia = 0.0

        self._ultimo_dia = dia
        self._volume_dia += vol

        # Minuto do dia em TOD de Brasilia (0-390 para 6h45 de pregao)
        tod = tod_de_ts_br(ts_ms)
        minuto = (tod - 9 * 3600 * 1000) // 60000  # desde 09:00 BRT
        if minuto >= 0 and minuto < 405:
            self._minuto_atual = minuto
            self._volume_por_minuto[minuto] += vol

    def snapshot(self):
        """Retorna features de volume relativo."""
        # Volume ate agora
        vol_acum = self._volume_dia

        # Volume por minuto atual
        vol_min = self._volume_por_minuto.get(self._minuto_atual, 0.0) if self._minuto_atual >= 0 else 0.0

        # Volume esperado: media historica deste minuto
        vol_esperado = 0.0
        if self._minuto_atual >= 0 and self._historico.get(self._minuto_atual):
            vol_esperado = sum(self._historico[self._minuto_atual]) / len(self._historico[self._minuto_atual])

        # Volume relativo: atual / esperado
        vol_relativo = vol_acum / max(vol_esperado, 1.0) if vol_esperado > 0 else 1.0

        return {
            'volume_acumulado_dia': round(vol_acum, 2),
            'volume_por_minuto': round(vol_min, 2),
            'volume_relativo': round(vol_relativo, 4),
        }

    def reset_diario(self):
        """v12.2: Reset diário para evitar acúmulo entre dias."""
        self._volume_dia = 0.0
        self._volume_por_minuto.clear()
        self._minuto_atual = -1
        self._historico.clear()
        self._ultimo_dia = None
