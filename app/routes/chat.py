from fastapi import APIRouter
from app.schemas import ChatRequest, ChatResponse
router = APIRouter(tags=["chat"])
from app.graph import executar_fluxo_assessor

@router.post("/chat", response_model=ChatResponse)
def conversar(requisicao: ChatRequest) -> ChatResponse:
    resposta = executar_fluxo_assessor(
        pergunta_usuario=requisicao.pergunta,
        session_id=requisicao.session_id,
    )
    return ChatResponse(resposta=resposta)