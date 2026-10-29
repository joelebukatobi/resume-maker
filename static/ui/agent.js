// The model this browser picked. Keys live on the server; the choice of
// provider and model lives here, under the same key the editor has always used.

import { api } from "./dom.js";

const PREFS = "resume-maker.chat";

export async function agent() {
  let prefs = {};
  try { prefs = JSON.parse(localStorage.getItem(PREFS)) || {}; } catch (e) { /* none */ }
  const providers = await api("/api/llm/providers");
  const provider = providers.find((p) => p.id === prefs.provider && p.available);
  const model = provider && (prefs.models || {})[provider.id];
  return provider && model ? { provider: provider.id, model, label: provider.label } : null;
}

// The open application, from the page the server sent.
export const APP = document.body.dataset.app || "";

export function withApp(path) {
  return APP ? `${path}${path.includes("?") ? "&" : "?"}app=${encodeURIComponent(APP)}` : path;
}
