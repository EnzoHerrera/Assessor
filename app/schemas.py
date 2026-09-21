from typing import Literal
from pydantic import BaseModel, Field, model_validator

class ChatRequest(BaseModel):
    """O que o navegador envia no POST /chat."""

    session_id: str = Field(
        ...,
        description="Identifica a conversa (UUID gerado pelo front a cada sessão).",
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )
    user_id: str = Field(
        default="usuario_teste",
        description="Identifica o usuário de forma estável entre sessões. "
                    "É o que permite a memória de longo prazo funcionar.",
        examples=["usuario_teste"],
    )
    pergunta: str = Field(
        ...,
        min_length=1,
        description="A mensagem do usuário — o que antes vinha do input().",
        examples=["gastei 50 reais no mercado hoje"],
    )

class ChatResponse(BaseModel):
    """O que a API devolve no POST /chat."""
    resposta: str

class SessionResponse(BaseModel):
    session_id: str
    resumo: str | None = None

class PerfilRequest(BaseModel):
    """Contrato fixo enviado pela tela Perfil no POST /perfil. Não renomear campos."""

    user_id: str = Field(..., min_length=1)
    renda_mensal: float = Field(..., gt=0)
    gasto_fixo_mensal: float = Field(..., ge=0)
    horizonte_meses: int = Field(..., ge=1, le=120)
    perfil_investidor: Literal["conservador", "moderado", "arrojado"]
    restricoes: list[str] = Field(..., min_length=1, max_length=5)

    @model_validator(mode="after")
    def _gasto_menor_que_renda(self) -> "PerfilRequest":
        if self.gasto_fixo_mensal >= self.renda_mensal:
            raise ValueError("gasto_fixo_mensal deve ser menor que renda_mensal")
        return self