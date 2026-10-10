"use strict";
/* Shared behaviour for the setup pages (Style, Notes).
 *
 * Each page supplies which file it edits ("assistant" or "notes") and the
 * elements to bind; this handles loading, saving, the unsaved marker, the
 * readiness pill, the character count, and Edit / Preview for markdown. */

const LABELS = { empty: "Empty", started: "Started", ready: "Ready" };

const $ = (id) => document.getElementById(id);

// The application whose documents the chat on this page edits.
const APP = document.body.dataset.app || "";
const withApp = (path) => (APP ? `${path}${path.includes("?") ? "&" : "?"}app=${encodeURIComponent(APP)}` : path);

function el(tag, attrs = {}, kids = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "text") node.textContent = value;
    else if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value != null) node.setAttribute(key, value);
  }
  for (const kid of kids) node.append(kid);
  return node;
}

// The same toast the editor uses, so "Saved." is noticed rather than
// whispered into the status line at the bottom of the page.
let toastTimer;

function showProgress(text, state) {
  const toast = document.getElementById("toast");
  if (!toast) return;
  clearTimeout(toastTimer);
  if (!state) { toast.classList.remove("show"); delete toast.dataset.state; return; }
  toast.dataset.state = state;
  toast.querySelector(".toast-text").textContent = text;
  toast.classList.add("show");
  if (state === "done") toastTimer = setTimeout(() => showProgress(null), 1800);
}

function setStatus(text, bad = false) {
  if (!text) return showProgress(null);
  showProgress(text, bad ? "error" : text.endsWith("…") ? "busy" : "done");
}

async function api(path, body) {
  const options = body === undefined
    ? {}
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || response.statusText);
  }
  return response;
}

async function apiJson(path, body) {
  return (await api(path, body)).json();
}

/* Safe markdown subset — same approach as chat.js: build nodes, never
   innerHTML, so file content cannot inject markup. Prefixed so chat.js
   can load on the same page without clobbering these. */
const SETUP_INLINE = /(\*\*[^*]+\*\*|__[^_]+__|(?<![*\w])\*[^*\n]+\*(?!\w)|`[^`]+`)/g;

function setupInlineNodes(text) {
  const out = [];
  for (const part of text.split(SETUP_INLINE)) {
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

function setupMarkdownBlock(text) {
  const lines = text.split("\n");
  const bullet = /^\s*[-*•]\s+/;
  const numbered = /^\s*\d+[.)]\s+/;
  if (lines.every((l) => bullet.test(l) || !l.trim())) {
    const list = el("ul", { class: "md-list" });
    for (const line of lines.filter((l) => l.trim())) {
      list.append(el("li", {}, setupInlineNodes(line.replace(bullet, ""))));
    }
    return list;
  }
  if (lines.every((l) => numbered.test(l) || !l.trim())) {
    const list = el("ol", { class: "md-list" });
    for (const line of lines.filter((l) => l.trim())) {
      list.append(el("li", {}, setupInlineNodes(line.replace(numbered, ""))));
    }
    return list;
  }
  const heading = lines[0].match(/^\s*(#{1,6})\s+(.*)$/);
  if (heading) {
    const level = Math.min(heading[1].length, 3);
    const group = el("div", { class: "md-group" },
      [el(`h${level}`, { class: "md-heading" }, setupInlineNodes(heading[2]))]);
    const rest = lines.slice(1).join("\n").trim();
    if (rest) group.append(setupMarkdownBlock(rest));
    return group;
  }
  return el("p", { class: "md-p" }, setupInlineNodes(text));
}

function renderMarkdown(text, host) {
  host.replaceChildren();
  const trimmed = text.trim();
  if (!trimmed) {
    host.append(el("p", { class: "md-empty", text: "Nothing to preview yet." }));
    return;
  }
  for (const para of trimmed.split(/\n{2,}/)) {
    if (!para.trim()) continue;
    host.append(setupMarkdownBlock(para.trim()));
  }
}

function setupPage({ kind, statusKey }) {
  const editor = $("editor");
  const preview = $("preview");
  const note = $("note");
  const pill = $("state");
  const editBtn = $("view-edit");
  const previewBtn = $("view-preview");
  let saved = "";
  let view = "edit";

  const dirty = () => editor.value.trim() !== saved.trim();

  function paintState(status) {
    if (!status || !pill) return;
    pill.dataset.state = status[statusKey];
    pill.querySelector(".label").textContent = LABELS[status[statusKey]];
  }

  function say(text, bad = false, { toast = true } = {}) {
    note.textContent = text;
    note.classList.toggle("bad", bad);
    if (toast && text) setStatus(text, bad);
  }

  function count() {
    const chars = editor.value.trim().length;
    $("count").textContent = `${chars.toLocaleString()} characters${dirty() ? " · unsaved" : ""}`;
  }

  function paintPreview() {
    if (preview) renderMarkdown(editor.value, preview);
  }

  function setView(mode) {
    view = mode === "preview" ? "preview" : "edit";
    const editing = view === "edit";
    editor.hidden = !editing;
    if (preview) preview.hidden = editing;
    if (editBtn) {
      editBtn.classList.toggle("active", editing);
      editBtn.setAttribute("aria-pressed", String(editing));
    }
    if (previewBtn) {
      previewBtn.classList.toggle("active", !editing);
      previewBtn.setAttribute("aria-pressed", String(!editing));
    }
    if (!editing) paintPreview();
    // Drop keyboard focus so Pico's focus ring never wraps the pair.
    if (document.activeElement && document.activeElement.blur) {
      document.activeElement.blur();
    }
  }

  async function load() {
    try {
      const data = await apiJson("/api/setup");
      saved = data[kind] || "";
      editor.value = saved;
      paintState(data.status);
      say(saved ? "" : "Nothing saved yet.", false, { toast: false });
    } catch (error) {
      say(`Could not load: ${error.message}`, true);
    }
    count();
    if (view === "preview") paintPreview();
  }

  async function save() {
    say("Saving…");
    try {
      const data = await apiJson(`/api/setup/${kind}`, { text: editor.value });
      saved = editor.value;
      paintState(data.status);
      say(kind === "notes" ? "Notes saved." : "Voice saved.");
    } catch (error) {
      say(error.message, true);
    }
    count();
  }

  editor.addEventListener("input", count);
  $("save").addEventListener("click", save);
  if (editBtn) editBtn.addEventListener("click", () => setView("edit"));
  if (previewBtn) previewBtn.addEventListener("click", () => setView("preview"));
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "s") { event.preventDefault(); save(); }
  });
  window.addEventListener("beforeunload", (event) => {
    if (dirty()) event.preventDefault();
  });

  setView("edit");
  load();
  return { editor, save, say, count, isDirty: dirty, setView, paintPreview };
}
