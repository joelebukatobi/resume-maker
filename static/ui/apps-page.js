// Applications: every application, searchable, with the same actions the old
// hub had. Endpoints as before: GET /api/apps, POST /api/apps, GET
// /api/setup for the empty-file nudge, GET /api/llm/providers.

import { api, el, toast } from "./dom.js";

const $ = (id) => document.getElementById(id);
const SVG = "http://www.w3.org/2000/svg";

let all = [];
let current = "";
let query = "";
let openMenu = null;

const go = (slug) => { window.location.href = `/${encodeURIComponent(slug)}`; };
const call = (payload) => api("/api/apps", payload);

function kebab() {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("width", "18");
  svg.setAttribute("height", "18");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  for (const y of ["5", "12", "19"]) {
    const dot = document.createElementNS(SVG, "circle");
    dot.setAttribute("cx", "12");
    dot.setAttribute("cy", y);
    dot.setAttribute("r", "1.4");
    dot.setAttribute("fill", "currentColor");
    svg.append(dot);
  }
  return svg;
}

function closeMenu() {
  if (!openMenu) return;
  openMenu.menu.hidden = true;
  openMenu.button.setAttribute("aria-expanded", "false");
  openMenu = null;
}

function act(fn) {
  return async () => {
    closeMenu();
    try { await fn(); } catch (error) { toast(error.message, "error"); }
  };
}

function menuFor(app) {
  const live = !app.archived;
  const item = (text, fn, danger = false) => el("button", {
    type: "button", role: "menuitem", class: danger ? "menu-item menu-danger" : "menu-item", text, onclick: act(fn),
  });
  const menu = el("div", { class: "menu", role: "menu", hidden: true }, [
    item("Rename", async () => {
      const name = window.prompt("Name this application", app.name);
      if (name) { await call({ action: "rename", slug: app.slug, name }); await load(); }
    }),
    item("Duplicate", async () => {
      const name = window.prompt("Name the copy", `${app.name} (copy)`);
      if (name) go((await call({ action: "create", name, source: app.slug })).application.slug);
    }),
    item(`Track: ${app.track || "none"}`, async () => {
      const chosen = window.prompt("Track: Product, Engineering, Data, Design or Other", app.track || "");
      if (chosen !== null) { await call({ action: "track", slug: app.slug, track: chosen.trim() }); await load(); }
    }),
    item(live ? "Archive" : "Restore", async () => {
      await call({ action: live ? "archive" : "restore", slug: app.slug });
      await load();
    }),
    item("Delete", async () => {
      if (!window.confirm(`Delete "${app.name}" for good? Its CV, letter and chat cannot be recovered.`)) return;
      await call({ action: "delete", slug: app.slug });
      await load();
    }, true),
  ]);
  const button = el("button", {
    class: "icon-btn", type: "button", "aria-label": `More actions for ${app.name}`,
    "aria-haspopup": "menu", "aria-expanded": "false",
  }, [kebab()]);
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    const was = openMenu && openMenu.menu === menu;
    closeMenu();
    if (was) return;
    menu.hidden = false;
    button.setAttribute("aria-expanded", "true");
    openMenu = { menu, button };
    menu.querySelector("button").focus();
  });
  return el("div", { class: "row-actions" }, [
    el("a", { href: `/${encodeURIComponent(app.slug)}`, text: live ? "Open" : "View" }),
    button,
    menu,
  ]);
}

function row(app) {
  const date = app.created ? new Date(`${app.created}T00:00:00`).toLocaleDateString(undefined,
    { day: "numeric", month: "short", year: "numeric" }) : "—";
  return el("tr", { class: app.archived ? "is-archived" : null }, [
    el("td", {}, [
      el("a", { class: "app-name", href: `/${encodeURIComponent(app.slug)}`, text: app.name }),
      app.slug === current ? el("span", { class: "meta app-current", text: "Last open" }) : null,
    ]),
    el("td", { class: "meta", text: app.track || "—" }),
    el("td", { class: "meta tnum", text: date }),
    el("td", {}, [app.job_url
      ? el("a", { href: app.job_url, target: "_blank", rel: "noopener noreferrer", text: "Posting" })
      : el("span", { class: "meta", text: "—" })]),
    el("td", {}, [menuFor(app)]),
  ]);
}

function paint() {
  const needle = query.toLowerCase();
  const match = (a) => !needle || `${a.name} ${a.track}`.toLowerCase().includes(needle);
  const live = all.filter((a) => !a.archived && match(a));
  const archived = all.filter((a) => a.archived && match(a));
  const body = $("app-rows");
  const rows = live.map(row);
  if (archived.length) {
    rows.push(el("tr", { class: "group-row" }, [el("td", { colspan: "5" }, [el("span", { class: "eyebrow", text: "Archived" })])]));
    rows.push(...archived.map(row));
  }
  if (!rows.length) {
    rows.push(el("tr", {}, [el("td", { colspan: "5", class: "meta empty-row",
      text: all.length ? `Nothing matches “${query}”.` : "No applications yet. Start one with New application." })]));
  }
  body.replaceChildren(...rows);
}

async function load() {
  try {
    const data = await api("/api/apps");
    all = data.applications || [];
    current = data.current || "";
    paint();
  } catch (error) {
    toast(`Could not read your applications: ${error.message}`, "error");
  }
}

async function nudge() {
  try {
    const { status } = await api("/api/setup");
    const empty = [["voice", "Voice", "/voice"], ["notes", "Notes", "/notes"]].find(([k]) => status[k] === "empty");
    if (!empty) return;
    const [, name, href] = empty;
    $("nudge").replaceChildren(
      el("span", { text: `Your ${name} is empty. The assistant leans on it in every chat.` }),
      el("a", { class: "btn btn-sm", href, text: `Open ${name}` }),
    );
    $("nudge").hidden = false;
  } catch (error) { /* the list still works without it */ }
}

async function model() {
  try {
    const providers = await api("/api/llm/providers");
    const connected = providers.filter((p) => p.available).map((p) => p.label);
    $("model").textContent = connected.length ? `Model keys: ${connected.join(", ")}` : "No model key saved";
  } catch (error) { $("model").textContent = ""; }
}

$("search").addEventListener("input", (event) => { query = event.target.value.trim(); paint(); });
$("app-new").addEventListener("click", act(async () => {
  go((await call({ action: "create" })).application.slug);
}));
document.addEventListener("click", (event) => {
  if (openMenu && !openMenu.menu.contains(event.target)) closeMenu();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && openMenu) { const b = openMenu.button; closeMenu(); b.focus(); }
});

load();
nudge();
model();
