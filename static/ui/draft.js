// Unsaved document draft per application, in this browser only.
// Same key shape as the Pico editor so an old draft still restores.
// Cleared on a successful Save to disk; restored on load when it differs.

const keyFor = (appId) => `resume-maker.draft.${appId || "default"}`;

export function keepDraft(appId, documents) {
  if (!documents || !documents.cv) return;
  try {
    localStorage.setItem(keyFor(appId), JSON.stringify({
      at: Date.now(),
      cv: documents.cv,
      cover: documents.cover,
      theme: documents.theme,
      cover_theme: documents.cover_theme,
    }));
  } catch (e) { /* private window or full; disk still stands */ }
}

export function dropDraft(appId) {
  try { localStorage.removeItem(keyFor(appId)); } catch (e) { /* nothing to drop */ }
}

/** Draft when it differs from what the server just sent; otherwise null. */
export function pendingDraft(appId, saved) {
  let draft = null;
  try { draft = JSON.parse(localStorage.getItem(keyFor(appId)) || "null"); }
  catch (e) { return null; }
  if (!draft || !draft.cv || !saved || !saved.cv) return null;
  const same = JSON.stringify([draft.cv, draft.cover, draft.theme, draft.cover_theme])
    === JSON.stringify([saved.cv, saved.cover, saved.theme, saved.cover_theme]);
  return same ? null : draft;
}
