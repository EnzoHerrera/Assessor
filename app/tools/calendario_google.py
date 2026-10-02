"""
Cliente MCP do Google Calendar — só Python.

O Assessor é CLIENTE de um servidor que nós não escrevemos: o MCP oficial
da Google em https://calendarmcp.googleapis.com/mcp/v1 (streamable HTTP).

O login é OAuth Desktop com google-auth-oauthlib. O Chrome abre, o aluno
autoriza, o token fica em google-calendar.token.json na raiz do projeto.

Como o SQL continua em agenda.py, este módulo NÃO grava no Postgres.
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from langchain.tools import tool
from pydantic import BaseModel, Field

from app.config import (
    BASE_DIR,
    GOOGLE_CALENDAR_ACCESS_TOKEN,
    GOOGLE_CALENDAR_MCP_URL,
    GOOGLE_OAUTH_CREDENTIALS,
)

ESCOPOS = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]
ARQUIVO_TOKEN = BASE_DIR / "google-calendar.token.json"

CABECALHOS_MCP = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


def _credenciais_oauth() -> Optional[str]:
    if not GOOGLE_OAUTH_CREDENTIALS:
        return None
    caminho = Path(GOOGLE_OAUTH_CREDENTIALS)
    if not caminho.is_absolute():
        caminho = BASE_DIR / caminho
    return str(caminho) if caminho.is_file() else None


def _salvar_token(creds) -> None:
    ARQUIVO_TOKEN.write_text(creds.to_json(), encoding="utf-8")


def _carregar_token():
    """Devolve Credentials válidas, renovando se o access_token expirou."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not ARQUIVO_TOKEN.is_file():
        return None
    creds = Credentials.from_authorized_user_file(str(ARQUIVO_TOKEN), ESCOPOS)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _salvar_token(creds)
    if creds and creds.valid:
        return creds
    return None


def autenticar() -> dict:
    """
    Abre o Chrome, o aluno escolhe o Gmail e autoriza. Rode uma vez:

        python -c "from app.tools.calendario_google import autenticar; print(autenticar())"
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    keys = _credenciais_oauth()
    if not keys:
        return {
            "status": "error",
            "message": "Falta gcp-oauth.keys.json na raiz do projeto (o professor distribui).",
        }
    flow = InstalledAppFlow.from_client_secrets_file(keys, ESCOPOS)
    creds = flow.run_local_server(port=0, prompt="consent")
    _salvar_token(creds)
    return {"status": "ok", "arquivo": str(ARQUIVO_TOKEN)}


def _access_token() -> Optional[str]:
    if GOOGLE_CALENDAR_ACCESS_TOKEN:
        return GOOGLE_CALENDAR_ACCESS_TOKEN
    creds = _carregar_token()
    return creds.token if creds else None


def _resultado_mcp(result: Any) -> dict:
    if getattr(result, "structured_content", None):
        data = result.structured_content
        return data if isinstance(data, dict) else {"status": "ok", "result": data}
    textos = []
    for bloco in getattr(result, "content", []) or []:
        texto = getattr(bloco, "text", None)
        if texto:
            textos.append(texto)
    if not textos:
        return {"status": "error", "message": "O servidor MCP não devolveu conteúdo."}
    bruto = textos[0]
    try:
        return json.loads(bruto)
    except json.JSONDecodeError:
        return {"status": "ok", "result": bruto}


def _erro_mcp(result: Any) -> dict:
    conteudo = _resultado_mcp(result)
    if isinstance(conteudo, dict):
        mensagem = conteudo.get("result") or conteudo.get("message") or conteudo
    else:
        mensagem = conteudo
    return {
        "status": "error",
        "message": mensagem if isinstance(mensagem, str) else str(mensagem),
    }


def _motivo(erro: BaseException) -> str:
    if isinstance(erro, BaseExceptionGroup):
        return " | ".join(_motivo(sub) for sub in erro.exceptions)
    texto = str(erro).strip()
    return f"{type(erro).__name__}: {texto}" if texto else type(erro).__name__


async def _chamar_via_http(nome: str, argumentos: dict) -> dict:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client

    token = _access_token()
    if not token:
        return {
            "status": "error",
            "message": (
                "Sem token do Google Calendar. Rode no terminal: "
                'python -c "from app.tools.calendario_google import autenticar; print(autenticar())"'
            ),
        }

    client = create_mcp_http_client()
    client.headers["Authorization"] = f"Bearer {token}"

    async with client:
        async with streamable_http_client(GOOGLE_CALENDAR_MCP_URL, http_client=client) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(nome, argumentos)
                if getattr(result, "is_error", False):
                    return _erro_mcp(result)
                return _resultado_mcp(result)


def _executar(coro_factory) -> dict:
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro_factory())).result()


def _criar_via_rest(payload: dict) -> dict:
    """
    Grava o evento na Calendar API (Python + HTTP) com o mesmo token OAuth.

    O MCP oficial (calendarmcp.googleapis.com) devolve 403 se o serviço MCP
    não estiver ligado no Cloud. A Calendar API (calendar-json) já está.
    """
    import httpx

    token = _access_token()
    if not token:
        return {
            "status": "error",
            "message": (
                "Sem token do Google Calendar. Rode: "
                'python -c "from app.tools.calendario_google import autenticar; print(autenticar())"'
            ),
        }

    corpo = {
        "summary": payload["summary"],
        "start": {
            "dateTime": payload["startTime"],
            "timeZone": payload.get("timeZone", "America/Sao_Paulo"),
        },
        "end": {
            "dateTime": payload["endTime"],
            "timeZone": payload.get("timeZone", "America/Sao_Paulo"),
        },
    }
    if payload.get("location"):
        corpo["location"] = payload["location"]
    if payload.get("description"):
        corpo["description"] = payload["description"]

    resposta = httpx.post(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        headers={"Authorization": f"Bearer {token}", "content-type": "application/json"},
        json=corpo,
        timeout=30,
    )
    data = resposta.json()
    if resposta.status_code >= 400:
        return {
            "status": "error",
            "message": data.get("error", {}).get("message") or str(data),
        }
    return {
        "status": "ok",
        "id": data.get("id"),
        "titulo": data.get("summary"),
        "inicio": (data.get("start") or {}).get("dateTime"),
        "link": data.get("htmlLink"),
    }


def chamar_google_calendar(nome_tool: str, argumentos: dict) -> dict:
    """
    Tenta o MCP oficial. Se o serviço MCP não estiver habilitado no Cloud
    (403 típico da aula), grava pela Calendar API com o mesmo token.
    """
    try:
        mcp = _executar(lambda: _chamar_via_http(nome_tool, argumentos))
    except BaseException as e:
        mcp = {"status": "error", "message": _motivo(e)}

    texto = str(mcp.get("message") or "")
    if mcp.get("status") != "error":
        return mcp
    if "Calendar MCP API has not been used" in texto or "calendarmcp.googleapis.com" in texto or "Server returned an error" in texto:
        return _criar_via_rest(argumentos)
    return mcp


def listar_tools_google() -> dict:
    """tools/list no MCP oficial — sem token. É o catálogo, não os dados."""
    import httpx

    resposta = httpx.post(
        GOOGLE_CALENDAR_MCP_URL,
        headers=CABECALHOS_MCP,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        timeout=30,
    )
    return resposta.json()


def _fim_padrao(start_time: str) -> str:
    try:
        return (datetime.fromisoformat(start_time) + timedelta(hours=1)).isoformat()
    except ValueError:
        return start_time


class AddGoogleEventArgs(BaseModel):
    title: str = Field(..., description="Título do evento no Google Calendar.")
    start_time: str = Field(..., description="Início em ISO 8601 (ex.: 2026-09-18T15:00:00-03:00).")
    end_time: Optional[str] = Field(
        default=None,
        description="Fim em ISO 8601. Se omitido, usa 1 hora depois do início.",
    )
    location: Optional[str] = Field(default=None, description="Local.")
    description: Optional[str] = Field(default=None, description="Descrição / notas.")


@tool("add_google_event", args_schema=AddGoogleEventArgs)
def add_google_event(
    title: str,
    start_time: str,
    end_time: Optional[str] = None,
    location: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    """
    Cria o compromisso no Google Calendar da conta autenticada, via MCP oficial
    da Google (HTTP). NÃO grava no Postgres — isso é papel da tool add_event.
    Ao marcar um evento novo, chame as DUAS: primeiro add_event, depois esta.
    """
    fim = end_time or _fim_padrao(start_time)
    payload: dict[str, Any] = {
        "summary": title,
        "startTime": start_time,
        "endTime": fim,
        "timeZone": "America/Sao_Paulo",
    }
    if location:
        payload["location"] = location
    if description:
        payload["description"] = description

    bruto = chamar_google_calendar("create_event", payload)
    if bruto.get("status") == "ok" and bruto.get("id"):
        return {
            "status": "ok",
            "id": bruto["id"],
            "titulo": bruto.get("titulo") or title,
            "inicio": bruto.get("inicio") or start_time,
            "link": bruto.get("link"),
        }
    evento = bruto.get("event") if isinstance(bruto, dict) else None
    if isinstance(evento, dict) and evento.get("id"):
        return {
            "status": "ok",
            "id": evento["id"],
            "titulo": evento.get("summary"),
            "inicio": (evento.get("start") or {}).get("dateTime") or evento.get("start"),
            "link": evento.get("htmlLink") or evento.get("html_link"),
        }
    return bruto


TOOLS_GOOGLE = [add_google_event]