// My Experience: notes.md, searched from the nav, and what the chat offers:
// a fact, or a tool for the skills list.

import { api } from "./dom.js";
import { mountDocPage } from "./doc-page.js";
import { placeInto } from "./place.js";

function skill(text) {
  try {
    const parsed = JSON.parse(text);
    if (typeof parsed.label === "string" && typeof parsed.tool === "string") return parsed;
  } catch (e) { /* not a skill */ }
  return null;
}

mountDocPage({
  kind: "notes",
  filename: "notes.md",
  placeholder: "Add something you've done",
  search: true,
  cards: {
    notes: { label: "For My Experience", save: (note, card) => placeInto("notes", note, card) },
    skill: {
      label: "For your skills",
      show: (text) => { const s = skill(text); return s ? `${s.label}: ${s.tool}` : text; },
      save: (text) => {
        const s = skill(text);
        if (!s) throw new Error("That suggestion is not a skill.");
        return api("/api/setup/notes", { skill: { label: s.label, tool: s.tool } });
      },
    },
  },
});
