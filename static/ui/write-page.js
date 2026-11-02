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
import { drawLetter } from "./step-letter.js";
import { drawReview } from "./step-review.js";
import { drawSkills } from "./step-skills.js";
import { drawSummary } from "./step-summary.js";

// Same shell for CV and cover letter; path picks the document.
const MODE = /\/letter\/?$/.test(location.pathname) ? "cover" : "cv";
const CV_STEPS = [
  ["posting", "Job Description"],
  ["summary", "Professional Summary"],
  ["skills", "Skills"],
  ["bullets", "Work History"],
  ["additional", "Additional"],
  ["review", "Review"],
];
const LETTER_STEPS = [
  ["letter", "Letter"],
  ["review", "Review"],
];
const STEPS = MODE === "cover" ? LETTER_STEPS : CV_STEPS;

const $ = (id) => document.getElementById(id);
const unnamed = () => Boolean(document.body.dataset.unnamed);

let history = { messages: [], job_description: "" };
let terms = null;   // the /api/keywords state, once known
let documents = null; // /api/document: the cv, the letter and both themes
let refreshHandler = null; // current step's "run this section again"
let lastFit = null; // model's fit reading for this CV + posting

// JD model fill (skeleton + section turn) runs only from the foot refresh
// button. Opening an app, reloading the page, or clicking a step tab never
// starts it — those only show what is already saved.
function consumeAutoFill(_step) {
  return false;
}

const posting = () => (history.job_description || "").trim();
const appsHome = () => "/";

function current() {
  const wanted = location.hash.slice(1);
  if (STEPS.some(([id]) => id === wanted)) return wanted;
  if (MODE === "cover") return "letter";
  return posting() ? "summary" : "posting";
}

function activeDoc() {
  return MODE === "cover" ? documents.cover : documents.cv;
}

// Fit readings are paid model calls: keep them per application + document in
// this browser. Resume and Cover Letter never share a slot. The score always
// paints once saved — a fingerprint only notes staleness in the tooltip, so
// an edit or a reload cannot blank the strip the way a strict key match did.
const fitMode = () => (MODE === "cover" ? "cover" : "cv");
const appId = () => document.body.dataset.appId || APP || "default";
const fitStore = () => `resume-maker.fit.${appId()}.${fitMode()}`;
const langStore = () => `resume-maker.lang.${appId()}.${fitMode()}`;

function fingerprint(text) {
  let hash = 5381;
  const s = String(text || "");
  for (let i = 0; i < s.length; i += 1) hash = ((hash * 33) ^ s.charCodeAt(i)) >>> 0;
  return hash.toString(36);
}

function fitKey() {
  if (!documents) return `${APP}\n${MODE}\n${fingerprint(posting())}\n`;
  return `${APP}\n${MODE}\n${fingerprint(posting())}\n${fingerprint(JSON.stringify(activeDoc()))}`;
}

function loadFit() {
  lastFit = null;
  try {
    const raw = JSON.parse(localStorage.getItem(fitStore()) || "null");
    if (raw && typeof raw.score === "number"
        && (raw.mode == null || raw.mode === fitMode())) {
      lastFit = { ...raw, mode: fitMode() };
    }
  } catch (e) { lastFit = null; }
}

function saveFit(value) {
  lastFit = { ...value, mode: fitMode() };
  try { localStorage.setItem(fitStore(), JSON.stringify(lastFit)); }
  catch (e) { /* optional */ }
}

function loadLang() {
  try {
    const raw = JSON.parse(localStorage.getItem(langStore()) || "null");
    if (raw && raw.mode === fitMode() && typeof raw.score === "number") {
      postingScoreValue = raw.score;
      return;
    }
  } catch (e) { /* ignore */ }
  postingScoreValue = null;
}

function saveLang(score) {
  postingScoreValue = score;
  try {
    localStorage.setItem(langStore(), JSON.stringify({
      score, mode: fitMode(), at: Date.now(),
    }));
  } catch (e) { /* optional */ }
}

function scoreBand(n) {
  if (n >= 75) return "is-high";
  if (n >= 50) return "is-mid";
  return "is-low";
}

let postingScoreValue = null; // last posting-language count, painted with Fit

function fitStale() {
  return Boolean(lastFit && lastFit.key && lastFit.key !== fitKey());
}

function scoreChip(kind, label, value) {
  const shown = value == null ? "-" : String(value);
  const tip = kind === "fit"
    ? (fitStale()
      ? "Overall Fit - saved score; document changed. Click to refresh."
      : "Overall Fit - model read. Click to refresh.")
    : "Language Fit - keyword coverage. Click to refresh.";
  return el("button", {
    class: "steps-score-item", type: "button", title: tip,
    "aria-label": `${label} ${shown}. Click to refresh.`,
    "data-score-kind": kind,
    onclick: () => refreshOneScore(kind),
  }, [
    // nbsp around the hyphen so labels never collapse on wrap.
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
  // Always show the last saved score for this app + document. Never blank it
  // just because the fingerprint moved — that was wiping Overall Fit on reopen.
  const fitScore = lastFit && lastFit.mode === fitMode()
    && typeof lastFit.score === "number"
    ? lastFit.score
    : null;
  node.replaceChildren(
    scoreChip("fit", "Overall Fit", fitScore),
    scoreChip("posting", "Language Fit", postingScoreValue),
  );
}

// Write JSON and rebuild PDF + DOCX into the application's out/ folder —
// same Save the old editor used. Not a browser download.
async function saveDocuments(button) {
  if (!documents) { toast("Nothing to save yet.", "error"); return; }
  button.disabled = true;
  button.textContent = "Saving…";
  try {
    const data = await api("/api/save", {
      ...documents, document: MODE === "cover" ? "cover" : "cv", app: APP, build: true,
    });
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

function lockScoreChip(kind) {
  const button = $("steps") && $("steps").querySelector(`[data-score-kind="${kind}"]`);
  if (button) button.disabled = true;
  return button;
}

async function refreshOneScore(kind) {
  // Clear this chip first so a same-number rescore still looks like work.
  if (kind === "fit") lastFit = null;
  else postingScoreValue = null;
  paintScoreNode();
  lockScoreChip(kind);
  const started = Date.now();
  try {
    if (kind === "fit") await rateFit();
    else await loadPostingScore();
    // Keyword match can finish in one frame; keep the dash up briefly.
    const wait = 320 - (Date.now() - started);
    if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait));
    paintScoreNode();
    if (kind === "posting") {
      toast(postingScoreValue == null
        ? "Language Fit — no score yet"
        : `Language Fit — ${postingScoreValue}`);
    }
  } catch (error) {
    toast(error.message, "error");
    paintScoreNode();
  } finally {
    const button = $("steps") && $("steps").querySelector(`[data-score-kind="${kind}"]`);
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
      document: MODE === "cover" ? "cover" : "cv",
      doc: activeDoc(),
      job_description: history.job_description, app: APP,
    });
    const next = (scored.needs_list || scored.score === null)
      ? null
      : Math.round(Number(scored.score));
    if (next == null) postingScoreValue = null;
    else saveLang(next);
  } catch (error) {
    postingScoreValue = null;
  }
}

async function rateFit() {
  if (!documents || !posting()) throw new Error("Paste a posting first.");
  const model = await agent().catch(() => null);
  if (!model) throw new Error("Connect a model first.");
  const data = await api("/api/fit", {
    document: MODE === "cover" ? "cover" : "cv",
    doc: activeDoc(),
    job_description: history.job_description,
    provider: model.provider, model: model.model,
  });
  saveFit({
    score: Math.round(Number(data.score)),
    key: fitKey(),
    at: Date.now(),
  });
}

async function loadScore() {
  const node = $("steps") && $("steps").querySelector("[data-step-score]");
  if (!node) return;
  loadFit();
  loadLang();
  if (!documents || !posting()) {
    postingScoreValue = null;
    node.replaceChildren();
    return;
  }
  paintScoreNode(); // this mode's cached scores first; then refresh language
  await loadPostingScore();
  paintScoreNode();
}

function setStepProgress(done, total) {
  const bar = $("steps") && $("steps").querySelector("[data-step-progress]");
  if (!bar) return;
  const fill = bar.firstElementChild;
  if (!total || total < 0) {
    bar.hidden = true;
    bar.removeAttribute("aria-valuenow");
    if (fill) fill.style.width = "0%";
    return;
  }
  const pct = Math.max(0, Math.min(100, Math.round((100 * done) / total)));
  bar.hidden = false;
  bar.setAttribute("aria-valuenow", String(pct));
  bar.setAttribute("aria-valuemax", "100");
  if (fill) fill.style.width = `${pct}%`;
  if (done >= total) {
    window.setTimeout(() => {
      if (Number(bar.getAttribute("aria-valuenow")) === 100) setStepProgress(0, 0);
    }, 500);
  }
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
  const progress = el("div", {
    class: "steps-progress",
    "data-step-progress": "",
    role: "progressbar",
    "aria-label": "Writing this step",
    "aria-valuemin": "0",
    "aria-valuemax": "100",
    hidden: true,
  }, [el("span")]);
  $("steps").replaceChildren(links, el("div", { class: "steps-aside" }, [score, save]), progress);
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
      el("h1", { class: "title", text: "Job Description" }),
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
      el("h1", { class: "title", text: "Job Description" }),
      el("p", { class: "lead", text: "Paste the job description." }),
    ]),
    box,
    el("div", { class: "step-foot" }, [start]),
  ];
}

async function refreshPostingTerms() {
  const model = await agent().catch(() => null);
  if (!model) throw new Error("Connect a model first.");
  setStepProgress(0, 1);
  try {
    terms = await api("/api/keywords", {
      action: "read", app: APP, job_description: posting(),
      provider: model.provider, model: model.model,
    });
    toast(`${terms.keywords.length} terms frozen.`);
  } finally {
    setStepProgress(1, 1);
  }
}

function drawPosting() {
  // Paste only when empty. Once kept, the posting is read-only here.
  $("step").replaceChildren(el("div", { class: "measure" },
    posting() ? postingRead() : postingPaste()));
  // Same rule as the other steps: foot refresh re-reads terms; opening the
  // Foot refresh re-reads terms. Opening / reload / tabs do not.
  if (posting()) {
    setRefresh(refreshPostingTerms);
    if (consumeAutoFill("posting")) refreshPostingTerms().catch((error) => toast(error.message, "error"));
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

async function saveCover() {
  await api("/api/save", { ...documents, document: "cover", app: APP, build: false });
  await loadScore();
}

// One section turn of the editor's round, waited for in full. Returns the
// proposals it made: [{ path, text, show }].
const PROPOSAL = /```proposal\s*([\s\S]*?)```/g;

function parseProposals(reply) {
  const found = [];
  for (const match of reply.matchAll(PROPOSAL)) {
    try {
      const parsed = JSON.parse(match[1]);
      if (parsed && typeof parsed.path === "string") found.push(parsed);
    } catch (e) { /* not a proposal */ }
  }
  return found;
}

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
  return parseProposals(reply);
}

// Cover letter: same chat plumbing as Pico (letter rules + voice + notes +
// JD), one turn that proposes every paragraph; we auto-accept.
async function askLetter() {
  return parseProposals(await askTurn(phase, extra));
}

async function askTurn(phase, extra = {}) {
  const model = await agent().catch(() => null);
  if (!model) throw new Error("Connect a model first.");
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      document: "cover", doc: documents.cover, app: APP,
      provider: model.provider, model: model.model,
      job_description: history.job_description,
      wanted: terms && terms.current ? terms.keywords : [],
      messages: [{
        role: "user",
        content: "Write a one-page cover letter for this posting. Ground every "
          + "claim in the notes and the CV. Keep it on one printed page: about "
          + "three or four short paragraphs total (greeting opens the first), "
          + "under about 400 words. Prefer fewer stronger claims over a second "
          + "page. Propose every body paragraph as a proposal block "
          + "(paragraphs.0, paragraphs.1, …), first to last, so the letter "
          + "reads as one piece. Use [Company] and [Role] placeholders.",
      }],
    }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || response.statusText);
  }
  const [reply, failed] = (await response.text()).split(String.fromCharCode(0));
  if (failed) throw new Error(failed);
  return reply;
}

const sectionIndex = (word) => (documents.cv.sections || []).findIndex(
  (s) => String(s.heading || "").toUpperCase().includes(word));

function context() {
  return {
    app: APP, mode: MODE, documents, history, terms,
    saveCv, saveCover, askSection, askLetter, sectionIndex,
    setRefresh, sectionRefresh, setStepProgress, consumeAutoFill,
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
  // Cover letter shares the posting; it never has its own paste step.
  if (MODE === "cover") {
    if (!posting()) {
      location.replace(`/${encodeURIComponent(APP)}/write#posting`);
      return;
    }
    if (!documents) drawPending(here);
    else if (here === "letter") drawLetter(context());
    else if (here === "review") drawReview(context());
    else drawPending(here);
  } else if (here === "posting") drawPosting();
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
  loadFit();
  const app = (apps.applications || []).find((a) => a.slug === APP);
  paintAppTitle(app ? app.name : "");
  route();
}

// Company - role in the nav: keep a short hyphen, drawn smaller than the words.
// Resume | Cover Letter live in the left nav (same look as Application Tone).
function paintAppTitle(name) {
  const node = $("app-title");
  const text = String(name || "");
  const found = text.match(/^(.*?)\s*[—–-]\s*(.*)$/u);
  if (found && found[1] && found[2]) {
    node.replaceChildren(
      el("span", { class: "app-title-name" }, [
        document.createTextNode(found[1]),
        el("span", { class: "app-title-sep", text: "-" }),
        document.createTextNode(found[2]),
      ]),
    );
    return;
  }
  node.replaceChildren(el("span", { class: "app-title-name", text }));
}

window.addEventListener("hashchange", route);
start();
