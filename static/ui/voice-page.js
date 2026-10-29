// The Voice page: voice.md in its sections, or as the whole file.
// Uses the same endpoints the old page did: GET /api/setup,
// POST /api/setup/voice, GET /api/setup/templates.

import { api, download, el, toast } from "./dom.js";
import { join, label, split } from "./sections.js";

const STATES = { empty: "Empty", started: "Started", ready: "Ready" };

const $ = (id) => document.getElementById(id);

let text = "";
let saved = "";
let view = "parts";

const dirty = () => text.trim() !== saved.trim();

function paintFacts(status) {
  if (status) $("fact-state").textContent = STATES[status.voice] || "—";
  $("fact-size").textContent = `${text.trim().length.toLocaleString()} characters`;
  $("fact-saved").textContent = dirty() ? "Not saved" : "Saved";
  $("fact-saved").className = dirty() ? "is-open" : "";
}

function rowsFor(body) {
  return Math.min(Math.max(body.split("\n").length, 3), 14);
}

function drawParts() {
  const parts = split(text);
  const host = $("parts");
  host.replaceChildren(...parts.map((part, index) => {
    const id = `part-${index}`;
    const box = el("textarea", {
      class: "input", id, rows: rowsFor(part.body), spellcheck: "true",
      oninput: (event) => {
        parts[index].body = event.target.value;
        text = join(parts);
        paintFacts();
      },
    });
    box.value = part.body;
    return el("div", { class: "part" }, [
      el("label", { class: "label", for: id, text: label(part) }),
      box,
    ]);
  }));
}

function drawWhole() {
  $("whole").value = text;
}

function setView(next) {
  view = next;
  for (const tab of document.querySelectorAll(".tab")) {
    tab.setAttribute("aria-selected", String(tab.dataset.view === view));
  }
  $("parts").hidden = view !== "parts";
  $("whole-wrap").hidden = view !== "whole";
  if (view === "parts") drawParts(); else drawWhole();
}

async function load() {
  try {
    const data = await api("/api/setup");
    saved = text = data.voice || "";
    setView(view);
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
    toast("Voice saved.");
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
  const select = $("starter");
  select.append(...roles.map((role, index) => el("option", { value: String(index), text: role.label })));
  $("use-starter").addEventListener("click", () => {
    const role = roles[Number(select.value)];
    if (!role) return;
    if (text.trim() && text.trim() !== role.text.trim()
        && !confirm(`Replace what you have with the ${role.label} starter?`)) return;
    text = role.text;
    setView(view);
    paintFacts();
    toast(`${role.label} starter loaded. Edit it, then save.`);
  });
}

$("whole").addEventListener("input", (event) => {
  text = event.target.value;
  paintFacts();
});
for (const tab of document.querySelectorAll(".tab")) {
  tab.addEventListener("click", () => setView(tab.dataset.view));
}
$("save").addEventListener("click", save);
$("download").addEventListener("click", () => download("voice.md", text));
document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "s") { event.preventDefault(); save(); }
});
window.addEventListener("beforeunload", (event) => {
  if (dirty()) event.preventDefault();
});

load();
loadStarters();
