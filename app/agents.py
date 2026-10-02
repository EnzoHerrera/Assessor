from langchain.agents import create_agent
from app.tools.financeiro import TOOLS
from app.tools.faq import faq_retriever
from app.tools.mongo import TOOLS_MEMORIA
from app.tools.perfil import TOOLS_PERFIL
from app.tools.agenda import TOOLS_AGENDA
from app.tools.calendario_google import TOOLS_GOOGLE
from app.llms import llm_rapido, llm_especialista

from app.prompts import (
    ROUTER_PROMPT_COMPLETO,
    FINANCEIRO_PROMPT_COMPLETO,
    AGENDA_PROMPT_COMPLETO,
    ORQUESTRADOR_PROMPT_COMPLETO,
    FAQ_PROMPT,
)

router_app = create_agent(
    model=llm_rapido,
    tools=TOOLS_MEMORIA,
    system_prompt=ROUTER_PROMPT_COMPLETO,
)

financeiro_app = create_agent(
    model=llm_especialista,
    tools=TOOLS+TOOLS_PERFIL,
    system_prompt=FINANCEIRO_PROMPT_COMPLETO,
)

agenda_app = create_agent(
    model=llm_especialista,
    tools=TOOLS_AGENDA + TOOLS_GOOGLE + TOOLS_MEMORIA,
    system_prompt=AGENDA_PROMPT_COMPLETO,
)

orquestrador_app = create_agent(
    model=llm_rapido,
    system_prompt=ORQUESTRADOR_PROMPT_COMPLETO,
)

faq_app = create_agent(
    model=llm_rapido,
    tools=[faq_retriever],
    system_prompt=FAQ_PROMPT,
)