// Step 6, Review: the page as it will be sent, what to check, and a change
// chat. The page is /api/preview; language is /api/match; Save on the strip
// rebuilds out/. Make a Change opens a wider rail that talks to /api/chat;
// accepted ```proposal``` blocks write into the CV and refresh the page.

import { agent } from "./agent.js";
import { attachDictate } from "./dictate.js";
import { api, el, icon, toast } from "./dom.js";
import { renderMarkdown } from "./markdown.js";

const STREAM_ERROR = String.fromCharCode(0);
const BLOCK = /```(proposal|answer|notes|voice|gaps|ask|skill)\s*([\s\S]*?)```/g;

// Scrollbar + wheel zoom inside the preview (wheel events hit the iframe, not the parent).
// Track transparent, no arrow buttons — only a 6px thumb.
const PREVIEW_CHROME = `<style>
/* No scrollbar-width here — on Chromium/Linux it forces GTK arrows. */
html::-webkit-scrollbar,body::-webkit-scrollbar,
*::-webkit-scrollbar{
  width:16px;height:16px;background:transparent;
}
html::-webkit-scrollbar-button,body::-webkit-scrollbar-button,
*::-webkit-scrollbar-button{
  display:none!important;width:0!important;height:0!important;
  background:transparent!important;border:0!important;
}
html::-webkit-scrollbar-track,body::-webkit-scrollbar-track,
*::-webkit-scrollbar-track,*::-webkit-scrollbar-track-piece{
  background:transparent!important;border:none!important;box-shadow:none!important;
  margin-block:8px;
}
html::-webkit-scrollbar-corner,body::-webkit-scrollbar-corner,
*::-webkit-scrollbar-corner{
  background:transparent!important;border:none!important;box-shadow:none!important;
}
html::-webkit-scrollbar-thumb,body::-webkit-scrollbar-thumb,
*::-webkit-scrollbar-thumb{
  background:transparent!important;background-clip:padding-box!important;
  border:5px solid transparent!important;border-radius:8px;
}
html:is(:hover,:focus,:focus-within)::-webkit-scrollbar-thumb,
body:is(:hover,:focus,:focus-within)::-webkit-scrollbar-thumb,
*:is(:hover,:focus,:focus-within)::-webkit-scrollbar-thumb{
  background:#9B9BA4!important;background-clip:padding-box!important;
}
@supports not selector(::-webkit-scrollbar){
  html,body,*{scrollbar-width:thin;scrollbar-color:#9B9BA4 transparent}
}
</style><script>
window.addEventListener('wheel', function (event) {
  event.preventDefault();
  if (window.parent === window) return;
  window.parent.postMessage({
    type: 'resume-zoom-delta',
    delta: event.deltaY > 0 ? -0.1 : 0.1,
  }, '*');
}, { passive: false });
</script>`;

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
  const checks = el("div", { class: "checks" }, [el("p", { class: "meta", text: "Measuring the page…" })]);
  const coverage = el("div", { class: "coverage" }, [el("p", { class: "meta", text: "Counting…" })]);
  const side = el("div", { class: "review-side" });
  let pages = null;
  let room = 0;
  let issues = [];
  let missing = [];
  let previewZoom = 1;
  let chatting = false;

  function check(done, text) {
    return el("div", { class: "check" }, [el("span", { class: done ? "dot dot-backed" : "dot dot-open" }), el("span", { text })]);
  }

  function paintChecks() {
    const rows = [];
    if (pages !== null) {
      rows.push(pages > 1
        ? check(false, `runs to ${pages} pages`)
        : check(true, room > 0
          ? `one page, ${room} ${room === 1 ? "line" : "lines"} spare`
          : "one page, full"));
    }
    for (const issue of issues) rows.push(check(false, String(issue.message || "").toLowerCase()));
    for (const term of missing.slice(0, 5)) rows.push(check(false, `${term} not covered`));
    if (pages !== null && !issues.length && !missing.length && pages <= 1) {
      rows.push(check(true, "nothing left to fix"));
    }
    checks.replaceChildren(...(rows.length ? rows : [el("p", { class: "meta", text: "measuring the page…" })]));
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
    event.preventDefault();
    bumpZoom(event.deltaY > 0 ? -0.1 : 0.1);
  }, { passive: false });

  async function applyProposal(path, text) {
    if (!path || typeof text !== "string") throw new Error("That proposal has no field to change.");
    // Appending a new letter paragraph when the path is past the end.
    if (kind === "cover" && /^paragraphs\.\d+$/.test(path)) {
      const index = Number(path.slice("paragraphs.".length));
      while ((doc.paragraphs || (doc.paragraphs = [])).length <= index) doc.paragraphs.push("");
    }
    setPath(doc, path, text);
    await saveDoc();
    await reload();
  }

  function proposalCard(raw) {
    let parsed;
    try { parsed = JSON.parse(raw); }
    catch (e) { return el("p", { class: "meta", text: "Could not read that proposal." }); }
    const path = String(parsed.path || "");
    const text = String(parsed.text || "");
    const before = getPath(doc, path);
    const node = el("div", { class: "card chat-card" }, [
      el("p", { class: "eyebrow", text: path || "Proposal" }),
      before != null && before !== text
        ? el("p", { class: "meta", text: `Was: ${String(before).slice(0, 180)}` })
        : null,
      el("p", { class: "chat-card-text", text }),
    ].filter(Boolean));
    const actions = el("div", { class: "chat-card-actions" });
    const done = (word) => actions.replaceChildren(el("span", { class: "meta", text: word }));
    actions.append(
      el("button", { class: "btn btn-sm btn-primary", type: "button", text: "Keep", onclick: async (event) => {
        event.target.disabled = true;
        try {
          await applyProposal(path, text);
          done("Applied");
          toast(`Applied to the ${label}.`);
        } catch (error) {
          event.target.disabled = false;
          toast(error.message, "error");
        }
      } }),
      el("button", { class: "btn btn-sm", type: "button", text: "Skip", onclick: () => done("Skipped") }),
    );
    node.append(actions);
    return node;
  }

  function reply(content) {
    const node = el("div", { class: "chat-reply" });
    let last = 0;
    const text = (part) => {
      if (!part.trim()) return;
      const box = el("div", { class: "md" });
      renderMarkdown(part, box);
      node.append(box);
    };
    for (const match of content.matchAll(BLOCK)) {
      text(content.slice(last, match.index));
      last = match.index + match[0].length;
      if (match[1] === "proposal") node.append(proposalCard(match[2]));
      else text(match[0]);
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
    const close = el("button", {
      class: "icon-btn review-chat-close", type: "button", "aria-label": "Close chat",
      onclick: () => { dictate.cancel(); chatting = false; paintSide(); },
    }, [icon("xmark", 16)]);
    const messages = [];
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
      messages.push({ role: "user", content: question });
      clearEmpty();
      log.append(el("p", { class: "chat-mine", text: question }));
      const live = el("p", { class: "chat-live meta", text: "Thinking…" });
      log.append(live);
      log.scrollTop = log.scrollHeight;
      let content = "";
      try {
        const response = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            document: kind, doc, app: ctx.app,
            provider: connected.provider, model: connected.model,
            job_description: ctx.history.job_description,
            wanted: ctx.terms && ctx.terms.current ? ctx.terms.keywords : [],
            messages,
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
        content = said;
        messages.push({ role: "assistant", content });
        live.replaceWith(reply(content));
        if (failed) toast(failed, "error");
      } catch (error) {
        live.remove();
        messages.pop();
        toast(error.message, "error");
      } finally {
        busy = false;
        send.disabled = false;
        mic.disabled = false;
        log.scrollTop = log.scrollHeight;
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

    return el("div", { class: "review-chat" }, [
      close,
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

  function paintSide() {
    side.classList.toggle("is-chat", chatting);
    if (chatting) {
      side.replaceChildren(mountChat());
      return;
    }
    const blocks = [
      el("section", { class: "rail-block" }, [el("p", { class: "eyebrow", text: "Language" }), coverage]),
      el("section", { class: "rail-block" }, [el("p", { class: "eyebrow", text: "Before you send" }), checks]),
      el("section", { class: "rail-block" }, [
        el("p", { class: "eyebrow", text: "Changes" }),
        el("div", { class: "rows" }, [
          el("div", {}, [
            el("span", {
              text: kind === "cover"
                ? "Ask the model to adjust a paragraph"
                : "Ask the model to adjust a line",
            }),
            el("a", {
              href: "#change", text: "Change",
              onclick: (event) => { event.preventDefault(); chatting = true; paintSide(); },
            }),
          ]),
        ]),
      ]),
    ];
    // Cover letter is its own mode now — no Create link on CV Review.
    side.replaceChildren(...blocks);
  }

  host.replaceChildren(el("div", { class: "review" }, [
    el("div", { class: "review-main" }, [
      el("header", { class: "page-head" }, [
        el("h1", { class: "title", text: "Review" }),
        el("p", {
          class: "lead",
          text: kind === "cover" ? "Check the letter, then save." : "Check it, then save.",
        }),
      ]),
      paperWrap,
    ]),
    side,
  ]));
  paintSide();

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
        coverage.replaceChildren(el("p", {
          class: "meta",
          text: kind === "cover"
            ? "Read the posting's terms on Resume → Job Description first."
            : "Read the posting's terms on step 1 first.",
        }));
      } else {
        missing = scored.missing || [];
        const total = (scored.matched || []).length + missing.length;
        const score = Math.round(Number(scored.score));
        const band = scoreBand(score);
        coverage.replaceChildren(
          el("div", { class: "score-row" }, [
            el("span", { class: "meta tnum", text: `${(scored.matched || []).length} of ${total} Terms` }),
            el("span", { class: `meta tnum ${band}`, text: String(score) }),
          ]),
          el("div", { class: `meter ${band}`, role: "img", "aria-label": `Language ${score} of 100` }, [
            el("span", { style: `width: ${Math.max(0, Math.min(100, score))}%` }),
          ]),
        );
        paintChecks();
      }
    } catch (error) {
      coverage.replaceChildren(el("p", { class: "meta", text: `Could not count: ${error.message}` }));
    }
  }

  await reload();
}
