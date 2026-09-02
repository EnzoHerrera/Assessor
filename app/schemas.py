from pydantic import BaseModel, Field

class ChatRequest(BaseModel):
    """O que o navegador envia no POST /chat."""
    session_id: str = Field(..., examples=["id_usuario"])
    pergunta: str = Field(..., min_length=1, examples=["gastei 50 reais no mercado"])

class ChatResponse(BaseModel):
    """O que a API devolve no POST /chat."""
    resposta: str

class SessionResponse(BaseModel):
    session_id: str
    resumo: str | None = None