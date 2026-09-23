"use strict";
/* Chat panel: talk to an LLM about the open document.
 *
 * Replies stream in as plain text. Text the model writes for a specific field
 * arrives as a ```proposal {"path","text"}``` block and is shown as a diff
 * card; nothing changes until the user accepts. Any other paragraph can be
 * dropped into a field with "Replace…", through the same diff and Accept.
 *
 * Shares the editor's globals: $, el, api, setStatus, header(), draw(),
 * schedule(), documentMode.
 */

const PROPOSAL = /```proposal\s*([\s\S]*?)```/g;
const chats = { cv: null, cover: null };
let chatController = null;
let pendingImport = null; // { mode, doc } shown in the preview until accepted
let fieldCache = null;

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

function fieldLabel(path) {
  const found = fieldCache && fieldCache.list.find((f) => f.path === path);
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

function applyText(path, text) {
  const doc = header();
  if (!canSet(doc, path)) throw new Error(`"${path}" is not a field in this document`);
  setPath(doc, path, text);
  fieldCache = null;
  $("name").value = header().name || "";
  draw();
  refreshLabels();
}

function proposalCard(path, text, { state, onResolve }) {
  const before = getPath(header(), path);
  const valid = canSet(header(), path);
  const card = el("div", { class: `chat-card ${state || ""}` });
  card.append(el("div", { class: "chat-card-head", text: valid ? fieldLabel(path) : `Unknown field: ${path}` }));
  card.append(diffView(typeof before === "string" ? before : "", text));
  if (state) {
    card.append(el("div", { class: "chat-card-state", text: state === "accepted" ? "Applied" : "Dismissed" }));
    return card;
  }
  const actions = el("div", { class: "chat-card-actions" });
  if (valid) {
    actions.append(el("button", { class: "tiny", text: "Accept", onclick: () => {
      try { applyText(path, text); onResolve("accepted"); }
      catch (error) { setStatus(error.message, true); }
    } }));
  }
  actions.append(el("button", { class: "tiny secondary", text: "Dismiss", onclick: () => onResolve("dismissed") }));
  card.append(actions);
  return card;
}

// "Replace…" on any paragraph: pick a field, see the diff, accept.
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

function renderAssistant(message, index, streaming) {
  const box = el("div", { class: "chat-msg assistant" });
  const content = message.content;
  let last = 0, n = 0;
  const addText = (text) => {
    for (const para of text.split(/\n{2,}/)) {
      if (!para.trim()) continue;
      const p = el("div", { class: "chat-para" }, [markdownBlock(para.trim())]);
      if (!streaming) {
        p.append(el("button", { class: "chat-replace-btn", text: "Replace…",
          onclick: () => replacePicker(plainText(para.trim()), p) }));
      }
      box.append(p);
    }
  };
  for (const match of content.matchAll(PROPOSAL)) {
    addText(content.slice(last, match.index));
    last = match.index + match[0].length;
    const key = String(n++);
    let parsed = null;
    try { parsed = JSON.parse(match[1]); } catch { /* shown as text below */ }
    if (parsed && typeof parsed.path === "string" && typeof parsed.text === "string") {
      const state = message.resolved && message.resolved[key];
      box.append(proposalCard(parsed.path, parsed.text, {
        state,
        onResolve: (result) => {
          message.resolved = { ...(message.resolved || {}), [key]: result };
          saveHistory();
          renderChat();
        },
      }));
    } else {
      addText(match[1]);
    }
  }
  const tail = content.slice(last);
  const open = tail.indexOf("```proposal");
  addText(open === -1 ? tail : tail.slice(0, open));
  if (open !== -1) box.append(el("div", { class: "chat-pending", text: "Writing a proposal…" }));
  if (streaming && !content) box.append(el("div", { class: "chat-pending", text: "Thinking…" }));
  return box;
}

// Style and Notes readiness, read once so the empty state can point at them.
let setupStatus = null;
fetch("/api/setup").then((r) => r.json()).then((d) => { setupStatus = d.status; renderChat(); })
  .catch(() => { /* the plain empty state still works */ });

function emptyState() {
  const doc = documentMode === "cover" ? "cover letter" : "CV";
  const box = el("div", { class: "chat-empty" });
  const style = setupStatus && setupStatus.style !== "empty";
  const notes = setupStatus && setupStatus.notes !== "empty";
  if (style && notes) {
    box.append(el("p", { text: `Your Style and Notes are loaded into every answer. ` +
      `Paste a job description below and ask me to tailor your ${doc}.` }));
    return box;
  }
  box.append(el("p", { text: `Ask about your ${doc}, paste a job description below, ` +
    "or upload an existing document." }));
  if (setupStatus) {
    const missing = [!style && ["Style", "/style"], !notes && ["Notes", "/notes"]].filter(Boolean);
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
  const state = chats[documentMode];
  host.textContent = "";
  if (!state) return;
  if (!state.messages.length) host.append(emptyState());
  state.messages.forEach((m, i) => {
    const isLast = i === state.messages.length - 1;
    host.append(m.role === "user"
      ? el("div", { class: "chat-msg user" }, [el("p", { text: m.content })])
      : renderAssistant(m, i, streaming && isLast));
  });
  host.scrollTop = host.scrollHeight;
}

/* ---------------------------------------------------------- persistence */

async function loadHistory(mode) {
  if (chats[mode]) return;
  try {
    const data = await fetch(`/api/chat/history?document=${mode}`).then((r) => r.json());
    chats[mode] = {
      messages: Array.isArray(data.messages) ? data.messages : [],
      job_description: typeof data.job_description === "string" ? data.job_description : "",
    };
  } catch {
    chats[mode] = { messages: [], job_description: "" };
  }
}

async function saveHistory() {
  const state = chats[documentMode];
  if (!state) return;
  try {
    await api("/api/chat/history", { document: documentMode, ...state });
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

function setBusy(busy) {
  $("chat-send").hidden = busy;
  $("chat-stop").hidden = !busy;
  $("chat-input").disabled = busy;
}

async function sendMessage() {
  const input = $("chat-input");
  const text = input.value.trim();
  const state = chats[documentMode];
  if (!text || !state || chatController) return;
  const agent = agentModel();
  if (!agent) { openAgent(); setStatus("Connect an agent first", true); return; }
  input.value = "";
  state.messages.push({ role: "user", content: text });
  const reply = { role: "assistant", content: "" };
  state.messages.push(reply);
  renderChat({ streaming: true });
  setBusy(true);
  chatController = new AbortController();
  const failed = (message) => {
    // Put the question back so it can be retried; providers expect turns to alternate.
    state.messages.splice(-2, 2);
    input.value = text;
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
        messages: state.messages.slice(0, -1).map(({ role, content }) => ({ role, content })),
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
  const jd = (chats[documentMode] && chats[documentMode].job_description) || "";
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
  chats[documentMode].job_description = text;
  drawJobTile();
  saveHistorySoon();
}

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
  await loadHistory(documentMode);
  $("chat-title").textContent = "Chat";
  drawJobTile();
  drawDocTile();
  showImportBar();
  renderChat();
  refreshLabels();
}

// Proposal cards show field names ("Professional Summary"), not paths.
function refreshLabels() {
  fields().then(() => renderChat()).catch(() => { /* paths still work as labels */ });
}

$("chat-send").addEventListener("click", sendMessage);
$("chat-stop").addEventListener("click", () => chatController && chatController.abort());
$("chat-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendMessage(); }
});
$("chat-clear").addEventListener("click", () => {
  if (!chats[documentMode].messages.length) return;
  if (!confirm("Clear this chat? The job description is kept.")) return;
  chats[documentMode].messages = [];
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
  $("jd-full").value = chats[documentMode].job_description;
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

$("import-accept").addEventListener("click", acceptImport);
$("import-discard").addEventListener("click", discardImport);
$("chat-toggle").addEventListener("click", () => {
  const open = document.body.classList.toggle("chat-open");
  $("chat-toggle").setAttribute("aria-pressed", open ? "true" : "false");
  try { localStorage.setItem(`${PREFS}.open`, open ? "1" : "0"); } catch { /* optional */ }
});

(function startChat() {
  let open = true;
  try { open = localStorage.getItem(`${PREFS}.open`) !== "0"; } catch { /* default open */ }
  document.body.classList.toggle("chat-open", open);
  $("chat-toggle").setAttribute("aria-pressed", open ? "true" : "false");
  loadProviders().catch((error) => setStatus(error.message, true));
})();
