import operator
from typing import Annotated, TypedDict
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.message import add_messages
from langchain_core.runnables import RunnableConfig          # ← novo
from app.guardrail import guardrail_entrada, guardrail_saida, anonimizar_entrada
from app.memory import iniciar_sessao, salvar_mensagem, encerrar_sessao
# from langchain_core.messages import RemoveMessage
from app.agents import router_app, financeiro_app, agenda_app, orquestrador_app, faq_app

# ==============================================================================
# ESTADO
# ==============================================================================
class Estado(TypedDict):
    input:              str                                  # sobrescrito a cada etapa
    session_id:         str                                  # ID da sessão
    agentes_chamados:   Annotated[list[str], operator.add]  # acumula entre nós
    saida_especialista: str                                  # JSON do especialista ativo
    resposta_final:     str                                  # resposta para o usuário
    rota: str
    mapa_pii: dict
    messages:            Annotated[list, add_messages]        # histórico usado pelo roteador

# ==============================================================================
# NÓS
# ==============================================================================
def no_roteador(estado: Estado, config: RunnableConfig) -> dict:
    saida = router_app.invoke({"messages": list(estado["messages"])}, config=config)
    texto = saida["messages"][-1].text

    # Resposta direta (saudação, fora de escopo): já escreve no campo final
    if not texto.strip().startswith("ROUTE="):
        return {
            "agentes_chamados": ["roteador"],
            "resposta_final":   texto,
        }

    # Encaminhamento: extrai a rota da 1ª linha do protocolo (ex.: "ROUTE=financeiro")
    # e sobrescreve input com o protocolo para o especialista
    primeira_linha = texto.strip().splitlines()[0]
    rota = primeira_linha.split("=", 1)[1].strip().lower()

    return {
        "input":            texto,
        "rota":             rota,
        "agentes_chamados": ["roteador"],
    }

def no_orquestrador(estado: Estado) -> dict:
    json_especialista = estado["messages"][-1].text
    saida = orquestrador_app.invoke(
        {"messages": [{"role": "human", "content": json_especialista}]},
        config={"configurable": {"thread_id": estado['session_id']}},
    )
    return {
        "agentes_chamados": [estado["rota"], "orquestrador"],
        "messages":[{"role":"assistant", "content": saida["messages"][-1].text}],
    }

def no_guardrail_entrada(estado: Estado) -> dict:
    mensagem_original = estado["input"]
    texto = mensagem_original
    texto_anon, mapa = anonimizar_entrada(texto)
    resposta = guardrail_entrada(texto_anon)

    salvar_mensagem(
        session_id=estado["session_id"],
        role="usuario",
        content=texto_anon,
    )

    if resposta["bloqueado"]:
        return {
            "rota": "fim",
            "mapa_pii": mapa,
            "resposta_final": resposta["mensagem"],
        }
    return {
        "rota": "roteador",
        "mapa_pii": mapa,
        "input": texto_anon,
        "messages": [{"role": "human", "content": texto_anon}],
    }

def no_guardrail_saida(estado: Estado) -> dict:
    ultima = ""
    for msg in reversed(estado["messages"]):
        if msg.type == "ai" and msg.text:
            ultima = msg.text
            break

    resultado = guardrail_saida(ultima, estado.get("mapa_pii", {}))
    return {
        "messages":[{"role":"assistant", "content":resultado["conteudo"]}],
        "resposta_final":  resultado["conteudo"],
        "agentes_chamados": ["guardrail_saida"],
    }

# ==============================================================================
# FUNÇÃO DE DECISÃO
# ==============================================================================
def decidir_especialista(estado: Estado) -> str:
    if estado["rota"] in ("financeiro", "agenda", "faq"):
        return estado["rota"]
    else:
        return "fim"
    # return estado["rota"] if estado["rota"] in ("financeiro", "agenda", "faq") else "fim"

def decidir_pos_guardrail_entrada(estado: Estado) -> str:
    return "fim" if estado["rota"] == "fim" else "roteador"


# ==============================================================================
# CONSTRUÇÃO DO GRAFO
# ==============================================================================
grafo = StateGraph(Estado)

grafo.add_node("guardrail_entrada", no_guardrail_entrada)
grafo.add_node("roteador",     no_roteador)
grafo.add_node("financeiro",   financeiro_app)
grafo.add_node("agenda",       agenda_app)
grafo.add_node("faq",          faq_app)
grafo.add_node("orquestrador", no_orquestrador)
grafo.add_node("guardrail_saida", no_guardrail_saida)

grafo.set_entry_point("guardrail_entrada")

grafo.add_conditional_edges(
    "guardrail_entrada",
    decidir_pos_guardrail_entrada,
    {
        "roteador": "roteador",
        "fim":        END,
    },
)
grafo.add_conditional_edges(
    "roteador",
    decidir_especialista,
    {
        "financeiro": "financeiro",
        "agenda":     "agenda",
        "faq":        "faq",
        "fim":        END,       # resposta direta: sem especialista nem orquestrador
    },
)

grafo.add_edge("financeiro",   "orquestrador")
grafo.add_edge("agenda",       "orquestrador")
grafo.add_edge("orquestrador", "guardrail_saida")
grafo.add_edge("guardrail_saida", END)
grafo.add_edge("faq",          "guardrail_saida")   # FAQ bypassa o orquestrador

# Memória centralizada no grafo — persiste o Estado inteiro entre turns
memory = MemorySaver()
fluxo_agentes = grafo.compile(checkpointer=memory)


# ==============================================================================
# FLUXO PRINCIPAL
# ==============================================================================
def executar_fluxo_assessor(pergunta_usuario: str, session_id: str) -> str:
    estado_inicial = {
        "input":              pergunta_usuario,
        "session_id":         session_id,
        "agentes_chamados":   [],
        "saida_especialista": "",
        "resposta_final":     "",
        "messages":           [],
    }

    estado_final = fluxo_agentes.invoke(    
        estado_inicial,
        config={"configurable": {"thread_id": session_id}},
    )

    print(f"[debug] agentes chamados: {estado_final['agentes_chamados']}")
    return estado_final["resposta_final"]

# ==============================================================================
# LOOP DE CONVERSA
# ==============================================================================
# session_id fixo removido: cada sessão agora é criada sob demanda em
# salvar_mensagem() (app/memory.py), no primeiro turno de cada session_id
# real vindo do frontend.

# while True:
#     try:
#         user_input = input("> ")
#         if user_input.lo wer() in ("sair", "end", "fim", "tchau", "bye"):
#             encerrar_sessao(session_id)
#             print("Encerrando a conversa.")
#             break

#         resposta = executar_fluxo_assessor(
#             pergunta_usuario=user_input,
#             session_id=session_id,
#         )
#         print(resposta)

#     except Exception as e:
#         print("Erro ao consumir a API:", e)
#         continue
