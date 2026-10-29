// The workbench: six steps, one screen each, outside the chat.
//
// Each step is a hash on this page (#posting, #summary…), so Back and
// Forward work and a reload stays put. Steps not rebuilt yet point at the
// editor, which still does everything.
//
// Endpoints are the editor's own: /api/chat/history holds the posting,
// /api/apps {identify} names a new application from it, /api/keywords
// reads and reports the frozen keyword list.

import { agent, APP, withApp } from "./agent.js";
import { api, el, toast } from "./dom.js";

const STEPS = [
  ["posting", "Posting"],
  ["summary", "Summary"],
  ["skills", "Skills"],
  ["bullets", "Bullets"],
  ["additional", "Additional"],
  ["review", "Review"],
];

const $ = (id) => document.getElementById(id);
const unnamed = () => Boolean(document.body.dataset.unnamed);

let history = { messages: [], job_description: "" };
let terms = null;   // the /api/keywords state, once known
let setup = null;   // /api/setup status

const posting = () => (history.job_description || "").trim();
const editor = () => `/${encodeURIComponent(APP)}/resume`;

function current() {
  const wanted = location.hash.slice(1);
  if (STEPS.some(([id]) => id === wanted)) return wanted;
  return posting() ? "summary" : "posting";
}

function paintSteps(here) {
  const at = STEPS.findIndex(([id]) => id === here);
  $("steps").replaceChildren(...STEPS.map(([id, label], index) => el("a", {
    class: index < at ? "step is-done" : "step",
    href: `#${id}`,
    "aria-current": id === here ? "step" : null,
    text: `${index + 1} ${label}`,
  })));
}

/* -------------------------------------------------------------- the rail */

function railBlock(title, rows) {
  return el("section", { class: "rail-block" }, [
    el("p", { class: "eyebrow", text: title }),
    el("div", { class: "rows" }, rows),
  ]);
}

const STATE_WORD = { empty: "Empty", started: "Started", ready: "Ready" };

function paintRail() {
  const status = (setup && setup.status) || {};
  const count = terms && terms.current ? terms.keywords.length : null;
  $("rail").replaceChildren(
    railBlock("Written from", [
      el("div", {}, [el("a", { href: "/voice", text: "Your Voice" }),
        el("span", { class: "meta", text: STATE_WORD[status.voice] || "…" })]),
      el("div", {}, [el("a", { href: "/notes", text: "Your Notes" }),
        el("span", { class: "meta", text: STATE_WORD[status.notes] || "…" })]),
      el("div", {}, [el("span", { text: "Fixed Facts" }),
        el("span", { class: "meta", text: "Never rewritten" })]),
    ]),
    railBlock("Posting", [
      el("div", {}, [el("span", { text: "Terms" }), count === null
        ? el("span", { class: "meta", text: posting() ? "Not read" : "—" })
        : el("span", { class: "meta tnum", text: `${count} frozen` })]),
    ]),
  );
}

/* ----------------------------------------------------------- the posting */

function postingRead() {
  const box = el("div", { class: "posting-read" }, [el("p", { class: "posting-text", text: posting() })]);
  return [
    el("header", { class: "page-head" }, [
      el("h1", { class: "title", text: "Posting" }),
      el("p", { class: "lead", text: "Frozen for this application." }),
    ]),
    box,
    el("div", { class: "step-foot" }, [
      el("a", { class: "btn btn-primary", href: "#summary", text: "Continue" }),
      el("button", { class: "btn", type: "button", text: "Replace posting", onclick: () => drawPosting(true) }),
    ]),
  ];
}

function postingEdit() {
  const box = el("textarea", {
    class: "input", id: "posting", rows: "14",
    placeholder: "The whole posting, requirements and all.",
  });
  box.value = posting();
  const start = el("button", { class: "btn btn-primary", type: "button", text: "Start" });
  start.addEventListener("click", () => begin(box.value, start));
  return [
    el("header", { class: "page-head" }, [
      el("h1", { class: "title", text: "Posting" }),
      el("p", { class: "lead", text: "Paste the job posting." }),
    ]),
    el("label", { class: "label", for: "posting", text: "Posting" }),
    box,
    el("div", { class: "step-foot" }, [
      start,
      posting() ? el("button", { class: "btn", type: "button", text: "Cancel", onclick: () => drawPosting(false) }) : null,
    ]),
  ];
}

function drawPosting(editing) {
  $("step").replaceChildren(el("div", { class: "measure" },
    editing || !posting() ? postingEdit() : postingRead()));
}

// The same order the editor uses: keep the posting, then name a new
// application from it (which also freezes its terms and moves it to its
// new address), or read the terms of one that already has a name.
async function begin(text, button) {
  const fresh = text.trim();
  if (!fresh) { toast("Paste the posting first.", "error"); return; }
  const model = await agent().catch(() => null);
  if (!model) { toast("Connect a model first.", "error"); return; }
  button.disabled = true;
  button.textContent = "Reading…";
  try {
    history = { ...history, job_description: fresh };
    await api("/api/chat/history", { ...history, app: APP });
    if (unnamed()) {
      const found = await api("/api/apps", {
        action: "identify", slug: APP, job_description: fresh,
        provider: model.provider, model: model.model,
      });
      location.replace(`/${encodeURIComponent(found.application.slug)}/write#summary`);
      return;
    }
    terms = await api("/api/keywords", {
      action: "read", app: APP, job_description: fresh,
      provider: model.provider, model: model.model,
    });
    toast(`${terms.keywords.length} terms frozen.`);
    location.hash = "#summary";
  } catch (error) {
    toast(error.message, "error");
    button.disabled = false;
    button.textContent = "Start";
  }
}

/* ---------------------------------------------------- steps still to come */

function drawPending(id) {
  const label = STEPS.find(([key]) => key === id)[1];
  $("step").replaceChildren(el("div", { class: "measure" }, [
    el("header", { class: "page-head" }, [
      el("h1", { class: "title", text: label }),
      el("p", { class: "lead", text: "Not rebuilt yet." }),
    ]),
    el("a", { class: "btn", href: editor(), text: "Open the editor" }),
  ]));
}

/* ----------------------------------------------------------------- start */

function route() {
  const here = current();
  paintSteps(here);
  paintRail();
  if (here === "posting") drawPosting(false);
  else if (!posting()) { location.replace("#posting"); }
  else drawPending(here);
  window.scrollTo(0, 0);
}

async function start() {
  const [loaded, kept, status, apps] = await Promise.all([
    api(withApp("/api/chat/history")).catch(() => ({})),
    api("/api/keywords", { action: "get", app: APP }).catch(() => null),
    api("/api/setup").catch(() => null),
    api("/api/apps").catch(() => ({ applications: [] })),
  ]);
  history = {
    messages: Array.isArray(loaded.messages) ? loaded.messages : [],
    job_description: typeof loaded.job_description === "string" ? loaded.job_description : "",
  };
  terms = kept;
  setup = status;
  const app = (apps.applications || []).find((a) => a.slug === APP);
  $("app-title").textContent = app ? app.name : "";
  route();
}

window.addEventListener("hashchange", route);
start();
