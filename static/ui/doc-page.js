// A page that edits one markdown file: Voice or Notes.
//
// The file is one document, read as a preview or edited as markdown, with a
// small chat underneath whose cards append to it. Endpoints are the ones the
// old pages used: GET /api/setup, POST /api/setup/<kind>; the chat uses
// /api/chat.

import { mountChat } from "./doc-chat.js";
import { api, download, el, toast } from "./dom.js";
import { renderMarkdown } from "./markdown.js";

const STATES = { empty: "Empty", started: "Started", ready: "Ready" };

const $ = (id) => document.getElementById(id);

export function mountDocPage({ kind, filename, placeholder, cards, starters = false, search = false }) {
  let text = "";
  let saved = "";
  let mode = "preview";
  let query = "";

  const dirty = () => text.trim() !== saved.trim();

  function paintFacts(status) {
    if (status) $("fact-state").textContent = STATES[status[kind]] || "—";
    $("fact-size").textContent = `${text.trim().length.toLocaleString()} characters`;
    $("fact-saved").textContent = dirty() ? "Not saved" : "Saved";
    $("fact-saved").className = dirty() ? "meta is-open" : "meta";
  }

  // Search shows only the paragraphs that mention the query, in the preview.
  function shown() {
    if (!query) return text;
    const needle = query.toLowerCase();
    return text.split(/\n{2,}/).filter((p) => p.toLowerCase().includes(needle)).join("\n\n");
  }

  function paintSearch() {
    const note = $("search-note");
    if (!note) return;
    if (!query) { note.hidden = true; return; }
    const found = shown().trim() ? shown().split(/\n{2,}/).length : 0;
    note.hidden = false;
    note.textContent = found ? `${found} ${found === 1 ? "match" : "matches"} for “${query}”` : `Nothing mentions “${query}”`;
  }

  function setMode(next) {
    mode = next;
    for (const button of document.querySelectorAll("[data-mode]")) {
      button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
    }
    $("preview").hidden = mode !== "preview";
    $("source").hidden = mode !== "markdown";
    if (mode === "preview") renderMarkdown(shown(), $("preview"));
    else $("source").value = text;
    paintSearch();
  }

  async function load() {
    try {
      const data = await api("/api/setup");
      saved = text = data[kind] || "";
      setMode(mode);
      paintFacts(data.status);
    } catch (error) {
      toast(`Could not load ${filename}: ${error.message}`, "error");
    }
  }

  async function save() {
    toast("Saving…", "busy");
    try {
      const data = await api(`/api/setup/${kind}`, { text });
      saved = text;
      paintFacts(data.status);
      toast("Saved.");
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function loadStarters() {
    let roles = [];
    try {
      roles = await api("/api/setup/templates");
    } catch (error) {
      toast(`Could not load starters: ${error.message}`, "error");
      return;
    }
    $("starter").append(...roles.map((role, index) => el("option", { value: String(index), text: role.label })));
    $("use-starter").addEventListener("click", () => {
      const role = roles[Number($("starter").value)];
      if (!role) return;
      if (text.trim() && text.trim() !== role.text.trim()
          && !confirm(`Replace ${filename} with the ${role.label} starter?`)) return;
      text = role.text;
      setMode(mode);
      paintFacts();
      toast(`${role.label} loaded. Save to keep it.`);
    });
  }

  $("source").addEventListener("input", (event) => {
    text = event.target.value;
    paintFacts();
  });
  for (const button of document.querySelectorAll("[data-mode]")) {
    button.addEventListener("click", () => setMode(button.dataset.mode));
  }
  if (search) {
    $("search").addEventListener("input", (event) => {
      query = event.target.value.trim();
      setMode("preview");
    });
  }
  $("save").addEventListener("click", save);
  $("download").addEventListener("click", () => download(filename, text));
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "s") { event.preventDefault(); save(); }
  });
  window.addEventListener("beforeunload", (event) => {
    if (dirty()) event.preventDefault();
  });

  // Keep appends on disk. With unsaved edits on the page, the next Save would
  // overwrite what was appended, so the edits are saved first.
  const guarded = {};
  for (const [name, spec] of Object.entries(cards)) {
    guarded[name] = {
      ...spec,
      save: async (value) => {
        if (dirty()) throw new Error("Save your edits first, then keep this.");
        await spec.save(value);
      },
    };
  }
  mountChat({ host: $("chat"), placeholder, cards: guarded, onSaved: () => load() });

  load();
  if (starters) loadStarters();
}
