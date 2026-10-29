// The Voice page: voice.md as one document, read as a preview or edited as
// markdown, with a small chat underneath that can offer rules for it.
// Endpoints as before: GET /api/setup, POST /api/setup/voice,
// GET /api/setup/templates; the chat uses /api/chat.

import { mountChat } from "./doc-chat.js";
import { api, download, el, toast } from "./dom.js";
import { renderMarkdown } from "./markdown.js";

const STATES = { empty: "Empty", started: "Started", ready: "Ready" };

const $ = (id) => document.getElementById(id);

let text = "";
let saved = "";
let mode = "preview";

const dirty = () => text.trim() !== saved.trim();

function paintFacts(status) {
  if (status) $("fact-state").textContent = STATES[status.voice] || "—";
  $("fact-size").textContent = `${text.trim().length.toLocaleString()} characters`;
  $("fact-saved").textContent = dirty() ? "Not saved" : "Saved";
  $("fact-saved").className = dirty() ? "meta is-open" : "meta";
}

function setMode(next) {
  mode = next;
  for (const button of document.querySelectorAll("[data-mode]")) {
    button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
  }
  $("preview").hidden = mode !== "preview";
  $("source").hidden = mode !== "markdown";
  if (mode === "preview") renderMarkdown(text, $("preview"));
  else $("source").value = text;
}

async function load() {
  try {
    const data = await api("/api/setup");
    saved = data.voice || "";
    text = saved;
    setMode(mode);
    paintFacts(data.status);
  } catch (error) {
    toast(`Could not load your voice: ${error.message}`, "error");
  }
}

async function save() {
  toast("Saving…", "busy");
  try {
    const data = await api("/api/setup/voice", { text });
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
        && !confirm(`Replace your voice with the ${role.label} starter?`)) return;
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
$("save").addEventListener("click", save);
$("download").addEventListener("click", () => download("voice.md", text));
document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "s") { event.preventDefault(); save(); }
});
window.addEventListener("beforeunload", (event) => {
  if (dirty()) event.preventDefault();
});

mountChat({
  host: $("chat"),
  placeholder: "Ask for a rule, or to tighten one",
  cards: {
    voice: {
      label: "For your voice",
      // Keep appends on disk. With unsaved edits here, the next Save would
      // overwrite it, so the edits are saved first.
      save: async (rule) => {
        if (dirty()) throw new Error("Save your edits first, then keep the rule.");
        await api("/api/setup/voice", { append: rule });
      },
    },
  },
  onSaved: () => load(),
});

load();
loadStarters();
