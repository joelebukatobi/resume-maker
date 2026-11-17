// Step 2, Summary: one section turn, auto-accepted like the old chat yes.
//
// Shows a skeleton + strip progress while the model writes, then the
// recommendation (and past library lines). Next saves his pick. Refresh
// re-runs the turn. A filled summary is left as it is on soft entry; only an
// empty summary (or an explicit refresh) asks the model.

import { api, el, icon, stepAction, stepSkeleton, toast } from "./dom.js";

const fold = (text) => String(text || "").replace(/\s+/g, " ").trim();

export async function drawSummary(ctx) {
  const at = ctx.sectionIndex("SUMMARY");
  const host = ctx.host;
  if (at < 0) {
    host.replaceChildren(el("div", { class: "measure" }, [
      el("h1", { class: "title", text: "Professional Summary" }),
      el("p", { class: "lead", text: "This CV has no summary section." }),
      el("div", { class: "step-foot" }, [
        stepAction("a", { href: "#skills", "aria-label": "Next" }, "right"),
      ]),
    ]));
    return;
  }
  const section = ctx.documents.cv.sections[at];
  const path = `sections.${at}.text`;
  const now = fold(section.text);

  const versions = [];
  let picked = -1;

  const list = el("div", { class: "options", role: "group", "aria-label": "Versions" });
  const own = el("textarea", {
    class: "input posting-own", id: "own", rows: "4",
    placeholder: "Your words. Checked against your notes, not rewritten.",
  });

  function footKind(meta) {
    if (meta === "Current") return "current";
    if (meta === "Recommended") return "recommended";
    return "past";
  }

  // Saved CV text stays Current and sits above Past. Recommendations lead.
  function orderVersions() {
    if (now) {
      for (const v of versions) {
        if (fold(v.text) === now) v.meta = "Current";
      }
      if (!versions.some((v) => fold(v.text) === now)) {
        versions.push({ text: now, meta: "Current" });
      }
      const seen = new Set();
      for (let i = versions.length - 1; i >= 0; i -= 1) {
        const key = fold(versions[i].text);
        if (seen.has(key)) versions.splice(i, 1);
        else seen.add(key);
      }
    }
    const rank = (meta) => (meta === "Recommended" ? 0 : meta === "Current" ? 1 : 2);
    versions.sort((a, b) => rank(a.meta) - rank(b.meta));
  }

  function addPast(line) {
    const text = fold(line);
    if (!text || versions.some((v) => fold(v.text) === text)) return;
    versions.push({ text, meta: now && text === now ? "Current" : "Past" });
  }

  function paint() {
    orderVersions();
    if (picked < 0 || picked >= versions.length) {
      picked = versions.findIndex((v) => v.meta === "Current");
      if (picked < 0) picked = versions.length ? 0 : -1;
    }
    list.replaceChildren(...versions.map((v, index) => {
      const selected = index === picked && !own.value.trim();
      return el("button", {
        class: "card option", type: "button", "aria-pressed": String(selected),
        onclick: () => { picked = index; own.value = ""; paint(); },
      }, [
        el("span", { class: "option-body" }, [
          el("span", { class: "option-text posting-line", text: v.text }),
        ]),
        el("span", { class: `option-foot is-${footKind(v.meta)}` }, [
          el("span", { class: "meta", text: v.meta }),
          selected ? el("span", { class: "option-check", "aria-hidden": "true" }, [icon("check", 10)]) : null,
        ]),
      ]);
    }));
  }

  own.addEventListener("input", () => {
    paint();
    if (own.value.trim() && ctx.touchDraft) {
      section.text = fold(own.value);
      ctx.touchDraft();
    }
  });

  const lead = el("p", {
    class: "lead",
    text: "Writing a summary for this posting from your notes and past CVs.",
  });
  const nextButton = stepAction("button", { type: "button", "aria-label": "Next" }, "right");
  nextButton.disabled = true;
  nextButton.addEventListener("click", async () => {
    const text = fold(own.value) || (versions[picked] || {}).text || "";
    if (!text) { toast("Pick a version or write your own.", "error"); return; }
    nextButton.disabled = true;
    try {
      if (text !== now) {
        section.text = text;
        await ctx.saveCv();
      }
      ctx.next("skills");
    } catch (error) {
      toast(error.message, "error");
      nextButton.disabled = false;
    }
  });

  host.replaceChildren(el("div", { class: "measure" }, [
    el("header", { class: "page-head" }, [
      el("h1", { class: "title", text: "Professional Summary" }),
      lead,
    ]),
    list,
    el("div", { class: "own-block" }, [
      el("label", { class: "label", for: "own", text: "Write your own" }),
      own,
    ]),
    el("div", { class: "step-foot" }, [
      stepAction("a", { href: "#posting", "aria-label": "Back" }, "left"),
      nextButton,
      ctx.sectionRefresh(),
      stepAction("a", { href: "#skills", "aria-label": "Skip" }, "skip"),
    ]),
  ]));
  list.replaceChildren(stepSkeleton(3));

  async function loadPast() {
    try {
      const slot = await api("/api/library", {
        action: "slot", phase: "summary", app: ctx.app,
        job_description: ctx.history.job_description,
      });
      for (const line of slot.lines || []) addPast(line);
    } catch (error) {
      toast(`Could not load past summaries: ${error.message}`, "error");
    }
  }

  async function fill() {
    lead.textContent = "Writing a summary for this posting from your notes and past CVs.";
    nextButton.disabled = true;
    list.replaceChildren(stepSkeleton(3));
    if (ctx.setStepProgress) ctx.setStepProgress(0, 1);
    try {
      await loadPast();
      const proposals = (await ctx.askSection("summary")).filter((p) => p.path === path && p.text);
      if (!proposals.length) throw new Error("No new version came back. Try again.");
      const fresh = proposals.map((p) => ({ text: fold(p.text), meta: "Recommended" }));
      for (let i = versions.length - 1; i >= 0; i -= 1) {
        if (versions[i].meta === "Recommended") versions.splice(i, 1);
      }
      versions.unshift(...fresh.filter((p) => p.text));
      picked = 0;
      own.value = "";
      lead.textContent = "Recommended for this posting. Pick one, change it, or write your own.";
    } catch (error) {
      toast(error.message, "error");
      lead.textContent = versions.length || now
        ? "Recommended for this posting. Pick one, change it, or write your own."
        : "No recommendation yet. Write your own below.";
    } finally {
      if (ctx.setStepProgress) ctx.setStepProgress(1, 1);
      paint();
      nextButton.disabled = false;
    }
  }

  ctx.setRefresh(fill);
  // Empty summary: draft on hard reload. Filled: show Current + Past; refresh asks.
  const shouldDraft = !now && ctx.consumeAutoFill && ctx.consumeAutoFill("summary");
  if (shouldDraft) await fill();
  else {
    lead.textContent = now
      ? "Recommended for this posting. Pick one, change it, or write your own."
      : "Nothing written yet. Refresh to draft from your notes and past CVs.";
    if (now) versions.push({ text: now, meta: "Current" });
    await loadPast();
    paint();
    nextButton.disabled = false;
  }
}
