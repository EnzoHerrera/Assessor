import sys
from pathlib import Path
from typing import Annotated, Any, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from app.tools.financeiro import (
    add_transaction as _add_transaction,
    saldo_diario as _daily_balance,
    search_transactions as _query_transactions,
    saldo_total as _total_balance,
    update_transaction as _update_transaction,
)

mcp = MCPServer(
    name="assessor-financeiro",
    version="1.0.0",
    instructions="Ferramentas financeiras do Assessor.AI...",
)

LEITURA = ToolAnnotations(readOnlyHint=True)
ESCRITA = ToolAnnotations(readOnlyHint=False, destructiveHint=False)

@mcp.tool(
    name="total_balance",
    title="Saldo total",
    description="Saldo de todo o histórico: receitas menos despesas.",
    annotations=LEITURA,
)
def total_balance() -> dict[str, Any]:
    return _total_balance.invoke({})


@mcp.tool(
    name="add_transaction",
    title="Registrar transação",
    description="Registra uma transação na tabela transactions.",
    annotations=ESCRITA,
)
def add_transaction(
    amount: Annotated[float, Field(description="Valor (sempre positivo).")],
    source_text: Annotated[str, Field(description="Texto original.")],
    type_name: Annotated[Optional[str], Field(description="INCOME | EXPENSES | TRANSFER")] = None,
    category_name: Annotated[Optional[str], Field(description="Categoria em pt-BR.")] = None,
) -> dict[str, Any]:
    return _add_transaction.invoke({
        "amount": amount,
        "source_text": source_text,
        "type_name": type_name,
        "category_name": category_name,
    })


@mcp.tool(
    name="query_transactions",
    title="Consultar transações",
    description="Lista transações com filtro de texto e intervalo de datas (America/Sao_Paulo).",
    annotations=LEITURA,
)
def query_transactions(
    texto: Annotated[Optional[str], Field(description="Texto em source_text/description.")] = None,
    inicio_intervalo: Annotated[Optional[str], Field(description="Data inicial YYYY-MM-DD.")] = None,
    fim_intervalo: Annotated[Optional[str], Field(description="Data final YYYY-MM-DD.")] = None,
) -> dict[str, Any]:
    return _query_transactions.invoke({
        "texto": texto,
        "inicio_intervalo": inicio_intervalo,
        "fim_intervalo": fim_intervalo,
    })


@mcp.tool(
    name="daily_balance",
    title="Saldo diário",
    description="Saldo (receitas - despesas) de um dia YYYY-MM-DD.",
    annotations=LEITURA,
)
def daily_balance(
    dia_informado: Annotated[str, Field(description="Data YYYY-MM-DD (America/Sao_Paulo).")],
) -> dict[str, Any]:
    return _daily_balance.invoke({"dia_informado": dia_informado})


@mcp.tool(
    name="update_transaction",
    title="Atualizar transação",
    description="Atualiza uma transação por id ou por match_text + date_local.",
    annotations=ESCRITA,
)
def update_transaction(
    id: Optional[int] = None,
    match_text: Optional[str] = None,
    date_local: Optional[str] = None,
    amount: Optional[float] = None,
    type_name: Optional[str] = None,
    category_name: Optional[str] = None,
    description: Optional[str] = None,
    payment_method: Optional[str] = None,
    occurred_at: Optional[str] = None,
) -> dict[str, Any]:
    args = {
        "id": id, "match_text": match_text, "date_local": date_local, "amount": amount,
        "type_name": type_name, "category_name": category_name, "description": description,
        "payment_method": payment_method, "occurred_at": occurred_at,
    }
    return _update_transaction.invoke({k: v for k, v in args.items() if v is not None})


if __name__ == "__main__":
    from app.config import DATABASE_URL

    if not DATABASE_URL:
        print("[assessor-financeiro] DATABASE_URL ausente no .env", file=sys.stderr)

    mcp.run(transport="stdio")