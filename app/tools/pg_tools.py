import os
from dotenv import load_dotenv
import psycopg2
from typing import Optional, List
from langchain.tools import tool
from pydantic import BaseModel, Field

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")  

def get_conn():
    return psycopg2.connect(DATABASE_URL)

# Essa classe garante que o objeto de Python passe todos esses campos
class AddTransactionArgs(BaseModel):
    amount: float = Field(..., description="Valor da transação (use positivo).")
    source_text: str = Field(..., description="Texto original do usuário.")
    occurred_at: Optional[str] = Field(default=None, description="Timestamp ISO 8601; se ausente, usa NOW() no banco.")
    type_id: Optional[int] = Field(default=None, description="ID em transaction_types (1=INCOME, 2=EXPENSES, 3=TRANSFER).")
    type_name: Optional[str] = Field(default=None, description="Nome do tipo: INCOME | EXPENSES | TRANSFER.")
    category_id: Optional[int] = Field(default=None, description="FK de categories (opcional).")
    category_name: str = Field(..., description="Procure nessa frase uma dessas categorias: comida, besteira, estudo, férias, transporte, moradia, saúde, lazer, contas, investimento, presente, outros")
    description: Optional[str] = Field(default=None, description="Descrição (opcional).")
    payment_method: Optional[str] = Field(default=None, description="Forma de pagamento (opcional).")

categorias = {
    "comida": 1, "besteira": 2, "estudo": 3, "férias": 4, "transporte": 5, "moradia": 6,
    "saúde": 7, "lazer": 8, "contas": 9, "investimento": 10, "presente": 11, "outros": 12
}

TYPES_ALIASES = {"INCOME":"INCOME", "ENTRADA":"INCOME", "RECEITA":"INCOME", "SALÁRIO":"INCOME", 
                "EXPENSE":"EXPENSES", "EXPENSES":"EXPENSES", "DESPESA":"EXPENSES", "GASTO":"EXPENSES",
                "TRANSFER":"TRANSFER", "TRANSFERÊNCIA":"TRANSFER", "TRANSFERENCIA":"TRANSFER"
}

#Garante que o campo type da tabela transactions receba um id válido (1=INCOME, 2=EXPENSES, 3=TRANSFER)
def _resolve_type_id(cur, type_id: Optional[int], type_name: Optional[str]) -> Optional[int]:
    if type_name:
        t = type_name.strip().upper()
        if t in TYPES_ALIASES:
            t = TYPES_ALIASES[t]
        cur.execute("SELECT id FROM transaction_types WHERE UPPER(type)=%s LIMIT 1;", (t,))
        row = cur.fetchone()
        return row[0] if row else None
    if type_id:
        return int(type_id)
    return 2

def _resolve_category_id(cur, category_name: Optional[str], category_id: Optional[int]) -> Optional[int]:
    if not category_name:
        return 12
    name = category_name.strip().lower()
    if name in categorias:
        return categorias[name]
    return 12

# Tool: add_transaction
@tool("add_transaction", args_schema=AddTransactionArgs)
def add_transaction(
    amount: float,
    source_text: str,
    category_name: str,
    occurred_at: Optional[str] = None,
    type_id: Optional[int] = None,
    type_name: Optional[str] = None,
    category_id: Optional[int] = None,
    description: Optional[str] = None,
    payment_method: Optional[str] = None,
) -> dict:
    """Insere uma transação financeira no banco de dados Postgres.""" # docstring obrigatório da @tools do langchain (estranho, mas legal né?)
    conn = get_conn()
    cur = conn.cursor()
    try:
        resolved_type_id = _resolve_type_id(cur, type_id, type_name)
        category_id = _resolve_category_id(cur, category_name, category_id)
        if occurred_at:
            cur.execute(
                """
                INSERT INTO transactions
                    (amount, type, category_id, description, payment_method, occurred_at, source_text)
                VALUES
                    (%s, %s, %s, %s, %s, %s::timestamptz, %s)
                RETURNING id, occurred_at;
                """,
                (amount, resolved_type_id, category_id, description, payment_method, occurred_at, source_text),
            )
        else:
            cur.execute(
                """
                INSERT INTO transactions
                    (amount, type, category_id, description, payment_method, occurred_at, source_text)
                VALUES
                    (%s, %s, %s, %s, %s, NOW(), %s)
                RETURNING id, occurred_at;
                """,
                (amount, resolved_type_id, category_id, description, payment_method, source_text),
            )

        new_id, occurred = cur.fetchone()
        conn.commit()
        return {"status": "ok", "id": new_id, "occurred_at": str(occurred)}

    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass

class QueryTransactionsArgs(BaseModel):
    texto: Optional[str] = Field(default=None, description="Texto para buscar em source_text ou description")
    inicio_intervalo: Optional[str] = Field(default=None, description="Data inicial YYYY-MM-DD")
    fim_intervalo: Optional[str] = Field(default=None, description="Data final YYYY-MM-DD")

@tool("search_transaction", args_schema=QueryTransactionsArgs)
def search_transactions(
    texto: Optional[str] = None, 
    inicio_intervalo: Optional[str] = None, 
    fim_intervalo: Optional[str] = None
) -> dict:
    """Consulta transações com filtros por texto (source_text/description), tipos e data locais (America/Sao_Paulo)
    Os dados devem vir na seguinte ordem:
        - Intervalo (date_from_local/date_to_local): ASC 
        - Caso contrário: desc (mais recentes primeiro).
    """
    conexao = get_conn()
    pstmt = conexao.cursor()
    try:
        string_base = "select id, amount, type, category_id, description, payment_method, occurred_at, source_text from transactions where 1=1"
        parametros_filtros = []
        if texto is not None:
            string_base += " and (source_text ilike %s or description ilike %s)"
            parametros_filtros.append(f"%{texto}%")
            parametros_filtros.append(f"%{texto}%")
        if inicio_intervalo is not None:
            string_base += " and (occurred_at at time zone 'America/Sao_Paulo')::date >= %s"
            parametros_filtros.append(inicio_intervalo)
        if fim_intervalo is not None:
            string_base += " and (occurred_at at time zone 'America/Sao_Paulo')::date <= %s"
            parametros_filtros.append(fim_intervalo)
        string_base += " order by occurred_at "
        if inicio_intervalo is not None or fim_intervalo is not None:
            string_base += "asc"
        else:
            string_base += "desc"
        pstmt.execute(string_base, tuple(parametros_filtros))
        retorno = pstmt.fetchall()
        return {"resultado":retorno}
    except Exception as erro:
        return {"erro":str(erro)}
    finally:
        conexao.close()
        pstmt.close()


@tool("saldo_total")
def saldo_total() -> dict:
    """
    Retorna o saldo (INCOME - EXPENSES) em todo histórico (ignora Transfer).
    """
    conexao = get_conn()
    pstmt = conexao.cursor()
    try:
        pstmt.execute("select sum(case when type = 1 then amount else 0 end)-sum(case when type = 2 then amount else 0 end) from transactions")
        retorno = pstmt.fetchone()
        return {"saldo":retorno[0]}
    except Exception as erro:
        return {"erro":str(erro)}
    finally:
        conexao.close()
        pstmt.close()

@tool("saldo_diario")
def saldo_diario(
    dia_informado: Optional[str] = Field(default=None, description="data informado em YYYY-MM-DD em America/Sao_Paulo")
) -> dict:
    """
    Retorna o saldo (INCOME - EXPENSES) do dia local informado (YYYY-MM-DD) em America/Sao_Paulo. Ignora Transfer (type=3)
    """
    conexao = get_conn()
    pstmt = conexao.cursor()
    try:
        pstmt.execute("""
            select sum(case when type = 1 then amount else 0 end)-sum(case when type = 2 then amount else 0 end) from transactions where (occurred_at at time zone 'America/Sao_Paulo')::date = %s;
        """, (dia_informado, ))
        retorno = pstmt.fetchone()
        return {"saldo":retorno[0]}
    except Exception as erro:
        return {"erro":str(erro)}
    finally:
        pstmt.close()
        conexao.close()

class UpdateTransactionArgs(BaseModel):
    id: Optional[int] = Field(
        default=None,
        description="ID da transação a atualizar. Se ausente, será feita uma busca por (match_text + date_local)."
    )
    match_text: Optional[str] = Field(
        default=None,
        description="Texto para localizar transação quando id não for informado (busca em source_text/description)."
    )
    date_local: Optional[str] = Field(
        default=None,
        description="Data local (YYYY-MM-DD) em America/Sao_Paulo; usado em conjunto com match_text quando id ausente."
    )
    amount: Optional[float] = Field(default=None, description="Novo valor.")
    type_id: Optional[int] = Field(default=None, description="Novo type_id (1/2/3).")
    type_name: Optional[str] = Field(default=None, description="Novo type_name: INCOME | EXPENSES | TRANSFER.")
    category_id: Optional[int] = Field(default=None, description="Nova categoria (id).")
    category_name: Optional[str] = Field(default=None, description="Nova categoria (nome).")
    description: Optional[str] = Field(default=None, description="Nova descrição.")
    payment_method: Optional[str] = Field(default=None, description="Novo meio de pagamento.")
    occurred_at: Optional[str] = Field(default=None, description="Novo timestamp ISO 8601.")

def _local_date_filter_sql(field: str = "occurred_at") -> str:
    """
    Retorna um trecho SQL para filtragem por dia local em America/Sao_Paulo.
    Ex.: (occurred_at AT TIME ZONE 'America/Sao_Paulo')::date = %s::date
    """
    return f"(({field} AT TIME ZONE 'America/Sao_Paulo')::date = %s::date)"

@tool("update_transaction", args_schema=UpdateTransactionArgs)
def update_transaction(
    id: Optional[int] = None,
    match_text: Optional[str] = None,
    date_local: Optional[str] = None,
    amount: Optional[float] = None,
    type_id: Optional[int] = None,
    type_name: Optional[str] = None,
    category_id: Optional[int] = None,
    category_name: Optional[str] = None,
    description: Optional[str] = None,
    payment_method: Optional[str] = None,
    occurred_at: Optional[str] = None,
) -> dict:
    """
    Atualiza uma transação existente.
    Estratégias:
      - Se 'id' for informado: atualiza diretamente por ID.
      - Caso contrário: localiza a transação mais recente que combine (match_text em source_text/description)
        E (date_local em America/Sao_Paulo), então atualiza.
    Retorna: status, rows_affected, id, e o registro atualizado.
    """
    if not any([amount, type_id, type_name, category_id, category_name, description, payment_method, occurred_at]):
        return {"status": "error", "message": "Nada para atualizar: forneça pelo menos um campo (amount, type, category, description, payment_method, occurred_at)."}

    conn = get_conn()
    cur = conn.cursor()
    try:
        # Resolve target_id
        target_id = id
        if target_id is None:
            if not match_text or not date_local:
                return {"status": "error", "message": "Sem 'id': informe match_text E date_local para localizar o registro."}

            # Buscar o mais recente no dia local informado que combine o texto
            cur.execute(
                f"""
                SELECT t.id
                FROM transactions t
                WHERE (t.source_text ILIKE %s OR t.description ILIKE %s)
                  AND {_local_date_filter_sql("t.occurred_at")}
                ORDER BY t.occurred_at DESC
                LIMIT 1;
                """,
                (f"%{match_text}%", f"%{match_text}%", date_local)
            )
            row = cur.fetchone()
            if not row:
                return {"status": "error", "message": "Nenhuma transação encontrada para os filtros fornecidos."}
            target_id = row[0]

        # Resolver type_id / category_id a partir de nomes, se fornecidos
        resolved_type_id = _resolve_type_id(cur, type_id, type_name) if (type_id or type_name) else None
        resolved_category_id = category_id
        if category_name and not category_id:
            resolved_category_id = _resolve_category_id(cur, category_name)

        # Montar SET dinâmico
        sets = []
        params: List[object] = []
        if amount is not None:
            sets.append("amount = %s")
            params.append(amount)
        if resolved_type_id is not None:
            sets.append("type = %s")
            params.append(resolved_type_id)
        if resolved_category_id is not None:
            sets.append("category_id = %s")
            params.append(resolved_category_id)
        if description is not None:
            sets.append("description = %s")
            params.append(description)
        if payment_method is not None:
            sets.append("payment_method = %s")
            params.append(payment_method)
        if occurred_at is not None:
            sets.append("occurred_at = %s::timestamptz")
            params.append(occurred_at)

        if not sets:
            return {"status": "error", "message": "Nenhum campo válido para atualizar."}

        params.append(target_id)

        cur.execute(
            f"UPDATE transactions SET {', '.join(sets)} WHERE id = %s;",
            params
        )
        rows_affected = cur.rowcount
        conn.commit()

        # Retornar o registro atualizado
        cur.execute(
            """
            SELECT
              t.id, t.occurred_at, t.amount, tt.type AS type_name,
              c.name AS category_name, t.description, t.payment_method, t.source_text
            FROM transactions t
            JOIN transaction_types tt ON tt.id = t.type
            LEFT JOIN categories c ON c.id = t.category_id
            WHERE t.id = %s;
            """,
            (target_id,)
        )
        r = cur.fetchone()
        updated = None
        if r:
            updated = {
                "id": r[0],
                "occurred_at": str(r[1]),
                "amount": float(r[2]),
                "type": r[3],
                "category": r[4],
                "description": r[5],
                "payment_method": r[6],
                "source_text": r[7],
            }

        return {
            "status": "ok",
            "rows_affected": rows_affected,
            "id": target_id,
            "updated": updated
        }

    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


# Exporta a lista de tools
TOOLS = [add_transaction, search_transactions, saldo_total, saldo_diario, update_transaction]