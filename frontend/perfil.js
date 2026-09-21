/* ====================================================
   Tela Perfil — envio do formulário.

   Esta tela só ESCREVE. Ela não carrega o perfil salvo ao abrir,
   e a API não precisa expor rota de leitura.

   O campo "restricoes" é uma lista: o que for enviado substitui
   inteiramente o que estava salvo antes.
   ==================================================== */

// Precisa ser o mesmo usuário que o chat usa. Se o backend usar outro
// identificador quando o front não manda user_id, o assessor não vai
// encontrar o perfil cadastrado aqui.
const USER_ID = 'usuario_teste';

// A rota que a sua API precisa expor.
const ENDPOINT = '/perfil';

const MAX_RESTRICOES = 5;

const API_BASE =
  window.location.protocol === 'file:' ? 'http://localhost:8000' : '';

const els = {
  badge: document.getElementById('user-badge'),
  renda: document.getElementById('renda_mensal'),
  gasto: document.getElementById('gasto_fixo_mensal'),
  horizonte: document.getElementById('horizonte_meses'),
  perfil: document.getElementById('perfil_investidor'),
  restricoes: document.getElementById('restricoes'),
  add: document.getElementById('add-restricao'),
  status: document.getElementById('status'),
  submit: document.getElementById('submit'),
  echo: document.getElementById('echo'),
  echoBody: document.getElementById('echo-body'),
};

els.badge.textContent = USER_ID;

/* ---------------------------------------------- lista de restrições */

function criarLinha(valor = '') {
  const linha = document.createElement('div');
  linha.className = 'restricao';

  const input = document.createElement('input');
  input.className = 'restricao__input';
  input.type = 'text';
  input.maxLength = 200;
  input.value = valor;
  input.placeholder = 'ex.: prefiro não deixar dinheiro preso por muito tempo';

  const remover = document.createElement('button');
  remover.className = 'restricao__remove';
  remover.type = 'button';
  remover.textContent = 'X';
  remover.title = 'remover';
  remover.addEventListener('click', () => {
    linha.remove();
    sincronizarControles();
  });

  linha.append(input, remover);
  els.restricoes.appendChild(linha);
  sincronizarControles();
}

function sincronizarControles() {
  const linhas = els.restricoes.querySelectorAll('.restricao');
  els.add.disabled = linhas.length >= MAX_RESTRICOES;
  // Sempre sobra pelo menos uma linha na tela; se ela ficar vazia,
  // quem decide se isso é válido é a API.
  linhas.forEach((linha) => {
    linha.querySelector('.restricao__remove').disabled = linhas.length === 1;
  });
}

els.add.addEventListener('click', () => criarLinha());
criarLinha();

/* ---------------------------------------------- envio */

function setStatus(text, kind) {
  els.status.textContent = text;
  els.status.className = 'sheet__status' + (kind ? ` is-${kind}` : '');
}

function lerRestricoes() {
  return Array.from(els.restricoes.querySelectorAll('.restricao__input'))
    .map((input) => input.value.trim())
    .filter((texto) => texto !== '');
}

function numeroOuNulo(valor) {
  const texto = valor.trim();
  return texto === '' ? null : Number(texto);
}

function montarPayload() {
  // Campos vazios viram null de propósito, e a relação entre renda e gasto
  // não é conferida aqui: quem valida é a API, não esta tela.
  return {
    user_id: USER_ID,
    renda_mensal: numeroOuNulo(els.renda.value),
    gasto_fixo_mensal: numeroOuNulo(els.gasto.value),
    horizonte_meses: numeroOuNulo(els.horizonte.value),
    perfil_investidor: els.perfil.value || null,
    restricoes: lerRestricoes(),
  };
}

function mostrarResposta(dados) {
  els.echoBody.textContent = JSON.stringify(dados, null, 2);
  els.echo.hidden = false;
}

async function salvar() {
  const payload = montarPayload();

  els.submit.disabled = true;
  setStatus('enviando...');
  els.echo.hidden = true;

  try {
    const resposta = await fetch(API_BASE + ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    let corpo = null;
    try {
      corpo = await resposta.json();
    } catch {
      corpo = { detail: 'a resposta não era JSON' };
    }

    if (!resposta.ok) {
      setStatus(`a api recusou (${resposta.status})`, 'error');
      mostrarResposta(corpo);
      return;
    }

    setStatus('perfil salvo', 'ok');
    mostrarResposta(corpo);
  } catch (erro) {
    setStatus('não consegui falar com a api', 'error');
    mostrarResposta({ erro: String(erro) });
  } finally {
    els.submit.disabled = false;
  }
}

els.submit.addEventListener('click', salvar);
