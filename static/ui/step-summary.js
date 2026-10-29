// Step 2, Summary: the same section turn the chat ran after a paste.
//
// On an empty summary the step asks once, shows the recommendation, and Next
// is his yes. Past library lines stay as other versions he can pick instead.
// A filled summary is left as it is; he can pick another or write his own.

import { api, el, icon, stepAction, toast } from "./dom.js";

export async function drawSummary(ctx) {
  const at = ctx.sectionIndex("SUMMARY");
  const host = ctx.host;
  if (at < 0) {
    host.replaceChildren(el("div", { class: "measure" }, [
      el("h1", { class: "title", text: "Summary" }),
      el("p", { class: "lead", text: "This CV has no summary section." }),
      el("div", { class: "step-foot" }, [
        stepAction("a", { href: "#skills", "aria-label": "Next" }, "right"),
      ]),
    ]));
    return;
  }
  const section = ctx.documents.cv.sections[at];
  const path = `sections.${at}.text`;
  const now = String(section.text || "").trim();

  const versions = [];
  if (now) versions.push({ text: now, meta: "Current" });
  let picked = now ? 0 : -1;

  const list = el("div", { class: "options", role: "group", "aria-label": "Versions" });
  const own = el("textarea", {
    class: "input posting-own", id: "own", rows: "4",
    placeholder: "Your words. Checked against your notes, not rewritten.",
  });
  const status = el("p", { class: "meta", text: "" });

  function footKind(meta) {
    if (meta === "Current") return "current";
    if (meta === "Recommended") return "recommended";
    return "past";
  }

  function paint() {
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
    if (!versions.length) list.append(el("p", { class: "meta", text: status.textContent || "…" }));
  }

  own.addEventListener("input", paint);

  async function propose() {
    status.textContent = "Writing a summary for this posting…";
    paint();
    try {
      const proposals = (await ctx.askSection("summary")).filter((p) => p.path === path && p.text);
      if (!proposals.length) throw new Error("No new version came back. Try again.");
      const fresh = proposals.map((p) => ({
        text: p.text.trim(), meta: "Recommended",
      }));
      versions.unshift(...fresh);
      picked = 0;
      own.value = "";
      status.textContent = "";
      paint();
    } catch (error) {
      toast(error.message, "error");
      status.textContent = versions.length ? "" : "No recommendation yet. Write your own below.";
      paint();
    }
  }

  const nextButton = stepAction("button", { type: "button", "aria-label": "Next" }, "right");
  nextButton.addEventListener("click", async () => {
    const text = own.value.trim() || (versions[picked] || {}).text || "";
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
      el("h1", { class: "title", text: "Summary" }),
      el("p", { class: "lead", text: "Recommended for this posting. Pick one, change it, or write your own." }),
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
  paint();

  try {
    const slot = await api("/api/library", {
      action: "slot", phase: "summary", app: ctx.app, job_description: ctx.history.job_description,
    });
    for (const line of slot.lines || []) {
      if (line.trim() && !versions.some((v) => v.text === line.trim())) {
        versions.push({ text: line.trim(), meta: "Past" });
      }
    }
    if (picked < 0 && versions.length) picked = 0;
  } catch (error) {
    toast(`Could not load past summaries: ${error.message}`, "error");
  }
  paint();

  ctx.setRefresh(propose);

  // Empty page: the same turn the chat ran after a paste. Next is his yes.
  if (!now) await propose();
  else if (!versions.length) {
    status.textContent = "No past summaries yet. Write your own below.";
    paint();
  }
}
