# memory_tools.py
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from memory_mongodb import recuperar_historico


@tool
def buscar_historico(busca: str, config: RunnableConfig) -> str:
    """Consulta conversas ANTERIORES do usuário (sessões já encerradas).

    Use SOMENTE quando a resposta depende de algo dito numa conversa passada
    — preferências, decisões ou planos que o usuário mencionou antes.
    NÃO use para dados que estão no banco (gastos, saldos, eventos): para isso
    já existem as tools de consulta específicas como query_transactions, total_balance, daily_balance.

    Args:
        busca: assunto a procurar nos resumos das conversas anteriores.
    """
    session_id = config["configurable"]["thread_id"]   # injetado pelo main.py
    historico  = recuperar_historico(session_id, busca=busca, limite=3)

    if not historico:
        return "Nenhuma conversa anterior relevante encontrada."

    return "\n\n".join(
        f"[{h['iniciada_em']:%d/%m/%Y}] {h['resumo']}" for h in historico
    )


TOOLS_MEMORIA = [buscar_historico]