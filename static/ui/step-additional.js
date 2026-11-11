// Step 5, Additional: one section turn, auto-accepted like the old chat yes.
//
// Same tailor phase the Pico chat ran. Skeleton + strip progress while it
// writes; then edit / swap. Refresh re-runs the turn.

import { api, el, icon, stepAction, stepSkeleton, toast } from "./dom.js";

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
  let open = null;
  let dirty = false;
  const body = el("div", { class: "bullets" });
  const lead = el("p", {
    class: "lead",
    text: "Writing additional lines for this posting from your notes and past CVs.",
  });
  const nextButton = stepAction("button", { type: "button", "aria-label": "Next" }, "right");
  nextButton.disabled = true;
  function markDirty() {
    dirty = true;
    if (ctx.touchDraft) ctx.touchDraft();
  }

  function swap(index, other) {
    const was = lines[index];
    lines[index] = other.text;
    others = others.filter((o) => o !== other);
    if (was.trim()) others.unshift({ text: was, origin: "Swapped out" });
    markDirty();
    paint();
  }

  function acceptProposals(found) {
    let placed = 0;
    for (const p of found) {
      const text = String(p.text || "").trim();
      const match = /^sections\.\d+\.items\.(\d+)\.text$/.exec(p.path || "");
      if (!text || !match) continue;
      const index = Number(match[1]);
      if (Number.isInteger(index) && index >= 0 && index < lines.length) {
        const was = String(lines[index] || "").trim();
        if (was && was !== text && !others.some((o) => o.text === was) && !lines.includes(was)) {
          others.unshift({ text: was, origin: "Previous" });
        }
        lines[index] = text;
        placed += 1;
        markDirty();
        continue;
      }
      if (!lines.includes(text) && !others.some((o) => o.text === text)) {
        others.unshift({ text, origin: "Recommended" });
      }
    }
    return placed;
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
            const box = el("textarea", {
              class: "input",
              rows: String(Math.min(8, Math.max(2, Math.ceil(text.length / 80) + 1))),
              "aria-label": item.label || "This line",
            });
            box.value = text;
            box.addEventListener("input", () => { lines[index] = box.value; markDirty(); });
            return box;
          })()]
          : [el("p", {
            class: text.trim() ? "bullet-text" : "bullet-text is-empty",
            text: text.trim() || "Empty. Open it to pick a line or write one.",
          })]),
      ]),
      el("button", {
        class: "icon-btn", type: "button", "aria-expanded": String(isOpen),
        "aria-label": isOpen ? "Close this line" : "Edit or swap this line",
        onclick: () => { open = isOpen ? null : index; paint(); },
      }, [icon(isOpen ? "up" : "down", 18)]),
    ]);
    if (!isOpen) return el("div", { class: "bullet" }, [head]);
    const choices = others.filter((o) => !lines.includes(o.text));
    return el("div", { class: "bullet is-expanded" }, [head, el("div", { class: "alts" }, [
      el("p", { class: "eyebrow", text: "Others you have written" }),
      ...(choices.length
        ? choices.map((other) => el("div", { class: "alt" }, [
          el("div", { class: "alt-body" }, [
            el("p", { class: "alt-text", text: other.text }),
            el("p", { class: "meta", text: other.origin }),
          ]),
          el("button", {
            class: "icon-btn", type: "button", "aria-label": "Use this line",
            onclick: () => swap(index, other),
          }, [icon("check", 18)]),
        ]))
        : [el("p", { class: "meta", text: "No other lines yet." })]),
    ])]);
  }

  function paint() {
    body.replaceChildren(...items.map(row));
  }

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
      lead,
    ]),
    body,
    foot([
      nextButton,
      ctx.sectionRefresh(),
      stepAction("a", { href: "#review", "aria-label": "Skip" }, "skip"),
    ]),
  ]));
  body.replaceChildren(stepSkeleton(3));

  async function fill() {
    lead.textContent = "Writing additional lines for this posting from your notes and past CVs.";
    nextButton.disabled = true;
    body.replaceChildren(stepSkeleton(3));
    if (ctx.setStepProgress) ctx.setStepProgress(0, 1);
    try {
      try {
        const slot = await api("/api/library", {
          action: "slot", phase: "additional", app: ctx.app,
          job_description: ctx.history.job_description,
        });
        for (const text of slot.lines || []) {
          const line = text.trim();
          if (line && !lines.includes(line) && !others.some((o) => o.text === line)) {
            others.push({ text: line, origin: "From a past CV" });
          }
        }
      } catch (error) {
        toast(`Could not load past lines: ${error.message}`, "error");
      }
      const found = await ctx.askSection("additional");
      const placed = acceptProposals(found);
      if (!found.length) throw new Error("No new lines came back. Try again.");
      if (placed) toast(`${placed} ${placed === 1 ? "line" : "lines"} placed.`);
      lead.textContent = "Recommended for this posting. Open a line to change it, or swap in another.";
    } catch (error) {
      toast(error.message, "error");
      lead.textContent = "Open a line to change it, or refresh to ask the model again.";
    } finally {
      if (ctx.setStepProgress) ctx.setStepProgress(1, 1);
      paint();
      nextButton.disabled = false;
    }
  }

  ctx.setRefresh(fill);
  const empty = lines.every((line) => !line.trim());
  if (ctx.consumeAutoFill && ctx.consumeAutoFill("additional") && empty) await fill();
  else {
    lead.textContent = lines.some((line) => line.trim())
      ? "Recommended for this posting. Open a line to change it, or swap in another."
      : "Nothing written yet. Refresh to draft from your notes and past CVs.";
    try {
      const slot = await api("/api/library", {
        action: "slot", phase: "additional", app: ctx.app,
        job_description: ctx.history.job_description,
      });
      for (const text of slot.lines || []) {
        const line = text.trim();
        if (line && !lines.includes(line) && !others.some((o) => o.text === line)) {
          others.push({ text: line, origin: "From a past CV" });
        }
      }
    } catch (error) {
      toast(`Could not load past lines: ${error.message}`, "error");
    }
    paint();
    nextButton.disabled = false;
  }
}
