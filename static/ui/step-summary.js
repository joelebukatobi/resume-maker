// Step 2, Summary: pick one version or write your own.
//
// The versions are what is on the page now, the past summaries the round
// would show the model (POST /api/library slot), and any new one asked for
// here (one summary turn of the round). Whatever is picked is saved as the
// summary paragraph and nothing else.

import { api, el, toast } from "./dom.js";

export async function drawSummary(ctx) {
  const at = ctx.sectionIndex("SUMMARY");
  const host = ctx.host;
  if (at < 0) {
    host.replaceChildren(el("div", { class: "measure" }, [
      el("h1", { class: "title", text: "Summary" }),
      el("p", { class: "lead", text: "This CV has no summary section." }),
      el("div", { class: "step-foot" }, [el("a", { class: "btn btn-primary", href: "#skills", text: "Next" })]),
    ]));
    return;
  }
  const section = ctx.documents.cv.sections[at];
  const path = `sections.${at}.text`;
  const now = String(section.text || "").trim();

  const versions = [];
  if (now) versions.push({ text: now, meta: "On the page now" });
  let picked = now ? 0 : -1;

  const list = el("div", { class: "options", role: "group", "aria-label": "Versions" });
  const own = el("textarea", { class: "input", id: "own", rows: "4",
    placeholder: "Your words. Checked against your notes, not rewritten." });
  const askButton = el("button", { class: "btn", type: "button", text: "Write a new one" });

  function paint() {
    list.replaceChildren(...versions.map((v, index) => el("button", {
      class: "card option", type: "button", "aria-pressed": String(index === picked && !own.value.trim()),
      onclick: () => { picked = index; own.value = ""; paint(); },
    }, [
      el("span", { class: "option-text", text: v.text }),
      el("span", { class: "meta", text: v.meta }),
    ])));
    if (!versions.length) list.append(el("p", { class: "meta", text: "Loading your past summaries…" }));
  }

  own.addEventListener("input", paint);

  askButton.addEventListener("click", async () => {
    askButton.disabled = true;
    askButton.textContent = "Writing…";
    try {
      const proposals = (await ctx.askSection("summary")).filter((p) => p.path === path && p.text);
      if (!proposals.length) throw new Error("No new version came back. Try again.");
      for (const p of proposals) versions.push({ text: p.text.trim(), meta: "New, from your notes" });
      picked = versions.length - 1;
      own.value = "";
      paint();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      askButton.disabled = false;
      askButton.textContent = "Write a new one";
    }
  });

  const nextButton = el("button", { class: "btn btn-primary", type: "button", text: "Next" });
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
      el("p", { class: "lead", text: "Pick one, or write your own." }),
    ]),
    list,
    el("div", { class: "option-tools" }, [askButton]),
    el("label", { class: "label", for: "own", text: "Or write your own" }),
    own,
    el("div", { class: "step-foot" }, [
      el("a", { class: "btn", href: "#posting", text: "Back" }),
      nextButton,
      el("a", { class: "btn", href: "#skills", text: "Skip" }),
    ]),
  ]));
  paint();

  try {
    const slot = await api("/api/library", {
      action: "slot", phase: "summary", app: ctx.app, job_description: ctx.history.job_description,
    });
    for (const line of slot.lines || []) {
      if (line.trim() && !versions.some((v) => v.text === line.trim())) {
        versions.push({ text: line.trim(), meta: "From a past CV" });
      }
    }
    if (picked < 0 && versions.length) picked = 0;
  } catch (error) {
    toast(`Could not load past summaries: ${error.message}`, "error");
  }
  paint();
  if (!versions.length) {
    list.replaceChildren(el("p", { class: "meta", text: "No past summaries yet. Write a new one, or your own." }));
  }
}
