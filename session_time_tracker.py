# session_time_tracker.py — Tempo de sessao ao vivo (v9.40)
# Adicionado: minutos_desde_abertura (faltava no v9.37)
import math

_ABERTURA_TOD = 9 * 3600 * 1000  # 09:00 em ms
_FECHAMENTO_TOD = 17 * 3600 * 1000 + 45 * 60 * 1000  # 17:45

class SessionTimeTracker:
    def __init__(self):
        self._ultimo_dia = None

    def update(self, ts_ms):
        dia = (int(ts_ms) - 3*3600*1000) // 86400000
        if self._ultimo_dia is not None and dia != self._ultimo_dia:
            pass
        self._ultimo_dia = dia

    def snapshot(self, ts_ms):
        tod = int(ts_ms) % 86400000
        seg_abt = max(0, (tod - _ABERTURA_TOD) / 1000)
        min_abt = seg_abt / 60.0
        min_fc = max(0, (_FECHAMENTO_TOD - tod) / 60000)
        hora_frac = (tod / 86400000.0) * 2 * math.pi
        
        # Bloco da sessao
        if tod < _ABERTURA_TOD:
            bloco = 0  # pre-abertura
        elif tod < 10 * 3600 * 1000:
            bloco = 1  # abertura (primeiros 60min)
        elif tod < 12 * 3600 * 1000:
            bloco = 2  # manha
        elif tod < 13 * 3600 * 1000 + 30 * 60 * 1000:
            bloco = 3  # almoco
        elif tod < 16 * 3600 * 1000:
            bloco = 4  # tarde
        else:
            bloco = 5  # fechamento
        
        return {
            "segundos_desde_abertura": round(seg_abt, 1),
            "minutos_desde_abertura": round(min_abt, 1),
            "minutos_ate_fechamento": round(min_fc, 1),
            "sin_horario": round(math.sin(hora_frac), 4),
            "cos_horario": round(math.cos(hora_frac), 4),
            "bloco_sessao": bloco,
        }
