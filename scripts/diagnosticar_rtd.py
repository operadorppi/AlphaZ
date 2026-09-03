# -*- coding: utf-8 -*-
"""
diagnosticar_rtd.py — Le o que cada janela do ProfitChart contem.
Conecta ao RTD e mostra o ativo de cada T&T e BOOK (0-11).

Uso: python scripts/diagnosticar_rtd.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import comtypes.client
from adapters.rtd_connection import _connect, _normalizar_simbolo

def main():
    print("=" * 60)
    print("  DIAGNOSTICO RTD — Mapeamento de Janelas")
    print("=" * 60)
    print()

    try:
        comtypes.CoInitialize()
    except Exception:
        pass

    try:
        # Conectar ao servidor RTD
        clsid = '{E5F13E62-F45F-4ABE-A885-74748B5F5780}'
        srv = comtypes.client.CreateObject(clsid, comtypes.CLSCTX_ALL)
        print("[OK] Servidor RTD criado")

        # Pump para estabilizar
        import time
        deadline = time.perf_counter() + 3.0
        while time.perf_counter() < deadline:
            comtypes.client.PumpEvents(0.1)

        print()
        print(f"{'Janela':<10} {'Tipo':<8} {'Ativo':<15} {'Normalizado':<15}")
        print("-" * 50)

        ativos_encontrados = {}

        for i in range(12):
            for kind, prefix in [("BOOK", "BOOK"), ("T&T", "T&T")]:
                try:
                    tid, val = _connect(srv, [f"{prefix}{i}", "INFO", "ATV"])
                    v = _normalizar_simbolo(val)
                    if v:
                        status = "OK" if v.startswith(("WIN", "WDO", "IND", "DOL")) else "?"
                        print(f"{prefix}{i:<6} {kind:<8} {str(val):<15} {v:<15} {status}")
                        if v.startswith(("WIN", "WDO", "IND", "DOL")):
                            if v not in ativos_encontrados:
                                ativos_encontrados[v] = {'tt': [], 'book': []}
                            if kind == "T&T":
                                ativos_encontrados[v]['tt'].append(i)
                            else:
                                ativos_encontrados[v]['book'].append(i)
                    else:
                        print(f"{prefix}{i:<6} {kind:<8} {str(val):<15} {'(vazio)':<15}")
                except Exception as e:
                    pass  # Janela nao existe

            comtypes.client.PumpEvents(0.01)

        print()
        print("=" * 60)
        print("  RESUMO POR ATIVO")
        print("=" * 60)
        for ativo, info in sorted(ativos_encontrados.items()):
            tt = info['tt']
            book = info['book']
            tt_str = ", ".join(f"T&T{x}" for x in tt) if tt else "NENHUM"
            book_str = ", ".join(f"BOOK{x}" for x in book) if book else "NENHUM"
            match = "OK" if tt and book else "FALTANDO"
            print(f"  {ativo}:")
            print(f"    T&T:  {tt_str}")
            print(f"    BOOK: {book_str}")
            print(f"    Status: {match}")
            print()

        # Desconectar
        try:
            srv.ServerTerminate()
        except:
            pass

    except Exception as e:
        print(f"[ERRO] {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            comtypes.CoUninitialize()
        except:
            pass

if __name__ == '__main__':
    main()
