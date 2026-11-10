// A page that edits one markdown file: Playbook or My Experience.
//
// The file is one document, read as a preview or edited as markdown, with a
// small chat underneath whose cards append to it. Endpoints are the ones the
// old pages used: GET /api/setup, POST /api/setup/<kind>; the chat uses
// /api/chat.

import { mountChat } from "./doc-chat.js";
import { api, el, icon, toast } from "./dom.js";
import { renderMarkdown } from "./markdown.js";

const $ = (id) => document.getElementById(id);

export function mountDocPage({ kind, filename, placeholder, cards, starters = false, search = false }) {
  let text = "";
  let saved = "";
  let mode = "preview";
  let query = "";
  let starterOpen = false;
  let roles = [];

  const dirty = () => text.trim() !== saved.trim();

  // Markdown view: a plain code editor, unwrapped lines beside line numbers.
  const source = $("source");
  const gutter = el("pre", { class: "code-gutter", "aria-hidden": "true" });
  const editor = el("div", { class: "code-edit", hidden: true });
  source.replaceWith(editor);
  editor.append(gutter, source);
  source.hidden = false;
  source.setAttribute("wrap", "off");
  function paintGutter() {
    const count = source.value.split("\n").length;
    gutter.textContent = Array.from({ length: count }, (_, i) => i + 1).join("\n");
    gutter.scrollTop = source.scrollTop;
  }
  source.addEventListener("scroll", () => { gutter.scrollTop = source.scrollTop; });

  // The section being read in the preview: the last title in its top third.
  function sectionInView() {
    const preview = $("preview");
    const line = preview.getBoundingClientRect().top + preview.clientHeight / 3;
    let found = null;
    for (const heading of preview.querySelectorAll(".md-heading")) {
      if (heading.getBoundingClientRect().top > line) break;
      found = heading.textContent.trim().toLowerCase();
    }
    return found;
  }

  // Put that section's title at the top of the editor, caret on it.
  function openAt(title) {
    if (!title) return;
    const lines = text.split("\n");
    const at = lines.findIndex((row) => {
      const match = row.match(/^\s*#{1,6}\s+(.*)$/);
      return match && match[1].replace(/[*_`]/g, "").trim().toLowerCase() === title;
    });
    if (at < 0) return;
    const offset = lines.slice(0, at).reduce((sum, row) => sum + row.length + 1, 0);
    source.scrollTop = at * parseFloat(getComputedStyle(source).lineHeight);
    gutter.scrollTop = source.scrollTop;
    source.setSelectionRange(offset, offset);
    source.focus({ preventScroll: true });
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

  function runSearch() {
    const input = $("search");
    if (!input) return;
    query = input.value.trim();
    setMode("preview");
  }

  function setMode(next) {
    const title = mode === "preview" && next === "markdown" ? sectionInView() : null;
    mode = next;
    for (const button of document.querySelectorAll("[data-mode]")) {
      button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
    }
    $("preview").hidden = mode !== "preview";
    editor.hidden = mode !== "markdown";
    if (mode === "preview") renderMarkdown(shown(), $("preview"));
    else {
      source.value = text;
      paintGutter();
      openAt(title);
    }
    paintSearch();
  }

  async function load() {
    try {
      const data = await api("/api/setup");
      saved = text = data[kind] || "";
      setMode(mode);
    } catch (error) {
      toast(`Could not load ${filename}: ${error.message}`, "error");
    }
  }

  async function save() {
    toast("Saving…", "busy");
    try {
      await api(`/api/setup/${kind}`, { text });
      saved = text;
      toast("Saved.");
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function closeStarters() {
    const menu = $("starters-menu");
    const trigger = $("starters-trigger");
    if (!menu || !trigger) return;
    starterOpen = false;
    trigger.setAttribute("aria-expanded", "false");
    menu.classList.remove("opens-up");
    menu.style.top = "";
    menu.style.bottom = "";
    menu.style.left = "";
    menu.style.width = "";
    if (typeof menu.hidePopover === "function" && menu.matches(":popover-open")) {
      try { menu.hidePopover(); } catch (e) { /* already closed */ }
    }
  }

  function placeStarters(menu, trigger) {
    const rect = trigger.getBoundingClientRect();
    const width = 220;
    if (typeof menu.showPopover === "function") {
      try { menu.showPopover(); } catch (e) { /* already open */ }
    }
    const need = Math.min(220, menu.scrollHeight || 220);
    const roomBelow = window.innerHeight - rect.bottom - 12;
    const roomAbove = rect.top - 12;
    const openUp = roomBelow < need && roomAbove > roomBelow;
    menu.classList.toggle("opens-up", openUp);
    menu.style.width = `${width}px`;
    menu.style.left = `${Math.round(Math.max(8, rect.right - width))}px`;
    if (openUp) {
      menu.style.top = "auto";
      menu.style.bottom = `${Math.round(window.innerHeight - rect.top + 4)}px`;
    } else {
      menu.style.bottom = "auto";
      menu.style.top = `${Math.round(rect.bottom + 4)}px`;
    }
  }

  function paintStarters() {
    const menu = $("starters-menu");
    const trigger = $("starters-trigger");
    if (!menu || !trigger) return;
    menu.replaceChildren(...roles.map((role) => el("button", {
      class: "menu-item", type: "button", role: "option",
      onclick: () => {
        closeStarters();
        if (text.trim() && text.trim() !== role.text.trim()
            && !confirm(`Replace ${filename} with the ${role.label} starter?`)) return;
        text = role.text;
        setMode(mode);
        toast(`${role.label} loaded. Save to keep it.`);
      },
    }, [el("span", { text: role.label })])));
    if (starterOpen) placeStarters(menu, trigger);
  }

  function moreIcon() {
    const SVG = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("width", "16");
    svg.setAttribute("height", "16");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    for (const y of ["5", "12", "19"]) {
      const dot = document.createElementNS(SVG, "circle");
      dot.setAttribute("cx", "12");
      dot.setAttribute("cy", y);
      dot.setAttribute("r", "1.5");
      dot.setAttribute("fill", "currentColor");
      svg.append(dot);
    }
    return svg;
  }

  async function loadStarters() {
    const trigger = $("starters-trigger");
    const menu = $("starters-menu");
    if (!trigger || !menu) return;
    trigger.replaceChildren(moreIcon());
    try {
      roles = await api("/api/setup/templates");
    } catch (error) {
      toast(`Could not load starters: ${error.message}`, "error");
      return;
    }
    paintStarters();
    trigger.addEventListener("click", (event) => {
      event.stopPropagation();
      starterOpen = !starterOpen;
      trigger.setAttribute("aria-expanded", String(starterOpen));
      if (starterOpen) paintStarters();
      else closeStarters();
    });
    document.addEventListener("click", (event) => {
      if (!starterOpen) return;
      if (trigger.contains(event.target) || menu.contains(event.target)) return;
      closeStarters();
    });
  }

  source.addEventListener("input", (event) => {
    text = event.target.value;
    paintGutter();
  });
  for (const button of document.querySelectorAll("[data-mode]")) {
    button.addEventListener("click", () => setMode(button.dataset.mode));
  }
  if (search) {
    const go = $("search-go");
    if (go) go.replaceChildren(icon("search", 16));
    $("search").addEventListener("input", runSearch);
    $("search").addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); runSearch(); }
    });
    if (go) go.addEventListener("click", runSearch);
  }
  $("save").addEventListener("click", save);
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
