# -*- coding: utf-8 -*-
"""
adapters/dashboard/state.py — Estado compartilhado do dashboard.

Centraliza o acesso aos dados de live stats e filas do motor,
fornecendo uma API limpa para o DashboardAPI e handlers.
"""

import datetime


class DashboardState:
    """Estado compartilhado entre o motor (escritoras) e o dashboard (HTTP).

    Mantém referências para:
      - filas_book / filas_tt: filas multiprocessing do motor
      - live_stats: array compartilhado de contadores
      - ativos_config: lista de ativos monitorados
    """

    # Ordem dos campos LIVE_FIELDS (deve bater com adapters/rtd_writer.py)
    LIVE_FIELDS = [
        "book_capturados", "book_gravados",
        "tt_detectados", "tt_gravados",
        "drops", "falhas_gravacao",
    ]

    def __init__(self, filas_book, filas_tt, live_stats, base_pasta, ativos_config):
        self.filas_book = filas_book
        self.filas_tt = filas_tt
        self.live_stats = live_stats
        self.base_pasta = base_pasta
        self.ativos_config = ativos_config

    def _live_get(self, a_idx, campo):
        """Lê um contador de live stats para um ativo específico."""
        try:
            idx = self.LIVE_FIELDS.index(campo)
            return int(self.live_stats[a_idx * len(self.LIVE_FIELDS) + idx])
        except Exception:
            return 0

    def payload(self):
        """Retorna payload completo do dashboard (listagem de ativos + totais)."""
        ativos = []
        total = {k: 0 for k in self.LIVE_FIELDS}

        for i, ativo in enumerate(self.ativos_config):
            stats = {campo: self._live_get(i, campo) for campo in self.LIVE_FIELDS}
            integridade = (stats["tt_gravados"] / stats["tt_detectados"] * 100.0) if stats["tt_detectados"] else 100.0

            for k, v in stats.items():
                total[k] += v

            ativos.append({
                'simbolo': ativo['simbolo'],
                **stats,
                'integridade': round(integridade, 4),
            })

        return {
            'timestamp': datetime.datetime.now().strftime('%H:%M:%S'),
            'base': self.base_pasta,
            'ativos': ativos,
            'total': total,
        }
