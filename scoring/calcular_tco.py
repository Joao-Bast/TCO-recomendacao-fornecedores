# scoring/calcular_tco.py
from sqlalchemy import text
from config.database import get_engine
from modelo.previsao_preco import analisar_tendencia

# Pesos de partida — ajustáveis conforme necessidade do negócio
PESO_ATRASO = 0.30
PESO_AVARIA = 0.20

# Amostra mínima para considerar o score "confiável" (apenas para sinalização, não exclui o fornecedor)
AMOSTRA_MINIMA_CONFIAVEL = 10


def calcular_tco(preco_medio_ajustado, frete_por_unidade_medio, taxa_atraso, taxa_avaria):
    """Custo financeiro direto (preço + frete por unidade) acrescido de uma
    penalidade proporcional às taxas de atraso e avaria (valores entre 0 e 1).

    Retorna (custo_financeiro, tco)."""
    custo_financeiro = preco_medio_ajustado + frete_por_unidade_medio
    penalidade = 1 + PESO_ATRASO * taxa_atraso + PESO_AVARIA * taxa_avaria
    return custo_financeiro, custo_financeiro * penalidade


def montar_ranking(linhas_agregadas, preco_previsto_produto, top_n=3):
    """Calcula o TCO de cada fornecedor e devolve os `top_n` menores.

    `linhas_agregadas`: objetos com os atributos fornecedor, total_pedidos,
    preco_medio, frete_por_unidade_medio, taxa_atraso e taxa_avaria (é o que
    a consulta ao banco devolve, uma linha por fornecedor).

    O preço previsto do produto vira um fator de tendência (previsto ÷ média
    geral histórica), aplicado igualmente ao preço de todos os fornecedores —
    isso preserva a diferença relativa entre eles."""
    total_pedidos = sum(r.total_pedidos for r in linhas_agregadas)
    preco_medio_geral = sum(float(r.preco_medio) * r.total_pedidos for r in linhas_agregadas) / total_pedidos
    fator_tendencia = preco_previsto_produto / preco_medio_geral

    linhas = []
    for row in linhas_agregadas:
        preco_medio_historico = float(row.preco_medio)
        preco_medio_ajustado = preco_medio_historico * fator_tendencia

        frete_por_unidade_medio = float(row.frete_por_unidade_medio)
        taxa_atraso = float(row.taxa_atraso)
        taxa_avaria = float(row.taxa_avaria)

        custo_financeiro, tco_final = calcular_tco(
            preco_medio_ajustado, frete_por_unidade_medio, taxa_atraso, taxa_avaria
        )

        linhas.append({
            "fornecedor": row.fornecedor,
            "total_pedidos": row.total_pedidos,
            "preco_medio_historico": round(preco_medio_historico, 2),
            "preco_medio_ajustado": round(preco_medio_ajustado, 2),
            "frete_por_unidade_medio": round(frete_por_unidade_medio, 2),
            "taxa_atraso": round(taxa_atraso * 100, 1),
            "taxa_avaria": round(taxa_avaria * 100, 1),
            "custo_financeiro": round(custo_financeiro, 2),
            "tco_estimado": round(tco_final, 2),
            "amostra_confiavel": row.total_pedidos >= AMOSTRA_MINIMA_CONFIAVEL,
        })

    ranking = sorted(linhas, key=lambda x: x["tco_estimado"])
    return ranking[:top_n]


def calcular_ranking(nome_produto, sessao_id, top_n=3):
    engine = get_engine()

    with engine.connect() as conn:
        resultado = conn.execute(
            text("""
                SELECT
                    f.nome AS fornecedor,
                    COUNT(*) AS total_pedidos,
                    AVG(p.preco_unitario) AS preco_medio,
                    AVG(p.custo_frete / p.quantidade) AS frete_por_unidade_medio,
                    AVG(CASE WHEN p.sofreu_atraso THEN 1.0 ELSE 0.0 END) AS taxa_atraso,
                    AVG(CASE WHEN p.sofreu_avaria THEN 1.0 ELSE 0.0 END) AS taxa_avaria
                FROM pedido p
                JOIN fornecedor f ON f.id = p.fornecedor_id
                JOIN produto pr ON pr.id = p.produto_id
                WHERE pr.nome = :nome_produto AND p.sessao_id = :sessao_id
                GROUP BY f.nome
            """),
            {"nome_produto": nome_produto, "sessao_id": sessao_id},
        ).fetchall()

    if not resultado:
        raise ValueError(f"Nenhum pedido encontrado para o produto '{nome_produto}' nesta sessão")

    # O mesmo preço de referência exibido no painel de tendência ajusta o ranking.
    tendencia = analisar_tendencia(nome_produto, sessao_id)

    ranking = montar_ranking(resultado, tendencia["preco_referencia"], top_n)
    return ranking, tendencia


def exibir_ranking(nome_produto, sessao_id):
    ranking, tendencia = calcular_ranking(nome_produto, sessao_id)

    if tendencia["confiavel"]:
        origem = f"modelo de ML ({tendencia['modelo']}, R²={tendencia['r2']:.3f})"
    else:
        origem = "média histórica"
    print(f"\nTop {len(ranking)} fornecedores para '{nome_produto}':")
    print(f"(tendência do preço: {tendencia['variacao_percentual']:+.1f}% — baseado em: {origem})\n")

    for posicao, item in enumerate(ranking, start=1):
        aviso = "" if item["amostra_confiavel"] else "  ⚠ amostra pequena"
        print(
            f"{posicao}º - {item['fornecedor']}{aviso}\n"
            f"    TCO estimado: R$ {item['tco_estimado']}\n"
            f"    Preço histórico: R$ {item['preco_medio_historico']} | Preço ajustado por tendência: R$ {item['preco_medio_ajustado']}\n"
            f"    Frete médio por unidade: R$ {item['frete_por_unidade_medio']}\n"
            f"    Taxa de atraso: {item['taxa_atraso']}% | Taxa de avaria: {item['taxa_avaria']}%\n"
            f"    Baseado em {item['total_pedidos']} pedidos\n"
        )


if __name__ == "__main__":
    SESSAO_TESTE = "00000000-0000-0000-0000-000000000001"
    exibir_ranking("ACIDO SULFURICO PA", SESSAO_TESTE)