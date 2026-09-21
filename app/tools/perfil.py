"""
Tool de leitura do perfil financeiro do usuário.

Uma tool só, não duas: aconselhar exige ver o dado estruturado (renda, gasto,
horizonte, perfil de investidor) e as restrições relevantes AO MESMO TEMPO.
Separar em duas tools arriscaria o modelo chamar só uma e aconselhar com
metade do dado.

O user_id vem do config (contexto da requisição), nunca é argumento que o
modelo preenche — mesmo padrão de app/tools/mongo.py::buscar_historico.
Esta tool só lê; salvar/alterar perfil continua exclusivo da tela Perfil.
"""

from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig

from app.perfil import buscar_perfil_estruturado, buscar_restricoes_relevantes

@tool
def consultar_perfil_financeiro(pergunta: str, config: RunnableConfig) -> dict:
    """Consulta o perfil financeiro cadastrado do usuário (tela Perfil).

    Use ANTES de qualquer conselho que dependa da situação financeira do
    usuário — quanto guardar por mês, se um investimento cabe no horizonte
    ou no perfil de risco, se uma restrição pessoal impede algo.

    Args:
        pergunta: o que o usuário quer saber, usado para buscar as
            restrições cadastradas mais relevantes (busca semântica).
    """
    configuravel = (config or {}).get("configurable", {})
    user_id      = configuravel.get("user_id")  

    if not user_id:
        return {"erro": "Não foi possível identificar o usuário para consultar o perfil."}

    perfil = buscar_perfil_estruturado(user_id)
    if not perfil:
        return {
            "cadastrado": False,
            "mensagem": "Nenhum perfil cadastrado para este usuário. "
                        "Oriente-o a preencher a tela Perfil antes de aconselhar.",
        }

    restricoes_relevantes = buscar_restricoes_relevantes(user_id, pergunta)

    return {
        "cadastrado":          True,
        "renda_mensal":        perfil["renda_mensal"],
        "gasto_fixo_mensal":   perfil["gasto_fixo_mensal"],
        "horizonte_meses":     perfil["horizonte_meses"],
        "perfil_investidor":   perfil["perfil_investidor"],
        "restricoes_relevantes": restricoes_relevantes,
    }


TOOLS_PERFIL = [consultar_perfil_financeiro]
