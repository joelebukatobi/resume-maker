"use strict";
/* Bridge so the editor's chat.js can run on Style / Notes.
 * Proposals update the in-memory CV/cover; open Resume and Save to keep them. */

let documentMode = "cv";
let cv = {};
let cover = {};

function header() {
  return documentMode === "cover" ? cover : cv;
}

function draw() { /* no form fields on setup pages */ }
function schedule() { /* no live preview on setup pages */ }

function setStatus(text, bad = false) {
  const note = document.getElementById("note");
  if (note) {
    note.textContent = text || "";
    note.classList.toggle("bad", Boolean(bad && text));
  }
}

async function bootSetupChat() {
  try {
    const data = await fetch(withApp("/api/document")).then(async (r) => {
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.error || r.statusText);
      return body;
    });
    cv = data.cv || {};
    cover = data.cover || {};
  } catch (error) {
    setStatus(`Could not load documents for chat: ${error.message}`, true);
  }
  if (typeof chatModeChanged === "function") await chatModeChanged();
}
