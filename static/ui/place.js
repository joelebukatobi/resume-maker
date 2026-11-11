// Keep on a notes or playbook card: the model works the text into the section
// it belongs in, he sees that section with the new words marked, and only his
// Save writes it. Resolves true once saved, false if he cancels.

import { agent } from "./agent.js";
import { api, el } from "./dom.js";

const ROUTE = { notes: "/api/setup/notes", voice: "/api/setup/voice" };

export async function placeInto(file, text, card) {
  const model = await agent().catch(() => null);
  if (!model) throw new Error("Connect a model in the Models dialog first.");
  const placed = await api("/api/setup/place", {
    file, text, provider: model.provider, model: model.model,
  });
  const name = placed.heading.replace(/^#+\s*/, "");
  return new Promise((resolve, reject) => {
    const view = el("div", { class: "place-view" }, [
      el("p", { class: "meta", text: `Add under ${name}:` }),
      el("p", { class: "place-text" }, placed.segments.map((part) =>
        part.added ? el("mark", { text: part.text }) : document.createTextNode(part.text))),
    ]);
    const finish = (value) => { view.remove(); resolve(value); };
    view.append(el("div", { class: "chat-card-actions" }, [
      el("button", { class: "btn btn-sm btn-primary", type: "button", text: "Save", onclick: async (event) => {
        event.target.disabled = true;
        try {
          await api(ROUTE[file], { place: placed });
          finish(true);
        } catch (error) {
          view.remove();
          reject(error);
        }
      } }),
      el("button", { class: "btn btn-sm", type: "button", text: "Cancel", onclick: () => finish(false) }),
    ]));
    card.append(view);
  });
}
