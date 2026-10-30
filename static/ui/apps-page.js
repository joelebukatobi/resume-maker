// Applications: every application, searchable, with rename / duplicate /
// archive / delete as row icons. The list fills the viewport; when more
// would scroll, one row-height becomes «  n  » pagination instead.
// Endpoints: GET/POST /api/apps, GET /api/setup for the empty-file nudge.

import { api, el, icon, toast } from "./dom.js";
import { bespokeSelect } from "./select.js";

const $ = (id) => document.getElementById(id);
const ROW_FALLBACK = 52; // td padding + 14px line when nothing is painted yet
// Leave a bottom band for the pager; CSS centers « n » in that leftover.
const PAGER_ROWS = 2;
const EXTRA_ROWS = 1;    // floor() is tight; one more row still clears the viewport
// Same labels identify writes; the edit dialog also allows a short custom one.
const TRACKS = ["Product", "Engineering", "Data", "Design", "Other"];
const NEW_TRACK = "__new__";

let all = [];
let query = "";
let page = 1;
let editDialog = null;
let editSlug = "";
let editTrack = null;
let editNote = null;
let editCustom = null;
let editCustomWrap = null;
let editCompany = null;
let editRole = null;

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

function joinName(company, role) {
  return [company, role].map((s) => String(s || "").trim()).filter(Boolean).join(" — ");
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

function trackOptions(current) {
  const known = new Set(TRACKS);
  const extras = [];
  for (const app of all) {
    const track = String(app.track || "").trim();
    if (track && !known.has(track) && !extras.includes(track)) extras.push(track);
  }
  const here = String(current || "").trim();
  if (here && !known.has(here) && !extras.includes(here)) extras.push(here);
  return [
    { id: "", label: "No track" },
    ...TRACKS.map((track) => ({ id: track, label: track })),
    ...extras.map((track) => ({ id: track, label: track })),
    { id: NEW_TRACK, label: "New track…" },
  ];
}

function paintEditTrack() {
  const making = editTrack && editTrack.value === NEW_TRACK;
  if (editCustomWrap) editCustomWrap.hidden = !making;
  if (making && editCustom) editCustom.focus();
}

function noteEdit(text) {
  if (editNote) editNote.textContent = text || "";
}

async function saveEdit() {
  const company = editCompany.value.trim();
  const role = editRole.value.trim();
  const name = joinName(company, role);
  if (!name) {
    noteEdit("Add a company or a role.");
    return;
  }
  let track = editTrack ? editTrack.value : "";
  if (track === NEW_TRACK) {
    track = editCustom.value.trim();
    if (!track) {
      noteEdit("Name the new track.");
      return;
    }
  }
  noteEdit("Saving…");
  try {
    await call({ action: "rename", slug: editSlug, name });
    if (track) await call({ action: "track", slug: editSlug, track });
    editDialog.close();
    await load();
    toast("Saved.");
  } catch (error) {
    noteEdit(error.message);
  }
}

function buildEdit() {
  if (editDialog) return editDialog;

  editCompany = el("input", {
    class: "input input-sm", type: "text", id: "edit-company",
    autocomplete: "organization",
  });
  editRole = el("input", {
    class: "input input-sm", type: "text", id: "edit-role",
    autocomplete: "off",
  });
  editCustom = el("input", {
    class: "input input-sm", type: "text", id: "edit-track-new",
    placeholder: "e.g. Research", autocomplete: "off",
  });
  editCustomWrap = el("div", { class: "field", hidden: true }, [
    el("label", { class: "label", for: "edit-track-new", text: "New track" }),
    editCustom,
  ]);
  editNote = el("p", { class: "meta", "data-edit-note": "" });
  editTrack = bespokeSelect({
    label: "Track",
    options: trackOptions(""),
    value: "",
    onChange: () => paintEditTrack(),
  });
  // Match Models dialog control height.
  editTrack.root.querySelector(".select-trigger")?.classList.add("input-sm");

  const save = el("button", {
    class: "btn btn-sm btn-primary", type: "button", text: "Save",
    onclick: (event) => { event.preventDefault(); saveEdit(); },
  });
  for (const input of [editCompany, editRole, editCustom]) {
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); saveEdit(); }
    });
  }

  editDialog = el("dialog", { class: "modal" }, [
    el("div", { class: "modal-card" }, [
      el("header", { class: "modal-head" }, [
        el("div", { class: "modal-intro" }, [
          el("h2", { class: "card-title modal-title", text: "Edit application" }),
          el("p", {
            class: "meta modal-lead",
            text: "Company and role name the row. Track groups similar roles.",
          }),
        ]),
        el("button", {
          class: "modal-close", type: "button", "aria-label": "Close",
          onclick: () => editDialog.close(),
        }, [icon("xmark", 16)]),
      ]),
      el("hr", { class: "modal-rule" }),
      el("div", { class: "field" }, [
        el("label", { class: "label", for: "edit-company", text: "Company" }),
        editCompany,
      ]),
      el("div", { class: "field" }, [
        el("label", { class: "label", for: "edit-role", text: "Role" }),
        editRole,
      ]),
      el("div", { class: "field" }, [
        el("label", { class: "label", text: "Track" }),
        editTrack.root,
      ]),
      editCustomWrap,
      editNote,
      el("footer", { class: "modal-foot" }, [
        el("span", { class: "spacer" }),
        el("button", {
          class: "btn btn-sm", type: "button", text: "Cancel",
          onclick: () => editDialog.close(),
        }),
        save,
      ]),
    ]),
  ]);
  editDialog.addEventListener("click", (event) => {
    if (event.target === editDialog) editDialog.close();
  });
  editDialog.addEventListener("close", () => noteEdit(""));
  document.body.append(editDialog);
  return editDialog;
}

function openEdit(app) {
  buildEdit();
  editSlug = app.slug;
  const { company, role } = parts(app.name);
  editCompany.value = company;
  editRole.value = role;
  editCustom.value = "";
  const options = trackOptions(app.track);
  const start = options.some((o) => o.id === app.track) ? (app.track || "") : "";
  editTrack.setOptions(options);
  editTrack.value = start;
  paintEditTrack();
  noteEdit("");
  editDialog.showModal();
  editCompany.focus();
  editCompany.select();
}

function actionsFor(app) {
  const live = !app.archived;
  return el("div", { class: "row-actions" }, [
    actionBtn("Edit", "edit", async () => { openEdit(app); }),
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

function formatDay(value) {
  if (!value) return "—";
  const when = typeof value === "number"
    ? new Date(value * 1000)
    : new Date(`${value}T00:00:00`);
  if (Number.isNaN(when.getTime())) return "—";
  return when.toLocaleDateString(undefined,
    { day: "numeric", month: "short", year: "numeric" });
}

function row(app, number) {
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
    el("td", { class: "meta tnum col-num", text: String(number) }),
    el("td", { class: "col-company" }, [
      company
        ? el("a", { class: "app-name", href, text: company, title: company })
        : el("span", { class: "meta", text: "—" }),
    ]),
    el("td", { class: "col-role" }, [
      role
        ? el("a", { class: "app-role", href, text: role, title: role })
        : el("span", { class: "meta", text: "—" }),
    ]),
    el("td", { class: "meta", text: app.track || "—" }),
    el("td", { class: "meta tnum", text: formatDay(app.created) }),
    el("td", { class: "meta tnum", text: formatDay(app.opened) }),
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
  if (!main || !thead) return 8 + EXTRA_ROWS;
  const top = thead.getBoundingClientRect().bottom;
  const padBottom = parseFloat(getComputedStyle(main).paddingBottom) || 0;
  const gap = Math.max(0, window.innerHeight - top - padBottom);
  return Math.max(1, Math.floor(gap / rowHeight()) + EXTRA_ROWS);
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
    rows.push(el("tr", {}, [el("td", { colspan: "8", class: "meta empty-row",
      text: all.length ? `Nothing matches “${query}”.` : "No applications yet. Use Start Application." })]));
  } else {
    let sawArchivedHeader = false;
    slice.forEach((app, index) => {
      if (app.archived && !sawArchivedHeader) {
        // Only when this page starts the archived block (or is all archived).
        const prev = items[start - 1];
        if (!prev || !prev.archived) {
          rows.push(el("tr", { class: "group-row" }, [
            el("td", { colspan: "8" }, [el("span", { class: "eyebrow", text: "Archived" })]),
          ]));
        }
        sawArchivedHeader = true;
      }
      rows.push(row(app, start + index + 1));
    });
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
      nextSlice.forEach((app, index) => {
        if (app.archived && !sawArchivedHeader) {
          const prev = items[nextStart - 1];
          if (!prev || !prev.archived) {
            redraw.push(el("tr", { class: "group-row" }, [
              el("td", { colspan: "8" }, [el("span", { class: "eyebrow", text: "Archived" })]),
            ]));
          }
          sawArchivedHeader = true;
        }
        redraw.push(row(app, nextStart + index + 1));
      });
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
