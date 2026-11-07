import json
import operator
import re
from typing import Annotated, TypedDict
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.message import add_messages
from langchain_core.runnables import RunnableConfig          # ← novo
from app.guardrail import guardrail_entrada, guardrail_saida, anonimizar_entrada
from app.memory import iniciar_sessao, salvar_mensagem, encerrar_sessao
# from langchain_core.messages import RemoveMessage
from app.memory import salvar_mensagem
from app.observabilidade import abrir_turno, fechar_turno, status_do_estado
from app.agents import router_app, financeiro_app, agenda_app, orquestrador_app, faq_app
from app.juiz import no_juiz
from app.tools.write_staging import (
    iniciar_turno_escrita, encerrar_turno_escrita, listar_escritas_pendentes,
    descartar_escritas_pendentes, confirmar_escritas_pendentes,
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
    juiz_aprovou:        bool                                 # veredito do turno atual (libera a escrita)
    resposta_validada_para_escrita: bool                      # saída do especialista coerente com a escrita pendente
    status_turno:        str                                  # ok | erro | bloqueado (turno atual, não acumulado)
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

def _extrair_json(texto: str) -> dict | None:
    """Pega o objeto JSON da saída do especialista, mesmo cercado de markdown."""
    achado = re.search(r"\{.*\}", texto or "", re.DOTALL)
    if not achado:
        return None
    try:
        return json.loads(achado.group(0))
    except json.JSONDecodeError:
        return None


def no_orquestrador(estado: Estado, config: RunnableConfig) -> dict:
    json_especialista = estado["messages"][-1].text
    turno_id = (config or {}).get("configurable", {}).get("turn_id")
    pendentes = listar_escritas_pendentes(turno_id)

    # Só exige saída coerente quando há escrita pendente; consultas seguem como antes.
    coerente = True
    if pendentes:
        dados = _extrair_json(json_especialista)
        dominio = "financeiro" if estado.get("rota") == "financeiro" else "agenda"
        intencoes = {"inserir", "atualizar", "deletar"} if dominio == "financeiro" else {"criar"}
        coerente = bool(
            dados
            and dados.get("dominio") == dominio
            and dados.get("intencao") in intencoes
            and isinstance(dados.get("resposta"), str)
            and dados["resposta"].strip()
            and not dados.get("esclarecer")
            and not dados.get("acompanhamento")
        )

    saida = orquestrador_app.invoke(
        {"messages": [{"role": "human", "content": json_especialista}]},
        config=config,
    )
    return {
        "agentes_chamados": [estado["rota"], "orquestrador"],
        "resposta_validada_para_escrita": coerente,
        "messages":[{"role":"assistant", "content": saida["messages"][-1].text}],
    }

def no_guardrail_entrada(estado: Estado, config: RunnableConfig) -> dict:
    mensagem_original = estado["input"]
    texto = mensagem_original
    texto_anon, mapa = anonimizar_entrada(texto)
    resposta = guardrail_entrada(texto_anon)

    # user_id vem do contexto da requisição (config), nunca de thread_id/session_id.
    salvar_mensagem(
        session_id=estado["session_id"],
        role="usuario",
        content=texto_anon,
        user_id=(config or {}).get("configurable", {}).get("user_id"),
    )

    if resposta["bloqueado"]:
        return {
            "rota": "fim",
            "mapa_pii": mapa,
            "resposta_final": resposta["mensagem"],
            "status_turno": "bloqueado",
        }
    return {
        "rota": "roteador",
        "mapa_pii": mapa,
        "juiz_aprovou": False,          # nenhum veredito herdado de turno anterior
        "resposta_validada_para_escrita": False,
        "status_turno": "ok",
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

def no_confirmar_escritas(estado: Estado, config: RunnableConfig) -> dict:
    """Só confirma a fila se o juiz aprovou; qualquer dúvida é fail-closed."""
    turno_id = (config or {}).get("configurable", {}).get("turn_id")
    pendentes = listar_escritas_pendentes(turno_id)
    if not pendentes:
        return {}

    aprovou = (
        estado.get("juiz_aprovou") is True
        and estado.get("resposta_validada_para_escrita") is True
    )
    if not aprovou:
        descartadas = descartar_escritas_pendentes(turno_id)
        for item in pendentes:
            print(f"[escrita] marker={item['marker']} status=descartada_julgamento operacao={item['operacao']}")
        saida = {"agentes_chamados": ["escrita:descartada"]} if descartadas else {}
        if estado.get("juiz_aprovou") is True:
            # O juiz aprovou a frase "preparada", mas a saída era incoerente: sem esta
            # mensagem o usuário acharia que a operação segue pendente.
            saida["messages"] = [{"role": "assistant", "content": (
                "Não consegui confirmar essa operação, então nada foi salvo. "
                "Reformule o pedido com todos os dados."
            )}]
        return saida

    resultado = confirmar_escritas_pendentes(turno_id)
    if resultado["status"] != "ok":
        print(f"[escrita] status=erro message={resultado.get('message', '')}")
        return {
            "messages": [{"role": "assistant", "content": (
                "Não consegui confirmar a operação no banco. Nenhuma das alterações deste turno foi confirmada. "
                "Confira os dados e tente novamente."
            )}],
            "agentes_chamados": ["escrita:erro"],
            "status_turno": "erro",
        }

    google = resultado.get("google", [])
    if resultado.get("count", 0) or google:
        tipos = {item["operacao"] for item in resultado["results"]}
        for item in resultado["results"]:
            print(f"[escrita] marker={item['marker']} status=commit operacao={item['operacao']} id={item['id']}")
        mensagens = []
        if "add_transaction" in tipos:
            mensagens.append("Lançamento financeiro registrado")
        if "update_transaction" in tipos:
            mensagens.append("Lançamento financeiro atualizado")
        if "add_event" in tipos:
            mensagens.append("Compromisso registrado na agenda")
        texto = "; ".join(mensagens) + " com sucesso." if mensagens else ""
        if google:
            if all(g.get("status") == "ok" for g in google):
                texto = (texto + " Evento criado no Google Calendar.").strip()
            else:
                texto = (texto + " Não consegui criar o evento no Google Calendar; tente novamente mais tarde.").strip()
        return {
            "messages": [{"role": "assistant", "content": texto}],
            "agentes_chamados": ["escrita:confirmada"],
        }
    return {}

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
grafo.add_node("juiz", no_juiz)
grafo.add_node("confirmar_escritas", no_confirmar_escritas)

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

grafo.add_edge("financeiro",      "orquestrador")
grafo.add_edge("agenda",          "orquestrador")
grafo.add_edge("orquestrador",    "juiz")
grafo.add_edge("faq",             "juiz")                 # FAQ mantém o juiz que já tinha
grafo.add_edge("juiz",            "confirmar_escritas")   # veredito primeiro, escrita depois
grafo.add_edge("confirmar_escritas", "guardrail_saida")
grafo.add_edge("guardrail_saida", END)

# Memória centralizada no grafo — persiste o Estado inteiro entre turns
memory = MemorySaver()
fluxo_agentes = grafo.compile(checkpointer=memory)


# ==============================================================================
# FLUXO PRINCIPAL
# ==============================================================================
# def executar_fluxo_assessor(pergunta_usuario: str, session_id: str, user_id: str = "usuario_teste") -> str:
#     estado_inicial = {
#         "input":              pergunta_usuario,
#         "session_id":         session_id,
#         "agentes_chamados":   [],
#         "saida_especialista": "",
#         "resposta_final":     "",
#         "messages":           [],
#     }

#     estado_final = fluxo_agentes.invoke(    
#         estado_inicial,
#         config={"configurable": {"thread_id": session_id, "user_id": user_id}},
#     )

#     resposta = estado_final["messages"][-1].text

#     salvar_mensagem(session_id, "human", pergunta_usuario, user_id=user_id)
#     salvar_mensagem(session_id, "assistant", resposta, user_id=user_id)

#     return resposta

def executar_fluxo_assessor(
    pergunta_usuario: str, session_id: str, user_id: str = "usuario_teste"
) -> str:
    pergunta_usuario_anonimizado, _ = anonimizar_entrada(pergunta_usuario)
    estado_inicial = {
        "messages":         [{"role": "human", "content": pergunta_usuario}],
        "agentes_chamados": [],
        "rota":             "",
        "mapa_pii":         {},
        "session_id":       session_id,

    }

    # O registro abre antes do invoke e fecha no finally, inclusive quando
    # o grafo levanta. A rota /monitor só lê o que ficou anotado.
    turno_id = abrir_turno()
    iniciar_turno_escrita(turno_id)
    status = "erro"
    rota = ""
    try:
        estado_final = fluxo_agentes.invoke(
            estado_inicial,
            config={"configurable": {"thread_id": session_id, "user_id": user_id, "turn_id": turno_id}},
        )

        resposta = estado_final["messages"][-1].text

        # Salva no MongoDB após cada interação
        salvar_mensagem(session_id, "human",     pergunta_usuario_anonimizado, user_id=user_id)
        salvar_mensagem(session_id, "assistant", resposta, user_id=user_id)

        status = status_do_estado(estado_final)
        rota = estado_final.get("rota") or ""
        return resposta
    finally:
        # Se o grafo levantou antes do nó de confirmação, nenhuma intenção fica pendurada.
        descartar_escritas_pendentes(turno_id)
        encerrar_turno_escrita(turno_id)
        fechar_turno(turno_id, session_id=session_id, rota=rota, status=status)

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

