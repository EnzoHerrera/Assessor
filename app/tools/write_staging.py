"""
Estágio de escritas do turno ("adiar").

As tools que alteram dados (add_transaction, update_transaction, add_event e
add_google_event) NÃO gravam mais. Elas só registram a intenção aqui, numa fila
ligada ao id do turno. Depois que o juiz fala, o nó `confirmar_escritas` do grafo
decide:

  - juiz aprovou  -> confirmar_escritas_pendentes(): todas as intenções do
                     Postgres entram numa ÚNICA transação (tudo ou nada); só
                     depois dela o Google Calendar é chamado.
  - juiz recusou  -> descartar_escritas_pendentes(): a fila some, o banco nem
                     chega a ser aberto.

Fail-closed: sem turn_id, sem fila ativa, juiz sem resposta ou processo caindo
no meio do turno => nada é gravado (a fila vive só na memória do processo).
"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from typing import Any

from app.tools.db import get_conn

_lock = threading.RLock()
_filas: dict[str, list[dict[str, Any]]] = {}

OPERACOES_POSTGRES = ("add_transaction", "update_transaction", "add_event")
OPERACOES_EXTERNAS = ("add_google_event",)


# ------------------------------------------------------------------ ciclo do turno
def iniciar_turno_escrita(turno_id: str) -> None:
    with _lock:
        _filas[turno_id] = []


def encerrar_turno_escrita(turno_id: str) -> None:
    with _lock:
        _filas.pop(turno_id, None)


def listar_escritas_pendentes(turno_id: str | None) -> list[dict[str, Any]]:
    if not turno_id:
        return []
    with _lock:
        return list(_filas.get(turno_id, []))


def descartar_escritas_pendentes(turno_id: str | None) -> int:
    if not turno_id:
        return 0
    with _lock:
        fila = _filas.get(turno_id)
        if not fila:
            return 0
        quantidade = len(fila)
        fila.clear()
        return quantidade


# ------------------------------------------------------------------ preparar
def preparar_escrita(operacao: str, dados: dict[str, Any], turno_id: str | None) -> dict[str, Any]:
    """Registra a intenção sem tocar no banco. Chamada idêntica no turno não duplica."""
    if not turno_id:
        return {"status": "error", "message": "Turno de escrita não identificado; operação não executada."}

    assinatura = hashlib.sha256(
        json.dumps({"operacao": operacao, "dados": dados}, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()

    with _lock:
        fila = _filas.get(turno_id)
        if fila is None:
            return {"status": "error", "message": "Não há turno de escrita ativo; operação não executada."}
        for item in fila:
            if item["assinatura"] == assinatura:
                return _resposta_pendente(item, repetida=True)
        item = {
            "marker": uuid.uuid4().hex,
            "assinatura": assinatura,
            "operacao": operacao,
            "dados": dados,
        }
        fila.append(item)

    print(f"[escrita] marker={item['marker']} status=preparada operacao={operacao}")
    return _resposta_pendente(item, repetida=False)


def _resposta_pendente(item: dict[str, Any], repetida: bool) -> dict[str, Any]:
    return {
        "status": "pending_approval",
        "marker": item["marker"],
        "operacao": item["operacao"],
        "dados": item["dados"],
        "message": (
            "Registro preparado com os dados acima. A gravação definitiva acontece "
            "no fim do turno, depois da verificação da resposta."
            + (" (Este mesmo pedido já estava preparado; nada foi duplicado.)" if repetida else "")
        ),
    }


# ------------------------------------------------------------------ confirmar
def confirmar_escritas_pendentes(turno_id: str | None) -> dict[str, Any]:
    """Grava a fila do turno. Postgres: uma transação só. Google: depois do commit."""
    fila = listar_escritas_pendentes(turno_id)
    if not fila:
        return {"status": "ok", "count": 0, "results": [], "google": []}

    pg = [i for i in fila if i["operacao"] in OPERACOES_POSTGRES]
    externas = [i for i in fila if i["operacao"] in OPERACOES_EXTERNAS]

    resultados: list[dict[str, Any]] = []
    if pg:
        retorno = _gravar_postgres(pg)
        if retorno["status"] != "ok":
            descartar_escritas_pendentes(turno_id)
            return {**retorno, "google": []}
        resultados = retorno["results"]

    google = [_gravar_google(i) for i in externas]
    descartar_escritas_pendentes(turno_id)
    return {"status": "ok", "count": len(resultados), "results": resultados, "google": google}


def _gravar_postgres(itens: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        conn = get_conn()
    except Exception as exc:
        return {"status": "error", "count": 0, "message": str(exc), "results": []}

    cur = None
    resultados: list[dict[str, Any]] = []
    try:
        cur = conn.cursor()
        for item in itens:
            op, d, marker = item["operacao"], item["dados"], item["marker"]

            if op == "add_transaction":
                cur.execute(
                    """INSERT INTO transactions
                           (amount, type, category_id, description, payment_method, occurred_at, source_text)
                       VALUES (%s, %s, %s, %s, %s, COALESCE(%s::timestamptz, NOW()), %s)
                       RETURNING id, occurred_at;""",
                    (d["amount"], d["type_id"], d["category_id"], d.get("description"),
                     d.get("payment_method"), d.get("occurred_at"), d["source_text"]),
                )
                row = cur.fetchone()
                resultados.append({"marker": marker, "operacao": op, "id": row[0], "occurred_at": str(row[1])})

            elif op == "update_transaction":
                sets: list[str] = []
                params: list[Any] = []
                for coluna, chave in (
                    ("amount", "amount"), ("type", "type_id"), ("category_id", "category_id"),
                    ("description", "description"), ("payment_method", "payment_method"),
                ):
                    if d.get(chave) is not None:
                        sets.append(f"{coluna} = %s")
                        params.append(d[chave])
                if d.get("occurred_at") is not None:
                    sets.append("occurred_at = %s::timestamptz")
                    params.append(d["occurred_at"])
                if not sets:
                    raise ValueError("Atualização sem campos válidos.")
                params.append(d["target_id"])
                cur.execute(f"UPDATE transactions SET {', '.join(sets)} WHERE id = %s;", params)
                if cur.rowcount != 1:
                    raise ValueError(f"A transação {d['target_id']} não existe mais; nada foi confirmado.")
                resultados.append({"marker": marker, "operacao": op, "id": d["target_id"]})

            elif op == "add_event":
                cur.execute(
                    """INSERT INTO events (title, start_time, end_time, location, notes, source_text)
                       VALUES (%s, %s::timestamptz, %s::timestamptz, %s, %s, %s)
                       RETURNING id, start_time, end_time;""",
                    (d["title"], d["start_time"], d.get("end_time"), d.get("location"),
                     d.get("notes"), d["source_text"]),
                )
                row = cur.fetchone()
                resultados.append({
                    "marker": marker, "operacao": op, "id": row[0],
                    "start_time": str(row[1]), "end_time": str(row[2]) if row[2] else None,
                })
            else:
                raise ValueError(f"Operação de escrita desconhecida: {op}")

        conn.commit()
        return {"status": "ok", "count": len(resultados), "results": resultados}
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "count": 0, "message": str(exc), "results": []}
    finally:
        if cur is not None:
            cur.close()
        conn.close()


def _gravar_google(item: dict[str, Any]) -> dict[str, Any]:
    # import tardio: evita carregar o cliente MCP/Google se o turno não usa agenda
    from app.tools.calendario_google import criar_evento_google

    try:
        retorno = criar_evento_google(**item["dados"])
    except Exception as exc:
        retorno = {"status": "error", "message": str(exc)}
    print(f"[escrita] marker={item['marker']} status={retorno.get('status')} operacao=add_google_event")
    return {"marker": item["marker"], **retorno}
