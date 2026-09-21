"""
Persistência do perfil financeiro do usuário.

Dois lugares, uma escrita só (chamada pela rota POST /perfil):
  - MongoDB, collection "perfis": dado estruturado + cópia das restrições,
    consultável direto por user_id. Guardar as restrições aqui também é
    redundância proposital (mais de uma fonte para o mesmo dado), não é
    usada para busca — quem faz busca semântica é o Qdrant.
  - Qdrant, collection "perfil_restricoes": cada restrição vira um ponto
    próprio, para ser encontrada individualmente por busca semântica.

Salvar de novo com o mesmo user_id substitui o cadastro inteiro nos dois
bancos — não empilha restrições antigas ao lado das novas.
"""

import uuid
from datetime import datetime, timezone

from pymongo import MongoClient
from qdrant_client import models

from app.config import MONGODB_URI
from app.qdrant import qdrant, gerar_embedding, gerar_embeddings_batch, COLLECTION_PERFIL
from app.schemas import PerfilRequest

_mongo      = MongoClient(MONGODB_URI)
db          = _mongo["assessor"]
col_perfis  = db["perfis"]


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def salvar_perfil(perfil: PerfilRequest) -> None:
    """Grava o perfil validado no Mongo e reindexa as restrições no Qdrant."""

    col_perfis.replace_one(
        {"_id": perfil.user_id},
        {
            "_id":                perfil.user_id,
            "user_id":            perfil.user_id,
            "renda_mensal":       perfil.renda_mensal,
            "gasto_fixo_mensal":  perfil.gasto_fixo_mensal,
            "horizonte_meses":    perfil.horizonte_meses,
            "perfil_investidor":  perfil.perfil_investidor,
            "restricoes":         perfil.restricoes,
            "atualizado_em":      _agora(),
        },
        upsert=True,
    )

    qdrant.delete(
        collection_name=COLLECTION_PERFIL,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="user_id",
                        match=models.MatchValue(value=perfil.user_id),
                    )
                ]
            )
        ),
    )

    vetores = gerar_embeddings_batch(perfil.restricoes)
    pontos = [
        models.PointStruct(
            id=str(uuid.uuid4()),
            vector=vetor,
            payload={"user_id": perfil.user_id, "texto": texto},
        )
        for vetor, texto in zip(vetores, perfil.restricoes)
    ]
    qdrant.upsert(collection_name=COLLECTION_PERFIL, points=pontos)


def buscar_perfil_estruturado(user_id: str) -> dict | None:
    """Consulta direta por user_id — sem busca semântica, sem escrita."""
    return col_perfis.find_one({"_id": user_id})


def buscar_restricoes_relevantes(user_id: str, pergunta: str, limite: int = 5) -> list[str]:
    """Busca semântica nas restrições do usuário no Qdrant."""
    vetor = gerar_embedding(pergunta)
    resultados = qdrant.query_points(
        collection_name=COLLECTION_PERFIL,
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
    return [ponto.payload["texto"] for ponto in resultados.points]
