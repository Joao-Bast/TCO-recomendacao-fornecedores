# modelo/previsao_preco.py
import pandas as pd
from sqlalchemy import text
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import KFold, cross_validate
from config.database import get_engine

R2_MINIMO_PARA_CONFIAR = 0.0
N_SPLITS = 5
MINIMO_PEDIDOS_PARA_TREINAR = 70  # abaixo disso, nem tenta treinar — vai direto pra média histórica


def listar_produtos_cadastrados(sessao_id):
    """Lista só os produtos que têm pelo menos um pedido dentro dessa sessão."""
    engine = get_engine()
    with engine.connect() as conn:
        resultado = conn.execute(
            text("""
                SELECT DISTINCT pr.nome
                FROM produto pr
                JOIN pedido p ON p.produto_id = pr.id
                WHERE p.sessao_id = :sessao_id
                ORDER BY pr.nome
            """),
            {"sessao_id": sessao_id},
        ).fetchall()
    return [row[0] for row in resultado]


def carregar_dados_produto(nome_produto, sessao_id):
    engine = get_engine()
    with engine.connect() as conn:
        resultado = conn.execute(
            text("""
                SELECT p.data_compra, p.quantidade, p.preco_unitario
                FROM pedido p
                JOIN produto pr ON pr.id = p.produto_id
                WHERE pr.nome = :nome_produto AND p.sessao_id = :sessao_id
                ORDER BY p.data_compra, p.id
            """),
            {"nome_produto": nome_produto, "sessao_id": sessao_id},
        ).fetchall()

    df = pd.DataFrame(resultado, columns=["data_compra", "quantidade", "preco_unitario"])
    df["data_compra"] = pd.to_datetime(df["data_compra"])
    df["quantidade"] = df["quantidade"].astype(float)
    df["preco_unitario"] = df["preco_unitario"].astype(float)
    return df


def preparar_features(df):
    data_inicial = df["data_compra"].min()
    df = df.copy()
    df["dias_desde_inicio"] = (df["data_compra"] - data_inicial).dt.days
    df["mes"] = df["data_compra"].dt.month
    X = df[["dias_desde_inicio", "quantidade", "mes"]]
    y = df["preco_unitario"]
    return X, y, data_inicial


def avaliar_modelo_cv(modelo, X, y, n_splits=N_SPLITS):
    kf = KFold(n_splits=n_splits, shuffle=True, random_state=42)
    resultado = cross_validate(
        modelo, X, y, cv=kf,
        scoring={
            "mae": "neg_mean_absolute_error",
            "rmse": "neg_root_mean_squared_error",
            "r2": "r2",
        },
    )
    return {
        "mae_medio": -resultado["test_mae"].mean(),
        "rmse_medio": -resultado["test_rmse"].mean(),
        "r2_medio": resultado["test_r2"].mean(),
        "r2_desvio": resultado["test_r2"].std(),
    }


def treinar_e_avaliar(nome_produto, sessao_id, n_splits=N_SPLITS):
    """Compara Regressão Linear, Random Forest e Gradient Boosting via k-fold.
    Se houver menos que MINIMO_PEDIDOS_PARA_TREINAR registros, nem tenta treinar."""
    df = carregar_dados_produto(nome_produto, sessao_id)

    if len(df) < MINIMO_PEDIDOS_PARA_TREINAR:
        print(
            f"'{nome_produto}': apenas {len(df)} pedidos (mínimo exigido: "
            f"{MINIMO_PEDIDOS_PARA_TREINAR}) — usando média histórica, sem treinar modelo."
        )
        return None, None, df, None, None

    X, y, data_inicial = preparar_features(df)

    candidatos = {
        "Regressão Linear": LinearRegression(),
        "Random Forest": RandomForestRegressor(
            n_estimators=100, max_depth=4, min_samples_leaf=3, random_state=42
        ),
        "Gradient Boosting": GradientBoostingRegressor(
            n_estimators=100, max_depth=3, min_samples_leaf=3,
            learning_rate=0.05, random_state=42
        ),
    }

    print(f"\nValidação cruzada ({n_splits}-fold) para '{nome_produto}' ({len(df)} pedidos):")
    resultados = {}
    for nome, modelo in candidatos.items():
        resultados[nome] = avaliar_modelo_cv(modelo, X, y, n_splits)
        r = resultados[nome]
        print(
            f"  {nome:18s} | MAE: R$ {r['mae_medio']:.2f} | RMSE: R$ {r['rmse_medio']:.2f} "
            f"| R²: {r['r2_medio']:.3f} (±{r['r2_desvio']:.3f})"
        )

    melhor_nome = max(resultados, key=lambda n: resultados[n]["r2_medio"])
    melhor = resultados[melhor_nome]
    print(f"  → Selecionado: {melhor_nome} (maior R² médio)")

    modelo_final = candidatos[melhor_nome]
    modelo_final.fit(X, y)

    return modelo_final, data_inicial, df, melhor["r2_medio"], melhor_nome


def prever_preco_atual(nome_produto, sessao_id):
    """Retorna (preco_previsto, r2, confiavel) — sempre em tipos nativos do
    Python (float/bool/None), nunca tipos do NumPy, para serem serializáveis
    em JSON sem problema."""
    modelo, data_inicial, df, r2, nome_modelo = treinar_e_avaliar(nome_produto, sessao_id)

    if modelo is None:
        previsao = df["preco_unitario"].mean()
        print(f"Usando média histórica para '{nome_produto}': R$ {previsao:.2f}")
        return float(previsao), None, False

    quantidade_referencia = df["quantidade"].median()
    dias_ate_hoje = (df["data_compra"].max() - data_inicial).days
    mes_atual = df["data_compra"].max().month

    entrada = pd.DataFrame(
        [[dias_ate_hoje, quantidade_referencia, mes_atual]],
        columns=["dias_desde_inicio", "quantidade", "mes"],
    )

    confiavel = bool(r2 > R2_MINIMO_PARA_CONFIAR)

    if confiavel:
        previsao = modelo.predict(entrada)[0]
        print(f"Preço previsto ({nome_modelo}, R²={r2:.3f}) para '{nome_produto}': R$ {previsao:.2f}")
    else:
        previsao = df["preco_unitario"].mean()
        print(f"Modelo não confiável (R²={r2:.3f}) — usando média histórica para '{nome_produto}': R$ {previsao:.2f}")

    return float(previsao), float(r2), confiavel


if __name__ == "__main__":
    SESSAO_TESTE = "00000000-0000-0000-0000-000000000001"
    for produto in listar_produtos_cadastrados(SESSAO_TESTE):
        prever_preco_atual(produto, SESSAO_TESTE)
        print("-" * 50)