// Step 5, Additional: each Additional Information line, edited in place or
// swapped for another of his. The others are the lines the round would show
// the model (POST /api/library slot) or new ones from one additional turn.
// Refresh in the step strip re-runs that turn against the posting.

import { api, el, icon, stepAction, toast } from "./dom.js";

export async function drawAdditional(ctx) {
  const host = ctx.host;
  const at = ctx.sectionIndex("ADDITIONAL");
  const foot = (extra) => el("div", { class: "step-foot" }, [
    stepAction("a", { href: "#bullets", "aria-label": "Back" }, "left"), ...extra,
  ]);
  if (at < 0) {
    host.replaceChildren(el("div", { class: "measure-list" }, [
      el("h1", { class: "title", text: "Additional" }),
      el("p", { class: "lead", text: "This CV has no additional section." }),
      foot([stepAction("a", { href: "#review", "aria-label": "Next" }, "right")]),
    ]));
    return;
  }

  const items = ctx.documents.cv.sections[at].items || [];
  const lines = items.map((item) => String(item.text || ""));
  let others = [];
  let loading = true;
  let open = null;
  let dirty = false;
  const body = el("div", { class: "bullets" });

  function swap(index, other) {
    const was = lines[index];
    lines[index] = other.text;
    others = others.filter((o) => o !== other);
    if (was.trim()) others.unshift({ text: was, origin: "Swapped out" });
    dirty = true;
    paint();
  }

  function row(item, index) {
    const text = lines[index];
    const isOpen = open === index;
    const head = el("div", { class: "bullet-row" }, [
      el("span", { class: text.trim() ? "marker" : "marker is-empty", "aria-hidden": "true" }),
      el("div", { class: "bullet-body" }, [
        el("p", { class: "eyebrow", text: item.label || "Line" }),
        ...(isOpen
          ? [(() => {
            const box = el("textarea", { class: "input", rows: String(Math.min(8, Math.max(2, Math.ceil(text.length / 80) + 1))),
              "aria-label": item.label || "This line" });
            box.value = text;
            box.addEventListener("input", () => { lines[index] = box.value; dirty = true; });
            return box;
          })()]
          : [el("p", { class: text.trim() ? "bullet-text" : "bullet-text is-empty",
            text: text.trim() || "Empty. Open it to pick a line or write one." })]),
      ]),
      el("button", {
        class: "icon-btn", type: "button", "aria-expanded": String(isOpen),
        "aria-label": isOpen ? "Close this line" : "Edit or swap this line",
        onclick: () => { open = isOpen ? null : index; paint(); },
      }, [icon(isOpen ? "up" : "down")]),
    ]);
    if (!isOpen) return el("div", { class: "bullet" }, [head]);
    const choices = others.filter((o) => !lines.includes(o.text));
    return el("div", { class: "bullet is-expanded" }, [head, el("div", { class: "alts" }, [
      el("p", { class: "eyebrow", text: "Others you have written" }),
      ...(loading
        ? [el("p", { class: "meta", text: "Loading your past lines…" })]
        : choices.length
          ? choices.map((other) => el("div", { class: "alt" }, [
            el("div", { class: "alt-body" }, [
              el("p", { class: "alt-text", text: other.text }),
              el("p", { class: "meta", text: other.origin }),
            ]),
            el("button", { class: "icon-btn", type: "button", "aria-label": "Use this line",
              onclick: () => swap(index, other) }, [icon("check")]),
          ]))
          : [el("p", { class: "meta", text: "No other lines yet." })]),
    ])]);
  }

  function paint() {
    body.replaceChildren(...items.map(row));
  }

  function takeProposals(found, origin) {
    let placed = 0;
    for (const p of found) {
      const text = String(p.text || "").trim();
      const match = /^sections\.\d+\.items\.(\d+)\.text$/.exec(p.path || "");
      if (!text || !match) continue;
      const index = Number(match[1]);
      if (Number.isInteger(index) && index >= 0 && index < lines.length && !lines[index].trim()) {
        lines[index] = text;
        placed += 1;
        dirty = true;
        continue;
      }
      if (!lines.includes(text) && !others.some((o) => o.text === text)) {
        others.unshift({ text, origin });
      }
    }
    return placed;
  }

  async function propose() {
    const found = await ctx.askSection("additional");
    const placed = takeProposals(found, "Recommended for this posting");
    if (!found.length) throw new Error("No new lines came back. Try again.");
    if (placed) toast(`${placed} ${placed === 1 ? "line" : "lines"} recommended.`);
    else toast(`${found.length} other ${found.length === 1 ? "line" : "lines"} for this posting.`);
    paint();
  }

  const nextButton = stepAction("button", { type: "button", "aria-label": "Next" }, "right");
  nextButton.addEventListener("click", async () => {
    nextButton.disabled = true;
    try {
      if (dirty) {
        items.forEach((item, index) => { item.text = lines[index].trim(); });
        await ctx.saveCv();
        dirty = false;
      }
      ctx.next("review");
    } catch (error) {
      toast(error.message, "error");
      nextButton.disabled = false;
    }
  });
  window.onbeforeunload = (event) => { if (dirty) event.preventDefault(); };

  host.replaceChildren(el("div", { class: "measure-list" }, [
    el("header", { class: "page-head" }, [
      el("h1", { class: "title", text: "Additional" }),
      el("p", { class: "lead", text: "Recommended for this posting. Open a line to change it, or swap in another." }),
    ]),
    body,
    foot([
      nextButton,
      ctx.sectionRefresh(),
      stepAction("a", { href: "#review", "aria-label": "Skip" }, "skip"),
    ]),
  ]));
  paint();
  ctx.setRefresh(propose);

  try {
    const slot = await api("/api/library", {
      action: "slot", phase: "additional", app: ctx.app, job_description: ctx.history.job_description,
    });
    for (const text of slot.lines || []) {
      const line = text.trim();
      if (line && !lines.includes(line)) others.push({ text: line, origin: "From a past CV" });
    }
  } catch (error) {
    toast(`Could not load past lines: ${error.message}`, "error");
  }
  loading = false;
  paint();
  // Empty lines: the same additional turn the chat ran after a paste.
  if (!lines.some((line) => line.trim())) {
    try { await propose(); }
    catch (error) { toast(error.message, "error"); }
  }
}
