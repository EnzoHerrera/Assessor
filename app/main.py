import operator
from typing import Annotated, TypedDict
from dotenv import load_dotenv
from langgraph.graph import StateGraph, END

import os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.agents import create_agent
from langchain_groq import ChatGroq
from langgraph.checkpoint.memory import MemorySaver
from tools.pg_tools import TOOLS
from tools.faq_tools import faq_retriever
from prompts import (
    ROUTER_PROMPT_COMPLETO,
    FINANCEIRO_PROMPT_COMPLETO,
    AGENDA_PROMPT_COMPLETO,
    ORQUESTRADOR_PROMPT_COMPLETO,
    FAQ_PROMPT,
)

from guardrail import guardrail_entrada, guardrail_saida, anonimizar_entrada
from memory_mongodb import iniciar_sessao, salvar_mensagem, encerrar_sessao
from langchain_core.messages import RemoveMessage
from tools.memory_tools import TOOLS_MEMORIA

load_dotenv()

# ==============================================================================
# MODELOS E AGENTES  (sem checkpointer — a memória fica no grafo)
# ==============================================================================
llm_gemini = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0.7,
    top_p=0.95,
    api_key=os.getenv("GEMINI_API_KEY"),
)

llm_groq = ChatGroq(
    model="openai/gpt-oss-120b",
    temperature=0.7,
    api_key=os.getenv("GROQ_API_KEY"),
)

llm_especialista = llm_gemini.with_fallbacks([llm_groq])

llm_rapido = ChatGroq(
    model="llama-3.3-70b-versatile",
    temperature=0.0,
    api_key=os.getenv("GROQ_API_KEY"),
)

router_app = create_agent(
    model=llm_rapido,
    tools=TOOLS_MEMORIA,
    system_prompt=ROUTER_PROMPT_COMPLETO,
)

financeiro_app = create_agent(
    model=llm_especialista,
    tools=TOOLS,
    system_prompt=FINANCEIRO_PROMPT_COMPLETO,
)

agenda_app = create_agent(
    model=llm_especialista,
    system_prompt=AGENDA_PROMPT_COMPLETO,
)

orquestrador_app = create_agent(
    model=llm_rapido,
    system_prompt=ORQUESTRADOR_PROMPT_COMPLETO,
)

faq_app = create_agent(
    model=llm_rapido,
    tools=[faq_retriever],
    system_prompt=FAQ_PROMPT,
)


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


# ==============================================================================
# NÓS
# ==============================================================================
def no_roteador(estado: Estado) -> dict:
    saida = router_app.invoke(
        {"messages": [{"role": "human", "content": estado["input"]}]},
        config={"configurable": {"thread_id": estado["session_id"]}},
    )
    texto = saida["messages"][-1].text

    # Resposta direta (saudação, fora de escopo): já escreve no campo final
    if not texto.strip().startswith("ROUTE="):
        return {
            "agentes_chamados": ["roteador"],
            "resposta_final":   texto,
        }

    # Encaminhamento: sobrescreve input com o protocolo para o especialista
    return {
        "input":            texto,
        "agentes_chamados": ["roteador"],
    }

def no_orquestrador(estado: Estado) -> dict:
    saida = orquestrador_app.invoke(
        {"messages": [{"role": "human", "content": estado["saida_especialista"]}]},
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
    }

def no_guardrail_saida(estado: Estado) -> dict:
    ultima = ""
    for msg in reversed(estado["messages"]):
        if msg.type == "ai" and msg.content:
            ultima = msg.content
            break

    resultado = guardrail_saida(ultima, estado.get("mapa_pii", {}))
    return {
        "messages":[{"role":"assistant", "content":resultado["conteudo"]}],
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
grafo.add_edge("faq",          END)   # FAQ bypassa o orquestrador

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
session_id = "id_usuario"
iniciar_sessao(session_id)

while True:
    try:
        user_input = input("> ")
        if user_input.lower() in ("sair", "end", "fim", "tchau", "bye"):
            encerrar_sessao(session_id)
            print("Encerrando a conversa.")
            break

        resposta = executar_fluxo_assessor(
            pergunta_usuario=user_input,
            session_id=session_id,
        )
        print(resposta)

    except Exception as e:
        print("Erro ao consumir a API:", e)
        continue
