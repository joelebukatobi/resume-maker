// Voice: voice.md, its role starters, and rules the chat offers for it.

import { api } from "./dom.js";
import { mountDocPage } from "./doc-page.js";

mountDocPage({
  kind: "voice",
  filename: "voice.md",
  placeholder: "Ask for a rule, or to tighten one",
  starters: true,
  cards: {
    voice: { label: "For your voice", save: (rule) => api("/api/setup/voice", { append: rule }) },
  },
});
