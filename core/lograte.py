# -*- coding: utf-8 -*-
"""
core/lograte.py — Console limpo (v15.36).

Problema
--------
Durante o drain inicial do RTD e com reentregas persistentes da janela
T&T/RLP, certos avisos se repetem milhares de vezes por minuto com
conteudo quase identico (timestamp rejeitado, fora de ordem, BLOQUEADO,
salto temporal...). O cmd fica ilegivel e eventos raros se perdem no spam.

Solucao
-------
Agregador por CHAVE ESTAVEL (ex.: (tipo, ativo, motivo)):
  - a 1a ocorrencia loga imediatamente, com o detalhe real;
  - repeticoes dentro da janela sao contadas silenciosamente;
  - a cada virada de janela sai UMA linha-resumo com o total suprimido da
   quela janela e o ultimo detalhe (com a hora do ultimo evento).

Resultado: condicao persistente (ex.: 900 bloqueios/5s de CONFIANCA_BAIXA)
= no maximo 1 linha por janela configurada (default 60s) — nao 1 linha a
cada 5s. A 1a ocorrencia de cada condicao loga SEMPRE na hora (motivos
criticos nunca sofrem atraso). Thread-safe (usada pela thread do
RTD/adapter e pela thread do App/risk engine). Texto ASCII-only (console
Windows).
"""

import logging
import threading
import time

__all__ = ["LogRateLimit"]


class LogRateLimit:
    """Agrega avisos repetidos por chave estavel.

    Uso:
        rl = LogRateLimit(janela_s=5.0, logger=log)
        ...
        rl.aviso(("ts_rej", sym, motivo),
                 f"[RTD] {sym}: timestamp rejeitado: {motivo}",
                 f"(DAT={dat_str})")

    Saida com janela de 5s e 300 repeticoes:
        10:00:01 [WARNING] [RTD] DOLV26: timestamp rejeitado: timestamp_passado (DAT=09:53:00.858)
        ... (299 linhas suprimidas)
        10:00:06 [WARNING] [RTD] DOLV26: timestamp rejeitado: timestamp_passado - 299 ocorrencias na janela de 5s (ultima DAT=...)
    """

    def __init__(self, janela_s=5.0, nivel=logging.WARNING, logger=None):
        self.janela_s = max(0.5, float(janela_s))
        self.nivel = nivel
        self.log = logger or logging.getLogger("LogRate")
        self._lock = threading.Lock()
        # chave -> {"n": int (suprimidas desde o ultimo log),
        #           "ultimo_log": float monotonic,
        #           "ultimo_detalhe": str}
        self._estado = {}

    def aviso(self, chave, msg, detalhe=""):
        """Registra uma ocorrencia de `msg` (chave estavel).

        Loga a 1a ocorrencia e, na virada de janela (ou a cada FORCA_EMISSAO
        repeticoes), uma linha-resumo com a contagem suprimida. Retorna True
        quando logou (util p/ teste).
        """
        agora = time.monotonic()
        with self._lock:
            st = self._estado.get(chave)
            if st is None:
                self._estado[chave] = {
                    "n": 0, "ultimo_log": agora, "ultimo_detalhe": detalhe}
                self._emit(msg, detalhe, 0)
                return True

            decorrido = (agora - st["ultimo_log"]) >= self.janela_s
            if decorrido and st["n"] > 0:
                # Virada de janela com repeticoes suprimidas: resumo
                st["ultimo_log"] = agora
                n = st["n"]
                st["n"] = 0
                self._emit(msg, detalhe, n)
                return True
            if decorrido:
                # Virada de janela sem repeticao: linha normal
                st["ultimo_log"] = agora
                st["ultimo_detalhe"] = detalhe
                self._emit(msg, detalhe, 0)
                return True

            # Repeticao dentro da janela: conta silenciosamente. Nada e
            # emitido no meio da janela — o resumo sai so na virada.
            st["n"] += 1
            st["ultimo_detalhe"] = detalhe
            return False

    def _emit(self, msg, detalhe, repetidas):
        if repetidas <= 0:
            texto = f"{msg} {detalhe}".rstrip()
        else:
            texto = (
                f"{msg} - {repetidas} ocorrencias na janela de "
                f"{self.janela_s:.0f}s (ultima {detalhe})"
            ).rstrip()
        self.log.log(self.nivel, texto)

    def contagem(self, chave) -> int:
        """Suprimidas desde o ultimo log da chave (p/ testes)."""
        with self._lock:
            st = self._estado.get(chave)
            return st["n"] if st else 0
