// Step 6, Review: what to check, and a change chat. The page is /api/preview,
// opened in a modal from View Resume; language is /api/match; Save on the strip
// rebuilds out/. Under the lead, the page in words when it runs long or has
// lines to spare. Two accordion cards: Language (the terms in green or amber)
// and Gaps (a question for each unanswered term, then the gaps in the finished
// CV). The chat sits underneath, saved per application; the model's cards only
// suggest, and nothing is written until he presses the card's button.

import { agent } from "./agent.js";
import { attachDictate } from "./dictate.js";
import { api, el, icon, toast } from "./dom.js";
import { renderMarkdown } from "./markdown.js";

const STREAM_ERROR = String.fromCharCode(0);
const FIXED = /^(?:name|contact\.\d+\.text|sections\.\d+\.heading|sections\.\d+\.entries\.\d+\.(?:left|right|sub_left|sub_right))$/;
const MAX_TURNS = 200; // the server refuses a longer history
const BLOCK = /```(proposal|answer|notes|voice|gaps|ask|skill)\s*([\s\S]*?)```/g;

// Scrollbar + wheel zoom inside the preview (wheel events hit the iframe, not the parent).
// Same look as the app: always-shown 6px thumb, an arrow at each end, no track.
const PREVIEW_CHROME = `<style>
/* No scrollbar-width here — on Chromium/Linux it forces GTK arrows. */
html::-webkit-scrollbar,body::-webkit-scrollbar,
*::-webkit-scrollbar{
  width:16px;height:16px;background:transparent;
}
*::-webkit-scrollbar-button{display:none}
*::-webkit-scrollbar-button:single-button{
  display:block;width:16px;height:16px;border:0;
  background:transparent no-repeat center/8px 8px;
}
*::-webkit-scrollbar-button:single-button:vertical:decrement{
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 8 8'%3E%3Cpath d='M4 2 7 6H1z' fill='%239B9BA4'/%3E%3C/svg%3E");
}
*::-webkit-scrollbar-button:single-button:vertical:increment{
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 8 8'%3E%3Cpath d='M4 6 7 2H1z' fill='%239B9BA4'/%3E%3C/svg%3E");
}
html::-webkit-scrollbar-track,body::-webkit-scrollbar-track,
*::-webkit-scrollbar-track,*::-webkit-scrollbar-track-piece{
  background:transparent!important;border:none!important;box-shadow:none!important;
}
html::-webkit-scrollbar-corner,body::-webkit-scrollbar-corner,
*::-webkit-scrollbar-corner{
  background:transparent!important;border:none!important;box-shadow:none!important;
}
/* Always shown here: hover inside a frame is unreliable, and a zoomed page
   needs its scrollbar in sight. */
*::-webkit-scrollbar-thumb{
  background:#9B9BA4!important;background-clip:padding-box!important;
  border:5px solid transparent!important;border-radius:8px;
}
/* The layout script makes body a scroll box too; only the page should scroll. */
body{overflow:visible!important}
@supports not selector(::-webkit-scrollbar){
  html,body,*{scrollbar-width:thin;scrollbar-color:#9B9BA4 transparent}
}
</style><script>
window.addEventListener('wheel', function (event) {
  // A plain wheel scrolls; Ctrl + wheel (and a trackpad pinch) zooms.
  if (!event.ctrlKey) return;
  event.preventDefault();
  if (window.parent === window) return;
  window.parent.postMessage({
    type: 'resume-zoom-delta',
    delta: event.deltaY > 0 ? -0.1 : 0.1,
  }, '*');
}, { passive: false });
</script>`;

// The no that goes into his notes, in his voice rather than the question's.
function negative(question) {
  const asked = String(question).trim().replace(/[?.]+$/, "");
  for (const [pattern, opening] of [
    [/^do you have\b/i, "I do not have"],
    [/^do you\b/i, "I do not"],
    [/^have you ever\b/i, "I have never"],
    [/^have you\b/i, "I have not"],
    [/^are you\b/i, "I am not"],
    [/^can you\b/i, "I cannot"],
    [/^did you\b/i, "I did not"],
    [/^any\b/i, "I have no"],
  ]) {
    if (pattern.test(asked)) return `${asked.replace(pattern, opening)}.`;
  }
  return `Not mine. ${asked}.`;
}

function scoreBand(n) {
  if (n >= 75) return "is-high";
  if (n >= 50) return "is-mid";
  return "is-low";
}

function pathParts(path) {
  return String(path || "").split(".").map((part) => (/^\d+$/.test(part) ? Number(part) : part));
}

function getPath(doc, path) {
  return pathParts(path).reduce((node, key) => (node == null ? undefined : node[key]), doc);
}

function setPath(doc, path, text) {
  const parts = pathParts(path);
  const last = parts.pop();
  const parent = parts.reduce((node, key) => node[key], doc);
  parent[last] = text;
}

// Only a role can be switched on or off: a bullet or a heading has no such state.
function canShow(doc, path) {
  const parts = pathParts(path);
  const last = parts.pop();
  if (typeof last !== "number" || parts[parts.length - 1] !== "entries") return false;
  const entry = getPath(doc, path);
  return Boolean(entry) && typeof entry === "object" && !Array.isArray(entry);
}

// Only something in a list can be removed. Deleting a field of an object leaves
// it missing a key the document requires, and every later save fails.
function canRemove(doc, path) {
  const parts = pathParts(path);
  const last = parts.pop();
  const parent = parts.reduce((node, key) => (node == null ? undefined : node[key]), doc);
  return Array.isArray(parent) && typeof last === "number" && last < parent.length;
}

function removePath(doc, path) {
  const parts = pathParts(path);
  const last = parts.pop();
  parts.reduce((node, key) => node[key], doc).splice(last, 1);
}

const ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
  "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"];
const TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"];

function inWords(n) {
  if (n < 20) return ONES[n];
  if (n >= 100) return String(n);
  return TENS[Math.floor(n / 10)] + (n % 10 ? `-${ONES[n % 10]}` : "");
}

function gapQuestions(raw) {
  return raw.split("\n").map((line) => line.replace(/^[-*\d.\s]+/, "").trim()).filter(Boolean);
}

export async function drawReview(ctx) {
  const host = ctx.host;
  const kind = ctx.mode === "cover" ? "cover" : "cv";
  const doc = kind === "cover" ? ctx.documents.cover : ctx.documents.cv;
  const payload = { ...ctx.documents, document: kind, app: ctx.app };
  const saveDoc = kind === "cover" ? ctx.saveCover : ctx.saveCv;
  const label = kind === "cover" ? "cover letter" : "CV";

  const frame = el("iframe", {
    class: "paper-frame",
    title: kind === "cover" ? "Your cover letter" : "Your CV",
    sandbox: "allow-scripts",
  });
  let pageNotes = ""; // the preview's own warnings, said beside the page sentence
  const coverage = el("div", { class: "coverage" }, [el("p", { class: "meta", text: "Counting…" })]);
  const termsList = el("div", { class: "term-list" });
  const askBlock = el("div", { class: "ask-list" });
  const gapsBody = el("div", { class: "review-card-body review-gaps" });
  // Said in the Gaps header while the finished CV is being checked.
  const gapsStatus = el("span", { class: "meta review-card-status", "aria-live": "polite" });
  const fitLine = el("p", { class: "review-fit" });
  const viewLink = el("button", {
    class: "link-btn", type: "button", text: kind === "cover" ? "View Letter" : "View Resume",
  });
  const gapStore = `resume-maker.gaps.${ctx.appId ? ctx.appId() : ctx.app}`;
  // The letter's own read: opening, claims, tone, length. Kept apart from the CV's gaps.
  const checkStore = `resume-maker.letter-checks.${ctx.appId ? ctx.appId() : ctx.app}`;
  const checksBody = el("div", { class: "review-card-body review-gaps" });
  const checksStatus = el("span", { class: "meta review-card-status", "aria-live": "polite" });
  let checks = null; // what the last read of this letter found; null before one
  let checkingLetter = false;
  let pages = null;
  let room = 0;
  let gaps = null; // what the last check of the finished CV found; null before one
  let checkingGaps = false;
  let issues = [];
  let missing = [];
  let matched = [];
  let keywordMap = (ctx.terms && ctx.terms.map) || {};
  let previewZoom = 1;

  // Silent unless something needs fixing: too long, or a warning from the page.
  function paintChecks() {
    const notes = issues.map((issue) => String(issue.message || "").trim().replace(/\.?$/, "."));
    pageNotes = notes.join(" ");
    paintChanges();
  }

  // The chat sits under the cards; a Change link or a gap brings it into view.
  function openChat(text = "") {
    const input = document.getElementById("review-chat-input");
    if (!input) return;
    if (text) input.value = [input.value.trim(), text].filter(Boolean).join("\n");
    input.scrollIntoView({ block: "center", behavior: "smooth" });
    input.focus({ preventScroll: true });
  }

  // The page in words: too long is red, lines to spare are green, full says nothing.
  function fitSentence() {
    if (pages > 1) {
      return ["This runs to ", el("span", { class: "fit-over", text: inWords(pages) }), " pages."];
    }
    if (pages === 1 && room > 0) {
      return ["You have ", el("span", { class: "fit-spare", text: inWords(room) }),
        room === 1 ? " line spare." : " lines spare."];
    }
    return null;
  }

  // Under the lead: how the page sits, any warning, then View Resume.
  function paintChanges() {
    const said = [...(fitSentence() || []), ...(pageNotes ? [" ", pageNotes] : [])];
    fitLine.replaceChildren(
      ...said,
      ...(said.length ? [el("span", { class: "review-dot", "aria-hidden": "true", text: "\u00B7" })] : []),
      viewLink,
    );
  }

  function loadGaps() {
    try { return JSON.parse(localStorage.getItem(gapStore) || "null"); }
    catch (e) { return null; }
  }

  function saveGaps() {
    try { localStorage.setItem(gapStore, JSON.stringify({ key: ctx.fitKey(), gaps })); }
    catch (e) { /* the list just won't survive a reload */ }
  }

  // Everything to answer in one section: the posting's terms that are not on
  // the page, then what the last read of the finished CV found.
  function paintGaps() {
    if (kind === "cover") return; // the letter has Checks instead
    const found = (gaps || []).map((gap) => gapRow(gap, (settled) => {
      if (settled) gaps = gaps.filter((other) => other !== gap);
      saveGaps();
    }));
    const quiet = gaps && !gaps.length && !askBlock.childElementCount;
    gapsBody.replaceChildren(
      askBlock,
      ...found,
      ...(quiet && !checkingGaps ? [el("p", { class: "meta", text: "Nothing missing." })] : []),
    );
    gapsStatus.textContent = checkingGaps ? "Checking the CV against the posting…" : "";
    gapsFold.hidden = !checkingGaps && !gaps && !askBlock.childElementCount;
  }

  // One paid model turn, then the notes check. Run on arriving by Next or from
  // Overall Fit, never on opening the page, so a reload costs nothing.
  async function runGapCheck() {
    if (checkingGaps) return;
    checkingGaps = true;
    paintGaps();
    try {
      const found = await ctx.working(async () => {
        const said = await ctx.askTurn("gaps");
        const block = /```gaps\s*([\s\S]*?)```/.exec(said);
        const questions = block ? gapQuestions(block[1]) : [];
        // If the notes check cannot run, every question is still asked.
        return questions.length
          ? api("/api/gaps", { action: "check", questions })
            .catch(() => questions.map((question) => ({ question, status: "open" })))
          : [];
      });
      gaps = found.filter((gap) => gap.status !== "covered");
      saveGaps();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      checkingGaps = false;
      paintGaps();
    }
  }

  function loadChecks() {
    try { return JSON.parse(localStorage.getItem(checkStore) || "null"); }
    catch (e) { return null; }
  }

  function saveChecks() {
    try { localStorage.setItem(checkStore, JSON.stringify({ key: ctx.fitKey(), checks })); }
    catch (e) { /* the list just won't survive a reload */ }
  }

  // Click a finding to fix it in the chat; X dismisses it.
  function paintLetterChecks() {
    const rows = (checks || []).map((finding) => {
      const drop = el("button", {
        class: "icon-btn icon-btn-round", type: "button", "aria-label": `Dismiss: ${finding}`,
      }, [icon("xmark", 16)]);
      const row = el("div", { class: "ask-head" }, [
        el("button", {
          class: "ask-q ask-q-btn", type: "button", text: finding,
          "aria-label": `Fix in the chat: ${finding}`,
          onclick: () => openChat(`Fix this: ${finding}`),
        }),
        el("div", { class: "ask-actions" }, [drop]),
      ]);
      drop.addEventListener("click", () => {
        checks = checks.filter((other) => other !== finding);
        saveChecks();
        paintLetterChecks();
      });
      return row;
    });
    checksBody.replaceChildren(
      ...rows,
      ...(checks && !checks.length && !checkingLetter
        ? [el("p", { class: "meta", text: "Nothing to fix." })] : []),
      ...(!checks && !checkingLetter
        ? [el("p", { class: "meta", text: "Not checked yet. Refresh Overall Fit to check it." })] : []),
    );
    checksStatus.textContent = checkingLetter ? "Reading the letter…" : "";
  }

  // One paid model turn, on arriving by Next or from Overall Fit; a reload costs nothing.
  async function runLetterCheck() {
    if (checkingLetter) return;
    checkingLetter = true;
    paintLetterChecks();
    try {
      const said = await ctx.working(() => ctx.askCover(
        "Check this cover letter before it is sent. Do not rewrite it and do "
        + "not propose edits. Look at four things: the opening (does the first "
        + "paragraph say why this role at this company, not a stock line); "
        + "every claim (name any the notes and the CV do not support); tone "
        + "(does it sound like my voice, any stock phrases); length (one "
        + "printed page, under about 400 words). [Company] and [Role] are "
        + "placeholders filled at build, never a problem. Reply with one "
        + "```checks block: one problem per line, a short sentence that names "
        + "the paragraph. An empty block if nothing is wrong.",
      ));
      const block = /```checks\s*([\s\S]*?)```/.exec(said);
      checks = block ? gapQuestions(block[1]) : [];
      saveChecks();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      checkingLetter = false;
      paintLetterChecks();
    }
  }

  function paintTerms() {
    if (!matched.length && !missing.length) {
      termsList.replaceChildren(el("p", { class: "meta", text: "No frozen terms yet." }));
      return;
    }
    const term = (text, kind, status) => el("span", { class: `term ${kind}`, title: status }, [
      text,
      el("span", { class: "visually-hidden", text: ` (${status})` }),
    ]);
    termsList.replaceChildren(
      ...matched.map((text) => term(text, "is-covered", "Covered")),
      ...missing.map((text) => term(text, "is-open",
        (keywordMap[text] || {}).state === "declined" ? "You said no" : "Not on the page")),
    );
  }

  // One keyword question: X records a no; the first check opens a box and the
  // second keeps what is in it.
  function askRow(term, question, onDone) {
    // Clicking the question takes it to the chat, like every other gap.
    const ask = el("button", {
      class: "ask-q ask-q-btn", type: "button", text: question,
      "aria-label": `Answer in the chat: ${question}`,
      onclick: () => openChat(question),
    });
    const no = el("button", {
      class: "icon-btn icon-btn-round", type: "button", "aria-label": `I have not done ${term}`,
    }, [icon("xmark", 16)]);
    async function send(action, extra, button) {
      button.disabled = true;
      try {
        const model = await agent().catch(() => null);
        const next = await api("/api/keywords", {
          action, keyword: term, app: ctx.app, ...extra,
          ...(model ? { provider: model.provider, model: model.model } : {}),
        });
        keywordMap = next.map || keywordMap;
        if (ctx.terms) {
          ctx.terms.map = keywordMap;
          ctx.terms.skipped = next.skipped || ctx.terms.skipped;
        }
        toast(action === "answer" ? "Added to your notes." : "Noted as a no.");
        onDone(action);
      } catch (error) {
        button.disabled = false;
        toast(error.message, "error");
      }
    }
    no.addEventListener("click", () => send("decline", { line: `I have not done ${term}.` }, no));
    return el("div", { class: "ask-row" }, [
      el("div", { class: "ask-head" }, [
        ask,
        el("div", { class: "ask-actions" }, [no]),
      ]),
    ]);
  }

  function paintAsk() {
    const open = missing.filter((term) => {
      const state = (keywordMap[term] || {}).state;
      return state !== "declined" && state !== "answered";
    });
    askBlock.replaceChildren(...open.map((term) => askRow(
      term,
      (keywordMap[term] || {}).say || `Have you done work that shows ${term}?`,
      () => { paintAsk(); paintTerms(); },
    )));
    paintGaps();
  }

  function sendZoom() {
    // Scroll lives inside the preview document (styled there). The iframe
    // element itself stays overflow:hidden so its unstyled chrome never shows.
    frame.style.overflow = "hidden";
    if (frame.contentWindow) {
      frame.contentWindow.postMessage({ type: "resume-zoom", zoom: previewZoom }, "*");
    }
  }

  function bumpZoom(delta) {
    previewZoom = Math.min(3, Math.max(1, Math.round((previewZoom + delta) * 10) / 10));
    sendZoom();
  }

  window.onmessage = (event) => {
    if (event.source !== frame.contentWindow || !event.data) return;
    if (event.data.type === "resume-pages") {
      pages = Number(event.data.count) || 1;
      room = Number(event.data.room) || 0;
      paintChecks();
      paintChanges();
      sendZoom();
    } else if (event.data.type === "resume-zoom-delta") {
      bumpZoom(Number(event.data.delta) || 0);
    }
  };

  frame.addEventListener("load", sendZoom);

  const zoomOut = el("button", {
    class: "icon-btn", type: "button", "aria-label": "Zoom out",
    onclick: () => bumpZoom(-0.2),
  }, [icon("minus", 15)]);
  const zoomIn = el("button", {
    class: "icon-btn", type: "button", "aria-label": "Zoom in",
    onclick: () => bumpZoom(0.2),
  }, [icon("plus", 15)]);

  const paperWrap = el("div", { class: "paper-wrap" }, [
    frame,
    el("div", { class: "zoom-controls" }, [zoomIn, zoomOut]),
  ]);
  paperWrap.addEventListener("wheel", (event) => {
    if (!event.ctrlKey) return;
    event.preventDefault();
    bumpZoom(event.deltaY > 0 ? -0.1 : 0.1);
  }, { passive: false });

  async function applyProposal(path, text) {
    if (!path || typeof text !== "string") throw new Error("That proposal has no field to change.");
    // Same fields as FIXED_CV_FIELD in assistant.py: change them in Settings.
    if (kind === "cv" && FIXED.test(path)) throw new Error("That's part of the CV's structure. Change it in Settings.");
    // Appending a new letter paragraph when the path is past the end.
    if (kind === "cover" && /^paragraphs\.\d+$/.test(path)) {
      const index = Number(path.slice("paragraphs.".length));
      while ((doc.paragraphs || (doc.paragraphs = [])).length <= index) doc.paragraphs.push("");
    }
    setPath(doc, path, text);
    await saveDoc();
    await reload();
  }

  async function applyRemoval(path) {
    if (!canRemove(doc, path)) throw new Error("That line is no longer there.");
    removePath(doc, path);
    await saveDoc();
    await reload();
  }

  async function applyShown(path, show) {
    if (!canShow(doc, path)) throw new Error("That role is no longer there.");
    const entry = getPath(doc, path);
    if (show) delete entry.off;
    else entry.off = true;
    await saveDoc();
    await reload();
  }

  async function copyText(text, button) {
    try {
      await navigator.clipboard.writeText(text);
      const was = button.textContent;
      button.textContent = "Copied";
      setTimeout(() => { button.textContent = was; }, 1200);
    } catch (error) {
      toast("Could not copy. Select the text instead.", "error");
    }
  }

  function saveChat() {
    const all = ctx.history.messages;
    if (all.length > MAX_TURNS) all.splice(0, all.length - MAX_TURNS);
    return api("/api/chat/history", { ...ctx.history, app: ctx.app })
      .catch((error) => toast(`Could not save this chat: ${error.message}`, "error"));
  }

  // A suggestion with a yes and a no. The answer is kept on the message, so the
  // card reads the same after a reload and the model is not re-sent applied work.
  function offerCard({ label, text, before, state, saved, keep, onResolve }) {
    const node = el("div", { class: "card chat-card" }, [
      el("p", { class: "eyebrow", text: label }),
      before ? el("p", { class: "meta", text: before }) : null,
      el("p", { class: "chat-card-text", text }),
    ].filter(Boolean));
    if (state) {
      node.append(el("div", { class: "chat-card-actions" }, [
        el("span", { class: "meta", text: state === "accepted" ? saved : "Skipped" }),
      ]));
      return node;
    }
    node.append(el("div", { class: "chat-card-actions" }, [
      el("button", {
        class: "btn btn-sm btn-primary", type: "button", text: "Keep",
        onclick: async (event) => {
          event.target.disabled = true;
          try {
            await keep();
            toast(`${saved}.`);
            onResolve("accepted");
          } catch (error) {
            event.target.disabled = false;
            toast(error.message, "error");
          }
        },
      }),
      el("button", {
        class: "btn btn-sm", type: "button", text: "Skip",
        onclick: () => onResolve("dismissed"),
      }),
    ]));
    return node;
  }

  function answerCard(text) {
    return el("div", { class: "card chat-card" }, [
      el("p", { class: "eyebrow", text: "Answer to Copy" }),
      el("p", { class: "chat-card-text", text }),
      el("div", { class: "chat-card-actions" }, [
        el("button", {
          class: "btn btn-sm btn-primary", type: "button", text: "Copy",
          onclick: (event) => copyText(text, event.target),
        }),
      ]),
    ]);
  }

  function gapRow(gap, onChange = () => {}) {
    const row = el("div", { class: "ask-head" });
    const settled = (line) => {
      row.replaceChildren(
        el("p", { class: "ask-q", text: line }),
        el("span", { class: "meta", text: "In your notes" }),
      );
      onChange(true);
    };
    const act = (button, run) => async () => {
      button.disabled = true;
      try { await run(); } catch (error) {
        button.disabled = false;
        toast(error.message, "error");
      }
    };
    if (gap.status === "past") {
      // A past CV claims it. Click to talk it through in the chat; X says it
      // isn't true, so the line leaves the library and the row closes.
      const asked = `Your past CVs say: "${gap.line}" Is that right?`;
      const wrong = el("button", {
        class: "icon-btn icon-btn-round", type: "button", "aria-label": `Not true: ${gap.line}`,
      }, [icon("xmark", 16)]);
      wrong.addEventListener("click", act(wrong, async () => {
        await api("/api/library", { action: "strike", text: gap.line });
        row.remove();
        onChange(true);
      }));
      row.append(
        el("button", {
          class: "ask-q ask-q-btn", type: "button", text: asked,
          "aria-label": `Answer in the chat: ${asked}`,
          onclick: () => openChat(asked),
        }),
        el("div", { class: "ask-actions" }, [wrong]),
      );
      return row;
    }
    const no = el("button", {
      class: "icon-btn icon-btn-round", type: "button", "aria-label": `No: ${gap.question}`,
    }, [icon("xmark", 16)]);
    no.addEventListener("click", act(no, async () => {
      const line = negative(gap.question);
      await api("/api/setup/notes", { append: line });
      settled(line);
    }));
    // Yes needs his own words, so clicking the question moves it to the chat box.
    row.append(
      el("button", {
        class: "ask-q ask-q-btn", type: "button", text: gap.question,
        "aria-label": `Answer in the chat: ${gap.question}`,
        onclick: () => openChat(gap.question),
      }),
      el("div", { class: "ask-actions" }, [no]),
    );
    return row;
  }

  function gapsCard(raw, state, onResolve) {
    const questions = gapQuestions(raw);
    const list = el("div", { class: "ask-list" });
    const node = el("div", { class: "card chat-card" }, [
      el("p", { class: "eyebrow", text: "Gaps Against This Posting" }),
      list,
    ]);
    if (state) {
      list.replaceChildren(...questions.map((q) => el("p", { class: "ask-q", text: q })));
      node.append(el("div", { class: "chat-card-actions" }, [
        el("span", { class: "meta", text: "Skipped" }),
      ]));
      return node;
    }
    list.replaceChildren(el("p", { class: "meta", text: "Checking your notes and past CVs…" }));
    node.append(el("div", { class: "chat-card-actions" }, [
      el("button", {
        class: "btn btn-sm", type: "button", text: "Copy",
        onclick: (event) => copyText(questions.join("\n"), event.target),
      }),
      el("button", {
        class: "btn btn-sm", type: "button", text: "Skip",
        onclick: () => onResolve("dismissed"),
      }),
    ]));
    // If the check cannot run, every question is still asked.
    api("/api/gaps", { action: "check", questions })
      .catch(() => questions.map((question) => ({ question, status: "open" })))
      .then((found) => {
        const asked = found.filter((gap) => gap.status !== "covered");
        const covered = found.length - asked.length;
        list.replaceChildren(
          ...asked.map(gapRow),
          ...(covered ? [el("p", {
            class: "meta",
            text: asked.length
              ? `${covered} more already answered by your notes.`
              : "Nothing to ask: your notes cover all of it.",
          })] : []),
        );
      });
    return node;
  }

  function cardFor(kind, raw, message, key) {
    const state = (message.resolved || {})[key];
    const onResolve = (result) => {
      message.resolved = { ...(message.resolved || {}), [key]: result };
      saveChat();
      node.replaceWith(cardFor(kind, raw, message, key));
    };
    let node;
    let parsed = null;
    try { parsed = JSON.parse(raw); } catch (error) { /* plain text or a broken card */ }
    if (kind === "proposal") {
      if (parsed && typeof parsed.path === "string" && parsed.remove) {
        const was = getPath(doc, parsed.path);
        const line = typeof was === "string" ? was : (was && was.text) || "";
        node = offerCard({
          label: "Remove", state, onResolve,
          text: !state && line ? line : "This line.",
          saved: `Removed from the ${label}`,
          keep: () => applyRemoval(parsed.path),
        });
        return node;
      }
      if (parsed && typeof parsed.path === "string" && typeof parsed.show === "boolean") {
        const entry = getPath(doc, parsed.path);
        const name = entry && entry.left ? String(entry.left).split(",")[0] : "This role";
        node = offerCard({
          label: parsed.show ? "Put On the CV" : "Take Off the CV", text: name, state, onResolve,
          saved: parsed.show ? "Put on the CV" : "Taken off the CV",
          keep: () => applyShown(parsed.path, parsed.show),
        });
        return node;
      }
      if (!parsed || typeof parsed.path !== "string" || typeof parsed.text !== "string") {
        return el("p", { class: "chat-card-text", text: raw });
      }
      const path = parsed.path;
      const text = parsed.text;
      const was = getPath(doc, path);
      node = offerCard({
        label: path || "Proposal", text, state, onResolve,
        before: !state && was != null && was !== text ? `Was: ${String(was).slice(0, 180)}` : "",
        saved: `Applied to the ${label}`,
        keep: () => applyProposal(path, text),
      });
    } else if (kind === "notes") {
      node = offerCard({
        label: "For Your Notes", text: raw, state, onResolve,
        saved: "Added to your notes",
        keep: () => api("/api/setup/notes", { append: raw, source: "assistant" }),
      });
    } else if (kind === "voice") {
      node = offerCard({
        label: "For Your Tone", text: raw, state, onResolve,
        saved: "Added to your tone",
        keep: () => api("/api/setup/voice", { append: raw }),
      });
    } else if (kind === "skill" && parsed && parsed.label && parsed.tool) {
      node = offerCard({
        label: "For Your Skills", text: `${parsed.label}: ${parsed.tool}`, state, onResolve,
        saved: "Added to your skills",
        keep: () => api("/api/setup/notes", { skill: { label: parsed.label, tool: parsed.tool } }),
      });
    } else if (kind === "ask" && parsed && parsed.keyword && parsed.question) {
      node = el("div", { class: "card chat-card" }, [
        el("p", { class: "eyebrow", text: parsed.keyword }),
        state
          ? el("p", { class: "chat-card-text", text: parsed.question })
          : askRow(parsed.keyword, parsed.question, (action) => {
            onResolve(action === "answer" ? "accepted" : "dismissed");
            paintTerms();
          }),
        state ? el("span", {
          class: "meta", text: state === "accepted" ? "Added to your notes" : "Noted as a no",
        }) : null,
      ].filter(Boolean));
    } else if (kind === "gaps") {
      node = gapsCard(raw, state, onResolve);
    } else {
      node = el("p", { class: "chat-card-text", text: raw });
    }
    return node;
  }

  function reply(message) {
    const node = el("div", { class: "chat-reply" });
    const content = message.content;
    let last = 0;
    let n = 0; // same numbering the server uses to drop resolved proposals
    const text = (part) => {
      if (!part.trim()) return;
      const box = el("div", { class: "md" });
      renderMarkdown(part, box);
      node.append(box);
    };
    for (const match of content.matchAll(BLOCK)) {
      text(content.slice(last, match.index));
      last = match.index + match[0].length;
      const raw = match[2].trim();
      if (match[1] === "answer") node.append(answerCard(raw));
      else node.append(cardFor(match[1], raw, message, String(n++)));
    }
    text(content.slice(last));
    return node;
  }

  function mountChat() {
    const empty = el("p", { class: "chat-empty", text: "Make a change" });
    const log = el("div", { class: "chat-log", "aria-live": "polite" }, [empty]);
    const box = el("textarea", {
      class: "chat-input", rows: "3", id: "review-chat-input",
      placeholder: kind === "cover"
        ? "Ask for a change - a paragraph, the greeting…"
        : "Ask for a change - a bullet, the summary, a skill…",
    });
    const mic = el("button", {
      class: "icon-btn", type: "button", "aria-label": "Dictate", title: "Dictate",
    }, [icon("mic", 16)]);
    const send = el("button", {
      class: "icon-btn chat-send", type: "button", "aria-label": "Send",
    }, [icon("send")]);
    const status = el("p", { class: "meta chat-status" });
    if (!Array.isArray(ctx.history.messages)) ctx.history.messages = [];
    // One file holds both documents and the old drafting rounds nobody typed.
    const thread = () => ctx.history.messages.filter(
      (m) => !m.pipeline && (m.document || "cv") === kind);
    let busy = false;
    let connected = null;
    const dictate = attachDictate({ button: mic, input: box });

    function clearEmpty() {
      if (empty.isConnected) empty.remove();
    }

    async function ask() {
      const question = box.value.trim();
      if (!question || busy) return;
      if (!connected) { toast("Connect a model first.", "error"); return; }
      dictate.cancel();
      busy = true;
      send.disabled = true;
      mic.disabled = true;
      box.value = "";
      const asked = { role: "user", content: question, document: kind };
      ctx.history.messages.push(asked);
      clearEmpty();
      log.append(el("p", { class: "chat-mine", text: question }));
      const live = el("p", { class: "chat-live meta", text: "Thinking…" });
      log.append(live);
      log.scrollTop = log.scrollHeight;
      let content = "";
      let finish = () => {};
      // The reply shows itself in the chat; no "done" message for it.
      ctx.working(() => new Promise((resolve) => { finish = resolve; }), { quiet: true });
      try {
        const response = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            document: kind, doc, app: ctx.app,
            provider: connected.provider, model: connected.model,
            job_description: ctx.history.job_description,
            wanted: ctx.terms && ctx.terms.current ? ctx.terms.keywords : [],
            messages: thread().map(({ role, content: said, resolved }) => ({ role, content: said, resolved })),
          }),
        });
        if (!response.ok) {
          const data = await response.json().catch(() => ({}));
          throw new Error(data.error || response.statusText);
        }
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          content += decoder.decode(value, { stream: true });
          live.textContent = content.split(STREAM_ERROR)[0] || "Thinking…";
          log.scrollTop = log.scrollHeight;
        }
        const [said, failed] = content.split(STREAM_ERROR);
        if (!said.trim()) throw new Error(failed || "No reply came back. Try again.");
        const answer = { role: "assistant", content: said, document: kind };
        ctx.history.messages.push(answer);
        live.replaceWith(reply(answer));
        saveChat();
        if (failed) toast(failed, "error");
      } catch (error) {
        live.remove();
        ctx.history.messages.splice(ctx.history.messages.indexOf(asked), 1);
        toast(error.message, "error");
      } finally {
        finish();
        busy = false;
        send.disabled = false;
        mic.disabled = false;
        log.scrollTop = log.scrollHeight;
      }
    }

    if (thread().length) {
      empty.remove();
      for (const message of thread()) {
        log.append(message.role === "user"
          ? el("p", { class: "chat-mine", text: message.content })
          : reply(message));
      }
    }

    send.addEventListener("click", ask);
    box.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); ask(); }
    });

    agent().then((found) => {
      connected = found;
      status.textContent = found ? `${found.label} · ${found.model}` : "No model connected";
      box.disabled = !found;
    }).catch(() => { status.textContent = "No model connected"; box.disabled = true; });

    return el("section", { class: "chat review-chat", "aria-label": "Ask for a change" }, [
      log,
      el("div", { class: "chat-box" }, [
        el("label", { class: "visually-hidden", for: "review-chat-input", text: "Ask for a change" }),
        box,
        el("div", { class: "chat-row" }, [
          status,
          el("div", { class: "chat-tools" }, [mic, send]),
        ]),
      ]),
    ]);
  }

  // The page opens over the review, the rest blurred behind it. It stays in
  // the page while closed (only hidden) so the preview can still measure
  // its pages for the Changes card.
  const viewClose = el("button", {
    class: "modal-close cv-modal-close", type: "button", "aria-label": `Close the ${label}`,
  }, [icon("xmark", 16)]);
  const viewer = el("div", {
    class: "cv-modal", role: "dialog", "aria-modal": "true",
    "aria-label": kind === "cover" ? "Your cover letter" : "Your CV", "aria-hidden": "true",
  }, [
    el("div", { class: "cv-modal-card" }, [paperWrap]),
  ]);
  // Inside the canvas, top right, level with the top of the sheet.
  paperWrap.append(viewClose);
  function onViewerKey(event) { if (event.key === "Escape") closeViewer(); }
  function openViewer() {
    viewer.classList.add("is-shown");
    viewer.setAttribute("aria-hidden", "false");
    document.addEventListener("keydown", onViewerKey);
    viewClose.focus();
  }
  function closeViewer() {
    viewer.classList.remove("is-shown");
    viewer.setAttribute("aria-hidden", "true");
    document.removeEventListener("keydown", onViewerKey);
    if (viewLink.isConnected) viewLink.focus();
  }
  viewLink.addEventListener("click", openViewer);
  viewClose.addEventListener("click", closeViewer);
  viewer.addEventListener("click", (event) => { if (event.target === viewer) closeViewer(); });

  // An accordion: opening one card closes the other (same details name).
  function fold(title, body, open = false, status = null) {
    return el("details", { class: "card review-card", name: "review-cards", open }, [
      el("summary", { class: "review-card-head" }, [
        el("span", { class: "eyebrow", text: title }),
        status,
        icon("down", 16),
      ]),
      body,
    ]);
  }
  const languageCard = fold("Language",
    el("div", { class: "review-card-body" }, [coverage, termsList]), true);
  const gapsFold = kind === "cover"
    ? fold("Checks", checksBody, false, checksStatus)
    : fold("Gaps", gapsBody, false, gapsStatus);

  host.replaceChildren(el("div", { class: "measure-list review-page" }, [
    el("header", { class: "page-head" }, [
      el("h1", { class: "title", text: "Review" }),
      el("p", {
        class: "lead",
        text: kind === "cover" ? "Check the letter, then save." : "Check it, then save.",
      }),
      fitLine,
    ]),
    el("div", { class: "review-cards" }, [languageCard, gapsFold]),
    mountChat(),
    viewer,
  ]));
  ctx.setFitRefresh(kind === "cover" ? runLetterCheck : runGapCheck);
  // A saved list stays while the posting is the same; a new posting drops it.
  const postingKey = (key) => String(key).split("\n").slice(0, 3).join("\n");
  const savedGaps = kind === "cv" ? loadGaps() : null;
  if (savedGaps && postingKey(savedGaps.key) === postingKey(ctx.fitKey())) gaps = savedGaps.gaps;
  paintChecks();
  paintTerms();
  paintAsk();
  paintGaps();
  paintChanges();
  const savedChecks = kind === "cover" ? loadChecks() : null;
  if (savedChecks && savedChecks.key === ctx.fitKey()) checks = savedChecks.checks;
  if (kind === "cover") {
    gapsFold.hidden = false;
    paintLetterChecks();
    if (ctx.consumeAutoFill("review") && !checks) runLetterCheck();
  }
  // Reaching Review by Next means the CV is finished: check it, unless this
  // exact CV has been checked already.
  if (kind === "cv" && ctx.consumeAutoFill("review")
      && !(savedGaps && savedGaps.key === ctx.fitKey())) {
    runGapCheck();
  }

  async function reload() {
    pages = null;
    room = 0;
    issues = [];
    missing = [];
    paintChecks();
    coverage.replaceChildren(el("p", { class: "meta", text: "Counting…" }));
    try {
      const preview = await api("/api/preview", payload);
      issues = (preview.issues || []).filter((i) => i.level !== "info");
      frame.srcdoc = preview.html.replace(
        "<head>",
        `<head><base href="${location.origin}/">${PREVIEW_CHROME}`,
      );
    } catch (error) {
      toast(`Could not draw the page: ${error.message}`, "error");
    }
    paintChecks();

    try {
      const scored = await api("/api/match", {
        document: kind, doc, job_description: ctx.history.job_description, app: ctx.app,
      });
      if (scored.needs_list || scored.score === null) {
        matched = [];
        missing = [];
        coverage.replaceChildren(el("p", {
          class: "meta",
          text: kind === "cover"
            ? "Read the posting's terms on Resume → Job Description first."
            : "Read the posting's terms on step 1 first.",
        }));
      } else {
        matched = scored.matched || [];
        missing = scored.missing || [];
        const total = matched.length + missing.length;
        const score = Math.round(Number(scored.score));
        const band = scoreBand(score);
        coverage.replaceChildren(
          el("div", { class: "score-row" }, [
            el("span", { class: "meta tnum", text: `${matched.length} of ${total} Terms` }),
            el("span", { class: `meta tnum ${band}`, text: String(score) }),
          ]),
          el("div", { class: `meter ${band}`, role: "img", "aria-label": `Language ${score} of 100` }, [
            el("span", { style: `width: ${Math.max(0, Math.min(100, score))}%` }),
          ]),
        );
      }
      try {
        const state = await api("/api/keywords", { action: "get", app: ctx.app });
        keywordMap = state.map || {};
        if (ctx.terms) {
          ctx.terms.map = keywordMap;
          ctx.terms.keywords = state.keywords || ctx.terms.keywords;
          ctx.terms.current = state.current;
        }
      } catch (e) { /* map optional */ }
      paintChecks();
      paintTerms();
      paintAsk();
    } catch (error) {
      coverage.replaceChildren(el("p", { class: "meta", text: `Could not count: ${error.message}` }));
    }
  }

  await reload();
}
