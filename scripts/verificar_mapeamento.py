# -*- coding: utf-8 -*-
"""
verificar_mapeamento.py — Verifica se o motor esta mapeando corretamente.
Le os precos atuais e compara com o esperado.

Uso: python scripts/verificar_mapeamento.py
"""
import json
import urllib.request

def main():
    print("=" * 60)
    print("  VERIFICACAO DE MAPEAMENTO RTD")
    print("=" * 60)
    print()

    try:
        req = urllib.request.urlopen('http://127.0.0.1:5001/api/all')
        data = json.loads(req.read())
    except Exception as e:
        print(f"[ERRO] Motor nao esta rodando ou API inacessivel: {e}")
        return

    # Precos esperados por ativo (faixas)
    ESPERADOS = {
        'WINV26': {'min': 100000, 'max': 300000, 'desc': 'Mini Indice'},
        'WDOV26': {'min': 3000, 'max': 10000, 'desc': 'Mini Dolar'},
        'INDV26': {'min': 100000, 'max': 300000, 'desc': 'Indice Cheio'},
        'DOLV26': {'min': 3000, 'max': 10000, 'desc': 'Dolar Cheio'},
    }

    print(f"{'Ativo':<12} {'Preco':<12} {'Esperado':<20} {'Status':<10}")
    print("-" * 55)

    erros = 0
    for ativo in ['WINV26', 'WDOV26', 'INDV26', 'DOLV26']:
        f = data.get('features', {}).get(ativo, {})
        preco = f.get('preco_fim', 'N/A')
        esp = ESPERADOS[ativo]

        try:
            preco_num = float(preco) if preco != 'N/A' else 0
            if esp['min'] <= preco_num <= esp['max']:
                status = "OK"
            else:
                status = "ERRADO"
                erros += 1
        except:
            preco_num = 0
            status = "N/A"

        print(f"{ativo:<12} {str(preco):<12} {esp['min']}-{esp['max']:<10} {status}")

    print()
    if erros > 0:
        print(f"[!] {erros} ativo(s) com preco ERRADO")
        print()
        print("PROBLEMA: O ProfitChart tem janelas invertidas.")
        print("SOLUCAO: Reconfigurar as janelas T&T no ProfitChart:")
        print("  T&T0 = INDV26 (~180.000)")
        print("  T&T1 = WINV26 (~180.000)")
        print("  T&T2 = WDOV26 (~5.200)")
        print("  T&T3 = DOLV26 (~5.200)")
    else:
        print("[OK] Todos os ativos com preco correto!")

    # Mostrar tt_map e book_map
    print()
    print("=== LOG DO MOTOR (ultimas linhas) ===")
    try:
        with open('motor_stdout.log', 'r') as f:
            lines = f.readlines()
            for line in reversed(lines):
                if 'tt_map' in line or 'book_map' in line:
                    print(line.strip())
                    break
    except:
        print("(nao foi possivel ler o log)")

if __name__ == '__main__':
    main()
