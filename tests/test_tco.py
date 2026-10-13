"""Testes da fórmula de TCO e da montagem do ranking (sem banco de dados)."""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from scoring.calcular_tco import (
    AMOSTRA_MINIMA_CONFIAVEL,
    PESO_ATRASO,
    PESO_AVARIA,
    calcular_tco,
    montar_ranking,
)


def fornecedor(nome, preco, frete, atraso=0.0, avaria=0.0, pedidos=20):
    """Uma linha agregada, como a consulta ao banco devolve (uma por fornecedor)."""
    return SimpleNamespace(
        fornecedor=nome,
        total_pedidos=pedidos,
        preco_medio=preco,
        frete_por_unidade_medio=frete,
        taxa_atraso=atraso,
        taxa_avaria=avaria,
    )


def media_geral(*linhas):
    total = sum(r.total_pedidos for r in linhas)
    return sum(r.preco_medio * r.total_pedidos for r in linhas) / total


# ---------------------------------------------------------------- a fórmula

def test_sem_atraso_nem_avaria_o_tco_e_preco_mais_frete():
    custo_financeiro, tco = calcular_tco(50.0, 10.0, 0.0, 0.0)

    assert custo_financeiro == pytest.approx(60.0)
    assert tco == pytest.approx(60.0)


def test_atraso_e_avaria_aumentam_o_tco_proporcionalmente():
    custo_financeiro, tco = calcular_tco(50.0, 10.0, 0.5, 0.1)

    penalidade = 1 + PESO_ATRASO * 0.5 + PESO_AVARIA * 0.1
    assert custo_financeiro == pytest.approx(60.0)
    assert tco == pytest.approx(60.0 * penalidade)


@pytest.mark.parametrize("atraso, avaria", [(0.0, 0.0), (0.2, 0.1), (0.5, 0.0)])
def test_mais_atraso_nunca_reduz_o_tco(atraso, avaria):
    _, tco_base = calcular_tco(50.0, 10.0, atraso, avaria)
    _, tco_pior = calcular_tco(50.0, 10.0, atraso + 0.1, avaria)

    assert tco_pior > tco_base


@pytest.mark.parametrize("atraso, avaria", [(0.0, 0.0), (0.2, 0.1), (0.5, 0.3)])
def test_mais_avaria_nunca_reduz_o_tco(atraso, avaria):
    _, tco_base = calcular_tco(50.0, 10.0, atraso, avaria)
    _, tco_pior = calcular_tco(50.0, 10.0, atraso, avaria + 0.1)

    assert tco_pior > tco_base


# ------------------------------------------------------------------ o ranking

def test_ranking_vem_ordenado_do_menor_para_o_maior_tco_e_corta_no_top_n():
    linhas = [
        fornecedor("Caro", 80.0, 25.0, 0.5, 0.2),
        fornecedor("Barato", 45.0, 6.0, 0.4, 0.1),
        fornecedor("Medio", 60.0, 22.0, 0.2, 0.2),
        fornecedor("Intermediario", 47.0, 19.0, 0.4, 0.1),
    ]

    ranking = montar_ranking(linhas, media_geral(*linhas), top_n=3)

    tcos = [item["tco_estimado"] for item in ranking]
    assert len(ranking) == 3
    assert tcos == sorted(tcos)
    assert ranking[0]["fornecedor"] == "Barato"
    assert "Caro" not in [item["fornecedor"] for item in ranking]


def test_frete_por_unidade_baixo_compensa_preco_parecido():
    """Quem compra em lote grande paga mais frete por pedido, mas menos por unidade."""
    linhas = [
        fornecedor("Lote grande", 45.25, 5.57, 0.47, 0.09),
        fornecedor("Lote pequeno", 47.05, 19.02, 0.47, 0.09),
    ]

    ranking = montar_ranking(linhas, media_geral(*linhas))

    assert ranking[0]["fornecedor"] == "Lote grande"


def test_top_n_maior_que_o_numero_de_fornecedores_devolve_todos():
    linhas = [fornecedor("A", 10.0, 1.0), fornecedor("B", 12.0, 1.0)]

    assert len(montar_ranking(linhas, media_geral(*linhas), top_n=3)) == 2


def test_com_um_unico_fornecedor_o_ranking_tem_um_item():
    linhas = [fornecedor("Unico", 10.0, 1.0)]

    assert len(montar_ranking(linhas, 10.0)) == 1


def test_sem_tendencia_o_preco_ajustado_e_o_historico():
    linhas = [fornecedor("A", 40.0, 5.0, pedidos=30), fornecedor("B", 60.0, 5.0, pedidos=10)]

    ranking = montar_ranking(linhas, media_geral(*linhas))

    for item in ranking:
        assert item["preco_medio_ajustado"] == pytest.approx(item["preco_medio_historico"], abs=0.01)


def test_tendencia_de_alta_sobe_o_preco_de_todos_na_mesma_proporcao():
    linhas = [fornecedor("A", 40.0, 5.0, pedidos=30), fornecedor("B", 60.0, 5.0, pedidos=10)]

    ranking = montar_ranking(linhas, 1.5 * media_geral(*linhas))

    por_nome = {item["fornecedor"]: item for item in ranking}
    for item in ranking:
        assert item["preco_medio_ajustado"] == pytest.approx(1.5 * item["preco_medio_historico"], rel=1e-3)
    # a diferença relativa entre os fornecedores é preservada
    razao_historica = por_nome["B"]["preco_medio_historico"] / por_nome["A"]["preco_medio_historico"]
    razao_ajustada = por_nome["B"]["preco_medio_ajustado"] / por_nome["A"]["preco_medio_ajustado"]
    assert razao_ajustada == pytest.approx(razao_historica, rel=1e-3)


@pytest.mark.parametrize("pedidos, esperado", [
    (AMOSTRA_MINIMA_CONFIAVEL - 1, False),
    (AMOSTRA_MINIMA_CONFIAVEL, True),
    (200, True),
])
def test_sinaliza_amostra_pequena_no_limite(pedidos, esperado):
    linhas = [fornecedor("A", 10.0, 1.0, pedidos=pedidos)]

    ranking = montar_ranking(linhas, 10.0)

    assert ranking[0]["amostra_confiavel"] is esperado


def test_aceita_decimal_vindo_do_banco_e_devolve_float():
    """O PostgreSQL devolve médias como Decimal; misturar com float já gerou TypeError."""
    linhas = [
        fornecedor("A", Decimal("45.25"), Decimal("5.57"), Decimal("0.476"), Decimal("0.095"), pedidos=21),
    ]

    ranking = montar_ranking(linhas, 45.25)

    item = ranking[0]
    for campo in ("preco_medio_historico", "preco_medio_ajustado", "frete_por_unidade_medio",
                  "taxa_atraso", "taxa_avaria", "custo_financeiro", "tco_estimado"):
        assert type(item[campo]) is float, campo


def test_taxas_saem_em_percentual():
    linhas = [fornecedor("A", 10.0, 1.0, atraso=0.5, avaria=0.25)]

    item = montar_ranking(linhas, 10.0)[0]

    assert item["taxa_atraso"] == 50.0
    assert item["taxa_avaria"] == 25.0


def test_campos_do_ranking_batem_com_o_que_a_interface_le():
    """A página (templates/index.html) lê exatamente estes campos."""
    item = montar_ranking([fornecedor("A", 10.0, 1.0)], 10.0)[0]

    assert set(item) == {
        "fornecedor", "total_pedidos", "preco_medio_historico", "preco_medio_ajustado",
        "frete_por_unidade_medio", "taxa_atraso", "taxa_avaria", "custo_financeiro",
        "tco_estimado", "amostra_confiavel",
    }