# JUSTIFICATIVA.md

**Quais arquivos você criou ou modificou? Indique o caminho de cada um.**

Criados: app/perfil.py, app/routes/perfil.py, app/tools/perfil.py, frontend/perfil.html, frontend/perfil.css, frontend/perfil.js.
Modificados: app/schemas.py, app/qdrant.py, app/main.py, app/agents.py, app/prompts.py.

**Por onde o perfil entra, e onde cada parte dele é gravada?**

Entra por POST /perfil, validado pelo contrato em app/schemas.py PerfilRequest. A gravação acontece em uma única função, app/perfil.py salvar_perfil, chamada por essa rota, os campos estruturados (renda, gasto, horizonte, perfil investidor) e uma cópia das restrições vão para o MongoDB, cada restrição também é indexada individualmente na collection perfil_restricoes do Qdrant.

**Como o texto livre é indexado e consultado, e por que não é busca por palavra?**

Cada restrição vira um ponto próprio no Qdrant, um vetor de embedding por frase, com payload {user_id, texto}. A consulta gera o embedding da pergunta e faz query_points filtrado por user_id. Não é busca por palavra porque o objetivo é achar a restrição certa mesmo quando o usuário não usa os mesmos termos da frase original.

**Você criou uma tool ou duas? Por quê?**

Uma tool só que seria consultar_perfil_financeiro, pois duas tools arriscariam o modelo chamar apenas uma, por exemplo, só a estruturada e aconselhar sem considerar as restrições, ou vice-versa. Como um conselho financeiro sempre depende dos dois tipos de dado juntos, faz mais sentido trazer tudo numa única chamada.

**O que garante que o perfil de um usuário não apareceria para outro, se houvesse mais de um?**

O user_id nunca é argumento que o modelo preenche, ele vem de config["configurable"]["user_id"] (RunnableConfig), propagado pelo backend a partir da requisição, o mesmo padrão já usado em buscar_historico. A consulta no Mongo é por _id = user_id, e a busca no Qdrant usa query_filter por user_id (configurado como tenant field na collection).

**Sua tool consulta o banco diretamente ou faz uma chamada HTTP na própria API? Por quê?**

Consulta o banco diretamente (Mongo e Qdrant, via Python), sem chamada HTTP. Não existe rota de leitura de perfil, então uma chamada HTTP não teria endpoint para bater.

**Por que optamos por não criar um agente "perfil"?**

Perfil não é um novo domínio de conversa como finanças ou agenda, ele é um dado de apoio que só o especialista financeiro consulta para aconselhar melhor. Criar um agente separado exigiria roteá-lo no roteador, o que não faz sentido pois o perfil não é assunto sobre o qual o usuário conversa, é contexto de fundo usado por outro especialista.

**Qual a vantagem do chat não alterar o cadastro?**

Se o chat pudesse alterar o cadastro, qualquer frase do usuário no meio de uma conversa, poderia ser mal interpretada pelo modelo e poderia sobrescrever dados sensíveis como renda ou restrições sem a confirmação explícita de um formulário. Manter a escrita só na tela Perfil garante uma única porta de entrada, validada e intencional, para esse dado.