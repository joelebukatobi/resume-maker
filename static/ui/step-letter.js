// Cover letter body: one chat turn (same grounding as Pico), auto-accepted.
//
// Skeleton + strip progress while it writes. Foot refresh / hard reload
// re-runs; opening the Letter tab does not.

import { api, el, icon, stepAction, stepSkeleton, toast } from "./dom.js";

function acceptProposals(cover, found) {
  const byIndex = [];
  for (const p of found) {
    const text = String(p.text || "").trim();
    const match = /^paragraphs\.(\d+)$/.exec(String(p.path || ""));
    if (!text || !match) continue;
    byIndex[Number(match[1])] = text;
  }
  const ordered = byIndex.filter(Boolean);
  if (!ordered.length) return 0;
  cover.paragraphs = ordered;
  return ordered.length;
}

export async function drawLetter(ctx) {
  const host = ctx.host;
  const cover = ctx.documents.cover;
  if (!Array.isArray(cover.paragraphs)) cover.paragraphs = [];
  let dirty = false;
  let open = null;
  function markDirty() {
    dirty = true;
    if (ctx.touchDraft) ctx.touchDraft();
  }
  const body = el("div", { class: "letter-paras" });
  const lead = el("p", {
    class: "lead",
    text: cover.paragraphs.some((p) => String(p || "").trim())
      ? "Recommended for this posting. Open a paragraph to change it."
      : "Nothing written yet. Refresh to draft from your notes, CV and past letters.",
  });
  const nextButton = stepAction("button", { type: "button", "aria-label": "Next" }, "right");

  function paint() {
    const paras = cover.paragraphs || [];
    if (!paras.length) {
      body.replaceChildren(el("p", {
        class: "meta",
        text: "No paragraphs yet. Refresh to write the letter.",
      }));
      return;
    }
    body.replaceChildren(...paras.map((text, index) => {
      const isOpen = open === index;
      const head = el("div", { class: "bullet-row" }, [
        el("span", { class: String(text || "").trim() ? "marker" : "marker is-empty", "aria-hidden": "true" }),
        el("div", { class: "bullet-body" }, [
          el("p", { class: "eyebrow", text: `Paragraph ${index + 1}` }),
          ...(isOpen
            ? [(() => {
              const box = el("textarea", {
                class: "input",
                rows: String(Math.min(10, Math.max(3, Math.ceil(String(text || "").length / 70) + 1))),
                "aria-label": `Paragraph ${index + 1}`,
              });
              box.value = text || "";
              box.addEventListener("input", () => {
                cover.paragraphs[index] = box.value;
                markDirty();
              });
              return box;
            })()]
            : [el("p", {
              class: String(text || "").trim() ? "bullet-text" : "bullet-text is-empty",
              text: String(text || "").trim() || "Empty.",
            })]),
        ]),
        el("button", {
          class: "icon-btn", type: "button",
          "aria-expanded": String(isOpen),
          "aria-label": isOpen ? "Close this paragraph" : "Edit this paragraph",
          onclick: () => { open = isOpen ? null : index; paint(); },
        }, [icon(isOpen ? "up" : "down", 18)]),
      ]);
      return el("div", { class: "bullet" }, [head]);
    }));
  }

  nextButton.addEventListener("click", async () => {
    nextButton.disabled = true;
    try {
      if (dirty) {
        await ctx.saveCover();
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
      el("h1", { class: "title", text: "Letter" }),
      lead,
    ]),
    body,
    el("div", { class: "step-foot" }, [
      nextButton,
      ctx.sectionRefresh(),
    ]),
  ]));
  paint();

  async function fill() {
    lead.textContent = "Writing a cover letter for this posting from your notes and CV.";
    nextButton.disabled = true;
    body.replaceChildren(stepSkeleton(4));
    if (ctx.setStepProgress) ctx.setStepProgress(0, 1);
    try {
      const found = await ctx.askLetter();
      const placed = acceptProposals(cover, found);
      if (!placed) throw new Error("No paragraphs came back. Try again.");
      markDirty();
      await ctx.saveCover();
      dirty = false;
      toast(`${placed} ${placed === 1 ? "paragraph" : "paragraphs"} placed.`);
      lead.textContent = "Recommended for this posting. Open a paragraph to change it.";
    } catch (error) {
      toast(error.message, "error");
      lead.textContent = cover.paragraphs.some((p) => String(p || "").trim())
        ? "Recommended for this posting. Open a paragraph to change it."
        : "Nothing written yet. Refresh to draft from your notes, CV and past letters.";
    } finally {
      if (ctx.setStepProgress) ctx.setStepProgress(1, 1);
      paint();
      nextButton.disabled = false;
    }
  }

  ctx.setRefresh(fill);
  // The tab is a plain link with no Next before it, so a letter still empty or
  // still the base's one-line opener is written on open. [Role] and [Company]
  // are kept on purpose (filled at build), so they say nothing here.
  const unwritten = cover.paragraphs.filter((p) => String(p || "").trim()).length < 2;
  const marked = ctx.consumeAutoFill && ctx.consumeAutoFill("letter");
  if (marked || unwritten) await fill();
  else nextButton.disabled = false;
}
