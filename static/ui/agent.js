// The model this browser picked. Keys live on the server; the choice of
// provider and model lives here, under the same key the editor has always used.
//
// If this browser has never opened the Agent / Models dialog, fall back to the
// first provider with a key and its first model, and remember that choice so
// Voice and Write keep working the same way.

import { api } from "./dom.js";

const PREFS = "resume-maker.chat";

export function readPrefs() {
  try { return JSON.parse(localStorage.getItem(PREFS)) || {}; } catch (e) { return {}; }
}

export function writePrefs(prefs) {
  try { localStorage.setItem(PREFS, JSON.stringify(prefs)); } catch (e) { /* not kept */ }
}

export function rememberChoice(providerId, modelId, labels = {}) {
  const prefs = readPrefs();
  writePrefs({
    ...prefs,
    provider: providerId,
    models: { ...(prefs.models || {}), [providerId]: modelId },
    labels: { ...(prefs.labels || {}), ...labels },
  });
}

// Short prefix + last four — the server never echoes a full saved key.
export function maskKey(key) {
  const text = String(key || "").trim();
  if (text.length < 12) return "••••••••••••";
  return `${text.slice(0, 7)}••••••••${text.slice(-4)}`;
}

export function rememberHint(providerId, key) {
  const prefs = readPrefs();
  writePrefs({
    ...prefs,
    hints: { ...(prefs.hints || {}), [providerId]: maskKey(key) },
  });
}

export function clearHint(providerId) {
  const prefs = readPrefs();
  const hints = { ...(prefs.hints || {}) };
  delete hints[providerId];
  writePrefs({ ...prefs, hints });
}

export async function agent() {
  const prefs = readPrefs();
  const providers = (await api("/api/llm/providers")).filter((p) => p.kind !== "judge");
  let provider = providers.find((p) => p.id === prefs.provider && p.available);
  let model = provider && (prefs.models || {})[provider.id];

  if (!provider || !model) {
    provider = providers.find((p) => p.available);
    if (!provider) return null;
    const models = await api(`/api/llm/models?provider=${encodeURIComponent(provider.id)}`);
    if (!Array.isArray(models) || !models.length) return null;
    const remembered = (prefs.models || {})[provider.id];
    model = remembered && models.some((m) => m.id === remembered) ? remembered : models[0].id;
    rememberChoice(provider.id, model, Object.fromEntries(models.map((m) => [m.id, m.label])));
  }

  return { provider: provider.id, model, label: provider.label };
}

// The open application, from the page the server sent.
export const APP = document.body.dataset.app || "";

export function withApp(path) {
  return APP ? `${path}${path.includes("?") ? "&" : "?"}app=${encodeURIComponent(APP)}` : path;
}
