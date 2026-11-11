// Playbook: playbook.md, its role starters, and rules the chat offers for it.

import { placeInto } from "./place.js";
import { mountDocPage } from "./doc-page.js";

mountDocPage({
  kind: "voice",
  filename: "playbook.md",
  placeholder: "Ask for a rule, or to tighten one",
  starters: true,
  search: true,
  cards: {
    voice: { label: "For your Playbook", save: (rule, card) => placeInto("voice", rule, card) },
  },
});
