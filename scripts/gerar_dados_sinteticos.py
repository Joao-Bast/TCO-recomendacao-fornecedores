"""Gera a base sintética usada para validar o modelo de tendência e o ranking de TCO.

Uso (na raiz do projeto):
    python scripts/gerar_dados_sinteticos.py

Saída:
    dados_sinteticos/dados_sinteticos.xlsx  -> formato da planilha-modelo, pronta para upload
    dados_sinteticos/gabarito.json          -> resultado esperado (tendências e ranking)

Tudo é determinístico: a semente é fixa, então rodar de novo gera exatamente
os mesmos arquivos. 10 produtos x 10 fornecedores x 50 pedidos = 5.000 pedidos.
"""
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from scoring.calcular_tco import calcular_tco  # noqa: E402

SEMENTE = 2026
INICIO = date(2023, 10, 1)
FIM = date(2026, 9, 30)
PEDIDOS_POR_FORNECEDOR = 50
RUIDO_FRETE = 0.08
PASTA_SAIDA = RAIZ / "dados_sinteticos"


# ----------------------------------------------------------------- padrões de preço
# Cada padrão recebe o tempo em anos desde o início e o mês da compra e devolve
# o fator que multiplica o preço base do produto (sem ruído).

def anual(taxa):
    return lambda anos, mes: (1 + taxa) ** anos


def degrau(a_partir_de_anos, fator):
    return lambda anos, mes: fator if anos >= a_partir_de_anos else 1.0


def sazonal(amplitude):
    return lambda anos, mes: 1 + amplitude * math.sin(2 * math.pi * (mes - 1) / 12)


def anual_com_sazonalidade(taxa, amplitude):
    return lambda anos, mes: (1 + taxa) ** anos * (1 + amplitude * math.sin(2 * math.pi * (mes - 1) / 12))


def constante():
    return lambda anos, mes: 1.0


PRODUTOS = [
    {"nome": "Item A", "unidade": "KG", "preco_base": 50.0, "ruido": 0.04,
     "padrao": "Alta contínua de 25% ao ano", "fator": anual(0.25), "tem_tendencia": True},
    {"nome": "Item B", "unidade": "KG", "preco_base": 80.0, "ruido": 0.04,
     "padrao": "Alta contínua de 10% ao ano", "fator": anual(0.10), "tem_tendencia": True},
    {"nome": "Item C", "unidade": "KG", "preco_base": 120.0, "ruido": 0.04,
     "padrao": "Queda contínua de 10% ao ano", "fator": anual(-0.10), "tem_tendencia": True},
    {"nome": "Item D", "unidade": "KG", "preco_base": 35.0, "ruido": 0.04,
     "padrao": "Queda contínua de 20% ao ano", "fator": anual(-0.20), "tem_tendencia": True},
    {"nome": "Item E", "unidade": "KG", "preco_base": 60.0, "ruido": 0.04,
     "padrao": "Estável e salto de +40% nos últimos 6 meses", "fator": degrau(2.5, 1.40), "tem_tendencia": True},
    {"nome": "Item F", "unidade": "LITROS", "preco_base": 45.0, "ruido": 0.04,
     "padrao": "Estável e queda brusca de -30% no último ano", "fator": degrau(2.0, 0.70), "tem_tendencia": True},
    {"nome": "Item G", "unidade": "LITROS", "preco_base": 25.0, "ruido": 0.03,
     "padrao": "Sazonal (±15% ao longo do ano), sem tendência de longo prazo", "fator": sazonal(0.15),
     "tem_tendencia": True},
    {"nome": "Item H", "unidade": "LITROS", "preco_base": 90.0, "ruido": 0.03,
     "padrao": "Alta de 15% ao ano com sazonalidade de ±10%", "fator": anual_com_sazonalidade(0.15, 0.10),
     "tem_tendencia": True},
    {"nome": "Item I", "unidade": "LITROS", "preco_base": 70.0, "ruido": 0.15,
     "padrao": "Alta de 15% ao ano com muito ruído (caso difícil)", "fator": anual(0.15), "tem_tendencia": True},
    {"nome": "Item J", "unidade": "LITROS", "preco_base": 100.0, "ruido": 0.15,
     "padrao": "Só ruído, sem tendência (controle negativo)", "fator": constante(), "tem_tendencia": False},
]

# Perfis fixos, iguais em todos os produtos. multiplicador_preco é relativo ao
# preço de mercado do produto; o frete de cada pedido = fixo + por_unidade x quantidade.
FORNECEDORES = [
    {"nome": "Fornecedor 1", "perfil": "O mais barato, mas atrasa e avaria muito",
     "multiplicador_preco": 0.85, "quantidade": (5, 40), "frete_fixo": 35.0, "frete_por_unidade": 0.40,
     "prazo_prometido": 5, "prob_atraso": 0.60, "prob_avaria": 0.20},
    {"nome": "Fornecedor 2", "perfil": "Caro e pontual",
     "multiplicador_preco": 1.06, "quantidade": (5, 40), "frete_fixo": 35.0, "frete_por_unidade": 0.40,
     "prazo_prometido": 7, "prob_atraso": 0.03, "prob_avaria": 0.01},
    {"nome": "Fornecedor 3", "perfil": "Lote grande: frete alto por pedido, baixo por unidade; confiável",
     "multiplicador_preco": 0.95, "quantidade": (100, 250), "frete_fixo": 70.0, "frete_por_unidade": 0.30,
     "prazo_prometido": 8, "prob_atraso": 0.06, "prob_avaria": 0.02},
    {"nome": "Fornecedor 4", "perfil": "Preço bom, frete caro",
     "multiplicador_preco": 0.94, "quantidade": (5, 40), "frete_fixo": 90.0, "frete_por_unidade": 1.20,
     "prazo_prometido": 6, "prob_atraso": 0.10, "prob_avaria": 0.04},
    {"nome": "Fornecedor 5", "perfil": "Preço bom, avaria alta",
     "multiplicador_preco": 0.95, "quantidade": (5, 40), "frete_fixo": 35.0, "frete_por_unidade": 0.40,
     "prazo_prometido": 6, "prob_atraso": 0.12, "prob_avaria": 0.25},
    {"nome": "Fornecedor 6", "perfil": "Preço médio, atrasa bastante",
     "multiplicador_preco": 1.00, "quantidade": (5, 40), "frete_fixo": 35.0, "frete_por_unidade": 0.40,
     "prazo_prometido": 5, "prob_atraso": 0.35, "prob_avaria": 0.05},
    {"nome": "Fornecedor 7", "perfil": "Barato, risco moderado",
     "multiplicador_preco": 0.92, "quantidade": (5, 40), "frete_fixo": 35.0, "frete_por_unidade": 0.40,
     "prazo_prometido": 6, "prob_atraso": 0.25, "prob_avaria": 0.10},
    {"nome": "Fornecedor 8", "perfil": "Premium: o mais caro entre os confiáveis",
     "multiplicador_preco": 1.10, "quantidade": (5, 40), "frete_fixo": 35.0, "frete_por_unidade": 0.40,
     "prazo_prometido": 7, "prob_atraso": 0.05, "prob_avaria": 0.02},
    {"nome": "Fornecedor 9", "perfil": "Referência: tudo na média",
     "multiplicador_preco": 1.00, "quantidade": (5, 40), "frete_fixo": 35.0, "frete_por_unidade": 0.40,
     "prazo_prometido": 6, "prob_atraso": 0.15, "prob_avaria": 0.05},
    {"nome": "Fornecedor 10", "perfil": "O pior: caro, atrasa e avaria muito",
     "multiplicador_preco": 1.12, "quantidade": (5, 40), "frete_fixo": 35.0, "frete_por_unidade": 0.40,
     "prazo_prometido": 5, "prob_atraso": 0.50, "prob_avaria": 0.20},
]


def _sim_nao(valor):
    return "SIM" if valor else "NAO"


def _pedido(rng, produto, fornecedor):
    dias_totais = (FIM - INICIO).days
    data_compra = INICIO + timedelta(days=int(rng.integers(0, dias_totais + 1)))
    anos = (data_compra - INICIO).days / 365.25

    qtd_min, qtd_max = fornecedor["quantidade"]
    quantidade = int(rng.integers(qtd_min, qtd_max + 1))

    fator = produto["fator"](anos, data_compra.month)
    preco = produto["preco_base"] * fator * fornecedor["multiplicador_preco"] * (1 + rng.normal(0, produto["ruido"]))
    frete = (fornecedor["frete_fixo"] + fornecedor["frete_por_unidade"] * quantidade) * (1 + rng.normal(0, RUIDO_FRETE))

    prazo = fornecedor["prazo_prometido"]
    atrasou = bool(rng.random() < fornecedor["prob_atraso"])
    if atrasou:
        tempo_real = prazo + int(rng.integers(1, 7))
    else:
        tempo_real = max(1, prazo - int(rng.integers(0, 3)))
    avariou = bool(rng.random() < fornecedor["prob_avaria"])

    return {
        "Nome_Fornecedor": fornecedor["nome"],
        "Produto": produto["nome"],
        "Unidade_Medida": produto["unidade"],
        "Data_Compra": data_compra,
        "Quantidade": quantidade,
        "Preco_Unitario": round(max(preco, 0.01), 2),
        "Custo_Frete": round(max(frete, 0.0), 2),
        "Prazo_Entrega_Prometido_Dias_Uteis": prazo,
        "Tempo_Entrega_Real_Dias_Uteis": tempo_real,
        "Pedido_Sofreu_Atraso": _sim_nao(atrasou),
        "Pedido_Sofreu_Avaria": _sim_nao(avariou),
    }


def _frete_por_unidade_esperado(fornecedor):
    qtd_min, qtd_max = fornecedor["quantidade"]
    media_inverso_qtd = float(np.mean([1 / q for q in range(qtd_min, qtd_max + 1)]))
    return fornecedor["frete_por_unidade"] + fornecedor["frete_fixo"] * media_inverso_qtd


def _gabarito_produto(produto, pedidos_do_produto):
    datas = pd.to_datetime(pedidos_do_produto["Data_Compra"])
    anos = [(d.date() - INICIO).days / 365.25 for d in datas]
    fatores = [produto["fator"](a, d.month) for a, d in zip(anos, datas)]
    ultima = datas.max()
    fator_atual = produto["fator"]((ultima.date() - INICIO).days / 365.25, ultima.month)
    variacao = (fator_atual / float(np.mean(fatores)) - 1) * 100

    # Ranking esperado: os mesmos critérios do sistema aplicados aos parâmetros
    # verdadeiros (sem ruído) de cada fornecedor, no patamar de preço atual.
    esperados = []
    for fornecedor in FORNECEDORES:
        preco_atual = produto["preco_base"] * fator_atual * fornecedor["multiplicador_preco"]
        _, tco = calcular_tco(
            preco_atual, _frete_por_unidade_esperado(fornecedor),
            fornecedor["prob_atraso"], fornecedor["prob_avaria"],
        )
        esperados.append((fornecedor["nome"], round(tco, 2)))
    esperados.sort(key=lambda par: par[1])

    return {
        "nome": produto["nome"],
        "unidade": produto["unidade"],
        "padrao": produto["padrao"],
        "tem_tendencia": produto["tem_tendencia"],
        "variacao_esperada_percentual": round(variacao, 1),
        "data_referencia": ultima.date().isoformat(),
        "ranking_esperado": [{"posicao": i, "fornecedor": nome, "tco_esperado": tco}
                             for i, (nome, tco) in enumerate(esperados, start=1)],
    }


def gerar(semente=SEMENTE):
    """Devolve (DataFrame no formato da planilha-modelo, gabarito)."""
    rng = np.random.default_rng(semente)
    linhas = [
        _pedido(rng, produto, fornecedor)
        for produto in PRODUTOS
        for fornecedor in FORNECEDORES
        for _ in range(PEDIDOS_POR_FORNECEDOR)
    ]
    df = (
        pd.DataFrame(linhas)
        .sort_values(["Data_Compra", "Produto", "Nome_Fornecedor"], kind="stable")
        .reset_index(drop=True)
    )
    df.insert(0, "ID_Pedido", range(1, len(df) + 1))

    gabarito = {
        "semente": semente,
        "periodo": {"inicio": INICIO.isoformat(), "fim": FIM.isoformat()},
        "pedidos_por_fornecedor_por_produto": PEDIDOS_POR_FORNECEDOR,
        "observacao": (
            "variacao_esperada_percentual usa a mesma medida do painel: preço na data mais recente "
            "dividido pela média de todo o histórico, menos 1. Não é a taxa anual do padrão."
        ),
        "fornecedores": [
            {chave: valor for chave, valor in f.items() if chave != "quantidade"}
            | {"quantidade_min": f["quantidade"][0], "quantidade_max": f["quantidade"][1]}
            for f in FORNECEDORES
        ],
        "produtos": [_gabarito_produto(p, df[df["Produto"] == p["nome"]]) for p in PRODUTOS],
    }
    return df, gabarito


def main():
    df, gabarito = gerar()
    PASTA_SAIDA.mkdir(exist_ok=True)
    caminho_planilha = PASTA_SAIDA / "dados_sinteticos.xlsx"
    caminho_gabarito = PASTA_SAIDA / "gabarito.json"

    df.to_excel(caminho_planilha, index=False, sheet_name="Pedidos")
    caminho_gabarito.write_text(json.dumps(gabarito, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{len(df)} pedidos gerados -> {caminho_planilha}")
    print(f"Gabarito -> {caminho_gabarito}\n")
    for produto in gabarito["produtos"]:
        top3 = ", ".join(item["fornecedor"] for item in produto["ranking_esperado"][:3])
        print(f"{produto['nome']}: variação esperada {produto['variacao_esperada_percentual']:+.1f}% "
              f"| Top 3 esperado: {top3}")


if __name__ == "__main__":
    main()