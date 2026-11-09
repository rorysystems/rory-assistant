/* Rory Assistant — interface. Fala com o Python por window.pywebview.api e recebe
   eventos do Claude por window.onAgentEvent(evento). */
"use strict";

const $ = (sel) => document.querySelector(sel);
/* Todas as chamadas ao Python passam por este proxy: se uma falhar, vira um aviso na tela
   e um {ok:false, error} (em vez de uma promessa rejeitada que ninguém trata). */
let _apiProxy = null, _apiRaw = null;
function api() {
  const raw = window.pywebview.api;
  if (_apiProxy && _apiRaw === raw) return _apiProxy;
  _apiRaw = raw;
  _apiProxy = new Proxy(raw, {
    get(target, key) {
      const fn = target[key];
      if (typeof fn !== "function") return fn;
      return async (...args) => {
        try { return await fn.apply(target, args); }
        catch (e) {
          const msg = String((e && e.message) || e);
          toast(`Falha em ${String(key)}: ${msg}`, "error");
          return { ok: false, error: msg };
        }
      };
    },
  });
  return _apiProxy;
}

window.addEventListener("unhandledrejection", (e) => {
  e.preventDefault();
  toast("Erro inesperado: " + String((e.reason && e.reason.message) || e.reason), "error");
});

/* Textos do cartão do plano e avisos de modo: no idioma do usuário (pt-BR é o padrão). */
const I18N = {
  "pt-BR": {
    planTitle: "Deseja prosseguir?",
    planOpt1: "Sim, e aceitar edições automaticamente",
    planOpt2: "Sim, e aprovar edições manualmente",
    planOpt3: "Não, continuar planejando",
    planAsk: "O que você quer mudar no plano?",
    planSend: "Enviar",
    planBack: "Voltar",
    planNote: "Modo planejamento: o Claude não altera arquivos",
    denyDefault: "O usuário não aprovou o plano e quer continuar planejando.",
    denyWith: "O usuário não aprovou o plano. Continue no modo planejamento e ajuste o plano conforme o pedido: ",
  },
  en: {
    planTitle: "Would you like to proceed?",
    planOpt1: "Yes, and auto-accept edits",
    planOpt2: "Yes, and manually approve edits",
    planOpt3: "No, keep planning",
    planAsk: "What should change in the plan?",
    planSend: "Send",
    planBack: "Back",
    planNote: "Plan mode: Claude does not change files",
    denyDefault: "The user did not approve the plan and wants to keep planning.",
    denyWith: "The user did not approve the plan. Stay in plan mode and revise the plan as requested: ",
  },
  es: {
    planTitle: "¿Desea continuar?",
    planOpt1: "Sí, y aceptar las ediciones automáticamente",
    planOpt2: "Sí, y aprobar las ediciones manualmente",
    planOpt3: "No, seguir planificando",
    planAsk: "¿Qué quieres cambiar en el plan?",
    planSend: "Enviar",
    planBack: "Volver",
    planNote: "Modo planificación: Claude no modifica archivos",
    denyDefault: "El usuario no aprobó el plan y quiere seguir planificando.",
    denyWith: "El usuario no aprobó el plan. Continúa en modo planificación y ajusta el plan según lo pedido: ",
  },
};
const T = (() => {
  const l = (navigator.language || "pt-BR").toLowerCase();
  return l.startsWith("en") ? I18N.en : l.startsWith("es") ? I18N.es : I18N["pt-BR"];
})();

// nome do gerenciador de arquivos do sistema (para "Mostrar no ...")
const FM_NAME = (() => {
  const p = (navigator.platform || "") + " " + (navigator.userAgent || "");
  if (/mac/i.test(p)) return "Finder";
  if (/win/i.test(p)) return "Explorer";
  return "gerenciador de arquivos";
})();

const state = {
  convs: [],        // todas as conversas (de todas as pastas), mais recentes primeiro
  active: null,
  home: "",
  busy: {},         // convId -> bool
  pending: [],      // anexos ainda não enviados: {name, data, isImage}
  live: null,       // resposta em andamento na tela
  filter: "",
  recall: [],       // mensagens já enviadas nesta conversa (seta ↑/↓)
  recallPos: -1,    // -1 = rascunho atual
  draft: "",
  commands: {},     // convId -> lista de /comandos e skills
  slash: { open: false, items: [], sel: 0, mode: "slash", moved: false },
  shellSeq: 0,      // pedidos de sugestão do "!": só a resposta mais nova vale
  shellTimer: 0,
  shellWarm: false,
  commandsRetry: {}, // convId -> quando tentar listar os /comandos de novo (após falha)
  convsDirty: false, // a barra lateral precisa ser redesenhada quando o menu ⋮ fechar
  history: {},       // convId -> { start, total }: parte do histórico ainda não carregada
};

/* ---------- utilidades ---------- */
marked.setOptions({ gfm: true, breaks: true });

// restringe a detecção automática de linguagem (mais rápido e mais certeiro)
try {
  hljs.configure({ languages: ["javascript", "typescript", "python", "bash", "powershell", "json", "xml", "css", "sql", "java", "csharp", "cpp", "c", "go", "rust", "yaml", "markdown", "diff", "ini", "dos"] });
} catch (_) {}

// <img> remoto vira link (evita exfiltrar dados por URL de imagem); style é proibido
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "IMG") {
    const src = node.getAttribute("src") || "";
    if (/^https?:\/\//i.test(src)) {
      const a = document.createElement("a");
      a.setAttribute("href", src);
      a.textContent = node.getAttribute("alt") || src;
      node.replaceWith(a);
    }
  }
});

// realce de código sob demanda: só quando o bloco aparece na tela
let hlObserver = null;
function lazyHighlight(code) {
  if (!("IntersectionObserver" in window)) { try { hljs.highlightElement(code); } catch (_) {} return; }
  hlObserver ||= new IntersectionObserver((entries) => {
    for (const en of entries) {
      if (!en.isIntersecting) continue;
      hlObserver.unobserve(en.target);
      try { hljs.highlightElement(en.target); } catch (_) {}
    }
  }, { rootMargin: "200px" });
  hlObserver.observe(code);
}

/* hl: true = realça já; false = sem realce (streaming); "lazy" = realça quando aparecer */
function renderMarkdown(el, text, hl = true) {
  el.innerHTML = DOMPurify.sanitize(marked.parse(text || ""), { FORBID_TAGS: ["style", "form", "input"], FORBID_ATTR: ["style"] });
  if (hl === false) return;
  el.querySelectorAll("pre code").forEach((code) => {
    if (hl === "lazy") lazyHighlight(code);
    else { try { hljs.highlightElement(code); } catch (_) {} }
    const pre = code.parentElement;
    if (!pre.querySelector(".copy")) {
      const b = document.createElement("button");
      b.className = "copy"; b.textContent = "copiar";
      b.onclick = () => { copyText(code.innerText); b.textContent = "copiado"; setTimeout(() => (b.textContent = "copiar"), 1200); };
      pre.appendChild(b);
    }
  });
}

// links do markdown abrem no navegador, nunca dentro da janela do app
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[href]");
  if (!a) return;
  e.preventDefault();
  const href = a.getAttribute("href");
  if (/^https?:\/\//i.test(href)) api().open_url(href);
});

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (_) {}
  try {
    const r = await api().copy_text(text);
    if (!(r && r.ok === false)) return true;
  } catch (_) {}
  const t = document.createElement("textarea"); t.value = text; document.body.appendChild(t);
  t.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (_) {}
  t.remove();
  return ok;
}

function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = "toast " + kind; t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), 2800);
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso), now = new Date();
  const hm = d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === now.toDateString() ? hm : d.toLocaleDateString("pt-BR") + " " + hm;
}

function fmtAgo(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "agora";
  if (s < 3600) return Math.floor(s / 60) + " min";
  if (s < 86400) return Math.floor(s / 3600) + " h";
  if (s < 86400 * 7) return Math.floor(s / 86400) + " d";
  return new Date(iso).toLocaleDateString("pt-BR");
}

function folderName(path) {
  if (!path) return "";
  if (path.toLowerCase() === state.home.toLowerCase()) return "Pasta do usuário";
  return path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || path;
}

const isWide = () => window.matchMedia("(min-width: 760px)").matches;

/* ---------- início ---------- */
function start() {
  if (start.done) return;
  start.done = true;
  init().catch((e) => toast("Erro ao iniciar: " + e, "error"));
}
window.addEventListener("pywebviewready", start);
if (window.pywebview && window.pywebview.api) start();

async function init() {
  const s = await api().get_state();
  state.convs = s.conversations;
  state.home = s.home;
  state.app = s.app;
  $("#app-name").textContent = s.app.name;
  applyTheme(s.theme);
  bindUi();
  await selectConv(s.active);
  const p = await api().get_prefs();
  if (p.files_open && window.innerWidth >= 900 && state.active) openFiles();
  checkHealth();
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("#hl-theme").href = theme === "light" ? "vendor/hl-light.min.css" : "vendor/hl-dark.min.css";
}

/* ---------- conversas ---------- */
function conv(id = state.active) { return state.convs.find((c) => c.id === id); }

function upsertConv(c) {
  const i = state.convs.findIndex((x) => x.id === c.id);
  if (i >= 0) state.convs[i] = { ...state.convs[i], ...c }; else state.convs.unshift(c);
  state.convs.sort((a, b) => (b.updated || "").localeCompare(a.updated || ""));
}

// conversas visíveis (filtro da busca) agrupadas por pasta, na ordem da conversa mais recente de cada pasta
function convGroups() {
  const q = state.filter.trim().toLowerCase();
  const shown = state.convs.filter((c) => !q || (c.title || "").toLowerCase().includes(q) || (c.path || "").toLowerCase().includes(q));
  const groups = new Map();
  for (const c of shown) {
    const key = (c.path || "").toLowerCase();
    if (!groups.has(key)) groups.set(key, { path: c.path, items: [] });
    groups.get(key).items.push(c);
  }
  return { shown, groups, q };
}

function renderConvs() {
  const list = $("#conv-list");
  // renomeando na barra lateral: redesenhar apagaria o que está sendo digitado
  // (o fim da edição redesenha)
  if (list.querySelector('[contenteditable="true"]')) return;
  state.convsDirty = false;
  closeConvMenu();
  list.innerHTML = "";
  const { shown, groups, q } = convGroups();
  if (!shown.length) list.append(el("div", "group", q ? "Nada encontrado" : "Sem conversas"));
  for (const g of groups.values()) {
    const head = el("div", "group");
    head.innerHTML = FOLDER_SVG;
    head.append(document.createTextNode(folderName(g.path)));
    head.title = g.path;
    list.append(head);
    for (const c of g.items) {
      const row = el("div", "conv" + (c.id === state.active ? " active" : ""));
      row.dataset.id = c.id;
      row.tabIndex = 0;
      row.setAttribute("role", "button");
      const meta = el("div", "meta");
      meta.append(el("div", "n", c.title || "Nova conversa"), el("div", "t", fmtAgo(c.updated)));
      row.append(meta);
      const x = el("button", "icon-btn x"); x.title = "Opções da conversa"; x.setAttribute("aria-label", "Opções da conversa");
      x.innerHTML = '<svg viewBox="0 0 24 24"><circle cx="12" cy="5" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="12" cy="19" r="1.4"/></svg>';
      x.onclick = (e) => { e.stopPropagation(); openConvMenu(c.id, x, meta.querySelector(".n")); };
      row.append(x);
      setRowIndicator(row, c.id);
      row.onclick = () => { selectConv(c.id); toggleSidebar(false); };
      row.onkeydown = (e) => {
        if (e.target !== row) return;
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectConv(c.id); toggleSidebar(false); }
      };
      list.append(row);
    }
  }
}

// ⏸ (esperando permissão) ou ponto (trabalhando) da linha, antes do botão ⋮
function setRowIndicator(row, id) {
  row.querySelectorAll(".wait, .dot").forEach((n) => n.remove());
  const x = row.querySelector(".x");
  if (isWaiting(id)) { const w = el("span", "wait", "⏸"); w.title = "Parado esperando sua permissão"; row.insertBefore(w, x); }
  else if (state.busy[id]) row.insertBefore(el("span", "dot"), x);
}

/* Atualiza só a linha da conversa (não fecha o menu ⋮ aberto nem perde o foco).
   Redesenha tudo só se a ordem da lista mudou. */
function refreshConvRow(id) {
  const list = $("#conv-list");
  const row = list.querySelector(`.conv[data-id="${CSS.escape(id)}"]`);
  const want = [...convGroups().groups.values()].flatMap((g) => g.items.map((c) => c.id));
  const have = [...list.querySelectorAll(".conv")].map((r) => r.dataset.id);
  const sameOrder = want.length === have.length && want.every((v, i) => v === have[i]);
  if (!sameOrder || (!row && want.includes(id))) {
    if (document.querySelector(".conv-menu")) { state.convsDirty = true; if (!row) return; }
    else { renderConvs(); return; }
  }
  if (!row) return;
  const c = conv(id);
  if (c) {
    const n = row.querySelector(".n");
    if (!n.isContentEditable) n.textContent = c.title || "Nova conversa";
    row.querySelector(".t").textContent = fmtAgo(c.updated);
  }
  setRowIndicator(row, id);
}

// fecha o menu ⋮ e, se a lista mudou enquanto ele estava aberto, redesenha
function dismissConvMenu() {
  if (!document.querySelector(".conv-menu")) return;
  closeConvMenu();
  if (state.convsDirty) renderConvs();
}

let selectSeq = 0;
async function selectConv(id) {
  if (!conv(id)) id = state.convs[0]?.id;
  if (!id) return;
  const seq = ++selectSeq;
  state.active = id;
  state.live = null;
  liveCarry = null;
  renderHeader();
  filesFollowConv();
  closeSettings();
  renderConvs();
  const r = await api().set_active(id);
  // trocou de conversa de novo enquanto esta carregava: a resposta mais nova vale
  if (seq !== selectSeq || id !== state.active) return;
  if (r.ok === false) { toast(r.error || "Não consegui abrir a conversa", "error"); return; }
  state.busy[id] = r.busy;
  compacting[id] = !!r.compacting;
  state.history[id] = { start: r.history_start || 0, total: r.history_total ?? (r.history || []).length };
  renderHistory(r.history || [], id);
  setShellChip(r.shell_ctx);
  setBusyUi();
  waiting[id] = new Set((r.permissions || []).map((p) => p.id));
  if (r.busy || waiting[id].size) {
    startLive();
    // recupera o que foi respondido enquanto esta conversa não estava na tela
    for (const part of r.live_parts || []) {
      if (part.type === "text") liveText(part.text);
      else if (part.type === "tool") { state.live.textEl = null; addTool(state.live.body, state.live.typing, { ...part, error: part.error ?? null }); }
    }
    // pedidos de permissão feitos enquanto esta conversa não estava na tela
    const lastPart = (r.live_parts || []).at(-1);
    if (lastPart && lastPart.type === "tool" && lastPart.error == null)
      state.live.tool = { id: lastPart.id, name: lastPart.name, summary: lastPart.summary, since: Date.now() };
    for (const p of r.permissions || []) {
      state.live.body.insertBefore(permissionCard(p), state.live.typing);
    }
    updateLiveStatus();
    scrollBottom(true);
  }
  updateWaitBar();
  loadConvUsage();
  $("#prompt").focus();
}

function renderHeader() {
  const c = conv();
  if (!c) return;
  $("#conv-name").textContent = c.title || "Nova conversa";
  $("#conv-path").textContent = c.path;
  $("#conv-path").title = c.path;
  setPermSelect(c.permission_mode || "default");
}

const MODE_ICONS = { default: "●", acceptEdits: "⏵⏵", plan: "⏸", bypassPermissions: "⚠" };

function setPermSelect(mode) {
  $("#perm-mode").value = mode;
  $(".mode").className = "mode m-" + mode;
  $("#mode-icon").textContent = MODE_ICONS[mode] || "●";
  const note = $("#plan-note");
  note.hidden = mode !== "plan";
  note.textContent = "⏸ " + T.planNote;
}

async function applyMode(mode, { quiet = false } = {}) {
  const id = state.active;
  setPermSelect(mode);
  await api().set_permission_mode(id, mode);
  if (conv(id)) conv(id).permission_mode = mode;
  if (!quiet) toast("Modo: " + $("#perm-mode").selectedOptions[0].textContent);
}

// Shift+Tab, como no terminal: perguntar → aceitar edições → plano → liberar tudo → perguntar
function cycleMode() {
  const order = ["default", "acceptEdits", "plan", "bypassPermissions"];
  const cur = conv().permission_mode || "default";
  const next = order[(order.indexOf(cur) + 1) % order.length] || "default";
  applyMode(next, { quiet: true });
}

/* Compactação automática: fica no backend (agent.py, _maybe_compact), logo depois de
   cada resposta, com o cache quente. Compactar ao abrir a conversa (cache frio) relia
   tudo pelo preço cheio. Aqui só o indicador "compactando…" no topo. */
const compacting = {};
// convId -> Set de pedidos de permissão sem resposta (o Claude está parado esperando)
const waiting = {};
const isWaiting = (id) => !!(waiting[id] && waiting[id].size);
function showCompacting(id) {
  if (id !== state.active || !compacting[id]) return;
  $("#conv-usage").hidden = false;
  $("#ctx-label").textContent = "compactando em segundo plano…";
}

/* ---------- tokens da conversa (topo) ---------- */
let usageSeq = 0;
async function loadConvUsage() {
  const id = state.active, seq = ++usageSeq;
  const r = await api().conversation_usage(id);
  if (seq !== usageSeq || id !== state.active) return;
  const box = $("#conv-usage");
  if (compacting[id]) { showCompacting(id); return; }
  if (!r.ok) { box.hidden = true; return; }
  box.hidden = false;
  // janela de contexto: 1M nos modelos atuais; 200 mil nos antigos (Haiku 4.5 etc.)
  const windowSize = /haiku-4|3-[57]|sonnet-4-5|opus-4-[015]\b/.test(r.model || "") ? 200000 : 1000000;
  const pct = Math.min(100, Math.round((r.context / windowSize) * 100));
  $("#ctx-label").textContent = `contexto ${compact(r.context)} · piso ${compact(r.floor)}`;
  const bar = $("#ctx-fill").parentElement;
  $("#ctx-fill").style.width = pct + "%";
  bar.className = "ctx-bar" + (pct >= 85 ? " full" : pct >= 60 ? " warn" : "");
  $("#tot-label").textContent = `${compact(r.input + r.output + r.cache_write + r.cache_read)} tokens · ${usd(r.cost)}`;
  box.title = [
    `Contexto atual: ${nf(r.context)} de ${nf(windowSize)} tokens, ${pct}% (${(r.model || "").replace("claude-", "")})`,
    `Piso: ${nf(r.floor)} — instruções do Claude Code, ferramentas, CLAUDE.md e skills; não some ao compactar.`,
    Number(state.prefs?.auto_compact_at ?? 60000)
      ? `Compacta sozinho quando passar de ${nf(r.floor + Number(state.prefs?.auto_compact_at ?? 60000))}.`
      : "Compactação automática desligada.",
    `Respostas: ${nf(r.messages)}`,
    `Entrada: ${nf(r.input)} · Saída: ${nf(r.output)}`,
    `Cache: ${nf(r.cache_write)} gravados · ${nf(r.cache_read)} lidos`,
    `Custaria na API: ${usd(r.cost)} (estimativa; na assinatura não é cobrado assim)`,
    "",
    "Clique para compactar: o Claude passa a ler um resumo; a tela continua mostrando tudo.",
  ].filter((l, i, a) => l || i === a.length - 2).join("\n");
}

async function deleteConv(id) {
  const c = conv(id);
  if (!c) return;
  if (state.busy[id]) { toast("Espere a resposta terminar", "error"); return; }
  if (!confirm(`Apagar a conversa "${c.title}"?`)) return;
  const s = await api().delete_conversation(id);
  state.convs = s.conversations;
  if (id === state.active || !conv()) await selectConv(s.active);
  else renderConvs();
}

// Renomeia direto no lugar: no título do topo ou no nome da conversa na barra lateral.
function renameConv(id = state.active, name = $("#conv-name")) {
  if (!name || name.isContentEditable) return;
  name.contentEditable = "true";
  name.focus();
  document.getSelection().selectAllChildren(name);
  name.onclick = (e) => e.stopPropagation();  // na barra lateral, clicar no texto não troca de conversa
  const finish = async (save) => {
    name.contentEditable = "false";
    name.onkeydown = name.onblur = name.onclick = null;
    const title = name.textContent.trim();
    const c = conv(id);
    if (save && c && title && title !== c.title) {
      await api().rename_conversation(id, title);
      c.title = title;
    }
    renderConvs();
    renderHeader();
  };
  name.onkeydown = (e) => {
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(false); }
  };
  name.onblur = () => finish(true);
}

// Menu ⋮ de cada conversa na barra lateral.
function closeConvMenu() {
  document.querySelector(".conv-menu")?.remove();
  document.querySelectorAll(".conv .x.open").forEach((b) => b.classList.remove("open"));
}

function openConvMenu(id, button, nameEl) {
  const already = button.classList.contains("open");
  closeConvMenu();
  if (already) return;
  button.classList.add("open");
  const menu = el("div", "menu conv-menu");
  const add = (label, fn, cls) => {
    const b = el("button", cls || "", label);
    b.onclick = (e) => { e.stopPropagation(); closeConvMenu(); fn(); };
    menu.append(b);
  };
  add("Renomear", () => renameConv(id, nameEl));
  add("Abrir pasta", () => api().open_folder(id));
  add("Continuar no terminal", () => api().open_terminal(id));
  menu.append(el("hr"));
  add("Apagar conversa", () => deleteConv(id), "danger");
  document.body.append(menu);
  const r = button.getBoundingClientRect();
  const top = Math.min(r.bottom + 4, window.innerHeight - menu.offsetHeight - 8);
  menu.style.top = Math.max(8, top) + "px";
  menu.style.left = Math.max(8, Math.min(r.left, window.innerWidth - menu.offsetWidth - 8)) + "px";
}

/* ---------- nova conversa (escolher pasta) ---------- */
async function openNewModal() {
  const modal = $("#new-modal");
  modal.hidden = false;
  const input = $("#new-path");
  input.value = state.home;
  const box = $("#recent-folders");
  box.innerHTML = "";
  const folders = await api().recent_folders();
  if (folders.length) {
    box.append(el("div", "lbl", "Pastas recentes"));
    for (const f of folders) {
      const b = el("button", "", f);
      b.title = f;
      b.onclick = () => { input.value = f; createConv(); };
      box.append(b);
    }
  }
  input.focus(); input.select();
}

function closeNewModal() { $("#new-modal").hidden = true; }

async function createConv() {
  const r = await api().new_conversation($("#new-path").value);
  if (!r.ok) { toast(r.error, "error"); return; }
  closeNewModal();
  upsertConv(r.conversation);
  await selectConv(r.conversation.id);
  toggleSidebar(false);
}

/* ---------- histórico ---------- */
const histNode = (m) => (m.role === "user" ? userMsg(m) : m.role === "divider" ? dividerEl(m) : m.role === "shell" ? shellEl(m) : assistantMsg(m, "lazy"));

function loadPrevButton(convId) {
  const h = state.history[convId];
  if (!h || h.start <= 0) return null;
  const b = el("button", "load-prev", `Carregar anteriores (${h.start})`);
  b.id = "load-prev";
  b.onclick = async () => {
    b.disabled = true;
    const before = h.start;
    const r = await api().load_history(convId, before, 40);
    if (convId !== state.active) return;
    const items = Array.isArray(r) ? r : (r && (r.items || r.history)) || [];
    if (!items.length) { b.disabled = false; if (r && r.ok === false) return; h.start = 0; b.remove(); return; }
    const newStart = (r && !Array.isArray(r) && r.start != null) ? r.start : Math.max(0, before - items.length);
    h.start = newStart;
    const box = $("#messages"), chat = $("#chat");
    const prevH = chat.scrollHeight, prevTop = chat.scrollTop;
    const frag = document.createDocumentFragment();
    for (const m of items) frag.append(histNode(m));
    b.remove();
    const nb = loadPrevButton(convId);
    if (nb) frag.prepend(nb);
    box.prepend(frag);
    state.recall = items.filter((m) => m.role === "user" && m.text).map((m) => m.text).concat(state.recall);
    chat.style.scrollBehavior = "auto";
    chat.scrollTop = prevTop + (chat.scrollHeight - prevH);
    chat.style.scrollBehavior = "";
  };
  return b;
}

function renderHistory(items, convId = state.active) {
  state.recall = items.filter((m) => m.role === "user" && m.text).map((m) => m.text);
  state.recallPos = -1;
  const box = $("#messages");
  box.innerHTML = "";
  const more = loadPrevButton(convId);
  if (more) box.append(more);
  for (const m of items) box.append(histNode(m));
  $("#empty").hidden = items.length > 0 || (state.history[convId]?.start || 0) > 0;
  scrollBottom(true);
}

// marca na conversa onde o contexto foi compactado (a tela continua com tudo)
function dividerEl(m) {
  const d = el("div", "divider");
  let text = m.text || "Contexto compactado";
  if (m.pre_tokens) text += ` (antes: ${compact(m.pre_tokens)} tokens)`;
  if (m.trigger === "auto") text += " · automático";
  d.append(el("span", "", text));
  d.title = (m.kind === "clear"
    ? "Você continua vendo tudo; o Claude começou do zero aqui e não lê o que está acima. "
    : "Você continua vendo tudo; o Claude passa a ler um resumo do que está acima. ") + fmtTime(m.time);
  return d;
}

// "!comando": rodado direto no PC (sem o Claude); a saída vai junto da próxima mensagem
function shellEl(m, liveConv = null) {
  const box = el("div", "shell" + (m.code == null ? " running" : m.code === 0 ? "" : " fail"));
  const head = el("div", "sh-head");
  head.append(el("span", "sh-cmd", "! " + m.command));
  head.append(el("span", "sh-st", m.code == null ? "rodando…" : m.code === 0 ? "✓" : `código ${m.code}`));
  if (m.code == null && liveConv) {
    const stop = el("button", "sh-stop", "parar");
    stop.title = "Parar este comando";
    stop.onclick = async () => { stop.disabled = true; await api().stop_shell(liveConv); };
    head.append(stop);
  }
  box.append(head);
  if (m.output != null) box.append(el("pre", "sh-out", m.output || "(sem saída)"));
  const foot = el("div", "time", fmtTime(m.time));
  foot.title = "Rodou no PC, sem o Claude. A saída vai como contexto junto da sua próxima mensagem.";
  box.append(foot);
  return box;
}

async function runShell(command) {
  const convId = state.active;
  const tmp = shellEl({ command, time: new Date().toISOString() }, convId);
  if (state.live) state.live.wrap.before(tmp); else $("#messages").append(tmp);
  $("#empty").hidden = true;
  scrollBottom(true);
  const r = await api().run_shell(convId, command);
  if (!r.ok) { tmp.remove(); toast(r.error, "error"); return; }
  if (tmp.isConnected) { tmp.replaceWith(shellEl(r.entry)); scrollBottom(); }
  if (convId === state.active) refreshShellChip();
}

/* chip "saída do shell: N comandos, ~X tokens" (vai junto da próxima mensagem) */
function setShellChip(ctx) {
  const box = $("#shell-chip");
  box.innerHTML = "";
  if (!ctx || !ctx.count) { box.hidden = true; return; }
  box.hidden = false;
  const t = el("span", "sc-text", `saída do shell: ${ctx.count} ${ctx.count === 1 ? "comando" : "comandos"}, ~${nf(ctx.tokens_est || 0)} tokens`);
  t.title = "Vai como contexto junto da sua próxima mensagem. Clique no ✕ para descartar.";
  const rm = el("button", "rm", "✕"); rm.title = "Descartar"; rm.setAttribute("aria-label", "Descartar a saída do shell");
  const id = state.active;
  rm.onclick = async () => {
    await api().clear_shell_ctx(id);
    if (id === state.active) refreshShellChip();
  };
  box.append(t, rm);
}

async function refreshShellChip() {
  const id = state.active;
  const r = await api().get_shell_ctx(id);
  if (id === state.active) setShellChip(r);
}

function userMsg(m) {
  const wrap = el("div", "msg user");
  const atts = m.attachments || [];
  if (atts.length) {
    const thumbs = el("div", "thumbs");
    for (const a of atts) {
      if (a.kind === "image") {
        const img = el("img", "thumb"); img.alt = a.name; img.title = a.name;
        if (a.dataUrl) img.src = a.dataUrl;
        else api().get_attachment(a.id).then((u) => u && (img.src = u));
        img.onclick = () => lightbox(img.src);
        thumbs.append(img);
      } else {
        const pill = el("span", "file-pill", "📎 " + a.name + (a.tokens_est ? ` · ~${nf(a.tokens_est)} tokens` : ""));
        if (a.id) pill.onclick = () => api().open_attachment(a.id);
        thumbs.append(pill);
      }
    }
    wrap.append(thumbs);
  }
  if (m.text) wrap.append(el("div", "bubble", m.text));
  wrap.append(el("div", "time", fmtTime(m.time)));
  return wrap;
}

// ferramentas na tela por id (várias chamadas iguais seguidas dividem a mesma linha)
const toolIndex = new Map();

const isAgentTool = (name) => name === "Agent" || name === "Task";

function toolEl(part) {
  const t = el("div", "tool");
  t.dataset.key = `${part.name}\u0000${part.summary || ""}`;
  t._n = 0; t._pending = new Set(); t._err = false;
  const agent = isAgentTool(part.name);
  t._agent = agent;
  t.append(el("span", "tn", agent ? "🤖 Subagente" : part.name), el("span", "ts", part.summary || ""), el("span", "cnt"), el("span", "st"));
  if (agent) {
    // o que o subagente está fazendo (quantas ações e a última)
    const sub = el("div", "sub", "começando…");
    t.append(sub);
    if (part.sub) setSub(t, part.sub.count, part.sub.last);
  }
  t.onclick = () => {
    const next = t.nextElementSibling;
    if (next && next.classList.contains("tool-out")) next.hidden = !next.hidden;
  };
  return t;
}

function setSub(t, count, last) {
  const sub = t.querySelector(".sub");
  if (!sub) return;
  sub.textContent = `↳ ${count} ${count === 1 ? "ação" : "ações"} · ${last || ""}`;
  sub.title = last || "";
}

function refreshTool(t) {
  if (t._agent) {
    const sub = t.querySelector(".sub");
    if (sub && !t._pending.size && sub.textContent === "começando…") sub.textContent = "↳ terminou";
  }
  t.className = "tool " + (t._pending.size ? "running" : t._err ? "err" : "ok");
  const cnt = t.querySelector(".cnt");
  cnt.textContent = t._n > 1 ? `×${t._n}` : "";
  t.title = t._n > 1 ? `${t._n} chamadas seguidas iguais` : "";
}

/* Acrescenta uma chamada de ferramenta em `container` (antes de `before`, ou no fim).
   Se a anterior for a mesma ferramenta com o mesmo alvo, só soma no contador. */
function addTool(container, before, part) {
  const key = `${part.name}\u0000${part.summary || ""}`;
  let prev = before ? before.previousElementSibling : container.lastElementChild;
  if (prev && prev.classList.contains("tool-out")) prev = prev.previousElementSibling;
  // subagentes nunca se juntam: cada um é uma frente de trabalho
  let t = !isAgentTool(part.name) && prev && prev.classList.contains("tool") && prev.dataset.key === key ? prev : null;
  if (!t) {
    t = toolEl(part);
    container.insertBefore(t, before || null);
  }
  t._n += 1;
  if (part.error === true) t._err = true;
  else if (part.error == null && part.id) t._pending.add(part.id);
  if (part.id) toolIndex.set(part.id, t);
  refreshTool(t);
  return t;
}

function assistantMsg(m, hl = true) {
  const wrap = el("div", "msg assistant");
  const body = el("div", "body");
  for (const part of m.parts || []) {
    if (part.type === "text") { const d = el("div", "md"); renderMarkdown(d, part.text, hl); body.append(d); }
    else if (part.type === "tool") addTool(body, null, { ...part, id: null, error: part.error ?? false });
  }
  wrap.append(body);
  if (m.error) wrap.append(el("div", "error-box", m.error));
  const foot = el("div", "time", fmtTime(m.time));
  if (m.cost) foot.title = `custo estimado: US$ ${m.cost.toFixed(3)}`;
  wrap.append(foot);
  return wrap;
}

function scrollBottom(force = false) {
  const c = $("#chat");
  const near = c.scrollHeight - c.scrollTop - c.clientHeight < 160;
  if (force || near) requestAnimationFrame(() => (c.scrollTop = c.scrollHeight));
}

function lightbox(src) {
  if (!src) return;
  const lb = el("div", "lightbox");
  const img = el("img"); img.src = src; lb.append(img);
  lb.onclick = () => lb.remove();
  document.body.append(lb);
}

/* Menu pop-up no estilo do .conv-menu. items: [{label, fn, cls}] ou "hr". Fecha com Esc / clique fora. */
function closePopMenu() { document.querySelector(".path-menu")?.remove(); }
function openPopMenu(items, x, y, anchorBottom = false) {
  closePopMenu();
  const menu = el("div", "menu path-menu");
  for (const it of items) {
    if (it === "hr") { menu.append(el("hr")); continue; }
    const b = el("button", it.cls || "", it.label);
    b.onclick = (e) => { e.stopPropagation(); closePopMenu(); it.fn(); };
    menu.append(b);
  }
  document.body.append(menu);
  const top = anchorBottom ? y - menu.offsetHeight : y;
  menu.style.top = Math.max(8, Math.min(top, window.innerHeight - menu.offsetHeight - 8)) + "px";
  menu.style.left = Math.max(8, Math.min(x, window.innerWidth - menu.offsetWidth - 8)) + "px";
  menu.querySelector("button")?.focus();
}
document.addEventListener("click", closePopMenu);
window.addEventListener("blur", closePopMenu);

/* ---------- resposta ao vivo ---------- */
function startLive() {
  const wrap = el("div", "msg assistant");
  const body = el("div", "body");
  // linha de status: diz o que o Claude está fazendo agora (e há quanto tempo)
  const typing = el("div", "live-line");
  typing.innerHTML = '<span class="typing"><i></i><i></i><i></i></span><span class="live-status"></span>';
  typing.onclick = () => { if (openPerms()) goToPermission(); };
  body.append(typing);
  wrap.append(body);
  $("#messages").append(wrap);
  $("#empty").hidden = true;
  const now = Date.now();
  // mensagem mandada no meio: a bolha nova herda o relógio e a ferramenta que ainda roda
  const c = liveCarry; liveCarry = null;
  state.live = { wrap, body, typing, textEl: null, text: "", raf: 0, since: c ? c.since : now, last: now, lastText: 0, tool: c ? c.tool : null, sub: c ? c.sub : null };
  startLiveTimer();
  updateLiveStatus();
  scrollBottom(true);
}

/* ---------- o que o Claude está fazendo (para ninguém achar que travou) ---------- */
let liveTimer = 0;
function startLiveTimer() {
  if (!liveTimer) liveTimer = setInterval(() => { if (state.live) updateLiveStatus(); else { clearInterval(liveTimer); liveTimer = 0; } }, 1000);
}
const fmtDur = (ms) => { const s = Math.max(0, Math.floor(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };

function updateLiveStatus() {
  const L = state.live; if (!L) return;
  const label = L.typing.querySelector(".live-status");
  const now = Date.now();
  let text, cls = "", title = "";
  const n = openPerms();
  if (n) {
    text = n > 1 ? `⏸ Parado esperando sua permissão (${n} pedidos) · clique para ver` : "⏸ Parado esperando sua permissão · clique para ver";
    cls = "warn";
    title = "O Claude não continua até você responder Permitir ou Negar no cartão";
  } else if (compacting[state.active]) {
    text = "Compactando o contexto… · " + fmtDur(now - L.last);
  } else if (L.tool) {
    const took = now - L.tool.since;
    const what = L.tool.summary ? `${L.tool.name}: ${L.tool.summary}` : L.tool.name;
    text = `Rodando ${what} · ${fmtDur(took)}`;
    if (L.sub) text += ` · ↳ ${L.sub}`;
    if (took > 45000 && L.tool.name !== "Agent") text += " (o Claude espera este comando terminar)";
    title = "Ferramenta em execução";
  } else if (now - L.lastText < 2500) {
    text = "Escrevendo…";
  } else {
    const idle = now - L.last;
    text = "Pensando… · " + fmtDur(idle);
    if (idle > 90000) { text += " (sem resposta do Claude há um tempo; Esc para parar)"; cls = "slow"; }
  }
  text += ` · total ${fmtDur(now - L.since)}`;
  label.textContent = text;
  label.title = title;
  L.typing.classList.toggle("warn", cls === "warn");
  L.typing.classList.toggle("slow", cls === "slow");
}

function goToPermission() {
  const card = [...document.querySelectorAll(".perm:not(.answered)")].pop();
  if (!card) return;
  card.scrollIntoView({ behavior: "smooth", block: "center" });
  card.classList.remove("flash"); void card.offsetWidth; card.classList.add("flash");
}

// aviso fixo acima da caixa de mensagem: não depende de rolar a conversa
// cartões sem resposta na tela (o que o usuário vê é o que vale)
const openPerms = () => (waiting[state.active] ? waiting[state.active].size : 0);

function updateWaitBar() {
  const n = openPerms();
  const bar = $("#wait-bar");
  if (!bar) return;
  bar.hidden = !n;
  if (n) bar.querySelector(".wt").textContent = n > 1
    ? `O Claude está parado esperando sua permissão (${n} pedidos).`
    : "O Claude está parado esperando sua permissão.";
}

// a resposta em andamento vira texto fixo (uma mensagem nova vai entrar embaixo dela)
let liveCarry = null;
function freezeLive() {
  const L = state.live;
  if (!L) return;
  liveCarry = { since: L.since, tool: L.tool, sub: L.sub };
  if (L.raf) { cancelAnimationFrame(L.raf); L.raf = 0; }
  if (L.textEl) renderMarkdown(L.textEl, L.text);  // trecho fechado: realça o código uma vez
  L.typing.remove();
  if (!L.body.children.length) L.wrap.remove();
  state.live = null;
}

function liveText(chunk) {
  const L = state.live; if (!L) return;
  if (!L.textEl) { L.textEl = el("div", "md"); L.body.insertBefore(L.textEl, L.typing); L.text = ""; }
  L.text += chunk;
  if (!L.raf) L.raf = requestAnimationFrame(() => { L.raf = 0; renderMarkdown(L.textEl, L.text, false); scrollBottom(); });
}

window.onAgentEvent = (ev) => {
  if (ev.type === "permission" || ev.type === "plan") (waiting[ev.conv] ||= new Set()).add(ev.id);
  if (ev.type === "permission_done") waiting[ev.conv]?.delete(ev.id);
  if (ev.type === "permission" || ev.type === "plan" || ev.type === "permission_done") { refreshConvRow(ev.conv); if (ev.conv === state.active) updateWaitBar(); }
  if (ev.type === "mode_changed") {
    const c = conv(ev.conv);
    if (c) c.permission_mode = ev.mode;
    if (ev.conv === state.active) setPermSelect(ev.mode);
    return;
  }
  if (ev.type === "status") {
    state.busy[ev.conv] = ev.busy;
    if (!ev.busy) {
      // turno acabou: nenhum pedido continua valendo
      waiting[ev.conv] = new Set();
      if (ev.conv === state.active) {
        document.querySelectorAll(".perm:not(.answered)").forEach((p) => { p.classList.add("answered"); p.querySelectorAll("button").forEach((b) => (b.disabled = true)); });
        updateWaitBar();
      }
    }
    const c = conv(ev.conv);
    if (c && !ev.busy) { c.updated = new Date().toISOString(); upsertConv(c); }
    refreshConvRow(ev.conv);
    if (ev.conv === state.active) {
      setBusyUi();
      if (!ev.busy) { loadConvUsage(); refreshFiles(true); } // ocioso: contexto do topo e arquivos que o Claude mexeu
    }
    return;
  }
  if (ev.type === "compacting") {
    compacting[ev.conv] = ev.on;
    if (ev.conv === state.active) { if (ev.on) showCompacting(ev.conv); else loadConvUsage(); }
    return;
  }
  if (ev.conv !== state.active) return;
  const L = state.live;
  if (L) {
    L.last = Date.now();
    if (ev.type === "text") { L.lastText = L.last; L.tool = null; L.sub = null; }
    else if (ev.type === "tool") { L.tool = { id: ev.id, name: ev.name, summary: ev.summary, since: L.last }; L.sub = null; }
    else if (ev.type === "tool_end" && L.tool && L.tool.id === ev.id) { L.tool = null; L.sub = null; }
    else if (ev.type === "subtool" && L.tool && L.tool.id === ev.parent) L.sub = `${ev.count} ações · ${ev.last || ""}`;
  }
  switch (ev.type) {
    case "text": liveText(ev.text); break;
    case "tool":
      if (!L) break;
      L.textEl = null;
      addTool(L.body, L.typing, { id: ev.id, name: ev.name, summary: ev.summary, error: null });
      scrollBottom();
      break;
    case "tool_end": {
      // a ferramenta pode ter começado antes de uma mensagem mandada no meio: busca pelo id
      const t = toolIndex.get(ev.id);
      if (t && t.isConnected) {
        t._pending.delete(ev.id);
        if (ev.error) t._err = true;
        refreshTool(t);
        if (ev.output) {
          let o = t.nextElementSibling;
          if (!o || !o.classList.contains("tool-out")) { o = el("pre", "tool-out"); o.hidden = true; t.after(o); }
          o.textContent = ev.output;
          if (ev.error) o.hidden = false;
        }
      }
      toolIndex.delete(ev.id);
      break;
    }
    case "permission":
    case "plan": {
      if (!L) startLive();
      const card = ev.type === "plan" ? planCard(ev) : permissionCard(ev);
      state.live.body.insertBefore(card, state.live.typing);
      scrollBottom(true);
      if (ev.type === "plan") card.focus({ preventScroll: true });
      break;
    }
    case "permission_done": {
      const card = document.querySelector(`.perm[data-id="${CSS.escape(ev.id)}"]`);
      if (card) { card.classList.add("answered"); card.querySelectorAll("button, textarea").forEach((b) => (b.disabled = true)); }
      break;
    }
    case "subtool": {
      const t = toolIndex.get(ev.parent);
      if (t && t.isConnected) setSub(t, ev.count, ev.last);
      break;
    }
    case "divider": {
      const d = dividerEl(ev);
      if (L) L.wrap.before(d); else $("#messages").append(d);
      break;
    }
    case "turn_start": {
      // o Claude começou um turno que a tela ainda não esperava (ex.: resposta a uma
      // mensagem mandada no meio, depois que o turno anterior acabou)
      if (!state.live) startLive();
      break;
    }
    case "done": {
      if (L) {
        const final = assistantMsg({ parts: ev.parts, error: ev.error, cost: ev.cost, time: new Date().toISOString() });
        // só cartões ainda sem resposta passam para a mensagem final; os respondidos
        // ficariam no fim da resposta e pareceriam um pedido novo
        L.body.querySelectorAll(".perm:not(.answered)").forEach((p) => final.querySelector(".body").append(p));
        L.wrap.replaceWith(final);
      }
      state.live = null;
      scrollBottom();
      loadConvUsage();
      break;
    }
  }
  updateLiveStatus();
  if (ev.type === "permission" || ev.type === "plan" || ev.type === "permission_done") updateWaitBar();
};

/* Cartão do plano (ExitPlanMode): plano em markdown + 3 opções (atalhos 1, 2, 3).
   Não tem "sempre permitir" e Esc / clique fora não fecham. */
function planCard(ev) {
  const card = el("div", "perm plan-card");
  card.dataset.id = ev.id;
  card.tabIndex = -1;
  card.setAttribute("role", "group");
  card.append(el("div", "pt", "📋 " + T.planTitle));
  const body = el("div", "plan-body md");
  renderMarkdown(body, ev.plan || "");
  card.append(body);
  const convId = ev.conv || state.active;
  const answer = (ans, message = "") => api().answer_permission(convId, ev.id, ans, message);
  const opts = el("div", "opts");
  const mk = (n, label, fn) => {
    const b = el("button", "opt");
    b.dataset.n = String(n);
    b.append(el("kbd", "", String(n)), el("span", "", label));
    b.onclick = fn;
    opts.append(b);
    return b;
  };
  mk(1, T.planOpt1, () => answer("accept_edits"));
  mk(2, T.planOpt2, () => answer("default"));
  const deny = el("div", "deny-box");
  deny.hidden = true;
  const ta = el("textarea");
  ta.placeholder = T.planAsk;
  ta.setAttribute("aria-label", T.planAsk);
  const row = el("div", "actions");
  const sendBtn = el("button", "btn primary", T.planSend);
  const backBtn = el("button", "btn", T.planBack);
  const submitDeny = () => { const t = ta.value.trim(); answer("deny", t ? T.denyWith + t : T.denyDefault); };
  sendBtn.onclick = submitDeny;
  backBtn.onclick = () => { deny.hidden = true; opts.hidden = false; card.focus({ preventScroll: true }); };
  ta.onkeydown = (e) => {
    e.stopPropagation();
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submitDeny(); }
    if (e.key === "Escape") { e.preventDefault(); ta.blur(); card.focus({ preventScroll: true }); }
  };
  row.append(sendBtn, backBtn);
  deny.append(ta, row);
  mk(3, T.planOpt3, () => { opts.hidden = true; deny.hidden = false; ta.focus(); });
  card.append(opts, deny);
  return card;
}

// atalhos 1/2/3 do cartão do plano (só quando não se está digitando em outro campo)
function planShortcut(e) {
  if (!/^[123]$/.test(e.key) || e.ctrlKey || e.altKey || e.metaKey || e.shiftKey) return false;
  const card = [...document.querySelectorAll("#messages .plan-card:not(.answered)")].pop();
  if (!card || card.querySelector(".opts").hidden) return false;
  const ae = document.activeElement;
  const typing = ae && (ae.tagName === "TEXTAREA" || ae.tagName === "INPUT" || ae.isContentEditable);
  if (typing && !(ae.id === "prompt" && !ae.value)) return false;
  e.preventDefault();
  card.querySelector(`.opt[data-n="${e.key}"]`)?.click();
  return true;
}

function permissionCard(ev) {
  if (ev.type === "plan" || ev.plan != null || ev.tool === "ExitPlanMode") return planCard(ev);
  const card = el("div", "perm");
  card.dataset.id = ev.id;
  card.append(el("div", "pt", "🔐 " + (ev.title || `Claude quer usar ${ev.tool}`)));
  const why = [];
  if (ev.reason) why.push("Motivo: " + ev.reason);
  if (ev.blocked_path) why.push("Caminho: " + ev.blocked_path);
  if (ev.mode === "bypassPermissions") why.push("Mesmo em “liberar tudo”, o Claude Code ainda pede confirmação nas checagens de segurança dele.");
  if (why.length) card.append(el("div", "why", why.join(" · ")));
  if (ev.detail) card.append(el("code", "", ev.detail));
  const actions = el("div", "actions");
  const btn = (label, answer, cls) => {
    const b = el("button", "btn " + (cls || ""), label);
    b.onclick = () => api().answer_permission(state.active, ev.id, answer);
    return b;
  };
  actions.append(btn("Permitir", "allow", "primary"));
  if (ev.can_always) actions.append(btn("Sempre permitir", "always"));
  actions.append(btn("Negar", "deny"));
  card.append(actions);
  return card;
}

function setBusyUi() {
  const busy = !!state.busy[state.active];
  // ocupado e caixa vazia: o botão vira "parar"; com texto, envia para a fila
  const hasContent = $("#prompt").value.trim() || state.pending.length;
  const stopMode = busy && !hasContent;
  $("#app").classList.toggle("busy", stopMode);
  $("#btn-send").title = stopMode ? "Parar (Esc ou Ctrl+C)" : busy ? "Enviar para a fila" : "Enviar";
}

/* ---------- envio ---------- */
/* Comandos de tela do terminal (/login, /config...) não rodam pelo SDK: lá eles são
   telas interativas do terminal. Aqui viram os recursos equivalentes do app. */
const LOCAL_COMMANDS = {
  login: () => { openSettings(); startLogin(); },
  logout: () => { openSettings(); logout(); },
  // /config é a tela de configurações do Claude Code (não do app): abre no terminal
  config: () => { api().open_terminal(state.active, "/config"); toast("Abrindo o /config do Claude Code no terminal"); },
  usage: () => openSettings(),
  cost: () => openSettings(),
  status: () => openSettings(),
  clear: () => clearConversation(),
  new: () => openNewModal(),
  resume: () => { toggleSidebar(true); $("#conv-search").focus(); },
  permissions: () => { $("#perm-mode").focus(); toast("Escolha o modo de permissões no topo"); },
  terminal: () => api().open_terminal(state.active),
  exit: () => toast("Para fechar, use o X da janela"),
  quit: () => toast("Para fechar, use o X da janela"),
  model: (arg) => {
    const m = /^(?:sub|subagent|subagentes?)\s*(.*)$/i.exec(arg || "");
    if (m) { setSubModel(m[1].trim()); return; }
    if (!arg) { openModelPicker(); return; }
    setMainModel(arg);
  },
};

/* Modelos: o principal é por conversa (set_model); o dos subagentes é uma preferência
   global (subagent_model: "auto" = um degrau abaixo do principal). */
let MODEL_CHOICES = [
  ["default", "Padrão do Claude Code"],
  ["fable", "Fable"],
  ["opus", "Opus"],
  ["sonnet", "Sonnet"],
  ["haiku", "Haiku"],
];
const modelLabel = (v) => (MODEL_CHOICES.find(([k]) => k === v) || [v, v])[1];

async function setMainModel(arg) {
  const id = state.active;
  const r = await api().set_model(id, arg);
  if (!r.ok) { toast("Não consegui trocar o modelo: " + r.error, "error"); return false; }
  if (conv(id)) conv(id).model = r.model;
  if (id === state.active) syncModelSelect();
  toast("Modelo desta conversa: " + (r.model || "padrão do Claude Code") + " (vale a partir da próxima mensagem)");
  return true;
}

async function setSubModel(arg) {
  const value = !arg || /^(auto|autom[aá]tico)$/i.test(arg) ? "auto" : arg.toLowerCase();
  const np = await api().set_pref("subagent_model", value);
  if (np && np.ok === false) return;
  state.prefs = np;
  syncModelSelect();
  toast("Modelo dos subagentes: " + (value === "auto" ? "automático (um degrau abaixo do principal)" : value) + " (vale a partir da próxima mensagem)");
}

function openModelPicker() {
  const cur = conv()?.model || "default";
  const sub = state.prefs?.subagent_model || "auto";
  const mark = (on) => (on ? "● " : "   ");
  const items = [{ label: "Modelo desta conversa", fn: () => {}, cls: "danger-off" }];
  for (const [k, label] of MODEL_CHOICES) items.push({ label: mark(k === cur) + label, fn: () => setMainModel(k) });
  items.push("hr", { label: "Modelo dos subagentes (/model sub <modelo>)", fn: () => {} });
  items.push({ label: mark(sub === "auto") + "Automático (um degrau abaixo)", fn: () => setSubModel("auto") });
  for (const [k, label] of MODEL_CHOICES.slice(1)) items.push({ label: mark(k === sub) + label, fn: () => setSubModel(k) });
  const r = $("#prompt").getBoundingClientRect();
  openPopMenu(items, r.left + 8, r.top - 6, true);
}

// seletores da tela de configurações
function syncModelSelect() {
  const fill = (sel, value, choices) => {
    if (!sel) return;
    if (value && ![...sel.options].some((o) => o.value === value)) sel.append(new Option(value, value));
    sel.value = value;
  };
  fill($("#pref-main-model"), conv()?.model || "default");
  fill($("#pref-sub-model"), state.prefs?.subagent_model || "auto");
}

function initModelSelects() {
  const main = $("#pref-main-model");
  main.innerHTML = "";
  for (const [k, label] of MODEL_CHOICES) main.append(new Option(label, k));
  main.onchange = () => setMainModel(main.value);
  const sub = $("#pref-sub-model");
  sub.innerHTML = "";
  sub.append(new Option("Automático (um degrau abaixo do principal)", "auto"));
  for (const [k, label] of MODEL_CHOICES.slice(1)) sub.append(new Option(label, k));
  sub.onchange = () => setSubModel(sub.value);
}

/* /clear: zera o contexto do Claude (sessão nova) sem criar outra conversa. A tela
   continua com o histórico, marcado por uma linha. Pasta, modo e modelo continuam. */
async function clearConversation() {
  const id = state.active;
  const msg = "Limpar o contexto do Claude nesta conversa?\n\nO Claude começa do zero e não lembra de nada do que foi dito até aqui."
    + "\nNo app você continua vendo e consultando o histórico." + (state.busy[id] ? "\n\nO trabalho em andamento será interrompido." : "");
  if (!confirm(msg)) return;
  const r = await api().clear_conversation(id);
  if (!r.ok) { toast("Não consegui limpar: " + r.error, "error"); return; }
  Object.assign(conv(id), r.conversation);
  if (id === state.active) setShellChip(null);
  state.busy[id] = false;
  compacting[id] = false;
  await selectConv(id);
  toast("Contexto limpo: o Claude começa do zero");
}

function runLocalCommand(text) {
  const m = /^\/([\w:.\-]+)\s*([\s\S]*)$/.exec(text);
  if (!m) return false;
  const name = m[1].toLowerCase(), arg = m[2].trim();
  if (LOCAL_COMMANDS[name]) { LOCAL_COMMANDS[name](arg); return true; }
  // outros comandos que só existem como tela do terminal
  const list = state.commands[state.active];
  const info = Array.isArray(list) && list.find((c) => c.name.toLowerCase() === name);
  if (info && info.terminal) {
    // comando de tela que o app não conhece (inclusive novos do Claude Code):
    // abre o terminal na mesma sessão já rodando o comando
    api().open_terminal(state.active, text);
    toast(`/${name} é uma tela do terminal: abrindo no terminal`);
    return true;
  }
  return false;
}

function stopTurn() {
  if (state.busy[state.active]) { api().stop(state.active); toast("Parando…"); }
}

async function send(textOverride) {
  const input = $("#prompt");
  const text = (textOverride ?? input.value).trim();
  const convId = state.active;  // a conversa pode mudar enquanto esperamos o Python
  const busy = !!state.busy[convId];
  if (!text && !state.pending.length) { if (busy) stopTurn(); return; }
  closeSlash();
  if (!state.pending.length && text.startsWith("!") && text.length > 1) {
    input.value = ""; autoGrow(); setBusyUi();
    state.recall.push(text); state.recallPos = -1;
    runShell(text.slice(1).trim());
    return;
  }
  if (!state.pending.length && text.startsWith("/") && runLocalCommand(text)) {
    input.value = ""; autoGrow(); setBusyUi();
    state.recall.push(text); state.recallPos = -1;
    return;
  }
  const attachments = state.pending.map((a) => ({ name: a.name, data: a.data }));
  const preview = state.pending.map((a) => ({ name: a.name, kind: a.isImage ? "image" : "file", dataUrl: a.isImage ? a.data : null }));
  input.value = ""; autoGrow();
  state.pending = []; renderChips();
  if (text) { state.recall.push(text); }
  state.recallPos = -1;
  // com o Claude trabalhando, a mensagem vai na hora (como no terminal): o que ele já
  // respondeu fica acima, a sua mensagem entra embaixo e a resposta continua depois
  if (busy) freezeLive();
  const bubble = userMsg({ text, attachments: preview, time: new Date().toISOString() });
  $("#messages").append(bubble);
  $("#empty").hidden = true;
  state.busy[convId] = true;
  startLive();
  setBusyUi();
  scrollBottom(true);
  setShellChip(null);  // a saída do shell vai junto desta mensagem
  if (attachments.some((a) => /\.(mp3|m4a|aac|ogg|oga|opus|wav|flac|wma|amr|weba|mp4|mkv|mov|avi|wmv|webm|m4v|3gp|flv|mpe?g|ts)$/i.test(a.name))) {
    toast("Transcrevendo áudio/vídeo no PC (sem gastar tokens)…");
  }
  const r = await api().send(convId, text, attachments);
  if (!r.ok) {
    toast(r.error, "error");
    if (!busy) state.busy[convId] = false;
    if (convId === state.active) {
      bubble.remove();
      if (state.live) { state.live.wrap.remove(); state.live = null; }
      setBusyUi();
      refreshShellChip();
    }
    return;
  }
  if (r.conversation) { upsertConv(r.conversation); if (convId === state.active) renderHeader(); refreshConvRow(convId); }
  // estimativa de tokens dos anexos (volta do Python)
  if (convId === state.active && Array.isArray(r.attachments)) {
    bubble.querySelectorAll(".file-pill").forEach((p, i) => {
      const est = r.attachments[i] && r.attachments[i].tokens_est;
      if (est && !p.textContent.includes("tokens")) p.textContent += ` · ~${nf(est)} tokens`;
    });
  }
}

/* ---------- anexos ---------- */
const IMAGE_RE = /\.(png|jpe?g|gif|webp|bmp)$/i;
const MAX_FILE = 30 * 1024 * 1024;
const TEXT_RE = /\.(txt|md|markdown|csv|tsv|json|jsonl|xml|html?|css|jsx?|tsx?|py|ps1|sh|bat|cmd|ya?ml|toml|ini|cfg|log|sql|java|cs|cpp|c|h|hpp|go|rs|rb|php|kt|swift|vue|svg)$/i;

function addFile(file, name) {
  if (!file) return;
  if (file.size > MAX_FILE) { toast(`${file.name} passa de 30 MB`, "error"); return; }
  const fname = name || file.name || "arquivo";
  const isImage = (file.type || "").startsWith("image/") || IMAGE_RE.test(fname);
  const reader = new FileReader();
  reader.onload = () => {
    // estimativa (~4 bytes por token) só para texto puro; PDF/Office/mídia são convertidos no Python
    const textLike = !isImage && (/^text\//.test(file.type || "") || TEXT_RE.test(fname));
    state.pending.push({ name: fname, data: reader.result, isImage, tokens: textLike ? Math.ceil(file.size / 4) : 0 });
    renderChips();
  };
  reader.readAsDataURL(file);
}

function renderChips() {
  const box = $("#chips");
  box.innerHTML = "";
  state.pending.forEach((a, i) => {
    const c = el("div", "chip");
    if (a.isImage) { const img = el("img"); img.src = a.data; img.onclick = () => lightbox(a.data); c.append(img); }
    else c.append(el("div", "ic", (a.name.split(".").pop() || "?").slice(0, 4)));
    c.append(el("span", "cn", a.name));
    if (a.tokens) { const tk = el("span", "tk", `~${nf(a.tokens)} tokens`); tk.title = "Estimativa do que este anexo gasta"; c.append(tk); }
    const rm = el("button", "rm", "✕"); rm.title = "Remover"; rm.setAttribute("aria-label", "Remover anexo");
    rm.onclick = () => { state.pending.splice(i, 1); renderChips(); };
    c.append(rm);
    box.append(c);
  });
  setBusyUi();
}

/* ---------- /comandos e skills ---------- */
async function loadCommands(convId) {
  const cached = state.commands[convId];
  if (cached === "loading") return [];
  if (cached && !(state.commandsRetry[convId] && Date.now() > state.commandsRetry[convId])) return cached;
  state.commands[convId] = "loading";
  let list = [];
  try {
    const r = await api().list_commands(convId);
    list = r.ok ? r.commands : [];
    if (!r.ok) { state.commandsRetry[convId] = Date.now() + 15000; toast("Não consegui listar os comandos: " + r.error, "error"); }
    else delete state.commandsRetry[convId];
  } catch (e) {
    state.commandsRetry[convId] = Date.now() + 15000;
  }
  const LOCAL_DESC = {
    login: "Entrar na conta Claude Code", logout: "Sair da conta", config: "Configurações do Claude Code (terminal)",
    usage: "Uso do plano e tokens", clear: "Limpar a conversa (contexto do zero)", resume: "Conversas anteriores",
    model: "Trocar o modelo desta conversa", permissions: "Modo de permissões", terminal: "Continuar no terminal",
  };
  for (const [name, desc] of Object.entries(LOCAL_DESC)) {
    if (!list.some((c) => c.name.toLowerCase() === name)) list.push({ name, description: desc, hint: name === "model" ? "<modelo> | sub <modelo|auto>" : "", kind: "app" });
  }
  state.commands[convId] = list;
  return list;
}

function slashQuery() {
  // só enquanto digita o nome do comando: "/nom" (sem espaço ainda)
  const v = $("#prompt").value;
  const m = /^\/([\w:.\-]*)$/.exec(v);
  return m ? m[1].toLowerCase() : null;
}

async function updateSlash() {
  if ($("#prompt").value.startsWith("!")) { scheduleShellSuggest(); return; }
  const q = slashQuery();
  if (q === null) { closeSlash(); return; }
  const convId = state.active;
  let list = state.commands[convId];
  const expired = state.commandsRetry[convId] && Date.now() > state.commandsRetry[convId];
  if (!list || list === "loading" || expired) {
    renderSlash([], "carregando comandos…");
    if (list !== "loading") {
      try { await loadCommands(convId); } catch (_) {}
      if (convId === state.active) updateSlash();
    }
    return;
  }
  const items = list
    .filter((c) => c.name.toLowerCase().includes(q))
    .sort((a, b) => (b.name.toLowerCase().startsWith(q) - a.name.toLowerCase().startsWith(q)) || a.name.localeCompare(b.name));
  state.slash.sel = Math.min(state.slash.sel, Math.max(items.length - 1, 0));
  renderSlash(items, items.length ? "" : "Nenhum comando com esse nome");
}

function renderSlash(items, message) {
  const box = $("#slash");
  state.slash.items = items;
  state.slash.mode = "slash";
  state.slash.open = true;
  box.hidden = false;
  box.innerHTML = "";
  if (message) { box.append(el("div", "empty-line", message)); return; }
  items.slice(0, 80).forEach((c, i) => {
    const row = el("div", "item" + (i === state.slash.sel ? " sel" : "") + (c.terminal ? " off" : ""));
    row.append(el("span", "cmd", "/" + c.name));
    if (c.hint) row.append(el("span", "hint", c.hint));
    const local = LOCAL_COMMANDS[c.name.toLowerCase()];
    if (local) row.classList.remove("off");
    row.append(el("span", "desc", local ? (c.description || "") + (c.name.toLowerCase() === "config" ? "" : " · abre no app") : c.terminal ? (c.description || "") + " · abre no terminal" : c.description || ""));
    if (c.kind === "skill") row.append(el("span", "tag", "skill"));
    row.onmousedown = (e) => { e.preventDefault(); pickSlash(i); };
    box.append(row);
  });
  box.querySelector(".sel")?.scrollIntoView({ block: "nearest" });
}

function pickSlash(i) {
  if (state.slash.mode === "shell") { pickShell(i); return; }
  const c = state.slash.items[i];
  if (!c) return;
  const input = $("#prompt");
  input.value = "/" + c.name + " ";
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
  closeSlash();
  autoGrow(); setBusyUi();
}

function closeSlash() {
  state.slash.open = false;
  state.slash.sel = 0;
  state.slash.moved = false;
  state.shellSeq++;  // descarta sugestões do "!" que ainda estejam a caminho
  clearTimeout(state.shellTimer);
  $("#slash").hidden = true;
}

/* ---------- sugestões do "!comando" (PowerShell + histórico) ---------- */
const SHELL_KIND = {
  Command: "comando", Parameter: "parâmetro", ParameterValue: "valor", ProviderContainer: "pasta",
  ProviderItem: "arquivo", Variable: "variável", Property: "propriedade", Method: "método", Type: "tipo",
};

function scheduleShellSuggest() {
  if (!state.shellWarm) { state.shellWarm = true; api().shell_warm(); }
  clearTimeout(state.shellTimer);
  state.shellTimer = setTimeout(updateShellSuggest, 120);
}

async function updateShellSuggest() {
  const input = $("#prompt");
  const value = input.value;
  const cmd = value.slice(1);
  if (!value.startsWith("!") || !cmd.trim() || value.includes("\n")) { closeSlash(); return; }
  const col = Math.max(0, (input.selectionStart ?? value.length) - 1);
  const seq = ++state.shellSeq;
  const convId = state.active;
  const r = await api().shell_complete(convId, cmd, col);
  if (seq !== state.shellSeq || convId !== state.active || input.value !== value) return;
  const items = [];
  for (const h of r.history || []) items.push({ hist: true, text: h, label: h, desc: "histórico" });
  for (const c of r.items || []) {
    if (c.t === cmd.slice(r.start, r.start + r.length) && (r.items || []).length === 1) continue; // já completo
    items.push({ text: c.t, label: c.l || c.t, kind: c.k, desc: c.d && c.d !== c.t && c.d !== c.l ? c.d : "", start: r.start, length: r.length });
  }
  if (!items.length) { closeSlash(); return; }
  if (state.slash.mode !== "shell" || !state.slash.open) { state.slash.sel = 0; state.slash.moved = false; }
  state.slash.sel = Math.min(state.slash.sel, items.length - 1);
  renderShell(items);
}

function renderShell(items) {
  const box = $("#slash");
  state.slash.items = items;
  state.slash.mode = "shell";
  state.slash.open = true;
  box.hidden = false;
  box.innerHTML = "";
  items.forEach((c, i) => {
    const row = el("div", "item" + (i === state.slash.sel ? " sel" : ""));
    row.append(el("span", "cmd", c.label));
    row.append(el("span", "desc", c.desc || ""));
    row.append(el("span", "tag", c.hist ? "histórico" : SHELL_KIND[c.kind] || c.kind || ""));
    row.onmousedown = (e) => { e.preventDefault(); pickShell(i); };
    box.append(row);
  });
  box.append(el("div", "empty-line", "Tab completa · Enter roda o comando · Esc fecha"));
  box.querySelector(".sel")?.scrollIntoView({ block: "nearest" });
}

function pickShell(i) {
  const c = state.slash.items[i];
  if (!c) return;
  const input = $("#prompt");
  let caret;
  if (c.hist) {
    input.value = "!" + c.text;
    caret = input.value.length;
  } else {
    const cmd = input.value.slice(1);
    const tail = cmd.slice(c.start + c.length);
    let text = c.text;
    let back = 0;  // pasta entre aspas: o cursor fica antes da aspa final
    if (c.kind === "ProviderContainer") {
      const sep = text.includes("/") && !text.includes("\\") ? "/" : "\\";
      const q = /['"]$/.test(text) ? text.slice(-1) : "";
      text = q ? text.slice(0, -1) + sep + q : text + sep;
      back = q ? 1 : 0;
    }
    const space = c.kind === "ProviderContainer" || tail.startsWith(" ") ? "" : " ";
    input.value = "!" + cmd.slice(0, c.start) + text + space + tail;
    caret = 1 + c.start + text.length + space.length - back;
  }
  input.focus();
  input.setSelectionRange(caret, caret);
  closeSlash();
  autoGrow(); setBusyUi();
  // pasta: já mostra o que tem dentro, como o Tab do terminal
  if (!c.hist && c.kind === "ProviderContainer") scheduleShellSuggest();
}

/* ---------- seta ↑/↓: mensagens anteriores ---------- */
function recall(dir) {
  const input = $("#prompt");
  const list = state.recall;
  if (!list.length) return false;
  if (state.recallPos === -1) {
    if (dir > 0) return false;
    state.draft = input.value;
    state.recallPos = list.length - 1;
  } else {
    state.recallPos += dir;
    if (state.recallPos < 0) state.recallPos = 0;
    if (state.recallPos >= list.length) { state.recallPos = -1; input.value = state.draft; autoGrow(); setBusyUi(); return true; }
  }
  input.value = list[state.recallPos];
  autoGrow(); setBusyUi();
  input.setSelectionRange(input.value.length, input.value.length);
  return true;
}

function caretOnFirstLine(t) { return !t.value.slice(0, t.selectionStart).includes("\n"); }
function caretOnLastLine(t) { return !t.value.slice(t.selectionEnd).includes("\n"); }

function onPaste(e) {
  if (!$("#new-modal").hidden) return;
  const items = (e.clipboardData || {}).items || [];
  let used = false;
  for (const item of items) {
    if (item.kind === "file") {
      const file = item.getAsFile();
      if (!file) continue;
      used = true;
      const d = new Date(), pad = (n) => String(n).padStart(2, "0");
      const stamp = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}_${pad(d.getHours())}-${pad(d.getMinutes())}-${pad(d.getSeconds())}`;
      const isImg = (file.type || "").startsWith("image/");
      addFile(file, isImg && (!file.name || file.name === "image.png") ? `print_${stamp}.png` : file.name);
    }
  }
  if (used) { e.preventDefault(); toast("Anexo colado"); }
}

/* ---------- explorador de arquivos (painel ao lado do chat) ---------- */
const files = {
  open: false,
  conv: null,          // conversa cuja pasta está na árvore
  expanded: new Set(), // pastas abertas (caminho relativo; "" = raiz)
  current: null,       // arquivo aberto no visualizador (resposta do read_file)
  showCode: false,     // .md: ver o código em vez de formatado
  width: 0, treeWidth: 220, prefsLoaded: false, hasCode: false,
};
const FOLDER_SVG = '<svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
const FILE_SVG = '<svg viewBox="0 0 24 24"><path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5"/></svg>';
const HL_LANG = {
  js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript",
  py: "python", pyw: "python", ps1: "powershell", psm1: "powershell", sh: "bash", bat: "dos", cmd: "dos",
  md: "markdown", markdown: "markdown", yml: "yaml", toml: "ini", cfg: "ini", iss: "ini", spec: "python",
  html: "xml", htm: "xml", svg: "xml", vue: "xml", cs: "csharp", h: "c", hpp: "cpp", kt: "kotlin", rs: "rust",
};

function fmtSize(n) {
  if (n < 1024) return n + " B";
  if (n < 1048576) return (n / 1024).toFixed(n < 10240 ? 1 : 0) + " KB";
  return (n / 1048576).toFixed(1) + " MB";
}

function filesOverlay() { return files.open && window.innerWidth < 900; }

function toggleFiles() {
  files.open ? closeFiles() : openFiles();
  if (!filesOverlay()) api().set_pref("files_open", files.open);
}

async function openFiles() {
  if (!files.prefsLoaded) {
    files.prefsLoaded = true;
    const p = await api().get_prefs();
    files.width = p.files_width || 0;
    files.treeWidth = p.files_tree_width || 220;
    files.hasCode = await api().has_vscode();
  }
  files.open = true;
  $("#files").hidden = false;
  $("#files-resizer").hidden = false;
  $("#btn-docs").classList.add("on");
  applyFilesWidth();
  if (files.conv !== state.active || !$("#tree").childElementCount) filesFollowConv(true);
}

function closeFiles() {
  files.open = false;
  $("#files").hidden = true;
  $("#files-resizer").hidden = true;
  $("#btn-docs").classList.remove("on");
}

function applyFilesWidth() {
  const box = $("#files");
  const total = $("#app").clientWidth - ($("#sidebar").offsetWidth || 0);
  const w = files.width || Math.round(total * 0.5);
  box.style.width = window.innerWidth < 900 ? "" : Math.max(300, Math.min(w, total - 340)) + "px";
  $("#tree").style.width = files.treeWidth + "px";
  box.classList.toggle("compact", box.offsetWidth < 560);
}

// Trocou de conversa: a árvore passa a mostrar a pasta dela.
function filesFollowConv(force = false) {
  if (!files.open) { files.conv = null; return; }
  const c = conv();
  const prev = conv(files.conv);
  if (!force && prev && c && (prev.path || "").toLowerCase() === (c.path || "").toLowerCase()) { files.conv = state.active; return; }
  files.conv = state.active;
  files.expanded = new Set([""]);
  files.current = null;
  $("#files-title").textContent = c ? folderName(c.path) : "Arquivos";
  $("#files-title").title = c?.path || "";
  clearViewer();
  renderTree();
}

let treeSeq = 0;
async function renderTree() {
  const tree = $("#tree");
  const scroll = tree.scrollTop;
  const ctx = { conv: files.conv || state.active, seq: ++treeSeq };
  const frag = document.createDocumentFragment();
  await renderDir(frag, "", 0, ctx);
  // outra renderização (ou outra conversa) começou enquanto esta esperava: descarta
  if (ctx.seq !== treeSeq || ctx.conv !== (files.conv || state.active)) return;
  const focused = document.activeElement?.closest?.("#tree .node")?.dataset.path;
  tree.innerHTML = "";
  tree.append(frag);
  tree.scrollTop = scroll;
  markCurrentInTree();
  if (focused != null) [...tree.querySelectorAll(".node")].find((n) => n.dataset.path === focused)?.focus({ preventScroll: true });
}

async function renderDir(parent, rel, depth, ctx) {
  const r = await api().list_dir(ctx.conv, rel);
  if (ctx.seq !== treeSeq) return;
  if (!r.ok) { parent.append(el("div", "hint", r.error)); return; }
  if (!r.entries.length && depth === 0) parent.append(el("div", "hint", "Pasta vazia."));
  const pending = [];  // pastas abertas são lidas em paralelo
  for (const e of r.entries) {
    const path = rel ? rel + "/" + e.name : e.name;
    const row = el("div", "node" + (e.dir ? " dir" : ""));
    row.style.paddingLeft = 6 + depth * 14 + "px";
    row.dataset.path = path;
    row.dataset.dir = e.dir ? "1" : "";
    row.tabIndex = 0;
    row.title = e.dir ? path : `${path} · ${fmtSize(e.size)}`;
    const open = e.dir && files.expanded.has(path);
    row.append(el("span", "caret", e.dir ? (open ? "▾" : "▸") : ""));
    const icon = el("span", "ic"); icon.innerHTML = e.dir ? FOLDER_SVG : FILE_SVG; row.append(icon);
    row.append(el("span", "nm", e.name));
    if (e.name.startsWith(".")) row.classList.add("dim");
    row.onclick = () => (e.dir ? toggleDir(path) : openFile(path));
    row.onkeydown = (ev) => {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); row.click(); }
      else if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        ev.preventDefault();
        const all = [...$("#tree").querySelectorAll(".node")];
        all[all.indexOf(row) + (ev.key === "ArrowDown" ? 1 : -1)]?.focus();
      } else if (e.dir && ev.key === "ArrowRight" && !files.expanded.has(path)) toggleDir(path);
      else if (e.dir && ev.key === "ArrowLeft" && files.expanded.has(path)) toggleDir(path);
      else if (ev.key === "ContextMenu" || (ev.shiftKey && ev.key === "F10")) {
        ev.preventDefault();
        const b = row.getBoundingClientRect();
        openPathMenu(path, b.left + 24, b.bottom);
      }
    };
    row.oncontextmenu = (ev) => { ev.preventDefault(); openPathMenu(path, ev.clientX, ev.clientY); };
    parent.append(row);
    if (open) {
      const kids = el("div", "kids");
      parent.append(kids);
      pending.push(renderDir(kids, path, depth + 1, ctx));
    }
  }
  if (r.more) parent.append(el("div", "hint", `… e mais ${r.more} itens`));
  await Promise.all(pending);
}

/* ---------- menu de caminho (clique direito na árvore / botão do visualizador) ---------- */
async function copyPath(rel, absolute) {
  const id = state.active;
  let text = rel.replaceAll("\\", "/");
  if (absolute) {
    const r = await api().get_abs_path(id, rel);
    text = typeof r === "string" ? r : r && r.ok !== false ? (r.path || r.abs || "") : "";
    if (!text) { toast("Não consegui obter o caminho", "error"); return; }
  }
  if (await copyText(text)) toast("Caminho copiado"); else toast("Não consegui copiar", "error");
}

function openPathMenu(rel, x, y) {
  if (!rel) return;
  const id = state.active;
  openPopMenu([
    { label: "Copiar caminho", fn: () => copyPath(rel, true) },
    { label: "Copiar caminho relativo", fn: () => copyPath(rel, false) },
    { label: `Mostrar no ${FM_NAME}`, fn: async () => { const r = await api().reveal_path(id, rel); if (r && r.ok === false) toast(r.error || "Não consegui abrir", "error"); } },
  ], x, y);
}

async function toggleDir(path) {
  if (files.expanded.has(path)) files.expanded.delete(path); else files.expanded.add(path);
  await renderTree();
}

function markCurrentInTree() {
  $("#tree").querySelectorAll(".node.sel").forEach((n) => n.classList.remove("sel"));
  if (!files.current?.path) return;
  const node = [...$("#tree").querySelectorAll(".node")].find((n) => n.dataset.path === files.current.path.replaceAll("\\", "/"));
  node?.classList.add("sel");
}

let openSeq = 0;
async function openFile(path) {
  const id = state.active, seq = ++openSeq;
  const r = await api().read_file(id, path);
  if (seq !== openSeq || id !== state.active) return;  // outro arquivo/conversa foi aberto no meio
  if (!r.ok) { toast(r.error, "error"); return; }
  files.showCode = false;
  showFile(r);
}

function clearViewer() {
  $("#viewer-bar").hidden = true;
  $("#viewer-body").innerHTML = '<div class="hint">Escolha um arquivo na árvore.</div>';
  $("#files").classList.remove("viewing");
  $("#btn-files-back").hidden = true;
}

function showFile(d, keepScroll = false) {
  const prev = files.current;
  files.current = d;
  const body = $("#viewer-body");
  const scroll = keepScroll ? body.scrollTop : 0;
  $("#viewer-bar").hidden = false;
  $("#vb-name").textContent = d.name;
  $("#vb-name").title = d.path || d.name;
  $("#vb-info").textContent = fmtSize(d.size || 0) + (d.cut ? " · mostrando o começo" : "");
  const isMd = d.kind === "md";
  $("#vb-toggle").hidden = !isMd;
  $("#vb-toggle").textContent = files.showCode ? "Ver formatado" : "Ver código";
  $("#vb-copy").hidden = !("content" in d);
  $("#vb-mention").hidden = !d.path;
  $("#vb-path").hidden = !d.path;
  $("#vb-open").hidden = !d.path;
  $("#vb-code").hidden = !d.path || !files.hasCode;
  body.innerHTML = "";
  body.className = "viewer-body";
  if (d.kind === "image") {
    const img = el("img", "pic"); img.src = d.data; img.alt = d.name; body.append(img);
  } else if (d.kind === "binary") {
    body.append(el("div", "hint", d.note || "Este arquivo não é texto e não dá para mostrar aqui."));
    const b = el("button", "btn", "Abrir no programa padrão"); b.onclick = () => api().open_file(state.active, d.path); body.append(b);
  } else if ((isMd && !files.showCode) || d.kind === "doc") {
    if (d.kind === "doc") body.append(el("div", "hint doc-note", "Visualização convertida para texto (sem o layout original)."));
    const art = el("article", "md"); renderMarkdown(art, d.content); body.append(art);
  } else {
    body.classList.add("code-view");
    const lines = (d.content.match(/\n/g) || []).length + 1;
    const gutter = el("pre", "gutter", Array.from({ length: lines }, (_, i) => i + 1).join("\n"));
    const pre = el("pre", "src"); const code = el("code"); code.textContent = d.content; pre.append(code);
    const lang = HL_LANG[d.ext] || d.ext;
    // arquivo grande: sem realce (pesa mais que ajuda)
    if (d.content.length < 50000 && !d.big && lang && hljs.getLanguage(lang)) {
      code.className = "language-" + lang;
      try { hljs.highlightElement(code); } catch (_) {}
    }
    body.append(gutter, pre);
  }
  body.scrollTop = keepScroll && prev?.path === d.path ? scroll : 0;
  $("#files").classList.add("viewing");
  $("#btn-files-back").hidden = false;
  markCurrentInTree();
}

// Depois de cada resposta (o Claude pode ter mexido nos arquivos) ou no botão Atualizar.
async function refreshFiles(quiet = false) {
  if (!files.open || files.conv !== state.active) return;
  await renderTree();
  const cur = files.current;
  if (cur?.path) {
    const id = state.active, seq = openSeq;
    // mtime/size conhecidos: o Python responde {unchanged:true} sem reler nem reconverter
    const r = await api().read_file(id, cur.path, cur.mtime ?? null, cur.size ?? null);
    if (id !== state.active || seq !== openSeq || files.current !== cur) return;
    if (r.unchanged) return;
    if (r.ok && (r.content !== cur.content || r.data !== cur.data || r.size !== cur.size || r.mtime !== cur.mtime)) showFile(r, true);
    else if (!r.ok && !quiet) toast("O arquivo aberto não existe mais.", "error");
  }
}

function mentionFile() {
  const p = files.current?.path;
  if (!p) return;
  const input = $("#prompt");
  const ins = "@" + p.replaceAll("\\", "/") + " ";
  const at = input.selectionStart ?? input.value.length;
  const pre = input.value.slice(0, at);
  input.value = pre + (pre && !/\s$/.test(pre) ? " " : "") + ins + input.value.slice(input.selectionEnd ?? at);
  input.focus();
  autoGrow(); setBusyUi();
  if (filesOverlay()) closeFiles();
}

function dragResize(handle, onMove, onEnd) {
  handle.addEventListener("mousedown", (e) => {
    e.preventDefault();
    document.body.classList.add("resizing");
    const move = (ev) => onMove(ev);
    const up = () => {
      document.body.classList.remove("resizing");
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
      onEnd();
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  });
}

function bindFiles() {
  $("#btn-docs").onclick = toggleFiles;
  $("#btn-files-close").onclick = closeFiles;
  $("#btn-files-refresh").onclick = () => refreshFiles();
  $("#btn-files-back").onclick = () => $("#files").classList.remove("viewing");
  $("#vb-toggle").onclick = () => { files.showCode = !files.showCode; showFile(files.current); };
  $("#vb-copy").onclick = async () => { toast((await copyText(files.current?.content || "")) ? "Copiado" : "Não consegui copiar", ""); };
  $("#vb-mention").onclick = mentionFile;
  $("#vb-path").onclick = (e) => {
    e.stopPropagation();
    const p = files.current?.path;
    if (!p) return;
    const b = e.currentTarget.getBoundingClientRect();
    openPathMenu(p.replaceAll("\\", "/"), b.left, b.bottom + 4);
  };
  $("#vb-open").onclick = () => api().open_file(state.active, files.current.path);
  $("#vb-code").onclick = () => api().open_file(state.active, files.current.path, "code");
  // arrastar a divisória entre o chat e o painel
  dragResize($("#files-resizer"), (ev) => {
    const right = $("#app").getBoundingClientRect().right;
    files.width = Math.round(right - ev.clientX);
    applyFilesWidth();
  }, () => api().set_pref("files_width", files.width));
  // arrastar a borda direita da árvore
  const treeHandle = el("div", "tree-resizer");
  $("#tree").after(treeHandle);
  dragResize(treeHandle, (ev) => {
    files.treeWidth = Math.max(140, Math.min(Math.round($("#tree").getBoundingClientRect().right - ev.clientX), 480));
    applyFilesWidth();
  }, () => api().set_pref("files_tree_width", files.treeWidth));
  window.addEventListener("resize", () => { if (files.open) applyFilesWidth(); });
}

/* ---------- saúde do Claude Code ---------- */
async function checkHealth(showToast = false) {
  const h = await api().check_health();
  state.health = h;
  renderHealth(h);
  document.querySelector(".banner")?.remove();
  if (!h.installed || !h.logged_in) {
    const b = el("div", "banner");
    if (!h.installed) {
      b.append(el("strong", "", "Claude Code não encontrado"));
      b.append(el("span", "", "Instale e reabra o app. No Windows (PowerShell):"));
      b.append(el("code", "", "irm https://claude.ai/install.ps1 | iex"));
      b.append(el("span", "", "No macOS/Linux:"));
      b.append(el("code", "", "curl -fsSL https://claude.ai/install.sh | bash"));
    } else {
      b.append(el("strong", "", "Falta entrar na sua conta Claude"));
      const go = el("button", "btn primary", "Abrir configurações para entrar");
      go.onclick = () => openSettings();
      b.append(go);
    }
    $("#chat").prepend(b);
  } else if (showToast) {
    toast("Conta Claude Code ok");
  }
  return h;
}

const PLANS = { pro: "Pro", max: "Max", team: "Team", enterprise: "Enterprise", free: "Gratuito" };

function renderHealth(h) {
  // rodapé da barra lateral
  const line = $("#health-line"), dot = $("#health-dot");
  dot.className = "sf-dot " + (h.installed && h.logged_in ? "ok" : "bad");
  line.textContent = !h.installed ? "Claude Code não instalado"
    : !h.logged_in ? "Sem login no Claude Code"
    : `${h.email || "logado"}${h.plan ? " · " + (PLANS[h.plan] || h.plan) : ""}`;
  line.title = line.textContent;

  // tela de configurações
  const st = $("#acc-status"), info = $("#acc-info");
  st.className = "acc-status " + (h.installed && h.logged_in ? "ok" : "bad");
  st.textContent = !h.installed ? "Claude Code não instalado" : h.logged_in ? "Conectado" : "Desconectado";
  info.innerHTML = "";
  const row = (k, v) => { if (v) { info.append(el("dt", "", k), el("dd", "", v)); } };
  if (h.logged_in) {
    row("Conta", h.email);
    row("Plano", PLANS[h.plan] || h.plan);
    row("Organização", h.org);
    row("Tipo de login", h.method === "claude.ai" ? "Assinatura claude.ai" : h.method);
    if (h.expires) row("Sessão válida até", new Date(h.expires).toLocaleString("pt-BR"));
  }
  row("Claude Code", h.version);
  if (h.error) row("Erro", h.error);
  const login = $("#btn-login");
  login.disabled = !h.installed;
  login.textContent = h.logged_in ? "Trocar de conta" : "Entrar na conta";
  login.classList.toggle("primary", !h.logged_in);
  $("#btn-logout").disabled = !h.installed || !h.logged_in;
}

/* ---------- configurações ---------- */
function openSettings() {
  if (filesOverlay()) closeFiles();
  $("#settings").hidden = false;
  toggleSidebar(false);
  const app = state.app || {};
  $("#about-line").textContent = `${app.name || "Rory Assistant"} ${app.version || ""} · Rory Systems`;
  checkHealth();
  loadUsage();
  loadPrefs();
}

/* ---------- uso do Claude (limites do plano + tokens) ---------- */
const nf = (n) => new Intl.NumberFormat("pt-BR").format(n || 0);
const compact = (n) => new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 1 }).format(n || 0);
const usd = (n) => "US$ " + (n || 0).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function untilText(iso) {
  if (!iso) return "";
  const ms = new Date(iso) - Date.now();
  if (ms <= 0) return "renova agora";
  const h = Math.floor(ms / 3600000), m = Math.floor((ms % 3600000) / 60000);
  const when = new Date(iso).toLocaleString("pt-BR", { weekday: h >= 24 ? "short" : undefined, hour: "2-digit", minute: "2-digit" });
  const rel = h >= 24 ? `${Math.floor(h / 24)} d ${h % 24} h` : h ? `${h} h ${m} min` : `${m} min`;
  return `renova em ${rel} (${when})`;
}

async function loadUsage() {
  const box = $("#limits");
  const btn = $("#btn-usage-refresh");
  btn.disabled = true; btn.textContent = "Atualizando…";
  let r;
  try { r = await api().get_usage(); } finally { btn.disabled = false; btn.textContent = "Atualizar"; }

  // limites do plano
  box.innerHTML = "";
  const plan = r.plan || {};
  if (!plan.ok) {
    box.append(el("p", "set-hint", "Limites do plano indisponíveis: " + (plan.error || "erro desconhecido")));
  } else {
    for (const w of plan.windows) {
      const pct = Math.max(0, Math.min(100, Math.round(w.percent)));
      const item = el("div");
      const head = el("div", "limit-head");
      head.append(el("span", "", w.label), el("b", "", pct + "% usado"));
      const bar = el("div", "bar" + (pct >= 90 ? " full" : pct >= 70 ? " warn" : ""));
      const fill = el("i"); fill.style.width = pct + "%"; bar.append(fill);
      item.append(head, bar, el("div", "limit-sub", untilText(w.resets_at)));
      box.append(item);
    }
    if (plan.breakdown && plan.breakdown.length) {
      box.append(el("p", "set-hint", "Na semana: " + plan.breakdown.map((b) => `${b.name} ${b.percent}%`).join(" · ")));
    }
    const ex = plan.extra;
    if (ex && ex.limit != null) {
      const fmt = (v) => (v / Math.pow(10, ex.decimals || 0)).toLocaleString("pt-BR", { style: "currency", currency: ex.currency || "USD" });
      box.append(el("p", "set-hint", `Créditos extras: ${ex.enabled ? "ligados" : "desligados"} · ${fmt(ex.used || 0)} de ${fmt(ex.limit)} no mês`));
    }
  }

  // tokens
  const table = $("#usage-table");
  table.innerHTML = "";
  const t = r.tokens || {};
  if (!t.ok) { table.innerHTML = ""; $("#usage-models").textContent = "Não consegui ler os registros: " + (t.error || ""); return; }
  const head = el("tr");
  ["Período", "Respostas", "Entrada", "Saída", "Cache (gravação)", "Cache (leitura)", "Custaria"].forEach((h) => head.append(el("th", "", h)));
  table.append(head);
  const rows = [["Sessão (5 h)", t.session], ["Semana", t.week], [`Mês (${t.month_label})`, t.month]];
  for (const [label, s] of rows) {
    const tr = el("tr");
    tr.append(el("td", "", label), el("td", "", nf(s.messages)), el("td", "", compact(s.input)), el("td", "", compact(s.output)),
      el("td", "", compact(s.cache_write)), el("td", "", compact(s.cache_read)), el("td", "cost", usd(s.cost)));
    tr.title = `Entrada ${nf(s.input)} · Saída ${nf(s.output)} · Cache gravação ${nf(s.cache_write)} · Cache leitura ${nf(s.cache_read)}`;
    table.append(tr);
  }
  const models = (t.month.models || []).map((m) => `${m.model}: ${usd(m.cost)}`).join(" · ");
  $("#usage-models").textContent = models ? "No mês, por modelo: " + models : "";
  $("#usage-checked").textContent = "Atualizado às " + new Date(r.checked_at).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

/* ---------- preferências (Mídia e voz) ---------- */
async function loadPrefs() {
  const p = await api().get_prefs();
  state.prefs = p;
  // lista de modelos vinda do Python (get_prefs().model_choices = [{id, label}])
  if (Array.isArray(p.model_choices) && p.model_choices.length) {
    const next = p.model_choices.map((m) => [m.id, m.label]);
    if (JSON.stringify(next) !== JSON.stringify(MODEL_CHOICES)) { MODEL_CHOICES = next; initModelSelects(); }
  }
  document.querySelectorAll("[data-pref]").forEach((input) => {
    const v = p[input.dataset.pref];
    if (v == null) return;
    if (input.type === "checkbox") input.checked = !!v; else input.value = String(v);
  });
  syncModelSelect();
  $("#ocr-note").hidden = p.ocr_available;
  $("#pref-image").disabled = !p.ocr_available;
}

function bindPrefs() {
  document.querySelectorAll("[data-pref]").forEach((input) => {
    input.onchange = async () => {
      const value = input.type === "checkbox" ? input.checked : input.dataset.num ? Number(input.value) : input.value;
      const np = await api().set_pref(input.dataset.pref, value);
      if (np && np.ok === false) return;
      state.prefs = np;
      const key = input.dataset.pref;
      toast(["use_subagents", "effort", "subagent_model"].includes(key)
        ? "Configuração salva: vale a partir da próxima mensagem de cada conversa"
        : "Configuração salva");
    };
  });
}

/* ---------- microfone: grava no app e transcreve no PC ---------- */
const rec = { media: null, chunks: [], stream: null, started: 0, timer: 0, cancelled: false };

async function toggleMic() {
  const btn = $("#btn-mic");
  if (btn.classList.contains("busy")) return;
  if (rec.media) { stopMic(false); return; }
  try {
    rec.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    toast("Sem acesso ao microfone: " + (e.message || e.name), "error");
    return;
  }
  rec.chunks = []; rec.cancelled = false;
  const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
  rec.media = new MediaRecorder(rec.stream, mime ? { mimeType: mime } : undefined);
  rec.media.ondataavailable = (e) => { if (e.data.size) rec.chunks.push(e.data); };
  rec.media.onstop = onMicStopped;
  rec.media.start();
  rec.started = Date.now();
  btn.classList.add("recording");
  btn.title = "Parar e transcrever (Esc cancela)";
  const t = $("#rec-time"); t.hidden = false; t.textContent = "0:00";
  rec.timer = setInterval(() => {
    const s = Math.floor((Date.now() - rec.started) / 1000);
    t.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }, 250);
}

function stopMic(cancel) {
  if (!rec.media) return;
  rec.cancelled = cancel;
  rec.media.stop();
}

async function onMicStopped() {
  const btn = $("#btn-mic");
  clearInterval(rec.timer);
  $("#rec-time").hidden = true;
  rec.stream?.getTracks().forEach((tr) => tr.stop());
  rec.media = null;
  btn.classList.remove("recording");
  btn.title = "Falar (clique para gravar, clique de novo para transcrever)";
  if (rec.cancelled || !rec.chunks.length) { if (rec.cancelled) toast("Gravação cancelada"); return; }
  const blob = new Blob(rec.chunks, { type: "audio/webm" });
  const data = await new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = () => rej(r.error); r.readAsDataURL(blob); })
    .catch((e) => { toast("Falha ao ler a gravação: " + e, "error"); return null; });
  if (!data) return;
  btn.classList.add("busy");
  btn.title = "Transcrevendo no PC…";
  let res;
  try {
    res = await api().transcribe_voice(data);
  } catch (e) {
    res = { ok: false, error: String((e && e.message) || e) };
  } finally {
    btn.classList.remove("busy");
    btn.title = "Falar (clique para gravar, clique de novo para transcrever)";
  }
  if (!res.ok) { toast("Não consegui transcrever: " + res.error, "error"); return; }
  const text = (res.text || "").trim();
  if (!text) { toast("Não reconheci nenhuma fala"); return; }
  const input = $("#prompt");
  input.value = input.value.trim() ? input.value.trimEnd() + " " + text : text;
  autoGrow(); setBusyUi(); input.focus();
  if (state.prefs?.voice_autosend) send();
}

function closeSettings() {
  $("#settings").hidden = true;
  stopLoginWatch();
}

let loginWatch = 0;
function stopLoginWatch() {
  if (loginWatch) clearInterval(loginWatch);
  loginWatch = 0;
  $("#login-hint").hidden = true;
}

async function startLogin() {
  const ok = await api().start_login();
  if (!ok) { toast("Claude Code não encontrado", "error"); return; }
  $("#login-hint").hidden = false;
  stopLoginWatch(); $("#login-hint").hidden = false;
  const started = Date.now();
  loginWatch = setInterval(async () => {
    const h = await checkHealth();
    if (h.logged_in) { stopLoginWatch(); toast("Conectado como " + (h.email || "sua conta")); }
    else if (Date.now() - started > 5 * 60 * 1000) stopLoginWatch();
  }, 3000);
}

async function logout() {
  const busy = Object.values(state.busy).some(Boolean);
  if (busy) { toast("Espere as respostas em andamento terminarem", "error"); return; }
  if (!confirm("Sair da conta Claude Code?\n\nIsso vale para este computador inteiro: o Claude Code no terminal também fica deslogado.")) return;
  $("#btn-logout").disabled = true;
  const h = await api().logout();
  state.health = h;
  renderHealth(h);
  checkHealth();
  toast(h.logged_in ? "Não consegui sair da conta" : "Conta desconectada", h.logged_in ? "error" : "");
}

/* ---------- ligações da interface ---------- */
function toggleSidebar(open) {
  if (isWide()) { $("#sidebar").classList.remove("open"); $("#scrim").hidden = true; return; }
  const show = open ?? !$("#sidebar").classList.contains("open");
  $("#sidebar").classList.toggle("open", show);
  $("#scrim").hidden = !show;
}

function autoGrow() {
  const t = $("#prompt");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 220) + "px";
}

function bindUi() {
  const input = $("#prompt");
  input.addEventListener("keydown", (e) => {
    if (e.isComposing) return;
    // lista de /comandos aberta: setas escolhem, Tab/Enter completa, Esc fecha
    if (state.slash.open && state.slash.items.length) {
      const n = state.slash.items.length;
      const redraw = () => (state.slash.mode === "shell" ? renderShell : renderSlash)(state.slash.items);
      if (e.key === "ArrowDown") { e.preventDefault(); state.slash.sel = (state.slash.sel + 1) % n; state.slash.moved = true; redraw(); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); state.slash.sel = (state.slash.sel - 1 + n) % n; state.slash.moved = true; redraw(); return; }
      if (e.key === "Tab" && !e.shiftKey) { e.preventDefault(); pickSlash(state.slash.sel); return; }
      // no "!", Enter roda o que foi digitado (como no terminal); só completa se escolheu com as setas
      if (e.key === "Enter" && !e.shiftKey && (state.slash.mode !== "shell" || state.slash.moved)) { e.preventDefault(); pickSlash(state.slash.sel); return; }
    }
    // Tab no "!" com a lista fechada: pede as sugestões na hora (em vez de sair da caixa)
    if (e.key === "Tab" && !e.shiftKey && !e.ctrlKey && !e.altKey && input.value.startsWith("!")) { e.preventDefault(); clearTimeout(state.shellTimer); updateShellSuggest(); return; }
    if (state.slash.open && e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeSlash(); return; }
    // Shift+Tab só com o foco na caixa de mensagem: alterna o modo (no resto da tela,
    // continua sendo o Shift+Tab normal de navegação)
    if (e.key === "Tab" && e.shiftKey && !e.ctrlKey && !e.altKey) { e.preventDefault(); cycleMode(); return; }
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); closeSlash(); send(); return; }
    // ↑ na primeira linha / ↓ na última: mensagens enviadas antes
    if (e.key === "ArrowUp" && !e.shiftKey && caretOnFirstLine(input) && (input.value === "" || state.recallPos !== -1 || input.selectionStart === 0)) {
      if (recall(-1)) e.preventDefault();
    } else if (e.key === "ArrowDown" && !e.shiftKey && state.recallPos !== -1 && caretOnLastLine(input)) {
      if (recall(1)) e.preventDefault();
    }
  });
  input.addEventListener("input", () => {
    autoGrow();
    setBusyUi();
    if (state.recallPos !== -1 && input.value !== state.recall[state.recallPos]) state.recallPos = -1;
    updateSlash();
  });
  input.addEventListener("blur", () => setTimeout(closeSlash, 150));
  document.addEventListener("paste", onPaste);
  $("#btn-wait-go").onclick = () => goToPermission();
  $("#btn-send").onclick = () => send();
  $("#btn-attach").onclick = () => $("#file-input").click();
  $("#file-input").onchange = (e) => { [...e.target.files].forEach((f) => addFile(f)); e.target.value = ""; };

  $("#btn-side").onclick = () => toggleSidebar();
  $("#btn-side-close").onclick = () => toggleSidebar(false);
  $("#scrim").onclick = () => toggleSidebar(false);
  window.addEventListener("resize", () => { if (isWide()) toggleSidebar(false); });
  $("#conv-search").oninput = (e) => { state.filter = e.target.value; renderConvs(); };

  $("#btn-new").onclick = openNewModal;
  $("#btn-new-side").onclick = openNewModal;
  $("#btn-new-cancel").onclick = closeNewModal;
  $("#btn-new-ok").onclick = createConv;
  $("#new-path").onkeydown = (e) => { if (e.key === "Enter") createConv(); };
  $("#btn-pick").onclick = async () => {
    const p = await api().pick_folder($("#new-path").value);
    if (p) { $("#new-path").value = p; createConv(); }
  };
  $("#new-modal").onclick = (e) => { if (e.target.id === "new-modal") closeNewModal(); };

  $("#conv-name").onclick = () => renameConv();
  $("#btn-theme").onclick = () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next); api().set_theme(next);
  };
  document.addEventListener("click", dismissConvMenu);
  $("#conv-list").addEventListener("scroll", dismissConvMenu);
  initModelSelects();

  $("#btn-settings").onclick = () => ($("#settings").hidden ? openSettings() : closeSettings());
  $("#btn-settings-close").onclick = closeSettings;
  $("#btn-login").onclick = startLogin;
  $("#btn-logout").onclick = logout;
  $("#btn-recheck").onclick = () => checkHealth(true);
  $("#btn-usage-refresh").onclick = loadUsage;
  $("#btn-mic").onclick = toggleMic;
  $("#conv-usage").onclick = () => {
    if (state.busy[state.active]) { toast("Espere a resposta terminar para compactar", "error"); return; }
    if (!confirm("Compactar o contexto desta conversa?\n\nO Claude passa a ler só um resumo do que já foi dito (gasta bem menos tokens a cada mensagem). Na tela você continua vendo a conversa inteira.")) return;
    send("/compact");
  };
  bindPrefs();
  api().get_prefs().then((p) => (state.prefs = p));

  $("#perm-mode").onchange = (e) => applyMode(e.target.value);

  bindFiles();

  const menu = $("#menu-more");
  $("#btn-more").onclick = (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; };
  document.addEventListener("click", () => (menu.hidden = true));
  menu.onclick = async (e) => {
    const action = e.target.dataset.action;
    if (!action) return;
    menu.hidden = true;
    if (action === "rename") renameConv();
    if (action === "delete") deleteConv(state.active);
    if (action === "folder") api().open_folder(state.active);
    if (action === "terminal") api().open_terminal(state.active);
    if (action === "health") openSettings();
    if (action === "convert") {
      const r = await api().convert_file(state.active);
      if (!r) return;
      if (!r.ok) { toast("Falhou: " + r.error, "error"); return; }
      toast("Salvo: " + r.name);
      openFiles();
      showFile({ ok: true, kind: "md", name: r.name, path: r.rel, content: r.content, size: r.content.length });
      if (r.rel) refreshFiles();
    }
  };

  document.querySelectorAll(".quick button").forEach((b) => (b.onclick = () => send(b.dataset.prompt)));

  // arrastar e soltar
  let depth = 0;
  window.addEventListener("dragenter", (e) => { if ([...(e.dataTransfer?.types || [])].includes("Files")) { depth++; $("#dropzone").hidden = false; } });
  window.addEventListener("dragleave", () => { depth = Math.max(0, depth - 1); if (!depth) $("#dropzone").hidden = true; });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => {
    e.preventDefault(); depth = 0; $("#dropzone").hidden = true;
    [...(e.dataTransfer?.files || [])].forEach((f) => addFile(f));
  });

  document.addEventListener("keydown", (e) => {
    // Ctrl+C sem nada selecionado = parar a resposta (como no terminal)
    if (e.ctrlKey && !e.shiftKey && e.key.toLowerCase() === "c" && state.busy[state.active]) {
      const t = $("#prompt");
      // foco em outro campo (busca, caminho, renomear): Ctrl+C é só copiar
      const ae = document.activeElement;
      if (ae && ae !== t && (ae.tagName === "INPUT" || ae.tagName === "TEXTAREA" || ae.isContentEditable)) return;
      const inputSel = document.activeElement === t && t.selectionStart !== t.selectionEnd;
      const pageSel = String(window.getSelection() || "").length > 0;
      if (!inputSel && !pageSel) { e.preventDefault(); stopTurn(); return; }
    }
    if (planShortcut(e)) return;
    if (e.key === "Escape") {
      // primeiro fecha o que estiver aberto; só depois interrompe o Claude
      const lb = document.querySelector(".lightbox");
      if (lb) { lb.remove(); return; }
      if (document.querySelector(".path-menu")) { closePopMenu(); return; }
      if (document.querySelector(".conv-menu")) { dismissConvMenu(); return; }
      if (!$("#menu-more").hidden) { $("#menu-more").hidden = true; return; }
      if (rec.media) { stopMic(true); return; }
      // cartão do plano aguardando resposta: Esc não o dispensa nem interrompe o turno
      if ($("#new-modal").hidden && $("#settings").hidden && document.querySelector("#messages .plan-card:not(.answered)")) return;
      if (!$("#new-modal").hidden) closeNewModal();
      else if (!$("#settings").hidden) closeSettings();
      else if (state.busy[state.active]) api().stop(state.active);
      else if (filesOverlay()) closeFiles();
      else toggleSidebar(false);
    }
    if (e.ctrlKey && !e.shiftKey && e.key.toLowerCase() === "e") { e.preventDefault(); toggleFiles(); }
    if (e.ctrlKey && !e.shiftKey && e.key.toLowerCase() === "b") { e.preventDefault(); toggleSidebar(); }
    if (e.ctrlKey && !e.shiftKey && e.key.toLowerCase() === "n") { e.preventDefault(); openNewModal(); }
    if (e.ctrlKey && e.key === ",") { e.preventDefault(); $("#settings").hidden ? openSettings() : closeSettings(); }
  });
}
