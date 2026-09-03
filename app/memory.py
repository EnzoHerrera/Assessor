"""
=================
Modelagem
---------
Um documento por acesso (sessão = uma conversa completa).
O _id é um UUID gerado internamente — a main.py só conhece o session_id.
O session_id identifica o usuário.

Documento
---------
{
  "_id":           "uuid-gerado-internamente",
  "session_id":    "id_usuario",
  "iniciada_em":   datetime,
  "atualizada_em": datetime,
  "resumo":        "Usuário registrou Pix de R$50...",
  "mensagens":     [
    {"role": "usuario",     "content": "oi"},
    {"role": "assistente", "content": "Olá!"}
  ]
}

Funções
----------------
  iniciar_sessao(session_id)                 → cria documento no MongoDB
  salvar_mensagem(session_id, role, content) → adiciona mensagem na sessão ativa
  encerrar_sessao(session_id)                → gera resumo e salva no documento
"""

import uuid
from datetime import datetime, timezone
from app.llms import llm_rapido
from app.config import MONGODB_URI

from qdrant_client import models
from app.qdrant import qdrant, gerar_embedding, COLLECTION_MEMORIA
from langchain_groq import ChatGroq
from pymongo import MongoClient



# ==============================================================================
# CONEXÃO
# ==============================================================================

_mongo      = MongoClient(MONGODB_URI)
db          = _mongo["assessor"]
col_sessoes = db["sessoes"]

col_sessoes.create_index("session_id")
col_sessoes.create_index("iniciada_em")

# ==============================================================================
# LLM PARA RESUMO
# ==============================================================================
_PROMPT_RESUMO = """\
Você é um assistente que resume conversas de assessoria financeira e agenda.
Gere um resumo conciso em 2-4 frases capturando:
- O que o usuário fez (transações registradas, eventos agendados)
- O que o usuário perguntou
- Informações relevantes mencionadas (valores, datas, categorias)

Responda APENAS com o resumo, sem introdução ou explicação.

Conversa:
{conversa}
"""
_sessoes_ativas: dict = {}

def _agora() -> datetime:
    return datetime.now(timezone.utc)

def _formatar_conversa(mensagens: list[dict]) -> str:
    """Formata o array de mensagens em texto para o prompt de resumo."""
    linhas = []
    for msg in mensagens:
        linhas.append(f"{msg['role']}: {msg['content']}")
    return "\n".join(linhas)


def _gerar_resumo(mensagens: list[dict]) -> str:
    """Chama o LLM para gerar o resumo da sessão."""
    conversa = _formatar_conversa(mensagens)
    return llm_rapido.invoke(
        _PROMPT_RESUMO.format(conversa=conversa)
    ).content.strip()


# ==============================================================================
# FUNÇÕES
# ==============================================================================
def _doc_id_da_sessao(session_id: str) -> str | None:
    """
    Descobre o documento da sessão EM ANDAMENTO deste usuário, ou None.

    Olha primeiro o cache em memória (_sessoes_ativas). Se não achar, procura
    no MongoDB a sessão mais recente que ainda não foi encerrada — isto é, com
    resumo vazio.

    Essa segunda tentativa existe porque _sessoes_ativas vive na RAM do
    processo: um --reload do uvicorn no meio da conversa esvazia o dicionário.
    Sem ela, iniciar_sessao() criaria um documento novo para a mesma conversa a
    cada reinício, e encerrar_sessao() não acharia nada para resumir — sem erro
    nenhum, apenas silêncio.
    """
    doc_id = _sessoes_ativas.get(session_id)
    if doc_id:
        return doc_id

    doc = col_sessoes.find_one(
        {"session_id": session_id, "resumo": {"$in": ["", None]}},
        {"_id": 1},
        sort=[("iniciada_em", -1)],
    )
    if not doc:
        return None

    _sessoes_ativas[session_id] = doc["_id"]   # repovoa o cache
    return doc["_id"]


def iniciar_sessao(session_id: str, user_id: str = "usuario_teste") -> None: # se o session_id for int tem que mudar aqui
    """
    Cria um novo documento de sessão no MongoDB.
    O doc_id (UUID) é gerado aqui e guardado em _sessoes_ativas.
    """
    doc_id = str(uuid.uuid4())
    agora  = _agora()

    col_sessoes.insert_one({
        "_id":           doc_id,
        "session_id":    session_id,
        "user_id":       user_id,
        "iniciada_em":   agora,
        "atualizada_em": agora,
        "resumo":        "",
        "mensagens":     [],
    })

    _sessoes_ativas[session_id] = doc_id


def salvar_mensagem(session_id: str, role: str, content: str, user_id: str = "usuario_teste") -> None:
    """ Adiciona uma mensagem ao array de mensagens da sessão ativa.

    Cria a sessão sob demanda se ainda não estiver registrada em
    _sessoes_ativas (ex.: primeiro turno de um session_id novo, ou o
    processo foi reiniciado e perdeu o mapa em memória).
    """
    doc_id = _doc_id_da_sessao(session_id)
    if doc_id is None:
        iniciar_sessao(session_id, user_id=user_id)
        doc_id = _sessoes_ativas[session_id]

    col_sessoes.update_one(
        {"_id": doc_id},
        {
            "$push": {"mensagens": {"role": role, "content": content}},
            "$set":  {"atualizada_em": _agora()},
        },
    )

def encerrar_sessao(session_id) -> str:
    """
    Encerra a sessão ativa:
       1. Carrega mensagens do MongoDB
       2. Gera resumo via LLM
       3. Atualiza documento com resumo e atualizada_em
       4. Remove sessão do estado interno
    Retorna o resumo gerado ou string vazia se não houver mensagens.
    """
    doc_id = _doc_id_da_sessao(session_id)   # ← era: _sessoes_ativas.get(session_id)
    if not doc_id:
        return ""
    doc = col_sessoes.find_one({"_id": doc_id})
    if not doc:
        _sessoes_ativas.pop(session_id, None)
        return ""
    mensagens = doc.get("mensagens")
    if not mensagens:
        col_sessoes.delete_one({"_id": doc_id})
        _sessoes_ativas.pop(session_id, None)
        return ""
    if len(mensagens) % 2 != 0:
        mensagens.pop()
    resumo = _gerar_resumo(mensagens)
    col_sessoes.update_one(
        {"_id": doc_id},
        {
            "$set": {
                "mensagens": mensagens,
                "resumo": resumo,
                "atualizada_em": _agora(),
            }
        },
    )
    # Salva o embedding do resumo no Qdrant para busca semântica futura.
    # O filtro de multitenancy usa user_id (estável entre sessões), não session_id.
    user_id = doc.get("user_id", "usuario_teste")
    vetor = gerar_embedding(resumo)
    qdrant.upsert(
        collection_name=COLLECTION_MEMORIA,
        points=[
            models.PointStruct(
                id=doc_id,
                vector=vetor,
                payload={
                    "user_id":     user_id,
                    "session_id":  session_id,
                    "resumo":      resumo,
                    "iniciada_em": doc["iniciada_em"].isoformat(),
                },
            )
        ],
    )
    _sessoes_ativas.pop(session_id, None)
    return resumo


def recuperar_historico(user_id: str, busca: str = "", limite: int = 3) -> list[dict]:
    if busca:
        vetor = gerar_embedding(busca)
        resultados = qdrant.query_points(
            collection_name=COLLECTION_MEMORIA,
            query=vetor,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="user_id",
                        match=models.MatchValue(value=user_id),
                    )
                ]
            ),
            limit=limite,
        )

        if resultados.points:
            return [
                {
                    "doc_id":      ponto.id,
                    "iniciada_em": ponto.payload.get("iniciada_em", ""),
                    "resumo":      ponto.payload["resumo"],
                }
                for ponto in resultados.points
            ]

    filtro = {"user_id": user_id, "resumo": {"$nin": ["", None]}}
    docs = (
        col_sessoes
        .find(filtro, {"resumo": 1, "iniciada_em": 1})
        .sort("iniciada_em", -1)
        .limit(limite)
    )

    # Quero adicionar um fallback para o mongo
    return [
        {"doc_id": d["_id"], "iniciada_em": d["iniciada_em"], "resumo": d["resumo"]}
        for d in docs
    ]


def recuperar_mensagens(doc_id: str) -> list[dict]:
    """
    Busca o array completo de mensagens de um documento específico, pelo _id.
    Usada no passo 2 — só quando o resumo deu match e você precisa do detalhe
    literal da conversa. No futuro, o doc_id virá do Qdrant.
    """
    doc = col_sessoes.find_one({"_id": doc_id}, {"mensagens": 1})
    return doc["mensagens"] if doc else []
