// A small chat under a document page. It talks to /api/chat exactly as the
// workbench chat does, with the open application's CV and the model this
// browser picked, and turns the reply's fenced cards (```voice, ```notes)
// into Keep / Skip choices. Replies are rendered as nodes, never as HTML.

import { api, el, icon, toast } from "./dom.js";
import { renderMarkdown } from "./markdown.js";

const PREFS = "resume-maker.chat";
const STREAM_ERROR = String.fromCharCode(0);
const BLOCK = /```(proposal|answer|notes|voice|gaps|ask|skill)\s*([\s\S]*?)```/g;

async function agent() {
  let prefs = {};
  try { prefs = JSON.parse(localStorage.getItem(PREFS)) || {}; } catch (e) { /* none */ }
  const providers = await api("/api/llm/providers");
  const provider = providers.find((p) => p.id === prefs.provider && p.available);
  const model = provider && (prefs.models || {})[provider.id];
  return provider && model ? { provider: provider.id, model, label: provider.label } : null;
}

function withApp(path) {
  const app = document.body.dataset.app || "";
  return app ? `${path}?app=${encodeURIComponent(app)}` : path;
}

export function mountChat({ host, placeholder, cards, onSaved }) {
  const log = el("div", { class: "chat-log", "aria-live": "polite" });
  const box = el("textarea", { class: "chat-input", rows: "2", placeholder, id: "chat-input" });
  const send = el("button", { class: "icon-btn chat-send", type: "button", "aria-label": "Send" }, [icon("send")]);
  const status = el("p", { class: "meta chat-status" });
  host.append(
    log,
    el("div", { class: "chat-box" }, [
      el("label", { class: "visually-hidden", for: "chat-input", text: placeholder }),
      box,
      el("div", { class: "chat-row" }, [status, send]),
    ]),
  );

  const messages = [];
  let connected = null;
  let cv = null;
  let busy = false;

  function card(kind, text) {
    const spec = cards[kind];
    const node = el("div", { class: "card chat-card" }, [
      el("p", { class: "eyebrow", text: spec.label }),
      el("p", { class: "chat-card-text", text: text.trim() }),
    ]);
    const actions = el("div", { class: "chat-card-actions" });
    const done = (word) => actions.replaceChildren(el("span", { class: "meta", text: word }));
    actions.append(
      el("button", { class: "btn btn-sm btn-primary", type: "button", text: "Keep", onclick: async (event) => {
        event.target.disabled = true;
        try {
          await spec.save(text.trim());
          done("Kept");
          if (onSaved) onSaved();
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
      const host = el("div", { class: "md" });
      renderMarkdown(part, host);
      node.append(host);
    };
    for (const match of content.matchAll(BLOCK)) {
      text(content.slice(last, match.index));
      last = match.index + match[0].length;
      if (cards[match[1]]) node.append(card(match[1], match[2]));
    }
    text(content.slice(last));
    return node;
  }

  async function ask() {
    const question = box.value.trim();
    if (!question || busy) return;
    if (!connected) { toast("Connect a model first.", "error"); return; }
    busy = true;
    send.disabled = true;
    box.value = "";
    messages.push({ role: "user", content: question });
    log.append(el("p", { class: "chat-mine", text: question }));
    const live = el("p", { class: "chat-live meta", text: "Thinking…" });
    log.append(live);
    let content = "";
    try {
      if (!cv) cv = (await api(withApp("/api/document"))).cv || {};
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ document: "cv", doc: cv, messages, provider: connected.provider, model: connected.model }),
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
}
