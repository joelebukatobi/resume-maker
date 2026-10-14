"use strict";
/* Chat panel: talk to an LLM about the open document.
 *
 * Replies stream in as plain text. Text the model writes for a specific field
 * arrives as a ```proposal {"path","text"}``` block and is shown as a diff
 * card; nothing changes until the user accepts. Text meant for somewhere else
 * entirely — an application form's questions — arrives as an ```answer```
 * block and is copy-only. Any other paragraph can be copied, or dropped into a
 * field with "Replace…", through the same diff and Accept.
 *
 * Shares the editor's globals: $, el, api, setStatus, header(), draw(),
 * schedule(), documentMode.
 */

const BLOCK = /```(proposal|answer|notes|voice)\s*([\s\S]*?)```/g;
// One conversation per application, covering both documents.
let chat = null;
let chatController = null;
let pendingImport = null; // { mode, doc } shown in the preview until accepted
let fieldCache = null;
// A file attached to the next message. Extracted on pick, carried into the
// message body on send, then forgotten: it lives in the transcript after that.
let chatAttachment = null; // { name, text, truncated }
const MAX_ATTACH_CHARS = 60000;

/* ---------------------------------------------------------------- paths */

function pathParts(path) {
  return String(path).split(".").map((p) => (/^\d+$/.test(p) ? Number(p) : p));
}

function getPath(doc, path) {
  return pathParts(path).reduce((node, key) => (node == null ? undefined : node[key]), doc);
}

// Only text fields may be written, and only where they already exist, except
// one new bullet at the end of a bullet list.
function canSet(doc, path) {
  const parts = pathParts(path);
  const last = parts.pop();
  const parent = parts.reduce((node, key) => (node == null ? undefined : node[key]), doc);
  if (parent == null) return false;
  if (Array.isArray(parent)) {
    if (typeof last !== "number") return false;
    if (last < parent.length) return typeof parent[last] === "string";
    return last === parent.length && parts[parts.length - 1] === "bullets";
  }
  return typeof last === "string" && (typeof parent[last] === "string" || parent[last] === undefined);
}

// Removing is a different write from setting: a bullet has to leave the list,
// not become an empty line in it.
function canRemove(doc, path) {
  const parts = pathParts(path);
  const last = parts.pop();
  const parent = parts.reduce((node, key) => (node == null ? undefined : node[key]), doc);
  if (parent == null) return false;
  if (Array.isArray(parent)) return typeof last === "number" && last < parent.length;
  return typeof last === "string" && parent[last] !== undefined;
}

function removePath(doc, path) {
  const parts = pathParts(path);
  const last = parts.pop();
  const parent = parts.reduce((node, key) => node[key], doc);
  if (Array.isArray(parent)) parent.splice(last, 1);
  else delete parent[last];
}

function setPath(doc, path, text) {
  const parts = pathParts(path);
  const last = parts.pop();
  const parent = parts.reduce((node, key) => node[key], doc);
  parent[last] = text;
}

async function fields() {
  if (fieldCache && fieldCache.mode === documentMode) return fieldCache.list;
  const list = await api("/api/chat/fields", { document: documentMode, doc: header() })
    .then((r) => r.json());
  fieldCache = { mode: documentMode, list };
  return list;
}

// Labels are cached for the open document only; a proposal for the other one
// shows its raw path rather than a label borrowed from the wrong document.
function fieldLabel(path, kind = documentMode) {
  const found = kind === documentMode && fieldCache
    && fieldCache.list.find((f) => f.path === path);
  return found ? found.label : path;
}

/* ----------------------------------------------------------------- diff */

// Word-level diff (LCS). Paragraph-sized inputs keep this cheap.
function wordDiff(before, after) {
  const a = before.split(/(\s+)/), b = after.split(/(\s+)/);
  const n = a.length, m = b.length;
  const dp = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const out = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { out.push(["same", a[i]]); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) out.push(["del", a[i++]]);
    else out.push(["add", b[j++]]);
  }
  while (i < n) out.push(["del", a[i++]]);
  while (j < m) out.push(["add", b[j++]]);
  return out;
}

function diffView(before, after) {
  const box = el("div", { class: "chat-diff" });
  for (const [kind, text] of wordDiff(before || "", after)) {
    box.append(kind === "same" ? document.createTextNode(text) : el(kind === "add" ? "ins" : "del", { text }));
  }
  return box;
}

/* ------------------------------------------------------------ proposals */

// The conversation spans both documents, so a proposal says which one it was
// written for and is applied there, open or not. Save writes both.
function docFor(kind) {
  return kind === "cover" ? cover : cv;
}

function applyText(path, text, kind = documentMode, { remove = false } = {}) {
  const doc = docFor(kind);
  if (remove) {
    if (!canRemove(doc, path)) throw new Error(`"${path}" is not there to remove`);
    removePath(doc, path);
  } else {
    if (!canSet(doc, path)) throw new Error(`"${path}" is not a field in that document`);
    setPath(doc, path, text);
  }
  fieldCache = null;
  if (kind !== documentMode) {
    setStatus(`Applied to your ${kind === "cover" ? "cover letter" : "CV"}`);
    return;
  }
  $("name").value = header().name || "";
  draw();
  refreshLabels();
}

function proposalCard(path, text, { state, onResolve, kind = documentMode, remove = false }) {
  const doc = docFor(kind);
  const before = getPath(doc, path);
  const valid = remove ? canRemove(doc, path) : canSet(doc, path);
  const card = el("div", { class: `chat-card ${state || ""}` });
  const where = kind === documentMode ? "" : ` · ${kind === "cover" ? "cover letter" : "CV"}`;
  const head = remove ? `Remove ${fieldLabel(path, kind)}` : fieldLabel(path, kind) + where;
  card.append(el("div", { class: "chat-card-head",
    text: valid ? head : `Unknown field: ${path}` }));
  card.append(diffView(typeof before === "string" ? before : "", remove ? "" : text));
  if (state) {
    card.append(el("div", { class: "chat-card-state", text: state === "accepted" ? "Applied" : "Dismissed" }));
    return card;
  }
  const actions = el("div", { class: "chat-card-actions" });
  if (valid) {
    actions.append(el("button", { class: "tiny", text: remove ? "Remove" : "Accept", onclick: () => {
      try { applyText(path, text, kind, { remove }); onResolve("accepted"); }
      catch (error) { setStatus(error.message, true); }
    } }));
  }
  actions.append(el("button", { class: "tiny secondary", text: "Copy",
    onclick: (e) => copyText(text, e.target) }));
  actions.append(el("button", { class: "tiny secondary", text: "Dismiss", onclick: () => onResolve("dismissed") }));
  card.append(actions);
  return card;
}

// Something about the candidate's own history, offered for their experience
// bank. Notes feed every future application, so this is the one thing the chat
// writes outside the open document, and only once the user has accepted it.
function notesCard(text, { state, onResolve }) {
  const card = el("div", { class: `chat-card ${state || ""}` });
  card.append(el("div", { class: "chat-card-head", text: "For your notes" }));
  for (const para of text.split(/\n{2,}/)) {
    if (para.trim()) card.append(el("p", { class: "chat-answer-text", text: para.trim() }));
  }
  if (state) {
    card.append(el("div", { class: "chat-card-state",
      text: state === "accepted" ? "Added to Notes" : "Dismissed" }));
    return card;
  }
  card.append(el("div", { class: "chat-card-actions" }, [
    el("button", { class: "tiny", text: "Add to Notes", onclick: async (e) => {
      e.target.disabled = true;
      try {
        await api("/api/setup/notes", { append: text });
        setStatus("Added to your notes");
        onResolve("accepted");
      } catch (error) {
        e.target.disabled = false;
        setStatus(`Could not add to notes: ${error.message}`, true);
      }
    } }),
    el("button", { class: "tiny secondary", text: "Copy",
      onclick: (e) => copyText(text, e.target) }),
    el("button", { class: "tiny secondary", text: "Dismiss",
      onclick: () => onResolve("dismissed") }),
  ]));
  return card;
}

// A rule the model noticed about how the user wants to write, offered for the
// Voice file (assistant.md). It is not a fact about their work — that is Notes —
// so it is written as an instruction, and only once accepted.
function voiceCard(text, { state, onResolve }) {
  const card = el("div", { class: `chat-card ${state || ""}` });
  card.append(el("div", { class: "chat-card-head", text: "For your voice" }));
  for (const para of text.split(/\n{2,}/)) {
    if (para.trim()) card.append(el("p", { class: "chat-answer-text", text: para.trim() }));
  }
  if (state) {
    card.append(el("div", { class: "chat-card-state",
      text: state === "accepted" ? "Added to Voice" : "Dismissed" }));
    return card;
  }
  card.append(el("div", { class: "chat-card-actions" }, [
    el("button", { class: "tiny", text: "Add to Voice", onclick: async (e) => {
      e.target.disabled = true;
      try {
        await api("/api/setup/voice", { append: text });
        setStatus("Added to your voice");
        onResolve("accepted");
      } catch (error) {
        e.target.disabled = false;
        setStatus(`Could not add to voice: ${error.message}`, true);
      }
    } }),
    el("button", { class: "tiny secondary", text: "Copy",
      onclick: (e) => copyText(text, e.target) }),
    el("button", { class: "tiny secondary", text: "Dismiss",
      onclick: () => onResolve("dismissed") }),
  ]));
  return card;
}

// An answer to an application question: it belongs on someone else's form, so
// the card offers no way to write it into the document.
function answerCard(text) {
  const card = el("div", { class: "chat-card" });
  card.append(el("div", { class: "chat-card-head", text: "Answer to copy" }));
  for (const para of text.split(/\n{2,}/)) {
    if (para.trim()) card.append(el("p", { class: "chat-answer-text", text: para.trim() }));
  }
  card.append(el("div", { class: "chat-card-actions" }, [
    el("button", { class: "tiny", text: "Copy", onclick: (e) => copyText(text, e.target) }),
  ]));
  return card;
}

// "Copy" on any paragraph: the text goes to the clipboard, nothing is edited.
async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // Clipboard API refused (older browser, focus lost); fall back to a selection.
    const box = document.body.appendChild(document.createElement("textarea"));
    box.value = text;
    box.select();
    document.execCommand("copy");
    box.remove();
  }
  const was = button.textContent;
  button.textContent = "Copied";
  setTimeout(() => { button.textContent = was; }, 1200);
}

// "Replace…" on any paragraph: pick a field, see the diff, accept.
// Replacing a field with something the model wrote freehand is rare, so it
// gets a rare affordance: select the words you want and the button appears.
// Works on a phrase, not just a whole paragraph.
const selectionTool = (() => {
  const button = el("button", { class: "tiny", text: "Replace…" });
  const tool = el("div", { class: "chat-selection", hidden: "" }, [button]);
  let host = null, text = "";
  button.addEventListener("mousedown", (event) => event.preventDefault());  // keep the selection
  button.addEventListener("click", () => {
    tool.hidden = true;
    if (host) replacePicker(text, host);
  });
  const hide = () => { tool.hidden = true; };
  const show = () => {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed) return hide();
    const picked = selection.toString().trim();
    const node = selection.anchorNode;
    const para = node && (node.nodeType === 1 ? node : node.parentElement)?.closest(".chat-para");
    if (!picked || !para || !$("chat-log").contains(para)) return hide();
    host = para;
    text = plainText(picked);
    const rect = selection.getRangeAt(0).getBoundingClientRect();
    tool.style.top = `${Math.max(8, rect.top - 34)}px`;
    tool.style.left = `${rect.left}px`;
    tool.hidden = false;
  };
  document.addEventListener("DOMContentLoaded", () => document.body.append(tool));
  if (document.body) document.body.append(tool);
  document.addEventListener("mouseup", () => setTimeout(show, 0));
  document.addEventListener("scroll", hide, true);
  return { hide };
})();

function replacePicker(text, host) {
  host.querySelector(".chat-replace")?.remove();
  const wrap = el("div", { class: "chat-replace" });
  const select = el("select");
  select.append(el("option", { value: "", text: "Replace which field?" }));
  fields().then((list) => {
    for (const f of list) select.append(el("option", { value: f.path, text: f.label }));
  }).catch((error) => setStatus(error.message, true));
  const slot = el("div");
  select.addEventListener("change", () => {
    slot.textContent = "";
    if (!select.value) return;
    slot.append(proposalCard(select.value, text, { onResolve: () => wrap.remove() }));
  });
  wrap.append(select, slot);
  host.append(wrap);
}

/* ------------------------------------------------------------ markdown */

// Models answer in Markdown. Render a safe subset by building nodes - never
// innerHTML - so a reply cannot introduce markup of its own.
const INLINE = /(\*\*[^*]+\*\*|__[^_]+__|(?<![*\w])\*[^*\n]+\*(?!\w)|`[^`]+`)/g;

function inlineNodes(text) {
  const out = [];
  for (const part of text.split(INLINE)) {
    if (!part) continue;
    if ((part.startsWith("**") && part.endsWith("**")) || (part.startsWith("__") && part.endsWith("__"))) {
      out.push(el("strong", { text: part.slice(2, -2) }));
    } else if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
      out.push(el("em", { text: part.slice(1, -1) }));
    } else if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      out.push(el("code", { text: part.slice(1, -1) }));
    } else {
      out.push(document.createTextNode(part));
    }
  }
  return out;
}

// One blank-line-separated block: heading, list, or paragraph.
function markdownBlock(text) {
  const lines = text.split("\n");
  const bullet = /^\s*[-*•]\s+/;
  const numbered = /^\s*\d+[.)]\s+/;
  if (lines.every((l) => bullet.test(l) || !l.trim())) {
    const list = el("ul", { class: "chat-list" });
    for (const line of lines.filter((l) => l.trim())) {
      list.append(el("li", {}, inlineNodes(line.replace(bullet, ""))));
    }
    return list;
  }
  if (lines.every((l) => numbered.test(l) || !l.trim())) {
    const list = el("ol", { class: "chat-list" });
    for (const line of lines.filter((l) => l.trim())) {
      list.append(el("li", {}, inlineNodes(line.replace(numbered, ""))));
    }
    return list;
  }
  const heading = lines[0].match(/^\s*#{1,6}\s+(.*)$/);
  if (heading) {
    // A heading only owns its own line; whatever follows is its own block.
    const group = el("div", { class: "chat-group" },
      [el("p", { class: "chat-heading" }, inlineNodes(heading[1]))]);
    const rest = lines.slice(1).join("\n").trim();
    if (rest) group.append(markdownBlock(rest));
    return group;
  }
  return el("p", {}, inlineNodes(text));
}

/* -------------------------------------------------------------- render */

// Field values are plain text: strip Markdown before offering a replacement.
function plainText(text) {
  return text
    .replace(/^\s*#{1,6}\s+/gm, "")
    .replace(/^\s*[-*•]\s+/gm, "")
    .replace(/\*\*([^*]+)\*\*|__([^_]+)__/g, "$1$2")
    .replace(/(?<![*\w])\*([^*\n]+)\*(?!\w)/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .trim();
}

// A user turn. An attached file is shown as a chip, its text hidden from the
// bubble but kept in the message so the model reads it.
function renderUser(message) {
  const box = el("div", { class: "chat-msg user" });
  const { name, text } = userParts(message.content);
  if (name) box.append(el("div", { class: "chat-attachment-chip", text: `Attached: ${name}` }));
  if (text.trim()) box.append(el("p", { text }));
  return box;
}

function renderAssistant(message, index, streaming) {
  const box = el("div", { class: "chat-msg assistant" });
  const content = message.content;
  const kind = message.document || documentMode;
  let last = 0, n = 0;
  const addText = (text) => {
    for (const para of text.split(/\n{2,}/)) {
      if (!para.trim()) continue;
      box.append(el("div", { class: "chat-para" }, [markdownBlock(para.trim())]));
    }
  };
  for (const match of content.matchAll(BLOCK)) {
    addText(content.slice(last, match.index));
    last = match.index + match[0].length;
    if (match[1] === "answer") {
      box.append(answerCard(match[2].trim()));
      continue;
    }
    if (match[1] === "notes" || match[1] === "voice") {
      const key = String(n++);
      const card = match[1] === "notes" ? notesCard : voiceCard;
      box.append(card(match[2].trim(), {
        state: message.resolved && message.resolved[key],
        onResolve: (result) => {
          message.resolved = { ...(message.resolved || {}), [key]: result };
          saveHistory();
          renderChat();
        },
      }));
      continue;
    }
    const key = String(n++);
    let parsed = null;
    try { parsed = JSON.parse(match[2]); } catch { /* shown as text below */ }
    const wantsRemoval = Boolean(parsed && parsed.remove) && typeof parsed.path === "string";
    if (parsed && typeof parsed.path === "string"
        && (wantsRemoval || typeof parsed.text === "string")) {
      const state = message.resolved && message.resolved[key];
      box.append(proposalCard(parsed.path, parsed.text || "", {
        state,
        kind,
        remove: wantsRemoval,
        onResolve: (result) => {
          message.resolved = { ...(message.resolved || {}), [key]: result };
          saveHistory();
          renderChat();
        },
      }));
    } else {
      addText(match[2]);
    }
  }
  const tail = content.slice(last);
  const open = tail.search(/```(proposal|answer|notes|voice)/);
  addText(open === -1 ? tail : tail.slice(0, open));
  if (open !== -1) box.append(el("div", { class: "chat-pending", text: "Writing a proposal…" }));
  if (streaming && !content) box.append(el("div", { class: "chat-pending", text: "Thinking…" }));
  // One action for the message, not one per paragraph: a paragraph is usually
  // the model thinking out loud, and there is nowhere to put it. Text that
  // does have a home arrives as a proposal or an answer, with its own card.
  if (!streaming && content.trim()) {
    box.append(el("div", { class: "chat-msg-actions" }, [
      el("button", { class: "tiny secondary", text: "Copy",
        onclick: (e) => copyText(messageText(content), e.target) }),
    ]));
  }
  return box;
}

// What the message reads as: the prose and any answer, without the proposal
// blocks, which are machinery rather than something to paste anywhere.
function messageText(content) {
  return content
    .replace(/```(?:proposal|notes|voice)\s*[\s\S]*?```/g, "")
    .replace(/```answer\s*([\s\S]*?)```/g, "$1")
    .split(/\n{2,}/)
    .map((para) => plainText(para.trim()))
    .filter(Boolean)
    .join("\n\n");
}

// Voice and Notes readiness, read once so the empty state can point at them.
let setupStatus = null;
fetch("/api/setup").then((r) => r.json()).then((d) => { setupStatus = d.status; renderChat(); })
  .catch(() => { /* the plain empty state still works */ });

function emptyState() {
  const doc = documentMode === "cover" ? "cover letter" : "CV";
  const box = el("div", { class: "chat-empty" });
  const voice = setupStatus && setupStatus.voice !== "empty";
  const notes = setupStatus && setupStatus.notes !== "empty";
  if (voice && notes) {
    box.append(el("p", { text: `Your Voice and Notes are loaded into every answer. ` +
      `Paste a job description below and ask me to tailor your ${doc}.` }));
    return box;
  }
  box.append(el("p", { text: `Ask about your ${doc}, paste a job description below, ` +
    "or upload an existing document." }));
  if (setupStatus) {
    const missing = [!voice && ["Voice", "/voice"], !notes && ["Notes", "/notes"]].filter(Boolean);
    const tip = el("p", { class: "chat-tip" }, [document.createTextNode("Add your ")]);
    missing.forEach(([label, href], i) => {
      if (i) tip.append(document.createTextNode(" and "));
      tip.append(el("a", { href, text: label }));
    });
    tip.append(document.createTextNode(" so I write like you and draw on what you have actually done."));
    box.append(tip);
  }
  return box;
}

function renderChat({ streaming = false } = {}) {
  const host = $("chat-log");
  const state = chat;
  host.textContent = "";
  if (!state) return;
  if (!state.messages.length) host.append(emptyState());
  state.messages.forEach((m, i) => {
    const isLast = i === state.messages.length - 1;
    host.append(m.role === "user" ? renderUser(m) : renderAssistant(m, i, streaming && isLast));
  });
  host.scrollTop = host.scrollHeight;
}

/* ---------------------------------------------------------- persistence */

async function loadHistory() {
  if (chat) return;
  try {
    const data = await fetch(withApp("/api/chat/history")).then((r) => r.json());
    chat = {
      messages: Array.isArray(data.messages) ? data.messages : [],
      job_description: typeof data.job_description === "string" ? data.job_description : "",
    };
  } catch {
    chat = { messages: [], job_description: "" };
  }
}

async function saveHistory() {
  if (!chat) return;
  try {
    await api("/api/chat/history", { ...chat, app: APP });
  } catch (error) { setStatus(`chat history not saved: ${error.message}`, true); }
}

const saveHistorySoon = (() => {
  let timer;
  return () => { clearTimeout(timer); timer = setTimeout(saveHistory, 600); };
})();

/* ---------------------------------------------------------------- agent */

// The chosen provider and model live in this browser; keys live on the server.
const PREFS = "resume-maker.chat";
const prefs = (() => { try { return JSON.parse(localStorage.getItem(PREFS)) || {}; } catch { return {}; } })();
const savePrefs = () => { try { localStorage.setItem(PREFS, JSON.stringify(prefs)); } catch { /* optional */ } };
const STREAM_ERROR = String.fromCharCode(0); // server marks a mid-stream failure with NUL
let providers = [];
let picked = null; // provider selected inside the Agent dialog

function agentModel() {
  const p = providers.find((x) => x.id === prefs.provider && x.available);
  const model = p && (prefs.models || {})[p.id];
  return p && model ? { provider: p.id, model, label: p.label } : null;
}

function updateAgentLink() {
  const agent = agentModel();
  const model = agent && ((prefs.labels || {})[agent.model] || agent.model);
  $("agent-link").textContent = agent ? `${agent.label} · ${model}` : "Connect Agent";
  $("agent-link").title = agent ? agent.model : "";
}

async function loadProviders() {
  providers = await api("/api/llm/providers").then((r) => r.json());
  updateAgentLink();
}

async function fillModels(providerId, models) {
  const select = $("agent-model");
  select.textContent = "";
  if (!models) {
    select.append(el("option", { text: "Loading models…" }));
    try {
      models = await fetch(`/api/llm/models?provider=${encodeURIComponent(providerId)}`)
        .then(async (r) => { const d = await r.json(); if (!r.ok) throw new Error(d.error); return d; });
    } catch (error) {
      select.textContent = "";
      select.append(el("option", { text: "Could not load models" }));
      $("agent-note").textContent = error.message;
      return;
    }
    select.textContent = "";
  }
  for (const m of models) {
    select.append(el("option", { value: m.id, text: m.label, title: m.id }));
  }
  const remembered = (prefs.models || {})[providerId];
  if (remembered && models.some((m) => m.id === remembered)) select.value = remembered;
  prefs.labels = { ...(prefs.labels || {}), ...Object.fromEntries(models.map((m) => [m.id, m.label])) };
  rememberModel();
}

function rememberModel() {
  const current = providers.find((p) => p.id === picked);
  if (!current || !current.available || !$("agent-model").value) return;
  prefs.provider = picked;
  prefs.models = { ...(prefs.models || {}), [picked]: $("agent-model").value };
  savePrefs();
  updateAgentLink();
}

function drawAgentDialog() {
  const host = $("agent-providers");
  host.textContent = "";
  for (const p of providers) {
    const note = p.source === "env" ? "env key" : p.available ? "connected" : "not set";
    host.append(el("button", {
      type: "button", "aria-pressed": String(p.id === picked),
      onclick: () => { picked = p.id; drawAgentDialog(); },
    }, [document.createTextNode(p.label), el("small", { text: note })]));
  }
  const current = providers.find((p) => p.id === picked);
  const key = $("agent-key");
  key.value = "";
  key.placeholder = !current ? "" : current.source === "env"
    ? `Using ${current.key_env} from the environment`
    : current.available ? "Connected" : `Paste your ${current.label} API key`;
  $("agent-disconnect").hidden = !(current && current.source === "saved");
  $("agent-note").textContent = "";
  if (current && current.available) fillModels(current.id);
  else { $("agent-model").textContent = ""; $("agent-model").append(el("option", { text: "Connect to see models" })); }
}

async function openAgent() {
  await loadProviders().catch((error) => setStatus(error.message, true));
  picked = prefs.provider || (providers.find((p) => p.available) || providers[0] || {}).id;
  drawAgentDialog();
  $("agent-dialog").showModal();
}

async function connectAgent() {
  const key = $("agent-key").value.trim();
  if (!key) { $("agent-note").textContent = "Paste an API key first."; return; }
  $("agent-note").textContent = "Checking the key…";
  try {
    const data = await api("/api/llm/key", { provider: picked, key }).then((r) => r.json());
    $("agent-key").value = "";
    await loadProviders();
    drawAgentDialog();
    await fillModels(picked, data.models);
    $("agent-note").textContent = "Connected. Pick a model.";
  } catch (error) {
    $("agent-note").textContent = error.message;
  }
}

async function disconnectAgent() {
  try {
    await api("/api/llm/key", { provider: picked, key: "" });
    await loadProviders();
    drawAgentDialog();
  } catch (error) { $("agent-note").textContent = error.message; }
}

/* ----------------------------------------------------------------- send */

/* ----------------------------------------------------------- attachments */

// A file read in as chat context. The text is extracted server-side (no model
// call) and carried on the next message, where the model then sees it like any
// other turn. A big file is trimmed, and the chip says so.
async function attachFile(file) {
  setStatus(`reading ${file.name}…`);
  try {
    const data = await api("/api/setup/extract", {
      filename: file.name, file: await readFile(file),
    }).then((r) => r.json());
    const full = String(data.text || "").trim();
    if (!full) { setStatus(`${file.name} had no readable text`, true); return; }
    chatAttachment = {
      name: file.name, text: full.slice(0, MAX_ATTACH_CHARS),
      truncated: full.length > MAX_ATTACH_CHARS,
    };
    drawAttachment();
    setStatus("");
    $("chat-input").focus();
  } catch (error) {
    setStatus(`could not read ${file.name}: ${error.message}`, true);
  }
}

function drawAttachment() {
  const box = $("chat-attachment");
  box.textContent = "";
  if (!chatAttachment) { box.hidden = true; return; }
  const note = chatAttachment.truncated ? " · trimmed" : "";
  box.hidden = false;
  box.append(
    el("span", { class: "chat-attachment-name",
      text: `${chatAttachment.name} · ${chatAttachment.text.length.toLocaleString()} chars${note}` }),
    el("button", { class: "tiny secondary", text: "Remove",
      onclick: () => { chatAttachment = null; drawAttachment(); } }),
  );
}

// The block that wraps an attached file inside a stored user message. Composed
// on send, unwrapped for display so the bubble shows a chip, not a wall of text.
const UPLOAD_RE = /^\[Uploaded file: ([^\]]*)\]\n[\s\S]*?\n\[End of uploaded file\]\n*/;
function userParts(content) {
  const match = UPLOAD_RE.exec(content);
  return match ? { name: match[1], text: content.slice(match[0].length) } : { name: "", text: content };
}

function setBusy(busy) {
  $("chat-send").hidden = busy;
  $("chat-stop").hidden = !busy;
  $("chat-input").disabled = busy;
  $("chat-mic").disabled = busy;
  $("chat-attach").disabled = busy;
}

/* ------------------------------------------------------------ dictation */

// Two ways to dictate. When a local Whisper engine is installed the audio is
// recorded here and transcribed on the machine, so it works everywhere. When
// there is none, we fall back to the browser's own recognition, which is not
// local: Chrome streams the audio to Google, and some builds (Vivaldi, Brave,
// Firefox) have no such service at all.
const Dictation = window.SpeechRecognition || window.webkitSpeechRecognition;
let listener = null;      // an active Web Speech session
let recorder = null;      // an active local capture session
let transcribeReady = false; // set once the server says a local engine exists

function insertTranscript(text) {
  const input = $("chat-input");
  input.value += input.value && !/\s$/.test(input.value) ? ` ${text}` : text;
  input.focus();
}

function toggleDictation() {
  if (transcribeReady) { recorder ? stopRecording() : startRecording(); return; }
  if (listener) { listener.stop(); return; }
  const speech = new Dictation();
  speech.lang = navigator.language || "en-US";
  speech.continuous = true;
  speech.onresult = (event) => {
    let heard = "";
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      if (event.results[i].isFinal) heard += event.results[i][0].transcript;
    }
    if (heard.trim()) insertTranscript(heard.trim());
  };
  speech.onerror = (event) => {
    const why = event.error === "not-allowed" ? "microphone access was refused" : event.error;
    setStatus(`Dictation stopped: ${why}`, true);
  };
  // Fires however it ended: stopped, errored, or the browser gave up on silence.
  speech.onend = () => {
    listener = null;
    $("chat-mic").classList.remove("listening");
  };
  speech.start();
  listener = speech;
  $("chat-mic").classList.add("listening");
  $("chat-input").focus();
}

// ----- local capture: raw PCM -> 16 kHz mono WAV -> /api/transcribe -----

// How often to transcribe while recording. Each pass re-reads everything said
// so far and replaces the tail, so the text self-corrects as context arrives;
// the engine is fast enough on a local CPU that this stays ahead of speech.
const LIVE_MS = 500;

async function startRecording() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const ctx = new AudioContext();
    const source = ctx.createMediaStreamSource(stream);
    // ponytail: ScriptProcessorNode is deprecated, but it is dependency-free
    // and works in every browser; swap to an AudioWorklet if it ever breaks.
    const node = ctx.createScriptProcessor(4096, 1, 1);
    const chunks = [];
    node.onaudioprocess = (event) => chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
    source.connect(node);
    node.connect(ctx.destination);
    recorder = {
      stream, ctx, source, node, chunks, rate: ctx.sampleRate,
      base: $("chat-input").value,   // text before dictation: we replace after it
      timer: null, busy: false,
    };
    recorder.timer = setInterval(flushRecording, LIVE_MS);
    $("chat-mic").classList.add("listening");
    setStatus("Listening… click the mic again to stop");
  } catch (error) {
    setStatus(`Microphone error: ${error.message}`, true);
  }
}

// One pass: transcribe everything captured so far and drop it after `base`.
async function transcribeChunk(rec) {
  if (!rec.chunks.length) return;
  const samples = mergePcm(rec.chunks);
  const wav = encodeWav(downsample(samples, rec.rate, 16000), 16000);
  const data = await api("/api/transcribe", { audio: bytesToBase64(wav) }).then((r) => r.json());
  const text = String(data.text || "").trim();
  const input = $("chat-input");
  input.value = text ? joinText(rec.base, text) : rec.base;
}

async function flushRecording() {
  const rec = recorder;
  if (!rec || rec.busy || !rec.chunks.length) return;  // skip if still busy
  rec.busy = true;
  try {
    await transcribeChunk(rec);
  } catch (error) {
    setStatus(`Could not transcribe: ${error.message}`, true);
  } finally {
    rec.busy = false;
  }
}

async function stopRecording() {
  const rec = recorder;
  recorder = null;
  $("chat-mic").classList.remove("listening");
  if (!rec) return;
  clearInterval(rec.timer);
  let failed = false;
  try {
    await transcribeChunk(rec);   // a last pass over the full utterance
  } catch (error) {
    failed = true;
    setStatus(`Could not transcribe: ${error.message}`, true);
  }
  rec.node.disconnect();
  rec.source.disconnect();
  rec.stream.getTracks().forEach((track) => track.stop());
  await rec.ctx.close();
  if (!failed) setStatus("");
}

function joinText(base, text) {
  return base && !/\s$/.test(base) ? `${base} ${text}` : base + text;
}

function mergePcm(chunks) {
  let length = 0;
  for (const chunk of chunks) length += chunk.length;
  const merged = new Float32Array(length);
  let offset = 0;
  for (const chunk of chunks) { merged.set(chunk, offset); offset += chunk.length; }
  return merged;
}

function downsample(samples, from, to) {
  if (to >= from) return samples;
  const ratio = from / to;
  const length = Math.floor(samples.length / ratio);
  const out = new Float32Array(length);
  for (let i = 0; i < length; i += 1) {
    const start = Math.floor(i * ratio);
    const end = Math.floor((i + 1) * ratio);
    let sum = 0;
    for (let j = start; j < end; j += 1) sum += samples[j];
    out[i] = sum / (end - start);
  }
  return out;
}

// 16-bit mono PCM in a WAV container — exactly what whisper.cpp reads.
function encodeWav(samples, rate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const ascii = (offset, text) => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
  };
  ascii(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);              // PCM
  view.setUint16(22, 1, true);              // mono
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);       // byte rate
  view.setUint16(32, 2, true);              // block align
  view.setUint16(34, 16, true);             // bits per sample
  ascii(36, "data");
  view.setUint32(40, samples.length * 2, true);
  let offset = 44;
  for (let i = 0; i < samples.length; i += 1, offset += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Uint8Array(buffer);
}

function bytesToBase64(bytes) {
  let binary = "";
  const step = 0x8000;
  for (let i = 0; i < bytes.length; i += step) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + step));
  }
  return btoa(binary);
}

async function sendMessage() {
  const input = $("chat-input");
  const typed = input.value.trim();
  const state = chat;
  if ((!typed && !chatAttachment) || !state || chatController) return;
  const agent = agentModel();
  if (!agent) { openAgent(); setStatus("Connect an agent first", true); return; }
  const attached = chatAttachment;   // kept to put back if the turn fails
  const content = attached
    ? `[Uploaded file: ${attached.name}]\n${attached.text}\n[End of uploaded file]\n\n${typed}`.trim()
    : typed;
  input.value = "";
  chatAttachment = null;
  drawAttachment();
  state.messages.push({ role: "user", content, document: documentMode });
  const reply = { role: "assistant", content: "", document: documentMode };
  state.messages.push(reply);
  renderChat({ streaming: true });
  setBusy(true);
  chatController = new AbortController();
  const failed = (message) => {
    // Put the question back so it can be retried; providers expect turns to alternate.
    state.messages.splice(-2, 2);
    input.value = typed;
    chatAttachment = attached;
    drawAttachment();
    setStatus(message, true);
  };
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: chatController.signal,
      body: JSON.stringify({
        document: documentMode,
        provider: agent.provider,
        model: agent.model,
        doc: header(),
        job_description: state.job_description,
        // What the preview measured, so the model knows the shape of the page.
        pages: typeof previewPages === "number" ? previewPages : 0,
        overflowing: typeof overflowing !== "undefined" ? overflowing : [],
        // resolved rides along so the server can drop already-applied proposals
        // from the transcript it sends on; undefined is omitted by stringify.
        messages: state.messages.slice(0, -1).map(({ role, content, resolved }) => ({ role, content, resolved })),
      }),
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({ error: response.statusText }));
      failed(detail.error || "chat failed");
    } else {
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        reply.content += decoder.decode(value, { stream: true });
        const cut = reply.content.indexOf(STREAM_ERROR);
        if (cut !== -1) {
          setStatus(reply.content.slice(cut + 1), true);
          reply.content = reply.content.slice(0, cut);
          break;
        }
        renderChat({ streaming: true });
      }
      if (!reply.content.trim()) state.messages.splice(-2, 2);
    }
  } catch (error) {
    if (error.name === "AbortError") {
      if (!reply.content.trim()) state.messages.splice(-2, 2);
    } else {
      failed(error.message);
    }
  } finally {
    chatController = null;
    setBusy(false);
    renderChat();
    refreshLabels();
    saveHistory();
    input.focus();
  }
}

/* ------------------------------------------------------ job description */

function drawJobTile() {
  const jd = (chat && chat.job_description) || "";
  const text = jd.trim();
  $("jd-input").hidden = Boolean(text);
  $("jd-chip").hidden = !text;
  $("jd-clear").hidden = !text;
  $("jd-input").value = "";
  if (text) {
    $("jd-preview").textContent = text;
    const lines = text.split("\n").length;
    $("jd-meta").textContent = `${lines} line${lines === 1 ? "" : "s"} · ${text.length.toLocaleString()} chars`;
  }
}

function setJobDescription(text) {
  chat.job_description = text;
  drawJobTile();
  saveHistorySoon();
  nameFromJob(text);
  if (typeof scoreSoon === "function") scoreSoon();
}

// A new application has no name until a job description arrives: the posting
// says who the company is and what the role is called, so the application,
// the cover letter's address and the exported filenames all come from it.
async function nameFromJob(text) {
  if (!document.body.dataset.unnamed || !text.trim()) return;
  const agent = agentModel();
  if (!agent) { setStatus("Connect an agent to name this application", true); return; }
  delete document.body.dataset.unnamed;  // one attempt, not one per keystroke
  setStatus("Reading the job description…");
  try {
    const found = await api("/api/apps", {
      action: "identify", slug: APP, job_description: text,
      provider: agent.provider, model: agent.model,
    }).then((r) => r.json());
    // The server may have swapped in a same-track CV/letter; drop this
    // application's unsaved draft so it cannot restore over the fresh copy.
    if (found.reseeded && typeof dropDraft === "function") dropDraft();
    await saveHistory();  // the chat follows the folder to its new name
    window.location.replace(`/${encodeURIComponent(found.application.slug)}${documentPath()}`);
  } catch (error) {
    document.body.dataset.unnamed = "1";
    setStatus(`Could not name this application: ${error.message}`, true);
  }
}

const documentPath = () => (documentMode === "cover" ? "/cover-letter" : "/resume");

/* --------------------------------------------------------------- import */

function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.onerror = () => reject(new Error("could not read the file"));
    reader.readAsDataURL(file);
  });
}

const uploaded = { cv: null, cover: null }; // file name shown in the tile

// The open document is the tile's default; uploading replaces it.
function currentDocName() {
  const doc = header();
  if (!doc) return "";
  const filled = documentMode === "cover"
    ? (doc.paragraphs || []).some((p) => p && p.trim())
    : (doc.sections || []).length > 0;
  if (!filled) return "";
  return documentMode === "cover" ? "Current cover letter" : "Current CV";
}

function drawDocTile() {
  const uploadedName = uploaded[documentMode];
  const name = uploadedName || currentDocName();
  $("cv-empty").hidden = Boolean(name);
  $("cv-doc").hidden = !name;
  $("cv-clear").hidden = !uploadedName; // nothing to clear on the default
  $("cv-doc-name").textContent = name;
  $("cv-empty-label").textContent = documentMode === "cover" ? "Upload Cover Letter" : "Upload CV";
}

async function importFile(file) {
  if (!file) return;
  const agent = agentModel();
  if (!agent) { openAgent(); setStatus("Connect an agent first", true); return; }
  uploaded[documentMode] = file.name;
  drawDocTile();
  setStatus(`importing ${file.name}…`);
  try {
    const body = { document: documentMode, doc: header(), provider: agent.provider, model: agent.model,
      file: await readFile(file), filename: file.name };
    const data = await api("/api/import", body).then((r) => r.json());
    pendingImport = { mode: documentMode, doc: data.doc };
    showImportBar();
    schedule();
    setStatus("imported — review it in the preview");
  } catch (error) {
    uploaded[documentMode] = null;
    drawDocTile();
    setStatus(error.message, true);
  }
}

function showImportBar() {
  $("import-bar").hidden = !(pendingImport && pendingImport.mode === documentMode);
}

function acceptImport() {
  if (!pendingImport) return;
  if (pendingImport.mode === "cover") cover = { ...pendingImport.doc };
  else cv = { ...pendingImport.doc };
  pendingImport = null;
  fieldCache = null;
  showImportBar();
  $("name").value = header().name || "";
  draw();
  setStatus("import applied — Save to keep it");
}

function discardImport() {
  pendingImport = null;
  showImportBar();
  schedule();
}

/* ---------------------------------------------------------------- wiring */

// Called by the editor whenever the open document changes.
async function chatModeChanged() {
  fieldCache = null;
  await loadHistory();
  $("chat-title").textContent = "Chat";
  drawJobTile();
  drawDocTile();
  showImportBar();
  renderChat();
  refreshLabels();
  // The hub's model line links here with #agent: open the dialog on arrival
  // rather than making you find it again.
  if (window.location.hash === "#agent") openAgent();
}

// Proposal cards show field names ("Professional Summary"), not paths.
function refreshLabels() {
  fields().then(() => renderChat()).catch(() => { /* paths still work as labels */ });
}

$("chat-send").addEventListener("click", sendMessage);
// The mic is offered when either path works: a local engine (asked of the
// server) or the browser's own recognition. Hidden only when neither is there.
$("chat-mic").addEventListener("click", toggleDictation);
fetch("/api/transcribe").then((r) => r.json())
  .then((d) => { transcribeReady = Boolean(d.available); })
  .catch(() => { /* no endpoint: keep the browser path */ })
  .finally(() => { $("chat-mic").hidden = !(transcribeReady || Dictation); });
$("chat-stop").addEventListener("click", () => chatController && chatController.abort());
$("chat-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendMessage(); }
});
$("chat-clear").addEventListener("click", () => {
  if (!chat.messages.length) return;
  if (!confirm("Clear this chat? The job description is kept.")) return;
  chat.messages = [];
  saveHistory();
  renderChat();
});

// Agent dialog
$("agent-link").addEventListener("click", openAgent);
$("agent-close").addEventListener("click", () => $("agent-dialog").close());
$("agent-done").addEventListener("click", () => { rememberModel(); $("agent-dialog").close(); });
$("agent-connect").addEventListener("click", connectAgent);
$("agent-disconnect").addEventListener("click", disconnectAgent);
$("agent-model").addEventListener("change", rememberModel);
$("agent-key").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); connectAgent(); } });

// Job description tile: pasting collapses into a "Pasted" chip.
$("jd-input").addEventListener("paste", (event) => {
  const text = event.clipboardData && event.clipboardData.getData("text");
  if (text && text.trim()) { event.preventDefault(); setJobDescription(text.trim()); }
});
$("jd-input").addEventListener("blur", (event) => {
  if (event.target.value.trim()) setJobDescription(event.target.value.trim());
});
$("jd-chip").addEventListener("click", () => {
  $("jd-full").value = chat.job_description;
  $("jd-dialog").showModal();
});
$("jd-dialog").addEventListener("close", () => setJobDescription($("jd-full").value.trim()));
$("jd-dialog-close").addEventListener("click", () => $("jd-dialog").close());
$("jd-clear").addEventListener("click", () => setJobDescription(""));

// Document tile: click or drop a file to import it.
const docTile = $("cv-tile");
const pickFile = () => { if (!uploaded[documentMode]) $("cv-file").click(); };
docTile.addEventListener("click", (event) => { if (!event.target.closest(".tile-x")) pickFile(); });
docTile.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") { event.preventDefault(); pickFile(); }
});
$("cv-file").addEventListener("change", (event) => { importFile(event.target.files[0]); event.target.value = ""; });
docTile.addEventListener("dragover", (event) => { event.preventDefault(); docTile.classList.add("drag"); });
docTile.addEventListener("dragleave", () => docTile.classList.remove("drag"));
docTile.addEventListener("drop", (event) => {
  event.preventDefault();
  docTile.classList.remove("drag");
  importFile(event.dataTransfer.files[0]);
});
$("cv-clear").addEventListener("click", () => {
  uploaded[documentMode] = null;
  drawDocTile();
  if (pendingImport && pendingImport.mode === documentMode) discardImport();
});

// Attach a file to the chat: click the paperclip, pick, extract, chip.
$("chat-attach").addEventListener("click", () => $("chat-file").click());
$("chat-file").addEventListener("change", (event) => {
  const file = event.target.files && event.target.files[0];
  event.target.value = "";
  if (file) attachFile(file);
});

$("import-accept").addEventListener("click", acceptImport);
$("import-discard").addEventListener("click", discardImport);
function setChatOpen(open) {
  document.body.classList.toggle("chat-open", open);
  const button = $("chat-toggle");
  button.setAttribute("aria-pressed", open ? "true" : "false");
  button.setAttribute("aria-label", open ? "Hide chat" : "Show chat");
  button.title = open ? "Hide chat" : "Show chat";
  const use = button.querySelector("use");
  if (use) use.setAttribute("href", open ? "#i-sidebar-collapse" : "#i-sidebar-expand");
  try { localStorage.setItem(`${PREFS}.open`, open ? "1" : "0"); } catch { /* optional */ }
}

$("chat-toggle").addEventListener("click", () => {
  setChatOpen(!document.body.classList.contains("chat-open"));
});

(function startChat() {
  let open = true;
  try { open = localStorage.getItem(`${PREFS}.open`) !== "0"; } catch { /* default open */ }
  setChatOpen(open);
  loadProviders().catch((error) => setStatus(error.message, true));
})();
