# volume_relativo_tracker.py — Volume relativo ao vivo (v9.40)
# Compara volume atual com volume esperado para aquele horario
# Volume esperado: media historica do mesmo horario (causal)
# P0-A22 (v15.16): dia e TOD de Brasilia via funcoes temporais oficiais
# (core.temporal). ANTES, o minuto do dia usava `ts % 86400000` cru (UTC):
# volume de 14h BRT caia no minuto 480 (17h UTC) e NEM ERA contabilizado
# (fora do range 0-405) — feature de volume relativo subestimada.
# P1-A26 (v15.21): baseline entre dias + disponibilidade explicita.
#   - reset_diario NÃO apaga mais _historico (o scorer chamava reset na 1a
#     linha de cada dia e o arquivo do dia anterior morria — em live a
#     referencia NUNCA acumulava).
#   - volume_relativo agora compara ACUMULADO de hoje vs ACUMULADO tipico
#     ate o mesmo minuto (normalizado ~1.0 = ritmo normal; antes dividia o
#     acumulado do dia pela media de UM minuto — valor sem escala).
#   - snapshot expoe referencia_disponivel + referencia_dias para o ML
#     distinguir "1.0 sem referencia (cold start)" de "1.0 = volume normal".

from collections import defaultdict

from core.temporal import dia_de_ts_br, tod_de_ts_br

# Janela de minutos do pregao: 09:00 ate 18:30 BRT (570 min).
# ANTES: 405 (= 15:45) — WIN negocia ate ~18:15 e WDO ate ~18:00; o acumulado
# do dia continuava crescendo apos 15:45 mas a referencia parava de ser
# registrada, inflando volume_relativo no fim da tarde.
_MINUTO_INICIO = 0
_MINUTO_FIM = 570  # 09:00 + 570 min = 18:30 BRT
_MAX_DIAS_HISTORICO = 20


class VolumeRelativoTracker:
    """Compara volume acumulado do dia com referencia historica causal.

    Referencia = perfis de minutos de DIAS ANTERIORES (max 20 dias). O perfil
    de um dia so entra no historico na virada para o dia seguinte (rollover
    interno do update()). reset_diario() zera o estado do dia corrente mas
    PRESERVA o historico — e a fonte dessa politica (v15.21/A26).
    """

    def __init__(self):
        self._volume_dia = 0.0
        self._volume_por_minuto = defaultdict(float)  # minuto -> total (dia corrente)
        self._minuto_atual = -1
        # v15.21: perfil diario = {minuto: volume}; um dict por dia anterior.
        # ANTES era {minuto: [volumes de dias diferentes]} (transposto, sem
        # fronteira de dia) e reset_diario() o apagava a cada virada.
        self._historico = []  # list[dict {minuto: volume}] — dias anteriores
        self._ultimo_dia = None
        # Cache do acumulado tipico por minuto (hist mudanca so no rollover).
        self._esp_cache = {}
        self._hist_versao = 0

    # ------------------------------------------------------------------
    def _arquivar_dia(self):
        """Move o perfil do dia corrente para o historico (rollover)."""
        if self._volume_por_minuto:
            perfil = dict(self._volume_por_minuto)
            self._historico.append(perfil)
            if len(self._historico) > _MAX_DIAS_HISTORICO:
                self._historico = self._historico[-_MAX_DIAS_HISTORICO:]
        self._volume_por_minuto.clear()
        self._volume_dia = 0.0
        self._minuto_atual = -1
        self._hist_versao += 1
        self._esp_cache.clear()

    def update(self, vol, ts_ms):
        """Atualiza com volume deste tick e timestamp."""
        if vol is None or vol <= 0:
            return

        # Detectar virada de dia (dia civil de Brasilia)
        dia = dia_de_ts_br(ts_ms)
        if self._ultimo_dia is not None and dia != self._ultimo_dia:
            self._arquivar_dia()
        self._ultimo_dia = dia
        self._volume_dia += vol

        # Minuto do dia em TOD de Brasilia
        tod = tod_de_ts_br(ts_ms)
        minuto = (tod - 9 * 3600 * 1000) // 60000  # desde 09:00 BRT
        if _MINUTO_INICIO <= minuto < _MINUTO_FIM:
            self._minuto_atual = minuto
            self._volume_por_minuto[minuto] += vol

    # ------------------------------------------------------------------
    def _esperado_acumulado(self, minuto):
        """Acumulado tipico ate `minuto` (media entre dias de referencia).

        Causal: usa apenas perfis de dias ANTERIORES arquivados. O acumulado
        de cada dia = soma dos volumes dos minutos <= `minuto` daquele dia.
        Um dia so entra na media se chegou ate `minuto` com volume (dia
        parcial capturado a partir das 14h nao puxa a media do 09h para baixo).
        Returns:
            (esperado_acumulado, n_dias) — (0.0, 0) sem historico no minuto.
        """
        if not self._historico or minuto < 0:
            return 0.0, 0
        cache = self._esp_cache.get(minuto)
        if cache is not None and cache[0] == self._hist_versao:
            return cache[1], cache[2]

        total = 0.0
        n = 0
        for perfil in self._historico:
            subtotal = 0.0
            for m, v in perfil.items():
                if m <= minuto:
                    subtotal += v
            if subtotal > 0:
                total += subtotal
                n += 1
        if n == 0:
            return 0.0, 0
        esperado = total / n
        self._esp_cache[minuto] = (self._hist_versao, esperado, n)
        return esperado, n

    def snapshot(self):
        """Retorna features de volume relativo.

        v15.21 (A26): `volume_relativo=1.0` pode significar duas coisas
        diferentes, e agora o ML consegue distinguir:
          - referencia_disponivel=False → 1.0 e FALLBACK (sem baseline ainda,
            cold start / 1o dia do processo);
          - referencia_disponivel=True  → valor real, e 1.0 = volume NORMAL
            (ritmo igual a media historica ate este minuto).
        `referencia_dias` diz quantos dias de baseline sustentam o valor.

        NOTA de escala: numerador e denominador usam a MESMA janela (minutos
        de pregao gravados), entao ~1.0 = ritmo historico, 2.0 = dobro do
        acumulado tipico neste horario. ANTES o denominador era a media de UM
        minuto vs acumulado do dia inteiro (sem escala comparavel).
        """
        # Volume por minuto atual
        vol_min = self._volume_por_minuto.get(self._minuto_atual, 0.0) if self._minuto_atual >= 0 else 0.0

        # Acumulado tipico ate o minuto atual (media dos dias anteriores)
        esperado_acum, n_dias = self._esperado_acumulado(self._minuto_atual)
        referencia_disponivel = n_dias > 0 and esperado_acum > 0

        # Acumulado de hoje dentro da MESMA janela de minutos gravados
        # (vol fora do pregao nao entra no numerador — senao o ratio infla
        # sem contraparte no historico). volume_acumulado_dia (abaixo) segue
        # expondo o total bruto do dia para o dashboard.
        vol_acum_janela = sum(self._volume_por_minuto.values()) if self._minuto_atual >= 0 else 0.0

        # Volume relativo: acumulado hoje / acumulado tipico ate este minuto
        if referencia_disponivel:
            vol_relativo = vol_acum_janela / esperado_acum
        else:
            vol_relativo = 1.0  # fallback SEM referencia (flag avisa)

        return {
            'volume_acumulado_dia': round(self._volume_dia, 2),
            'volume_por_minuto': round(vol_min, 2),
            'volume_relativo': round(vol_relativo, 4),
            # P1-A26 (v15.21): disponibilidade da baseline p/ o ML.
            'referencia_disponivel': bool(referencia_disponivel),
            'referencia_dias': int(n_dias),
        }

    def reset_diario(self):
        """v15.21 (A26): zera o estado do DIA CORRENTE e PRESERVA o historico.

        ANTES limpava `_historico` — e o scorer chamava reset_diario() na 1a
        linha de cada novo dia (apos o update() ja ter arquivado o dia
        anterior), entao em live a referencia entre dias NUNCA acumulava e
        volume_relativo ficava preso no fallback 1.0. O historico entre dias e
        a razao de existir do tracker e so pode ser apagado criando outra
        instancia.
        """
        self._volume_dia = 0.0
        self._volume_por_minuto.clear()
        self._minuto_atual = -1
        self._ultimo_dia = None
        # _historico, _esp_cache e _hist_versao permanecem (baseline causal)
