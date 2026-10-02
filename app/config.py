import os
from pathlib import Path
from dotenv import load_dotenv

# BASE_DIR aponta para migracao_fastAPI/. Montado a partir da localização
# do próprio arquivo: não depende da pasta de onde você rodou o uvicorn.
BASE_DIR     = Path(__file__).resolve().parent.parent
DATA_DIR     = BASE_DIR / "data"
FRONTEND_DIR = BASE_DIR / "frontend"

FAQ_PDF_PATH = DATA_DIR / "FAQ_assessor_v1.1.pdf"

load_dotenv(BASE_DIR / ".env")          # o único load_dotenv() do projeto

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY   = os.getenv("GROQ_API_KEY")
DATABASE_URL   = os.getenv("DATABASE_URL")
MONGODB_URI    = os.getenv("MONGODB_URI")
QDRANT_URL     = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")

OBRIGATORIAS = {
    "GEMINI_API_KEY": GEMINI_API_KEY,
    "GROQ_API_KEY":   GROQ_API_KEY,
    "DATABASE_URL":   DATABASE_URL,
    "MONGODB_URI":    MONGODB_URI,
    "QDRANT_URL":     QDRANT_URL,
    "QDRANT_API_KEY": QDRANT_API_KEY
}

GOOGLE_OAUTH_CREDENTIALS = os.getenv(
    "GOOGLE_OAUTH_CREDENTIALS",
    str(BASE_DIR / "gcp-oauth.keys.json"),
)
GOOGLE_CALENDAR_MCP_URL = os.getenv(
    "GOOGLE_CALENDAR_MCP_URL",
    "https://calendarmcp.googleapis.com/mcp/v1",
)
GOOGLE_CALENDAR_ACCESS_TOKEN = os.getenv("GOOGLE_CALENDAR_ACCESS_TOKEN")


def validar_config() -> list[str]:
    """Devolve a lista de problemas de configuração (vazia = tudo certo)."""
    problemas = []
    for nome, valor in OBRIGATORIAS.items():
        if not valor:
            problemas.append(f"Variável ausente no .env: {nome}")
    if not FAQ_PDF_PATH.exists():
        problemas.append(f"PDF do FAQ não encontrado em: {FAQ_PDF_PATH}")
    return problemas

