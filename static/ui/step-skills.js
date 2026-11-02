// Step 3, Skills: one section turn, auto-accepted like the old chat yes.
//
// Same tailor phase the Pico chat ran. Skeleton + strip progress while it
// writes; chips show the result. Refresh re-runs the turn.

import { api, el, stepAction, stepSkeleton, toast } from "./dom.js";

const norm = (name) => String(name).toLowerCase().replace(/&/g, "and").replace(/\s+/g, " ").trim();
const same = (a, b) => norm(a) === norm(b);

export function splitTools(text) {
  const out = [];
  let depth = 0;
  let start = 0;
  const s = String(text || "");
  for (let i = 0; i < s.length; i += 1) {
    if (s[i] === "(") depth += 1;
    else if (s[i] === ")") depth = Math.max(0, depth - 1);
    else if (s[i] === "," && depth === 0) { out.push(s.slice(start, i)); start = i + 1; }
  }
  out.push(s.slice(start));
  return out.map((t) => t.trim()).filter(Boolean);
}

export function readLine(text, listed) {
  const named = [];
  const extra = [];
  for (const token of splitTools(text)) {
    const whole = listed.find((t) => same(t, token));
    if (whole) { named.push(whole); continue; }
    const pieces = token.split(/\s+(?:and|&)\s+/i);
    const each = pieces.map((p) => listed.find((t) => same(t, p)));
    if (pieces.length > 1 && each.every(Boolean)) { named.push(...each); continue; }
    extra.push(token);
  }
  return { named, extra };
}

function groupFrom(item, listed, { changed = false, fallbackOn = () => false } = {}) {
  const key = Object.keys(listed).find((k) => same(k, item.label || ""));
  const mine = key ? listed[key] : [];
  const { named, extra } = readLine(item.text, mine);
  const fresh = !splitTools(item.text).length;
  return {
    item,
    label: item.label || "",
    changed,
    tools: [
      ...mine.map((name) => ({
        name, on: fresh ? fallbackOn(name) : named.includes(name),
      })),
      ...extra.map((name) => ({ name, on: true })),
    ],
  };
}

export async function drawSkills(ctx) {
  const at = ctx.sectionIndex("SKILL");
  const host = ctx.host;
  const foot = (extra = []) => el("div", { class: "step-foot" }, [
    stepAction("a", { href: "#summary", "aria-label": "Back" }, "left"), ...extra,
  ]);
  if (at < 0) {
    host.replaceChildren(el("div", { class: "measure-list" }, [
      el("h1", { class: "title", text: "Skills" }),
      el("p", { class: "lead", text: "This CV has no skills section." }),
      foot([stepAction("a", { href: "#bullets", "aria-label": "Next" }, "right")]),
    ]));
    return;
  }

  const posting = String(ctx.history.job_description || "").toLowerCase();
  const section = ctx.documents.cv.sections[at];
  const items = section.items || [];
  let listed = {};
  let groups = [];
  const body = el("div", { class: "groups" });
  const lead = el("p", {
    class: "lead",
    text: "Choosing skills for this posting from your notes and past CVs.",
  });
  const nextButton = stepAction("button", { type: "button", "aria-label": "Next" }, "right");
  nextButton.disabled = true;

  function paint() {
    body.replaceChildren(...groups.map((group, g) => {
      const input = el("input", {
        class: "input input-sm", id: `add-${g}`, type: "text", placeholder: "Add a skill",
      });
      const add = el("button", { class: "btn btn-sm", type: "button", text: "Add" });
      const addSkill = async () => {
        const skill = input.value.trim();
        if (!skill) return;
        add.disabled = true;
        try {
          await api("/api/setup/notes", { skill: { label: group.label, tool: skill } });
          if (!group.tools.some((t) => same(t.name, skill))) group.tools.push({ name: skill, on: true });
          group.changed = true;
          toast(`${skill} added to your skills list.`);
          paint();
        } catch (error) {
          toast(error.message, "error");
          add.disabled = false;
        }
      };
      add.addEventListener("click", addSkill);
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") { event.preventDefault(); addSkill(); }
      });
      const on = group.tools.filter((t) => t.on).length;
      return el("section", { class: "group" }, [
        el("div", { class: "group-head" }, [
          el("span", { class: "group-name", text: group.label || "Skills" }),
          el("span", { class: "meta tnum", text: `${on} of ${group.tools.length}` }),
        ]),
        el("div", { class: "chips" }, group.tools.map((tool) => el("button", {
          class: "chip", type: "button", "aria-pressed": String(tool.on), text: tool.name,
          onclick: () => { tool.on = !tool.on; group.changed = true; paint(); },
        }))),
        el("div", { class: "group-add inline-form" }, [
          el("label", {
            class: "visually-hidden", for: `add-${g}`,
            text: `Add a skill to ${group.label}`,
          }),
          input, add,
        ]),
      ]);
    }));
  }

  nextButton.addEventListener("click", async () => {
    nextButton.disabled = true;
    try {
      let changed = false;
      for (const group of groups) {
        if (!group.changed && splitTools(group.item.text).length) continue;
        const text = group.tools.filter((t) => t.on).map((t) => t.name).join(", ");
        if (text !== (group.item.text || "")) { group.item.text = text; changed = true; }
      }
      if (changed) await ctx.saveCv();
      ctx.next("bullets");
    } catch (error) {
      toast(error.message, "error");
      nextButton.disabled = false;
    }
  });

  host.replaceChildren(el("div", { class: "measure-list" }, [
    el("header", { class: "page-head" }, [
      el("h1", { class: "title", text: "Skills" }),
      lead,
    ]),
    body,
    foot([
      nextButton,
      ctx.sectionRefresh(),
      stepAction("a", { href: "#bullets", "aria-label": "Skip" }, "skip"),
    ]),
  ]));
  body.replaceChildren(stepSkeleton(4));

  async function fill() {
    lead.textContent = "Choosing skills for this posting from your notes and past CVs.";
    nextButton.disabled = true;
    body.replaceChildren(stepSkeleton(4));
    if (ctx.setStepProgress) ctx.setStepProgress(0, 1);
    try {
      try {
        listed = (await api("/api/library", {
          action: "slot", phase: "skills", app: ctx.app,
          job_description: ctx.history.job_description,
        })).skills || {};
      } catch (error) {
        toast(`Could not load your skills list: ${error.message}`, "error");
        listed = {};
      }
      groups = items.map((item) => groupFrom(item, listed, {
        fallbackOn: (name) => posting.includes(name.toLowerCase()),
      }));
      const found = (await ctx.askSection("skills"))
        .filter((p) => typeof p.text === "string" && p.path.startsWith(`sections.${at}.items.`));
      for (const p of found) {
        const match = /^sections\.\d+\.items\.(\d+)\.text$/.exec(p.path);
        if (!match) continue;
        const index = Number(match[1]);
        if (!items[index]) continue;
        items[index].text = p.text.trim();
        groups[index] = groupFrom(items[index], listed, { changed: true });
      }
      if (!found.length) throw new Error("No skills recommendation came back. Pick skills by hand.");
      lead.textContent = "Everything from your notes and past CVs. Blue ones are the match for this posting - tap to change.";
    } catch (error) {
      toast(error.message, "error");
      if (!groups.length) {
        groups = items.map((item) => groupFrom(item, listed, {
          fallbackOn: (name) => posting.includes(name.toLowerCase()),
        }));
      }
      lead.textContent = "Tap skills on or off. Refresh to ask the model again.";
    } finally {
      if (ctx.setStepProgress) ctx.setStepProgress(1, 1);
      paint();
      nextButton.disabled = false;
    }
  }

  ctx.setRefresh(fill);
  const empty = items.every((item) => !String(item.text || "").trim());
  if (ctx.consumeAutoFill && ctx.consumeAutoFill("skills") && empty) await fill();
  else {
    lead.textContent = "Everything from your notes and past CVs. Blue ones are the match for this posting - tap to change.";
    try {
      listed = (await api("/api/library", {
        action: "slot", phase: "skills", app: ctx.app,
        job_description: ctx.history.job_description,
      })).skills || {};
    } catch (error) {
      toast(`Could not load your skills list: ${error.message}`, "error");
      listed = {};
    }
    groups = items.map((item) => groupFrom(item, listed, {
      fallbackOn: (name) => posting.includes(name.toLowerCase()),
    }));
    paint();
    nextButton.disabled = false;
  }
}
