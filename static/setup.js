"use strict";
/* Shared behaviour for the setup pages (Style, Notes).
 *
 * Each page supplies which file it edits ("assistant" or "notes") and the
 * elements to bind; this handles loading, saving, the unsaved marker, the
 * readiness pill and the character count. */

const LABELS = { empty: "Empty", started: "Started", ready: "Ready" };

const $ = (id) => document.getElementById(id);

async function api(path, body) {
  const options = body === undefined
    ? {}
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

function setupPage({ kind, statusKey }) {
  const editor = $("editor");
  const note = $("note");
  const pill = $("state");
  let saved = "";

  const dirty = () => editor.value.trim() !== saved.trim();

  function paintState(status) {
    if (!status || !pill) return;
    pill.dataset.state = status[statusKey];
    pill.querySelector(".label").textContent = LABELS[status[statusKey]];
  }

  function say(text, bad = false) {
    note.textContent = text;
    note.classList.toggle("bad", bad);
  }

  function count() {
    const chars = editor.value.trim().length;
    $("count").textContent = `${chars.toLocaleString()} characters${dirty() ? " · unsaved" : ""}`;
  }

  async function load() {
    try {
      const data = await api("/api/setup");
      saved = data[kind] || "";
      editor.value = saved;
      paintState(data.status);
      say(saved ? "" : "Nothing saved yet.");
    } catch (error) {
      say(`Could not load: ${error.message}`, true);
    }
    count();
  }

  async function save() {
    say("Saving…");
    try {
      const data = await api(`/api/setup/${kind}`, { text: editor.value });
      saved = editor.value;
      paintState(data.status);
      say("Saved.");
    } catch (error) {
      say(error.message, true);
    }
    count();
  }

  editor.addEventListener("input", count);
  $("save").addEventListener("click", save);
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "s") { event.preventDefault(); save(); }
  });
  window.addEventListener("beforeunload", (event) => {
    if (dirty()) event.preventDefault();
  });

  load();
  return { editor, save, say, count, isDirty: dirty };
}
