// Step 3, Skills: the tools on this CV, by label, as chips.
//
// The tools come from his skills list in notes.md (POST /api/library slot,
// the list the skills turn is given, posting first) plus whatever the CV
// already names. On means on this CV. A fresh CV starts with the tools the
// posting names. Adding a tool writes it to the skills list for every CV
// after this one, through the same endpoint the chat's skill card uses.

import { api, el, toast } from "./dom.js";

// Names compared without case, spacing or "&" against "and".
const norm = (name) => String(name).toLowerCase().replace(/&/g, "and").replace(/\s+/g, " ").trim();
const same = (a, b) => norm(a) === norm(b);

// A CV skills line split at its commas, but not the ones inside brackets:
// "JavaScript (Node, React), SQL" is two tools, not three.
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

// Which of his listed tools a CV line names, and what it names that the
// list does not. "Jira and Confluence" is both of those tools when he lists
// them separately; "CI/CD & Linux" stays one when he lists it as one.
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

export async function drawSkills(ctx) {
  const at = ctx.sectionIndex("SKILL");
  const host = ctx.host;
  const foot = (extra = []) => el("div", { class: "step-foot" }, [
    el("a", { class: "btn", href: "#summary", text: "Back" }), ...extra,
  ]);
  if (at < 0) {
    host.replaceChildren(el("div", { class: "measure" }, [
      el("h1", { class: "title", text: "Skills" }),
      el("p", { class: "lead", text: "This CV has no skills section." }),
      foot([el("a", { class: "btn btn-primary", href: "#bullets", text: "Next" })]),
    ]));
    return;
  }

  host.replaceChildren(el("div", { class: "measure" }, [
    el("h1", { class: "title", text: "Skills" }),
    el("p", { class: "meta", text: "Loading your skills list…" }),
  ]));

  let listed = {};
  try {
    listed = (await api("/api/library", {
      action: "slot", phase: "skills", app: ctx.app, job_description: ctx.history.job_description,
    })).skills || {};
  } catch (error) {
    toast(`Could not load your skills list: ${error.message}`, "error");
  }

  const posting = String(ctx.history.job_description || "").toLowerCase();
  const section = ctx.documents.cv.sections[at];
  const items = section.items || [];

  // One group per label on the CV: his list for that label first, then any
  // tool the CV names that the list does not.
  const groups = items.map((item) => {
    const key = Object.keys(listed).find((k) => same(k, item.label || ""));
    const mine = key ? listed[key] : [];
    const { named, extra } = readLine(item.text, mine);
    const fresh = !splitTools(item.text).length;
    return {
      item,
      label: item.label || "",
      // Untouched groups are saved exactly as they were written.
      changed: false,
      tools: [
        ...mine.map((name) => ({
          name, on: fresh ? posting.includes(name.toLowerCase()) : named.includes(name),
        })),
        ...extra.map((name) => ({ name, on: true })),
      ],
    };
  });

  const body = el("div", { class: "groups" });

  function paint() {
    body.replaceChildren(...groups.map((group, g) => {
      const input = el("input", { class: "input input-sm", id: `add-${g}`, type: "text", placeholder: "Add a tool" });
      const add = el("button", { class: "btn btn-sm", type: "button", text: "Add" });
      const addTool = async () => {
        const tool = input.value.trim();
        if (!tool) return;
        add.disabled = true;
        try {
          await api("/api/setup/notes", { skill: { label: group.label, tool } });
          if (!group.tools.some((t) => same(t.name, tool))) group.tools.push({ name: tool, on: true });
          group.changed = true;
          toast(`${tool} added to your skills list.`);
          paint();
        } catch (error) {
          toast(error.message, "error");
          add.disabled = false;
        }
      };
      add.addEventListener("click", addTool);
      input.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); addTool(); } });
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
          el("label", { class: "visually-hidden", for: `add-${g}`, text: `Add a tool to ${group.label}` }),
          input, add,
        ]),
      ]);
    }));
  }

  const nextButton = el("button", { class: "btn btn-primary", type: "button", text: "Next" });
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

  host.replaceChildren(el("div", { class: "measure" }, [
    el("header", { class: "page-head" }, [
      el("h1", { class: "title", text: "Skills" }),
      el("p", { class: "lead", text: "Highlighted go on this CV. Tap to drop one." }),
    ]),
    body,
    foot([nextButton, el("a", { class: "btn", href: "#bullets", text: "Skip" })]),
  ]));
  paint();
}
