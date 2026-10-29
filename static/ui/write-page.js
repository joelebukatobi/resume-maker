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
import { api, el, icon, stepAction, toast } from "./dom.js";
import { drawAdditional } from "./step-additional.js";
import { drawBullets } from "./step-bullets.js";
import { drawReview } from "./step-review.js";
import { drawSkills } from "./step-skills.js";
import { drawSummary } from "./step-summary.js";

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
let documents = null; // /api/document: the cv, the letter and both themes
let refreshHandler = null; // current step's "run this section again"
let lastFit = null; // model's fit reading for this CV + posting

const posting = () => (history.job_description || "").trim();
const appsHome = () => "/";

function current() {
  const wanted = location.hash.slice(1);
  if (STEPS.some(([id]) => id === wanted)) return wanted;
  return posting() ? "summary" : "posting";
}

function fitKey() {
  return `${APP}\n${posting()}\n${JSON.stringify(documents && documents.cv)}`;
}

function scoreBand(n) {
  if (n >= 75) return "is-high";
  if (n >= 50) return "is-mid";
  return "is-low";
}

let postingScoreValue = null; // last posting-language count, painted with Fit

function scoreChip(kind, label, value) {
  const shown = value == null ? "-" : String(value);
  const tip = kind === "fit"
    ? "Overall - model read. Click to refresh."
    : "Language - keyword coverage. Click to refresh.";
  return el("button", {
    class: "steps-score-item", type: "button", title: tip,
    "aria-label": `${label} ${shown}. Click to refresh.`,
    "data-score-kind": kind,
    onclick: () => refreshOneScore(kind),
  }, [
    // nbsp around the hyphen so "Overall - 66" / "Language - 67" never collapse.
    el("span", { text: `${label}\u00A0-\u00A0` }),
    el("span", {
      class: value == null ? "steps-score-num" : `steps-score-num ${scoreBand(value)}`,
      text: shown,
    }),
  ]);
}

function paintScoreNode() {
  const node = $("steps") && $("steps").querySelector("[data-step-score]");
  if (!node) return;
  const fitScore = lastFit && lastFit.key === fitKey() ? lastFit.score : null;
  node.replaceChildren(
    scoreChip("fit", "Overall", fitScore),
    scoreChip("posting", "Language", postingScoreValue),
  );
}

// Write JSON and rebuild PDF + DOCX into the application's out/ folder —
// same Save the old editor used. Not a browser download.
async function saveDocuments(button) {
  if (!documents) { toast("Nothing to save yet.", "error"); return; }
  button.disabled = true;
  button.textContent = "Saving…";
  try {
    const data = await api("/api/save", { ...documents, document: "cv", app: APP, build: true });
    if (data.build_error) {
      toast(`Saved JSON, but rebuild failed: ${data.build_error}`, "error");
      button.textContent = "Save";
      return;
    }
    const files = (data.written || []).map((p) => String(p).split("/").pop()).filter(Boolean);
    button.textContent = "Saved";
    toast(files.length ? `Saved ${files.join(", ")}` : "Saved.");
  } catch (error) {
    toast(error.message, "error");
    button.textContent = "Save";
  } finally {
    button.disabled = false;
    if (button.textContent === "Saved") {
      setTimeout(() => { if (button.isConnected) button.textContent = "Save"; }, 1600);
    }
  }
}

function sectionRefreshButton() {
  return document.querySelector("[data-section-refresh]");
}

function setRefresh(fn) {
  refreshHandler = typeof fn === "function" ? fn : null;
  const button = sectionRefreshButton();
  if (button) button.disabled = !refreshHandler;
}

// Same size as Back / Next / Skip; lives in the step foot, not the strip.
function sectionRefresh() {
  return el("button", {
    class: "posting-action", type: "button", "data-section-refresh": "",
    "aria-label": "Refresh this step", disabled: true,
    onclick: () => runSectionRefresh(),
  }, [icon("refresh", 18)]);
}

async function runSectionRefresh() {
  if (!refreshHandler) return;
  const button = sectionRefreshButton();
  if (button) button.disabled = true;
  try {
    await refreshHandler();
    await loadScore();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    if (button) button.disabled = !refreshHandler;
  }
}

async function refreshOneScore(kind) {
  const button = $("steps") && $("steps").querySelector(`[data-score-kind="${kind}"]`);
  if (button) button.disabled = true;
  try {
    if (kind === "fit") await rateFit();
    else await loadPostingScore();
    paintScoreNode();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    if (button) button.disabled = false;
  }
}

async function loadPostingScore() {
  if (!documents || !posting()) {
    postingScoreValue = null;
    return;
  }
  try {
    const scored = await api("/api/match", {
      document: "cv", doc: documents.cv,
      job_description: history.job_description, app: APP,
    });
    postingScoreValue = (scored.needs_list || scored.score === null)
      ? null
      : Math.round(Number(scored.score));
  } catch (error) {
    postingScoreValue = null;
  }
}

async function rateFit() {
  if (!documents || !posting()) throw new Error("Paste a posting first.");
  const model = await agent().catch(() => null);
  if (!model) throw new Error("Connect a model first.");
  const data = await api("/api/fit", {
    document: "cv", doc: documents.cv,
    job_description: history.job_description,
    provider: model.provider, model: model.model,
  });
  lastFit = { score: Math.round(Number(data.score)), key: fitKey() };
}

async function loadScore() {
  const node = $("steps") && $("steps").querySelector("[data-step-score]");
  if (!node) return;
  if (!documents || !posting()) {
    postingScoreValue = null;
    node.replaceChildren();
    return;
  }
  await loadPostingScore();
  paintScoreNode();
}

function paintSteps(here) {
  const at = STEPS.findIndex(([id]) => id === here);
  refreshHandler = null;
  const links = el("div", { class: "steps-links" }, STEPS.map(([id, label], index) => el("a", {
    class: index < at ? "step is-done" : "step",
    href: `#${id}`,
    "aria-current": id === here ? "step" : null,
    text: `${index + 1} ${label}`,
  })));
  const score = el("div", { class: "steps-score", "data-step-score": "", "aria-live": "polite" });
  const save = el("button", {
    class: "steps-score-item", type: "button", text: "Save",
    "aria-label": "Save PDF and DOCX to this application",
    onclick: (event) => saveDocuments(event.currentTarget),
  });
  $("steps").replaceChildren(links, el("div", { class: "steps-aside" }, [score, save]));
  loadScore();
}

/* ----------------------------------------------------------- the posting */

// Display only: keep every word, tighten blank runs, mark section titles.
function isPostingHead(line, next) {
  const text = line.trim();
  if (!text || text.length > 48 || /[.!?]$/.test(text)) return false;
  return Boolean(next && next.trim());
}

function paintPosting(text) {
  // Keep the posting's own words. Only shorten Unicode dashes to ASCII "-";
  // never invent a hyphen where the posting had none.
  const raw = String(text || "").replace(/\r\n/g, "\n")
    .replace(/[\u2010-\u2015\u2212]/g, "-")
    .split("\n");
  const lines = [];
  for (const line of raw) {
    if (!line.trim()) {
      if (lines.length && lines[lines.length - 1] !== "") lines.push("");
      continue;
    }
    lines.push(line);
  }
  while (lines[0] === "") lines.shift();
  while (lines.length && lines[lines.length - 1] === "") lines.pop();

  const kids = [];
  let afterHead = false;
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (line === "") {
      if (afterHead) { afterHead = false; continue; }
      kids.push(el("div", { class: "posting-gap", "aria-hidden": "true" }));
      continue;
    }
    const next = lines.slice(i + 1).find((row) => row !== "");
    const head = isPostingHead(line, next);
    kids.push(el("p", { class: head ? "posting-head" : "posting-line", text: line }));
    afterHead = head;
  }
  return el("div", { class: "posting-body" }, kids);
}

function postingRead() {
  const box = el("div", { class: "posting-read" }, [paintPosting(posting())]);
  return [
    el("header", { class: "page-head" }, [
      el("h1", { class: "title", text: "Posting" }),
      el("p", { class: "lead", text: "Review it, then continue." }),
    ]),
    box,
    el("div", { class: "step-foot" }, [
      stepAction("a", { href: "#summary", "aria-label": "Continue" }, "right"),
      sectionRefresh(),
    ]),
  ];
}

function postingPaste() {
  const box = el("textarea", {
    class: "input", id: "posting", rows: "14",
    placeholder: "Paste the whole posting, requirements and all.",
    "aria-label": "Job posting",
  });
  const start = stepAction("button", { type: "button", "aria-label": "Start" }, "right");
  start.addEventListener("click", () => begin(box.value, start));
  return [
    el("header", { class: "page-head" }, [
      el("h1", { class: "title", text: "Posting" }),
      el("p", { class: "lead", text: "Paste the job posting." }),
    ]),
    box,
    el("div", { class: "step-foot" }, [start]),
  ];
}

function drawPosting() {
  // Paste only when empty. Once kept, the posting is read-only here.
  $("step").replaceChildren(el("div", { class: "measure" },
    posting() ? postingRead() : postingPaste()));
  if (posting()) {
    setRefresh(async () => {
      const model = await agent().catch(() => null);
      if (!model) throw new Error("Connect a model first.");
      terms = await api("/api/keywords", {
        action: "read", app: APP, job_description: posting(),
        provider: model.provider, model: model.model,
      });
      toast(`${terms.keywords.length} terms frozen.`);
    });
  }
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
  button.setAttribute("aria-busy", "true");
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
    button.removeAttribute("aria-busy");
  }
}

/* ------------------------------------------------- what the steps share */

// Saving writes the JSON only; the files are built at Review.
async function saveCv() {
  await api("/api/save", { ...documents, document: "cv", app: APP, build: false });
  await loadScore();
}

// One section turn of the editor's round, waited for in full. Returns the
// proposals it made: [{ path, text, show }].
const PROPOSAL = /```proposal\s*([\s\S]*?)```/g;

async function askSection(phase, extra = {}) {
  const model = await agent().catch(() => null);
  if (!model) throw new Error("Connect a model first.");
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      document: "cv", doc: documents.cv, app: APP,
      provider: model.provider, model: model.model,
      job_description: history.job_description,
      wanted: terms && terms.current ? terms.keywords : [],
      task: "tailor", phase, settled: [], declined: [], ...extra,
      messages: [{ role: "user", content: "Tailor this CV to the posting." }],
    }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || response.statusText);
  }
  const [reply, failed] = (await response.text()).split(String.fromCharCode(0));
  if (failed) throw new Error(failed);
  const found = [];
  for (const match of reply.matchAll(PROPOSAL)) {
    try {
      const parsed = JSON.parse(match[1]);
      if (parsed && typeof parsed.path === "string") found.push(parsed);
    } catch (e) { /* not a proposal */ }
  }
  return found;
}

const sectionIndex = (word) => (documents.cv.sections || []).findIndex(
  (s) => String(s.heading || "").toUpperCase().includes(word));

function context() {
  return {
    app: APP, documents, history, terms, saveCv, askSection, sectionIndex,
    setRefresh, sectionRefresh,
    host: $("step"), next: (id) => { location.hash = `#${id}`; },
  };
}

/* ---------------------------------------------------- steps still to come */

function drawPending(id) {
  const label = STEPS.find(([key]) => key === id)[1];
  $("step").replaceChildren(el("div", { class: "measure" }, [
    el("header", { class: "page-head" }, [
      el("h1", { class: "title", text: label }),
      el("p", { class: "lead", text: "Not rebuilt yet." }),
    ]),
    el("a", { class: "btn", href: appsHome(), text: "Applications" }),
  ]));
}

/* ----------------------------------------------------------------- start */

function route() {
  const here = current();
  window.onbeforeunload = null;
  window.onmessage = null;
  paintSteps(here);
  if (here === "posting") drawPosting();
  else if (!posting()) { location.replace("#posting"); }
  else if (!documents) drawPending(here);
  else if (here === "summary") drawSummary(context());
  else if (here === "skills") drawSkills(context());
  else if (here === "bullets") drawBullets(context());
  else if (here === "additional") drawAdditional(context());
  else if (here === "review") drawReview(context());
  else drawPending(here);
  window.scrollTo(0, 0);
}

async function start() {
  const [loaded, kept, apps, docs] = await Promise.all([
    api(withApp("/api/chat/history")).catch(() => ({})),
    api("/api/keywords", { action: "get", app: APP }).catch(() => null),
    api("/api/apps").catch(() => ({ applications: [] })),
    api(withApp("/api/document")).catch(() => null),
  ]);
  documents = docs;
  history = {
    messages: Array.isArray(loaded.messages) ? loaded.messages : [],
    job_description: typeof loaded.job_description === "string" ? loaded.job_description : "",
  };
  terms = kept;
  const app = (apps.applications || []).find((a) => a.slug === APP);
  paintAppTitle(app ? app.name : "");
  route();
}

// Company - role in the nav: keep a short hyphen, drawn smaller than the words.
function paintAppTitle(name) {
  const node = $("app-title");
  const text = String(name || "");
  const found = text.match(/^(.*?)\s*[—–-]\s*(.*)$/u);
  if (!found || !found[1] || !found[2]) {
    node.textContent = text;
    return;
  }
  node.replaceChildren(
    document.createTextNode(found[1]),
    el("span", { class: "app-title-sep", text: "-" }),
    document.createTextNode(found[2]),
  );
}

window.addEventListener("hashchange", route);
start();
