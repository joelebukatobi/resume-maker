// Applications: every application, searchable, with rename / duplicate /
// archive / delete as row icons. The list fills the viewport; when more
// would scroll, two row-heights become «  n  » pagination instead.
// Endpoints: GET/POST /api/apps, GET /api/setup for the empty-file nudge.

import { api, el, icon, toast } from "./dom.js";

const $ = (id) => document.getElementById(id);
const ROW_FALLBACK = 52; // td padding + 14px line when nothing is painted yet
const PAGER_ROWS = 2;    // room taken from the list so the pager never forces a scroll

let all = [];
let current = "";
let query = "";
let page = 1;

const open = (slug) => `/${encodeURIComponent(slug)}/write`;
const go = (slug) => { window.location.href = open(slug); };
const call = (payload) => api("/api/apps", payload);

// Identify joins company and role with an em dash; roles themselves often
// carry a hyphen, so only that join is the split.
function parts(name) {
  const text = String(name || "").trim();
  const at = text.indexOf(" — ");
  if (at < 0) return { company: text, role: "" };
  return { company: text.slice(0, at).trim(), role: text.slice(at + 3).trim() };
}

function act(fn) {
  return async (event) => {
    event?.stopPropagation?.();
    try { await fn(); } catch (error) { toast(error.message, "error"); }
  };
}

function actionBtn(label, name, fn, danger = false) {
  return el("button", {
    class: danger ? "icon-btn row-action is-danger" : "icon-btn row-action",
    type: "button",
    "aria-label": label,
    title: label,
    onclick: act(fn),
  }, [icon(name, 16)]);
}

function actionsFor(app) {
  const live = !app.archived;
  return el("div", { class: "row-actions" }, [
    actionBtn("Rename", "edit", async () => {
      const name = window.prompt("Name this application", app.name);
      if (name) { await call({ action: "rename", slug: app.slug, name }); await load(); }
    }),
    actionBtn("Duplicate", "copy", async () => {
      const name = window.prompt("Name the copy", `${app.name} (copy)`);
      if (name) go((await call({ action: "create", name, source: app.slug })).application.slug);
    }),
    actionBtn(live ? "Archive" : "Restore", "archive", async () => {
      await call({ action: live ? "archive" : "restore", slug: app.slug });
      await load();
    }),
    actionBtn("Delete", "trash", async () => {
      if (!window.confirm(`Delete "${app.name}" for good? Its CV, letter and chat cannot be recovered.`)) return;
      await call({ action: "delete", slug: app.slug });
      await load();
    }, true),
  ]);
}

function row(app) {
  const date = app.created ? new Date(`${app.created}T00:00:00`).toLocaleDateString(undefined,
    { day: "numeric", month: "short", year: "numeric" }) : "—";
  const { company, role } = parts(app.name);
  const href = open(app.slug);
  const openApp = () => go(app.slug);
  const tr = el("tr", {
    class: app.archived ? "app-row is-archived" : "app-row",
    tabindex: "0",
    role: "link",
    "aria-label": `Open ${app.name || "application"}`,
    onclick: (event) => {
      // Keep Posting (external) and the action icons on their own jobs.
      if (event.target.closest("a, button")) return;
      openApp();
    },
    onkeydown: (event) => {
      if (event.target !== tr) return;
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      openApp();
    },
  }, [
    el("td", {}, [
      company
        ? el("a", { class: "app-name", href, text: company })
        : el("span", { class: "meta", text: "—" }),
      app.slug === current ? el("span", { class: "meta app-current", text: "Last open" }) : null,
    ]),
    el("td", {}, [
      role
        ? el("a", { class: "app-role", href, text: role })
        : el("span", { class: "meta", text: "—" }),
    ]),
    el("td", { class: "meta", text: app.track || "—" }),
    el("td", { class: "meta tnum", text: date }),
    el("td", {}, [app.job_url
      ? el("a", { class: "app-posting", href: app.job_url, target: "_blank", rel: "noopener noreferrer", text: "Posting" })
      : el("span", { class: "meta", text: "—" })]),
    el("td", {}, [actionsFor(app)]),
  ]);
  return tr;
}

function filtered() {
  const needle = query.toLowerCase();
  const match = (a) => !needle || `${a.name} ${a.track}`.toLowerCase().includes(needle);
  const live = all.filter((a) => !a.archived && match(a));
  const archived = all.filter((a) => a.archived && match(a));
  return [...live, ...archived];
}

function rowHeight() {
  const sample = $("app-rows").querySelector("tr:not(.group-row)");
  if (sample) {
    const h = sample.getBoundingClientRect().height;
    if (h > 0) return h;
  }
  return ROW_FALLBACK;
}

// How many body rows fit without a page scrollbar. Measure to the viewport
// bottom; when the pager is shown it takes PAGER_ROWS out of that budget.
function rowsThatFit() {
  const main = document.querySelector(".main");
  const thead = document.querySelector(".table thead");
  if (!main || !thead) return 8;
  const top = thead.getBoundingClientRect().bottom;
  const padBottom = parseFloat(getComputedStyle(main).paddingBottom) || 0;
  const gap = Math.max(0, window.innerHeight - top - padBottom);
  return Math.max(1, Math.floor(gap / rowHeight()));
}

function pagePlan(items) {
  const fit = rowsThatFit();
  if (items.length <= fit) {
    return { size: Math.max(items.length, 1), pages: 1, paged: false };
  }
  const size = Math.max(1, fit - PAGER_ROWS);
  return { size, pages: Math.max(1, Math.ceil(items.length / size)), paged: true };
}

function paintPager(pages) {
  const pager = $("apps-pager");
  const label = $("apps-page");
  const prev = $("apps-prev");
  const next = $("apps-next");
  if (!pager) return;
  if (pages <= 1) {
    pager.hidden = true;
    return;
  }
  pager.hidden = false;
  label.textContent = String(page);
  label.setAttribute("aria-label", `Page ${page} of ${pages}`);
  prev.disabled = page <= 1;
  next.disabled = page >= pages;
}

function paint() {
  const items = filtered();
  const plan = pagePlan(items);
  page = Math.min(Math.max(1, page), plan.pages);
  const start = (page - 1) * plan.size;
  const slice = plan.paged ? items.slice(start, start + plan.size) : items;

  const body = $("app-rows");
  const rows = [];
  if (!items.length) {
    rows.push(el("tr", {}, [el("td", { colspan: "6", class: "meta empty-row",
      text: all.length ? `Nothing matches “${query}”.` : "No applications yet. Use Start Application." })]));
  } else {
    let sawArchivedHeader = false;
    for (const app of slice) {
      if (app.archived && !sawArchivedHeader) {
        // Only when this page starts the archived block (or is all archived).
        const prev = items[start - 1];
        if (!prev || !prev.archived) {
          rows.push(el("tr", { class: "group-row" }, [
            el("td", { colspan: "6" }, [el("span", { class: "eyebrow", text: "Archived" })]),
          ]));
        }
        sawArchivedHeader = true;
      }
      rows.push(row(app));
    }
  }
  body.replaceChildren(...rows);
  paintPager(plan.paged ? plan.pages : 1);

  // First paint may have used the fallback row height; remeasure once rows exist.
  if (items.length && plan.paged) {
    const again = pagePlan(items);
    if (again.size !== plan.size || again.pages !== plan.pages) {
      page = Math.min(page, again.pages);
      const nextStart = (page - 1) * again.size;
      const nextSlice = items.slice(nextStart, nextStart + again.size);
      const redraw = [];
      let sawArchivedHeader = false;
      for (const app of nextSlice) {
        if (app.archived && !sawArchivedHeader) {
          const prev = items[nextStart - 1];
          if (!prev || !prev.archived) {
            redraw.push(el("tr", { class: "group-row" }, [
              el("td", { colspan: "6" }, [el("span", { class: "eyebrow", text: "Archived" })]),
            ]));
          }
          sawArchivedHeader = true;
        }
        redraw.push(row(app));
      }
      body.replaceChildren(...redraw);
      paintPager(again.pages);
    }
  }
}

function runSearch() {
  query = $("search").value.trim();
  page = 1;
  paint();
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
    const empty = [["voice", "Tone", "/voice"], ["notes", "My Experience", "/notes"]].find(([k]) => status[k] === "empty");
    if (!empty) return;
    const [, name, href] = empty;
    $("nudge").replaceChildren(
      el("span", { text: `Your ${name} is empty. The assistant leans on it in every chat.` }),
      el("a", { class: "btn btn-sm", href, text: `Open ${name}` }),
    );
    $("nudge").hidden = false;
    paint();
  } catch (error) { /* the list still works without it */ }
}

$("search").addEventListener("input", runSearch);
$("search").addEventListener("keydown", (event) => {
  if (event.key === "Enter") { event.preventDefault(); runSearch(); }
});
$("search-go").addEventListener("click", runSearch);
$("app-new").addEventListener("click", act(async () => {
  go((await call({ action: "create" })).application.slug);
}));

$("apps-prev").addEventListener("click", () => {
  if (page <= 1) return;
  page -= 1;
  paint();
});
$("apps-next").addEventListener("click", () => {
  page += 1;
  paint();
});

$("app-new").querySelector(".app-new-icon").replaceChildren(icon("plus", 16));
$("search-go").replaceChildren(icon("search", 16));
$("apps-prev").replaceChildren(icon("left", 16));
$("apps-next").replaceChildren(icon("right", 16));

let resizeTimer = 0;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(paint, 100);
});

load();
nudge();
