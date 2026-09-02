from langchain.agents import create_agent
from app.tools.financeiro import TOOLS
from app.tools.faq import faq_retriever
from app.tools.mongo import TOOLS_MEMORIA
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
    tools=TOOLS,
    system_prompt=FINANCEIRO_PROMPT_COMPLETO,
)

agenda_app = create_agent(
    model=llm_especialista,
    system_prompt=AGENDA_PROMPT_COMPLETO,
    tools=TOOLS+TOOLS_MEMORIA,
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