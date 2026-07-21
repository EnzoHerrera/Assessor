from langchain_google_genai import ChatGoogleGenerativeAI
import os
from langchain.agents import create_agent
from langgraph.checkpoint.memory import MemorySaver
from app.tools.pg_tools import TOOLS
from langchain_groq import ChatGroq
from datetime import datetime
from app.prompts import (
    ROUTER_PROMPT_COMPLETO,
    FINANCEIRO_PROMPT_COMPLETO,
    AGENDA_PROMPT_COMPLETO,
    ORQUESTRADOR_PROMPT_COMPLETO,
    FAQ_PROMPT
)
from app.tools.faq_tools import faq_retriever

agora = datetime.now()

llm_gemini = ChatGoogleGenerativeAI (
    model = "gemini-2.5-flash-lite",
    temperature = 0.7,
    top_p = 0.95,
    api_key = os.getenv("GEMINI_API_KEY")
)

llm_groq = ChatGroq (
    model="openai/gpt-oss-120b",
    temperature=0.7,
    api_key=os.getenv("GROQ_API_KEY")
)

llm_especialista = llm_gemini.with_fallbacks([llm_groq])

llm_rapido = ChatGroq(
    model="llama-3.3-70b-versatile",
    temperature=0.0,
    api_key=os.getenv("GROQ_API_KEY"),
)

router_memory = MemorySaver()

# Criação dos Agentes

router_app = create_agent (
    model=llm_rapido,
    system_prompt=ROUTER_PROMPT_COMPLETO,
    checkpointer=router_memory,
)

agenda_app = create_agent (
    model=llm_especialista,
    system_prompt=AGENDA_PROMPT_COMPLETO,
)

orquestrador_app = create_agent (
    model=llm_especialista,
    system_prompt=ORQUESTRADOR_PROMPT_COMPLETO,
)

faq_app = create_agent (
    model=llm_rapido,
    tools=[faq_retriever],
    system_prompt=FAQ_PROMPT,   
)

financeiro_app = create_agent(
    model=llm_especialista,
    tools=TOOLS,
    system_prompt=FINANCEIRO_PROMPT_COMPLETO,
)

# =============================================================================
# PROMPT FINAL
# Ordem: escolha
# =============================================================================

def executar_fluxo_assessor(pergunta_usuario: str, session_id: str):
#     resposta_roteador = router_app.invoke(
#         {"messages":[{"role":"user", "content":pergunta_usuario}]},
#         config={"configurable":{"thread_id":session_id}}
#     )
#     if "ROUTE=" not in resposta_roteador["messages"][-1].content:
#         return resposta_roteador["messages"][-1].content
    resposta_roteador = router_app.invoke({"messages": [{"role":"human", "content": pergunta_usuario}]}, config={"configurable": {"thread_id": session_id}})
    if not "ROUTE=" in resposta_roteador['messages'][-1].content:
        return resposta_roteador['messages'][-1].content.upper()

    if "FINANCEIRO" in resposta_roteador['messages'][-1].content.upper():
        try:
            resposta_financeiro = financeiro_app.invoke({"messages": [{"role":"human", "content": pergunta_usuario}]},config={"configurable": {"thread_id": session_id}})
            return resposta_financeiro['messages'][-1].content
        except Exception as e:
            print("Erro no financeiro_app: ", e)
            return {"status":"erro"}

    if "AGENDA" in resposta_roteador['messages'][-1].content.upper():
        try:
            resposta_agenda = agenda_app.invoke({"messages": [{"role":"human", "content": pergunta_usuario}]},config={"configurable": {"thread_id": session_id}})
            return resposta_agenda['messages'][-1].text
        except Exception as e:
            print("Erro no agenda_app: ", e)
            return {"status":"erro"}

    if "FAQ" in resposta_roteador['messages'][-1].content.upper():
        try:
            resposta_faq = faq_app.invoke({"messages": [{"role":"human", "content": pergunta_usuario}]},config={"configurable": {"thread_id": session_id}})
            return resposta_faq['messages'][-1].content
        except Exception as e:
            print("Erro no faq_app: ", e)
            return {"status":"erro"}
    

while True:
    try:
        user_input = input("> ")
        if user_input.lower() in ('sair', 'end', 'fim', 'tchau', 'bye'):
            print("Encerrando a conversa.")
            break
        resposta = executar_fluxo_assessor(
            pergunta_usuario=user_input,
            session_id="dgxfchb"
        )
        print(resposta)
    except Exception as e:
        print("Erro ao consumir a API: ", e)
        continue


while True:
    try:
        user_input = input("> ")
        if user_input.lower() in ('sair', 'end', 'fim', 'tchau', 'bye'):
            print("Encerrando a conversa.")
            break
        resposta = executar_fluxo_assessor(
            pergunta_usuario=user_input,
            session_id="dgxfchb"
        )
        print(resposta)
    except Exception as e:
        print("Erro ao consumir a API: ", e)
        continue