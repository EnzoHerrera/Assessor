from fastapi import APIRouter
from app.schemas import PerfilRequest
from app.perfil import salvar_perfil

router = APIRouter(tags=["perfil"])

@router.post("/perfil", response_model=PerfilRequest)
def cadastrar(requisicao: PerfilRequest) -> PerfilRequest:
    salvar_perfil(requisicao)
    return requisicao
