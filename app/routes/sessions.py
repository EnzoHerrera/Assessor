"""
As rotas do ciclo de vida da sessão.

Antes: quem encerrava a sessão era o `while True` do script — o usuário digitava
"sair", o laço chamava encerrar_sessao() e imprimia o resumo. Na migração para a
API esse laço sumiu, e com ele o único ponto do sistema que fechava sessões.
Resultado: nenhuma sessão ganhava resumo, e como buscar_historico() só enxerga
sessões COM resumo, a memória de longo prazo ficava permanentemente vazia.

Agora: o navegador decide. O botão "nova sessão" chama POST /sessions/{id}/encerrar
antes de sortear um novo UUID — é o equivalente HTTP de digitar "sair".

Repare que este arquivo, como o chat.py, é fino de propósito: ele recebe o
pedido, chama quem sabe fazer o trabalho (app.memory) e devolve o resultado.
Toda a lógica de resumo mora no memory.py.
"""

from fastapi import APIRouter

from app.memory import encerrar_sessao, iniciar_sessao
from app.schemas import SessionResponse

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("/{session_id}/iniciar", response_model=SessionResponse)
def iniciar(session_id: str) -> SessionResponse:
    """
    Abre uma sessão explicitamente.

    Opcional na prática — salvar_mensagem() já abre a sessão sozinho na primeira
    mensagem. Existe para o caso de você querer registrar o acesso mesmo que o
    usuário não chegue a perguntar nada.
    """
    iniciar_sessao(session_id)
    return SessionResponse(session_id=session_id, resumo=None)


@router.post("/{session_id}/encerrar", response_model=SessionResponse)
def encerrar(session_id: str) -> SessionResponse:
    """
    Encerra a sessão: gera o resumo via LLM e grava no documento.

    É este resumo que a tool `buscar_historico` vai encontrar depois. Sem passar
    por aqui, a conversa fica guardada no Mongo mas invisível para a memória de
    longo prazo — porque recuperar_historico() filtra por resumo não-vazio.

    Devolve resumo=None (e não erro) quando não havia nada a encerrar: sessão
    inexistente ou sem nenhuma mensagem. Encerrar duas vezes é inofensivo.

    Atenção ao custo: esta rota faz uma chamada de LLM para gerar o resumo.
    Não a acione a cada mensagem — só ao fim da conversa.
    """
    resumo = encerrar_sessao(session_id)
    return SessionResponse(session_id=session_id, resumo=resumo or None)