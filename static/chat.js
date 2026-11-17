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

const BLOCK = /```(proposal|answer|notes|voice|gaps|ask|skill)\s*([\s\S]*?)```/g;
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
    // Appending is for a list of plain lines: a bullet, or a letter paragraph.
    // Only bullets used to qualify, so a letter could never gain a paragraph:
    // the model had one writable paragraph and an opening rule it could not
    // break, so it gave up and offered the whole letter to copy by hand.
    // A list of objects (contact) stays closed; a string there malforms it.
    if (last !== parent.length) return false;
    return parent.every((line) => typeof line === "string")
      && ["bullets", "paragraphs"].includes(parts[parts.length - 1]);
  }
  return typeof last === "string" && (typeof parent[last] === "string" || parent[last] === undefined);
}

// The CV carries every role the user has and prints the ones that are on, so a
// proposal can offer to put one on the page or take one off. It is the entry
// itself that moves, so the path has to name an entry in an entries list and
// nothing else: a bullet or a heading has no such state.
function canShow(doc, path) {
  const parts = pathParts(path);
  const last = parts.pop();
  if (typeof last !== "number" || parts[parts.length - 1] !== "entries") return false;
  const parent = parts.reduce((node, key) => (node == null ? undefined : node[key]), doc);
  if (!Array.isArray(parent) || last >= parent.length) return false;
  const entry = parent[last];
  return Boolean(entry) && typeof entry === "object" && !Array.isArray(entry);
}

function setShown(doc, path, show) {
  const entry = getPath(doc, path);
  if (show) delete entry.off;
  else entry.off = true;
}

// Removing is a different write from setting: a bullet has to leave the list,
// not become an empty line in it. So only something that sits in a list can be
// removed. Deleting a field of an object instead left the object missing a key
// the document requires, and every preview, save and export after it failed
// with "cv.sections[2].items[1].text: expected a string, got NoneType" — a
// labelled skills line with its text deleted out from under it.
function canRemove(doc, path) {
  const parts = pathParts(path);
  const last = parts.pop();
  const parent = parts.reduce((node, key) => (node == null ? undefined : node[key]), doc);
  if (!Array.isArray(parent)) return false;
  return typeof last === "number" && last < parent.length;
}

function removePath(doc, path) {
  const parts = pathParts(path);
  const last = parts.pop();
  parts.reduce((node, key) => node[key], doc).splice(last, 1);
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

function applyText(path, text, kind = documentMode, { remove = false, show = null } = {}) {
  const doc = docFor(kind);
  if (show !== null) {
    if (!canShow(doc, path)) throw new Error(`"${path}" is not an entry on that document`);
    setShown(doc, path, show);
  } else if (remove) {
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

function proposalCard(path, text, { state, onResolve, kind = documentMode, remove = false, show = null }) {
  const doc = docFor(kind);
  const before = getPath(doc, path);
  if (show !== null) return showCard(path, show, { state, onResolve, kind });
  const valid = remove ? canRemove(doc, path) : canSet(doc, path);
  const card = el("div", { class: `chat-card ${state || ""}` });
  const where = kind === documentMode ? "" : ` · ${kind === "cover" ? "cover letter" : "CV"}`;
  const head = remove ? `Remove ${fieldLabel(path, kind)}` : fieldLabel(path, kind) + where;
  // A removal can be refused for either reason, and the two need different
  // words: an invented path is unknown, while a real field that is not a list
  // item cannot be taken out at all.
  const refused = remove && getPath(doc, path) !== undefined
    ? `Cannot be removed, ${fieldLabel(path, kind)} is a field rather than an item in a list`
    : `Unknown field: ${path}`;
  card.append(el("div", { class: "chat-card-head", text: valid ? head : refused }));
  card.append(diffView(typeof before === "string" ? before : "", remove ? "" : text));
  if (state) {
    card.append(el("div", { class: "chat-card-state", text: state === "accepted" ? "Applied" : "Dismissed" }));
    return card;
  }
  const actions = el("div", { class: "chat-card-actions" });
  if (valid) {
    actions.append(el("button", { class: "tiny", text: "Use this", onclick: () => {
      try { applyText(path, text, kind, { remove }); onResolve("accepted"); }
      catch (error) { setStatus(error.message, true); }
    } }));
  }
  actions.append(el("button", { class: "tiny secondary", text: "Copy",
    onclick: (e) => copyText(text, e.target) }));
  actions.append(el("button", { class: "tiny secondary", text: "Skip", onclick: () => onResolve("dismissed") }));
  card.append(actions);
  return card;
}

// A role joining the page or leaving it. One page means the two usually come in
// pairs, but they are separate cards on purpose: the user decides each.
function showCard(path, show, { state, onResolve, kind }) {
  const doc = docFor(kind);
  const valid = canShow(doc, path);
  const entry = valid ? getPath(doc, path) : null;
  const name = entry ? String(entry.left || "").split(",")[0] : path;
  const card = el("div", { class: `chat-card ${state || ""}` });
  const where = kind === documentMode ? "" : ` · ${kind === "cover" ? "cover letter" : "CV"}`;
  card.append(el("div", { class: "chat-card-head",
    text: valid ? `${show ? "Put" : "Take"} ${name} ${show ? "on" : "off"} the CV${where}`
                : `Not an entry on this document: ${path}` }));
  if (valid && Boolean(entry.off) !== !show) {
    card.append(el("p", { class: "chat-answer-text",
      text: show ? "Already on the CV." : "Already off the CV." }));
  }
  if (state) {
    card.append(el("div", { class: "chat-card-state",
      text: state === "accepted" ? (show ? "Put on the CV" : "Taken off the CV") : "Dismissed" }));
    return card;
  }
  const actions = el("div", { class: "chat-card-actions" });
  if (valid) {
    actions.append(el("button", { class: "tiny", text: "Use this",
      onclick: () => {
        try { applyText(path, "", kind, { show }); onResolve("accepted"); }
        catch (error) { setStatus(error.message, true); }
      } }));
  }
  actions.append(el("button", { class: "tiny secondary", text: "Skip",
    onclick: () => onResolve("dismissed") }));
  card.append(actions);
  return card;
}

// Something about the candidate's own history, offered for their experience
// bank. Notes feed every future application, so this is the one thing the chat
// writes outside the open document, and only once the user has accepted it.
// A tool he said he has used, for the skills list in his notes: the only
// place the CV's skill lines are written from.
function skillCard(text, { state, onResolve }) {
  let skill = null;
  try { skill = JSON.parse(text); } catch { /* shown as text below */ }
  const card = el("div", { class: `chat-card notes ${state || ""}` });
  if (!skill || typeof skill.label !== "string" || typeof skill.tool !== "string") {
    card.append(el("p", { class: "chat-answer-text", text }));
    return card;
  }
  card.append(el("div", { class: "chat-card-head",
    text: state ? "For your skills list" : "For your skills list · not saved yet" }));
  card.append(el("p", { class: "chat-answer-text", text: `${skill.label}: ${skill.tool}` }));
  if (state) {
    card.append(el("div", { class: "chat-card-state",
      text: state === "accepted" ? "Added to Skills" : "Dismissed" }));
    return card;
  }
  card.append(el("div", { class: "chat-card-actions" }, [
    el("button", { class: "tiny", text: "Add to Skills", onclick: async (e) => {
      e.target.disabled = true;
      try {
        await api("/api/setup/notes", { skill: { label: skill.label, tool: skill.tool } });
        setStatus(`Added ${skill.tool} to your skills`);
        onResolve("accepted");
      } catch (error) {
        e.target.disabled = false;
        setStatus(`Could not add to skills: ${error.message}`, true);
      }
    } }),
    el("button", { class: "tiny secondary", text: "Skip", onclick: () => onResolve("dismissed") }),
  ]));
  return card;
}

function notesCard(text, { state, onResolve }) {
  const card = el("div", { class: `chat-card notes ${state || ""}` });
  // Says it is pending, because a card sitting in a column of applied
  // proposals looked applied. The model used to claim it had saved the fact,
  // the button went unpressed, and the fact never reached the file.
  card.append(el("div", { class: "chat-card-head",
    text: state ? "For your notes" : "For your notes · not saved yet" }));
  for (const para of text.split(/\n{2,}/)) {
    if (para.trim()) card.append(el("p", { class: "chat-answer-text", text: para.trim() }));
  }
  if (state) {
    card.append(el("div", { class: "chat-card-state",
      text: state === "accepted" ? "Added to Notes" : "Dismissed" }));
    // The fact is in the notes; whether to spend a round on it is a decision,
    // not a consequence. It used to run by itself on any accepted note, which
    // meant saving something in an ordinary conversation set the whole pass
    // going and rewrote work that was already settled.
    if (state === "accepted" && typeof tailorReady === "function" && tailorReady()) {
      card.append(el("div", { class: "chat-card-actions" }, [
        el("button", { class: "tiny", text: "Tailor again with this", onclick: (e) => {
          e.target.disabled = true;
          runTailor({ again: true });
        } }),
      ]));
    }
    return card;
  }
  card.append(el("div", { class: "chat-card-actions" }, [
    el("button", { class: "tiny", text: "Keep this", onclick: async (e) => {
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
    el("button", { class: "tiny secondary", text: "Skip",
      onclick: () => onResolve("dismissed") }),
  ]));
  return card;
}

// A rule the model noticed about how the user wants to write, offered for the
// Voice file (assistant.md). It is not a fact about their work — that is Notes —
// so it is written as an instruction, and only once accepted.
function voiceCard(text, { state, onResolve }) {
  const card = el("div", { class: `chat-card notes ${state || ""}` });
  card.append(el("div", { class: "chat-card-head",
    text: state ? "For your voice" : "For your voice · not saved yet" }));
  for (const para of text.split(/\n{2,}/)) {
    if (para.trim()) card.append(el("p", { class: "chat-answer-text", text: para.trim() }));
  }
  if (state) {
    card.append(el("div", { class: "chat-card-state",
      text: state === "accepted" ? "Added to Voice" : "Dismissed" }));
    return card;
  }
  card.append(el("div", { class: "chat-card-actions" }, [
    el("button", { class: "tiny", text: "Keep this", onclick: async (e) => {
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
    el("button", { class: "tiny secondary", text: "Skip",
      onclick: () => onResolve("dismissed") }),
  ]));
  return card;
}

// What the posting wants that the notes cannot support. Each line is a
// question, and answering one is the only typing this flow asks of the user:
// the answer goes into the chat, becomes a notes block, and once accepted
// starts another round that proposes what it now earns.
// "Do you have experience building with Drupal?" -> "I do not have experience
// building with Drupal." A no is a fact, and until it was written down the
// same question came back on every posting, because nothing in the notes said
// the answer.
function negative(question) {
  const asked = question.trim().replace(/[?.]+$/, "");
  for (const [pattern, opening] of [
    [/^do you have\b/i, "I do not have"],
    [/^do you\b/i, "I do not"],
    [/^have you ever\b/i, "I have never"],
    [/^have you\b/i, "I have not"],
    [/^are you\b/i, "I am not"],
    [/^can you\b/i, "I cannot"],
    [/^did you\b/i, "I did not"],
    [/^any\b/i, "I have no"],
  ]) {
    if (pattern.test(asked)) return `${opening}${asked.replace(pattern, "")}.`;
  }
  return `Not mine. ${asked}.`;
}

// One keyword from the posting's list that the notes do not answer, asked about
// the experience underneath it. "I have…" takes one sentence, written to the
// notes as typed; "I haven't" writes a plain no, so it is never asked again on
// any posting. Either way the keyword's line in the map is updated.
function askCard(keyword, question, { state, onResolve }) {
  const card = el("div", { class: `chat-card notes ${state || ""}` });
  card.append(el("div", { class: "chat-card-head", text: keyword }));
  card.append(el("p", { class: "chat-answer-text", text: question }));
  if (state) {
    card.append(el("div", { class: "chat-card-state",
      text: state === "accepted" ? "Added to your notes" : "Noted as a no" }));
    return card;
  }
  const actions = el("div", { class: "chat-card-actions" });
  const answer = el("textarea", { rows: 2, placeholder: "What you did, in a sentence" });
  answer.hidden = true;
  const send = async (action, extra, button) => {
    const agent = agentModel();
    button.disabled = true;
    try {
      keywordState = await api("/api/keywords", {
        action, keyword, app: APP, ...extra,
        ...(agent ? { provider: agent.provider, model: agent.model } : {}),
      }).then((r) => r.json());
      if (typeof updateScore === "function") updateScore();
      onResolve(action === "answer" ? "accepted" : "dismissed");
    } catch (error) {
      setStatus(error.message, true);
      button.disabled = false;
    }
  };
  const keep = el("button", { class: "tiny", text: "Keep this", onclick: (e) => {
    const sentence = answer.value.trim();
    if (sentence) send("answer", { sentence }, e.target);
  } });
  keep.hidden = true;
  actions.append(
    el("button", { class: "tiny", text: "I have…", onclick: (e) => {
      answer.hidden = false;
      keep.hidden = false;
      e.target.hidden = true;
      answer.focus();
    } }),
    el("button", { class: "tiny secondary", text: "I haven't", onclick: (e) =>
      send("decline", { line: declineLine(question) }, e.target) }),
    keep,
  );
  card.append(answer, actions);
  return card;
}

// The no that goes into the notes: the question's own answer, without the
// keyword the card leads with.
function declineLine(question) {
  return negative(String(question).replace(/^[^.?:]*[.:]\s+(?=\S)/, ""));
}

// What code found for each gap question before he is asked, by question:
// covered by his notes, answered by a past CV line, or open. Kept for the
// page's life, so a redraw does not ask again.
const gapChecks = new Map();

function checkGaps(questions) {
  const todo = questions.filter((q) => !gapChecks.has(q));
  if (!todo.length) return;
  for (const q of todo) gapChecks.set(q, null);
  api("/api/gaps", { action: "check", questions: todo })
    .then((r) => r.json())
    .then((found) => { for (const g of found) gapChecks.set(g.question, g); })
    .catch(() => { for (const q of todo) gapChecks.set(q, { question: q, status: "open" }); })
    .finally(() => renderChat());
}

function gapsCard(text, { state, onResolve }) {
  const questions = text.split("\n").map((line) => line.replace(/^[-*\d.\s]+/, "").trim())
    .filter(Boolean);
  const card = el("div", { class: `chat-card gaps ${state || ""}` });
  card.append(el("div", { class: "chat-card-head", text: "Gaps against this posting" }));
  if (state) {
    for (const question of questions) {
      card.append(el("p", { class: "chat-answer-text", text: question }));
    }
    card.append(el("div", { class: "chat-card-state", text: "Dismissed" }));
    return card;
  }
  checkGaps(questions);
  if (questions.some((q) => !gapChecks.get(q))) {
    card.append(el("p", { class: "chat-answer-text", text: "Checking your notes and past CVs…" }));
    return card;
  }
  const found = questions.map((q) => gapChecks.get(q));
  const covered = found.filter((g) => g.status === "covered").length;
  for (const gap of found) {
    if (gap.status === "past") card.append(pastRow(gap));
    else if (gap.status !== "covered") card.append(openRow(gap.question));
  }
  if (covered && covered < found.length) {
    card.append(el("div", { class: "chat-card-state",
      text: `${covered} more already answered by your notes, so not asked.` }));
  } else if (covered) {
    card.append(el("p", { class: "chat-answer-text", text: "Nothing to ask: your notes cover all of it." }));
  }
  card.append(el("div", { class: "chat-card-actions" }, [
    el("button", { class: "tiny secondary", text: "Copy",
      onclick: (e) => copyText(questions.join("\n"), e.target) }),
    el("button", { class: "tiny secondary", text: "Skip",
      onclick: () => onResolve("dismissed") }),
  ]));
  return card;
}

// A gap a past CV already answers: the line is quoted for him to confirm.
// Yes files it in his notes as his own words; no strikes it for good.
function pastRow(gap) {
  const row = el("div", { class: "chat-gap" }, [
    el("span", { class: "chat-gap-q", text: `Your past CVs say: "${gap.line}" Is that right?` }),
    el("button", { class: "tiny secondary", text: "That's right", onclick: async (e) => {
      e.target.disabled = true;
      try {
        await api("/api/gaps", { action: "confirm", line: gap.line });
        gapChecks.set(gap.question, { ...gap, status: "covered" });
        setStatus("Added to your notes");
        renderChat();
      } catch (error) {
        e.target.disabled = false;
        setStatus(`Could not add to notes: ${error.message}`, true);
      }
    } }),
    el("button", { class: "tiny secondary", text: "Not true", onclick: async (e) => {
      e.target.disabled = true;
      try {
        await api("/api/library", { action: "strike", text: gap.line });
        gapChecks.set(gap.question, { question: gap.question, status: "open" });
        renderChat();
      } catch (error) {
        e.target.disabled = false;
        setStatus(`Could not strike it: ${error.message}`, true);
      }
    } }),
  ]);
  return row;
}

function openRow(question) {
  const row = el("div", { class: "chat-gap" }, [
    el("span", { class: "chat-gap-q", text: question }),
    el("button", { class: "tiny secondary", text: "I have this", onclick: () => {
      const input = $("chat-input");
      input.value = joinText(input.value, question);
      input.focus();
    } }),
    el("button", { class: "tiny secondary", text: "I haven't", onclick: async (e) => {
      const line = negative(question);
      e.target.disabled = true;
      try {
        await api("/api/setup/notes", { append: line });
        row.textContent = "";
        row.append(el("span", { class: "chat-gap-q", text: line }),
                   el("span", { class: "chat-card-state", text: "In your notes" }));
        setStatus("Added to your notes");
      } catch (error) {
        e.target.disabled = false;
        setStatus(`Could not add to notes: ${error.message}`, true);
      }
    } }),
  ]);
  return row;
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
function renderUser(message, index) {
  // A tailoring round is a turn the user did not type. Showing it as their own
  // message would read as words put in their mouth, so it shows as a note that
  // the round ran. Once per round: every suggestion is its own request, and a
  // note before each one read as the posting being read again after every yes.
  if (message.pipeline) {
    const previous = chat && chat.messages[index - 1];
    if (previous && previous.pipeline) return el("div", { hidden: true });
    return el("div", { class: "chat-round", text: "Working from the posting" });
  }
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
  // A section arrives as several proposals in one message. The round waits
  // for all of them, and one button takes them together.
  const proposals = [];
  const settle = (result, path) => {
    if (index !== chat.messages.length - 1) return;
    const resolved = message.resolved || {};
    if (!proposals.every((p) => resolved[p.key])) return;
    const any = proposals.some((p) => resolved[p.key] === "accepted");
    cardResolved(proposals.length > 1 ? (any ? "accepted" : "dismissed") : result, path);
  };
  const addText = (text) => {
    for (const para of text.split(/\n{2,}/)) {
      if (!para.trim()) continue;
      box.append(el("div", { class: "chat-para" }, [markdownBlock(para.trim())]));
    }
  };
  for (const match of content.matchAll(BLOCK)) {
    addText(content.slice(last, match.index));
    last = match.index + match[0].length;
    if (message.section && !sectionAllows(message.section, match[1], match[2])) {
      if (match[1] !== "answer") n++;   // keys stay where the history put them
      continue;
    }
    if (match[1] === "answer") {
      box.append(answerCard(match[2].trim()));
      continue;
    }
    if (match[1] === "ask") {
      const key = String(n++);
      let asked = null;
      try { asked = JSON.parse(match[2]); } catch { /* shown as text below */ }
      if (!asked || typeof asked.keyword !== "string" || typeof asked.question !== "string") {
        addText(match[2]);
        continue;
      }
      box.append(askCard(asked.keyword, asked.question, {
        state: message.resolved && message.resolved[key],
        onResolve: (result) => {
          message.resolved = { ...(message.resolved || {}), [key]: result };
          saveHistory();
          renderChat();
          if (index === chat.messages.length - 1) cardResolved(result, asked.keyword);
        },
      }));
      continue;
    }
    if (["notes", "voice", "gaps", "skill"].includes(match[1])) {
      const key = String(n++);
      const card = { notes: notesCard, voice: voiceCard, gaps: gapsCard, skill: skillCard }[match[1]];
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
    const wantsShown = parsed && typeof parsed.show === "boolean" ? parsed.show : null;
    if (parsed && typeof parsed.path === "string"
        && (wantsRemoval || wantsShown !== null || typeof parsed.text === "string")) {
      const state = message.resolved && message.resolved[key];
      proposals.push({ key, path: parsed.path, text: parsed.text || "",
                       plain: !wantsRemoval && wantsShown === null });
      box.append(proposalCard(parsed.path, parsed.text || "", {
        state,
        kind,
        remove: wantsRemoval,
        show: wantsShown,
        onResolve: (result) => {
          message.resolved = { ...(message.resolved || {}), [key]: result };
          saveHistory();
          renderChat();
          settle(result, parsed.path);
        },
      }));
    } else {
      addText(match[2]);
    }
  }
  const waiting = proposals.filter((p) => p.plain && !(message.resolved || {})[p.key]);
  if (!streaming && waiting.length > 1) {
    box.append(el("div", { class: "chat-card-actions" }, [
      el("button", { class: "tiny", text: "Use all", onclick: () => {
        // In order: bullets.1 can only be added once bullets.0 is there.
        for (const p of waiting) {
          try { applyText(p.path, p.text, kind); }
          catch (error) { setStatus(error.message, true); break; }
          message.resolved = { ...(message.resolved || {}), [p.key]: "accepted" };
        }
        saveHistory();
        renderChat();
        settle("accepted", waiting[0].path);
      } }),
    ]));
  }
  const tail = content.slice(last);
  const open = tail.search(/```(proposal|answer|notes|voice|gaps|skill)/);
  addText(open === -1 ? tail : tail.slice(0, open));
  if (open !== -1 && streaming) {
    box.append(el("div", { class: "chat-pending", text: "Writing a proposal…" }));
  } else if (open !== -1) {
    const cut = el("div", { class: "chat-card" });
    cut.append(el("div", { class: "chat-card-head", text: "This reply was cut off" }));
    cut.append(el("p", { class: "chat-answer-text",
      text: "It stopped in the middle of a card, so there is nothing to apply." }));
    cut.append(el("div", { class: "chat-card-actions" }, [
      el("button", { class: "tiny", text: "Try again", onclick: () => retryTurn(index) }),
    ]));
    box.append(cut);
  }
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
    .replace(/```(?:proposal|notes|voice|gaps|skill)\s*[\s\S]*?```/g, "")
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

// Ask again for whatever that turn was, having dropped what arrived of it. A
// round's turn is one nobody typed, so retrying it means running the round
// again rather than resending a question the user never asked.
async function retryTurn(index) {
  if (!chat || chatController) return;
  const message = chat.messages[index];
  if (!message) return;
  const wasRound = Boolean(message.pipeline);
  const asked = chat.messages[index - 1];
  chat.messages.splice(index - 1, 2);
  renderChat();
  saveHistory();
  if (wasRound) { runTailor(); return; }
  if (asked && asked.role === "user") {
    $("chat-input").value = userParts(asked.content).text || "";
    sendMessage();
  }
}

function renderChat({ streaming = false } = {}) {
  const host = $("chat-log");
  const state = chat;
  // Follow new text only when he was already at the end; pressing a card's
  // button further up left him there, not at the bottom.
  const top = host.scrollTop;
  const atEnd = host.scrollHeight - top - host.clientHeight < 40;
  host.textContent = "";
  if (!state) return;
  if (!state.messages.length) host.append(emptyState());
  state.messages.forEach((m, i) => {
    const isLast = i === state.messages.length - 1;
    host.append(m.role === "user" ? renderUser(m, i) : renderAssistant(m, i, streaming && isLast));
  });
  host.scrollTop = atEnd ? host.scrollHeight : top;
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
  providers = (await api("/api/llm/providers").then((r) => r.json()))
    .filter((p) => p.kind !== "judge");
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

// Sending retires dictation. The local recorder rewrites the whole box on
// every pass, so left running it puts the sent words straight back into the
// box the send just cleared, again every pass until the mic is clicked off.
function cancelDictation() {
  if (listener) listener.stop();
  listener = null;
  if (recorder) {
    recorder.cancelled = true;
    stopRecording();
  }
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
    if (listener !== speech) return;   // retired by a send
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
  if (rec.cancelled) return;   // sent while this pass was in flight
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
    if (!rec.cancelled) await transcribeChunk(rec);   // a last pass over the full utterance
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

// What every turn sends. The tailoring rounds differ from a typed message by
// one field, so they share this rather than keeping a second copy in step.
function chatBody(state, agent, extra = {}) {
  return {
    document: documentMode,
    provider: agent.provider,
    model: agent.model,
    doc: header(),
    job_description: state.job_description,
    // What the preview measured, so the model knows the shape of the page.
    pages: typeof previewPages === "number" ? previewPages : 0,
    overflowing: typeof overflowing !== "undefined" ? overflowing : [],
    over_budget: typeof overBudget !== "undefined"
      ? overBudget.map((o) => `${o.label} bullet ${o.bullet} runs ${o.lines} lines, allowed ${o.allowed}`)
      : [],
    // What this posting requires, if it has been read. Without it the model
    // works from the notes alone, and the notes hold work done for every
    // other posting too.
    wanted: keywordList() || [],
    // resolved rides along so the server can drop already-applied proposals
    // from the transcript it sends on; undefined is omitted by stringify.
    messages: state.messages.slice(0, -1)
      .map(({ role, content, resolved }) => ({ role, content, resolved })),
    ...extra,
  };
}

// Stream a reply into `reply.content`, redrawing as it arrives. Throws on a
// refused request; a failure mid-stream arrives as text and is shown as such.
async function streamReply(reply, body) {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal: chatController.signal,
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ error: response.statusText }));
    throw new Error(detail.error || "chat failed");
  }
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
}

async function sendMessage() {
  cancelDictation();
  // The round is waiting on a card and the user has typed instead. That is an
  // answer of its own: they have taken over, so the round stops here.
  // While a section is on screen, typing asks for changes to it; the round
  // waits for the yes on the section that comes back.
  if (awaitingCard && !currentSection) cardResolved("stopped");
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
  if (currentSection) reply.section = { ...currentSection };
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
    await streamReply(reply, chatBody(state, agent, sectionTask()));
    if (!reply.content.trim()) state.messages.splice(-2, 2);
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

/* ----------------------------------------------------------- tailoring

   Pasting the posting is the instruction, so the round runs on its own. It is
   a conversation of one suggestion at a time, in three steps: which roles
   belong on this CV, then the wording of the ones that stayed, then making it
   fit the page. Each step asks for one card, waits for the yes or no, and asks
   again with that answer in hand. A page of cards at once was work done before
   the user had agreed to any of it, and a no has to change what comes next. */

// What the chat shows as the reason the round ran. Short on purpose: it is
// stored in the transcript and resent with every later turn.
const TAILOR_ASK = "Tailor this CV to the posting.";
// The steps, in order. "again" is not one of them: it runs on its own when a
// note is accepted while a gap is open.
const TAILOR_STEPS = ["cast", "wording", "fit"];
// Suggestions one step may make before it is cut off. A step normally ends by
// having nothing left to say; this is for the case where it never stops.
const MAX_STEP_CARDS = 8;
// Survives the reload that naming a new application performs.
const TAILOR_PENDING = "resume-maker.tailor-pending";
// Paths and entries the user has turned down in this round. A no is an answer,
// and the same suggestion two cards later makes the round a form.
let tailorDeclined = [];
let tailorRunning = false;
// Resolves when the user answers the card the round is waiting on.
let awaitingCard = null;

const tailorReady = () => Boolean(
  chat && (chat.job_description || "").trim() && documentMode !== "cover"
  && !chatController && agentModel() && !tailorRunning);

const STEP_STATUS = {
  cast: "reading the posting against your experience…",
  wording: "working on the wording…",
  summary: "writing your summary…",
  skills: "writing your skills…",
  bullets: "writing this job's bullets…",
  additional: "writing additional information…",
  fit: "making it fit the page…",
  again: "working from what you just told me…",
  gaps: "checking the finished CV against the posting…",
  lengthen: "using the room left on the page…",
};

// A card the round is waiting on calls this when the user answers it.
function cardResolved(result, what) {
  if (result === "dismissed" && what) {
    tailorDeclined.push(what);
    // Kept with the posting, so the next round does not offer it again.
    api("/api/keywords", { action: "skip", path: what, app: APP })
      .then((r) => r.json()).then((state) => { keywordState = state; })
      .catch(() => { /* this round still remembers it */ });
  }
  const waiting = awaitingCard;
  awaitingCard = null;
  if (waiting) waiting(result, what);
}

// Whether the reply that just arrived offered anything. A step that offers no
// card has nothing left to say, which is how it ends.
function offeredCard(content, section) {
  for (const match of String(content || "").matchAll(BLOCK)) {
    if (section && !sectionAllows(section, match[1], match[2])) continue;
    if (match[1] === "proposal" || match[1] === "gaps" || match[1] === "ask") return match[1];
  }
  return null;
}

async function askStep(phase, extra = {}) {
  const state = chat;
  const agent = agentModel();
  state.messages.push({ role: "user", content: TAILOR_ASK,
                        document: documentMode, pipeline: true });
  const reply = { role: "assistant", content: "", document: documentMode,
                  pipeline: true };
  if (writingSections) reply.section = { phase, ...extra };
  state.messages.push(reply);
  renderChat({ streaming: true });
  setBusy(true);
  setStatus(STEP_STATUS[phase] || STEP_STATUS.wording);
  chatController = new AbortController();
  try {
    await streamReply(reply, chatBody(state, agent, {
      task: "tailor", phase, ...extra,
      settled: settledPaths(),
      declined: tailorDeclined,
    }));
    if (!reply.content.trim()) {
      state.messages.splice(-2, 2);
      return "empty";
    }
    return offeredCard(reply.content, reply.section) || "empty";
  } catch (error) {
    if (error.name === "AbortError") {
      state.messages.splice(-2, 2);
      return "stopped";
    }
    // The turn stays, carrying why it failed. Removing it left the user with
    // an empty panel and a toast the next step overwrote.
    reply.content = reply.content.trim()
      ? `${reply.content}\n\nThis step stopped here. ${error.message}`
      : `This step could not run. ${error.message}`;
    reply.failed = true;
    setStatus(error.message, true);
    return "failed";
  } finally {
    chatController = null;
    setBusy(false);
    if ($("toast").dataset.state === "busy") showProgress(null);
    renderChat();
    refreshLabels();
    saveHistory();
  }
}

// One step: suggest, wait, suggest again, until it has nothing left to offer.
async function runStep(phase) {
  for (let card = 0; card < MAX_STEP_CARDS; card += 1) {
    const kind = await askStep(phase);
    if (kind === "stopped" || kind === "failed") return false;  // the round ends
    if (kind === "empty") return true;   // nothing left to suggest: step done
    if (kind === "gaps") return true;    // a question ends the step
    const answered = await new Promise((resolve) => { awaitingCard = resolve; });
    if (answered === "stopped") return false;
  }
  return true;
}

async function runTailor({ again = false } = {}) {
  if (!tailorReady()) return;
  tailorRunning = true;
  showTailorButton();
  if (!again) tailorDeclined = keywordState.current ? [...(keywordState.skipped || [])] : [];
  try {
    if (!again && nextSection(new Set())) await runSections();
    else {
      for (const phase of (again ? ["again"] : TAILOR_STEPS)) {
        const finished = await runStep(phase);
        if (!finished) break;
      }
    }
  } finally {
    tailorRunning = false;
    awaitingCard = null;
    currentSection = null;
    showTailorButton();
  }
}

/* A new CV opens on a blank frame and is written here, one section at a time,
   top to bottom: the summary, the skills, which roles are on, each job's
   bullets, Additional Information. Each is offered whole, and the next one
   waits for his yes. Typing asks for changes to the section on screen. */

// The section on screen, so a typed change is written for it and nothing else.
let currentSection = null;
// While a blank CV is being written, every reply is fenced to its section.
let writingSections = false;

const sectionOf = (doc, word) => ((doc && doc.sections) || []).findIndex(
  (s) => String(s.heading || "").toUpperCase().includes(word));
const blank = (text) => !String(text || "").trim();

// The first section still to write, or null. ``handled`` holds the ones
// already offered this round, so one he skipped is left empty, not re-asked.
function nextSection(handled) {
  const doc = typeof cv !== "undefined" ? cv : null;
  if (!doc) return null;
  const todo = [];
  const summary = sectionOf(doc, "SUMMARY");
  if (summary >= 0 && blank(doc.sections[summary].text)) todo.push({ phase: "summary" });
  const skills = sectionOf(doc, "SKILL");
  if (skills >= 0 && (doc.sections[skills].items || []).some((i) => blank(i.text))) {
    todo.push({ phase: "skills" });
  }
  const at = sectionOf(doc, "EXPERIENCE");
  const entries = at >= 0 ? doc.sections[at].entries || [] : [];
  const shown = entries.map((e, j) => [e, j]).filter(([e]) => !e.off);
  const empty = shown.filter(([e]) => !(e.bullets || []).length);
  // Roles first, while no job has bullets: a job taken off after its bullets
  // were written is work thrown away.
  if (empty.length && empty.length === shown.length) todo.push({ phase: "cast" });
  for (const [, j] of empty) todo.push({ phase: "bullets", entry: `sections.${at}.entries.${j}` });
  const extra = sectionOf(doc, "ADDITIONAL");
  if (extra >= 0 && (doc.sections[extra].items || []).some((i) => blank(i.text))) {
    todo.push({ phase: "additional" });
  }
  const key = (step) => `${step.phase} ${step.entry || ""}`;
  return todo.find((step) => !handled.has(key(step))) || null;
}

// What a reply written for one section may put in front of him. The frame
// (company, title, place, dates, skill labels, Education) is never offered,
// and gaps, notes and asks wait for the last turn, whatever the model writes.
function sectionAllows(section, kind, body) {
  if (kind === "answer") return true;
  if (section.phase === "gaps") return ["gaps", "ask", "notes", "skill"].includes(kind);
  if (kind !== "proposal") return false;
  let parsed = null;
  try { parsed = JSON.parse(body); } catch { return false; }
  if (!parsed || typeof parsed.path !== "string") return false;
  const doc = typeof cv !== "undefined" ? cv : null;
  const at = (word) => sectionOf(doc, word);
  const entry = String(section.entry || "").replace(/\./g, "\\.");
  const fields = {
    summary: `sections\\.${at("SUMMARY")}\\.text`,
    skills: `sections\\.${at("SKILL")}\\.items\\.\\d+\\.text`,
    cast: `sections\\.${at("EXPERIENCE")}\\.entries\\.\\d+`,
    bullets: `${entry}\\.bullets\\.\\d+`,
    additional: `sections\\.${at("ADDITIONAL")}\\.items\\.\\d+\\.text`,
    fit: `sections\\.${at("EXPERIENCE")}\\.entries\\.\\d+\\.bullets\\.\\d+`
      + `|sections\\.${at("SUMMARY")}\\.text`,
    lengthen: `sections\\.${at("EXPERIENCE")}\\.entries\\.(?!${headlineEntry(doc).index}\\.)\\d+`
      + `\\.bullets\\.\\d+`,
  }[section.phase];
  if (!fields || !new RegExp(`^(?:${fields})$`).test(parsed.path)) return false;
  // Roles are only shown or hidden; everything else is new text.
  return (section.phase === "cast") === (typeof parsed.show === "boolean");
}

async function runSections() {
  writingSections = true;
  try { await writeSections(); } finally { writingSections = false; }
}

async function writeSections() {
  const handled = new Set();
  for (let step = nextSection(handled); step; step = nextSection(handled)) {
    handled.add(`${step.phase} ${step.entry || ""}`);
    if (step.phase === "cast") {
      if (!await runStep("cast")) return;
      continue;
    }
    currentSection = step;
    const kind = await askStep(step.phase, step.entry ? { entry: step.entry } : {});
    if (kind === "stopped" || kind === "failed") return;
    if (kind !== "empty") {
      const answered = await new Promise((resolve) => { awaitingCard = resolve; });
      if (answered === "stopped") return;
    }
    currentSection = null;
  }
  // The page decides what comes next, not the model: over one page is
  // trimmed, a page with lines to spare may take one longer bullet.
  await measured();
  if (pagesNow() > 1) {
    if (!await runStep("fit")) return;
  } else if (roomNow() >= 1 && !longBulletShown()) {
    if (!await lengthenOne()) return;
    await measured();
    if (pagesNow() > 1 && !await runStep("fit")) return;
  }
  await askStep("gaps");
}

const pagesNow = () => (typeof previewPages === "number" ? previewPages : 1);
const roomNow = () => (typeof previewRoom === "number" ? previewRoom : 0);
const measured = () => (typeof nextMeasure === "function" ? nextMeasure() : null);

// The first role shown under Experience: the headline, with its own budget.
function headlineEntry(doc) {
  const at = sectionOf(doc, "EXPERIENCE");
  const entries = at >= 0 ? doc.sections[at].entries || [] : [];
  return { at, index: entries.findIndex((e) => !e.off), entries };
}

// Whether a role other than the headline already has its one longer bullet.
function longBulletShown() {
  const { index, entries } = headlineEntry(cv);
  return entries.some((e, j) => j !== index && !e.off && e.budget && e.budget.long > 0);
}

// One bullet outside the headline written to three lines, if he says yes.
async function lengthenOne() {
  const kind = await askStep("lengthen");
  if (kind === "stopped" || kind === "failed") return false;
  if (kind !== "proposal") return true;
  const [answered, path] = await new Promise((resolve) => {
    awaitingCard = (result, what) => resolve([result, what]);
  });
  if (answered === "stopped") return false;
  if (answered === "accepted" && path) makeLong(path);
  return true;
}

// The longer bullet moves to the front of its job, where the budget's long
// allowance applies, and the job is allowed it.
function makeLong(path) {
  const found = /^sections\.(\d+)\.entries\.(\d+)\.bullets\.(\d+)$/.exec(path);
  const entry = found && ((cv.sections[Number(found[1])] || {}).entries || [])[Number(found[2])];
  if (!entry || !Array.isArray(entry.bullets) || entry.bullets[Number(found[3])] === undefined) return;
  const [bullet] = entry.bullets.splice(Number(found[3]), 1);
  entry.bullets.unshift(bullet);
  entry.budget = { ...(entry.budget || {}), long: 1 };
  if (typeof draw === "function") { fieldCache = null; draw(); }
}

// What a typed message carries while a section is on screen: the same
// instruction, so the reply is the whole section again with his changes.
function sectionTask() {
  if (!currentSection) return {};
  return { task: "tailor", phase: currentSection.phase,
           ...(currentSection.entry ? { entry: currentSection.entry } : {}),
           settled: settledPaths(), declined: tailorDeclined };
}

// Every field the user has decided on in this conversation, by path. The
// transcript cannot show this: a resolved proposal is replaced by a marker
// before it is sent, so the model can see that something was applied there and
// not what. Without the list it rewrote settled fields differently each round.
function settledPaths() {
  const paths = new Set();
  for (const message of (chat && chat.messages) || []) {
    if (message.role !== "assistant") continue;
    const resolved = message.resolved || {};
    let n = 0;
    for (const match of String(message.content || "").matchAll(BLOCK)) {
      if (match[1] === "answer") continue;
      const key = String(n++);
      if (match[1] !== "proposal" || resolved[key] !== "accepted") continue;
      try {
        const parsed = JSON.parse(match[2]);
        if (parsed && typeof parsed.path === "string") paths.add(parsed.path);
      } catch { /* shown as text in the transcript; not a field */ }
    }
  }
  return [...paths];
}

// Naming a new application reloads the page at its real slug, so the round
// cannot start before the reload. The flag carries the intent across it.
function tailorAfterReload() {
  try {
    if (sessionStorage.getItem(TAILOR_PENDING) !== "1") return;
    sessionStorage.removeItem(TAILOR_PENDING);
  } catch { return; }
  runTailor();
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
  showTailorButton();
}

// Starts the round on the posting and keyword list already kept. A round used
// to start only when a new posting was pasted, so a reload mid-round lost it
// and pasting the same posting again did nothing.
function showTailorButton() {
  $("chat-tailor").hidden = !tailorReady();
}

function setJobDescription(text) {
  const fresh = text.trim() && text.trim() !== (chat.job_description || "").trim();
  chat.job_description = text;
  drawJobTile();
  saveHistorySoon();
  if (typeof scoreSoon === "function") scoreSoon();
  if (!fresh) return;
  tailorDeclined = [];                    // a new posting, a clean slate of noes
  // Naming happens first: it can re-seed the CV from the newest application of
  // the same track, and tailoring a document about to be replaced is wasted.
  // A named application gets a fresh keyword list for the new posting before
  // the round, which works down that list. An unnamed one is read by naming.
  nameFromJob(text).then(async (moved) => {
    if (moved) return;
    await readKeywords({ fresh: true });
    runTailor();
  });
}

// A new application has no name until a job description arrives: the posting
// says who the company is and what the role is called, so the application,
// the cover letter's address and the exported filenames all come from it.
async function nameFromJob(text) {
  if (!document.body.dataset.unnamed || !text.trim()) return false;
  const agent = agentModel();
  if (!agent) { setStatus("Connect an agent to name this application", true); return; }
  delete document.body.dataset.unnamed;  // one attempt, not one per keystroke
  setStatus("Reading the job description…");
  try {
    // Save first. Naming an application re-seeds the documents that still
    // match the bytes they were created with, and an edit made before the
    // posting arrived is exactly that case: it lived in the draft, the file
    // was untouched, and the re-seed copied another application's CV over
    // the top of it. Written to disk, it is no longer a candidate.
    if (typeof saveNow === "function") {
      try { await saveNow({ build: false }); } catch { /* re-seed may win; the draft still has it */ }
    }
    const found = await api("/api/apps", {
      action: "identify", slug: APP, job_description: text,
      provider: agent.provider, model: agent.model,
    }).then((r) => r.json());
    await saveHistory();  // the chat follows the folder to its new name
    // The round runs after the reload, on the document the re-seed left behind.
    try { sessionStorage.setItem(TAILOR_PENDING, "1"); } catch { /* it just won't run */ }
    window.location.replace(`/${encodeURIComponent(found.application.slug)}${documentPath()}`);
    return true;
  } catch (error) {
    document.body.dataset.unnamed = "1";
    setStatus(`Could not name this application: ${error.message}`, true);
  }
  return false;
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
  // The list before the round: the round works down it.
  await ensureKeywords();
  tailorAfterReload();
}

// Proposal cards show field names ("Professional Summary"), not paths.
function refreshLabels() {
  fields().then(() => renderChat()).catch(() => { /* paths still work as labels */ });
}

$("chat-send").addEventListener("click", sendMessage);
$("chat-tailor").addEventListener("click", () => runTailor());
// The mic is offered when either path works: a local engine (asked of the
// server) or the browser's own recognition. Hidden only when neither is there.
$("chat-mic").addEventListener("click", toggleDictation);
fetch("/api/transcribe").then((r) => r.json())
  .then((d) => { transcribeReady = Boolean(d.available); })
  .catch(() => { /* no endpoint: keep the browser path */ })
  .finally(() => { $("chat-mic").hidden = !(transcribeReady || Dictation); });
$("chat-stop").addEventListener("click", () => {
  // Ends the round as well as the request in flight: the round is a sequence
  // of requests, so aborting one of them alone just starts the next.
  if (awaitingCard) cardResolved("stopped");
  tailorRunning = false;
  if (chatController) chatController.abort();
});
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
