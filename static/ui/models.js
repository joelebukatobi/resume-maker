// Models dialog: pick a provider and model the way the old Agent dialog did,
// drawn in the redesign's language. Keys stay on the server; only the choice
// and a masked hint are kept in this browser under resume-maker.chat.

import {
  clearHint, readPrefs, rememberChoice, rememberHint,
} from "./agent.js";
import { api, el, icon, toast } from "./dom.js";

let dialog = null;
let providers = [];
let picked = null;
let models = [];
let modelId = "";
let openMenu = null; // "provider" | "model" | null
let keyEditing = false;

function note(text) {
  const node = dialog.querySelector("[data-note]");
  if (node) node.textContent = text || "";
}

function current() {
  return providers.find((p) => p.id === picked) || null;
}

async function loadProviders() {
  providers = await api("/api/llm/providers");
}

async function loadModels(providerId) {
  paintModelTrigger("Loading models…");
  try {
    models = await api(`/api/llm/models?provider=${encodeURIComponent(providerId)}`);
  } catch (error) {
    models = [];
    modelId = "";
    paintModelTrigger("Could not load models");
    note(error.message);
    return;
  }
  const prefs = readPrefs();
  const remembered = (prefs.models || {})[providerId];
  modelId = remembered && models.some((m) => m.id === remembered)
    ? remembered
    : (models[0] && models[0].id) || "";
  paintModelTrigger();
  paintModelMenu();
}

function keyHint(provider) {
  if (!provider) return "";
  const prefs = readPrefs();
  return provider.hint || (prefs.hints || {})[provider.id] || "";
}

function paintKey() {
  const provider = current();
  const input = dialog.querySelector("[data-key]");
  if (keyEditing) return;
  if (!provider) {
    input.value = "";
    input.placeholder = "";
    input.readOnly = true;
    return;
  }
  if (provider.source === "env") {
    input.value = "";
    input.placeholder = `Using ${provider.key_env} from the environment`;
    input.readOnly = true;
    return;
  }
  if (provider.available) {
    input.value = keyHint(provider) || "••••••••••••";
    input.placeholder = "Paste a new key to replace it";
    input.readOnly = false;
    return;
  }
  input.value = "";
  input.placeholder = `Paste your ${provider.label} key`;
  input.readOnly = false;
}

function paintConnect() {
  const provider = current();
  const button = dialog.querySelector("[data-connect]");
  const linked = Boolean(provider && provider.available && provider.source !== "env");
  button.textContent = linked ? "Disconnect" : "Connect";
  button.disabled = !provider || provider.source === "env";
  button.onclick = (event) => {
    event.preventDefault();
    if (linked) disconnect();
    else connect();
  };
}

function paintProviderTrigger() {
  const trigger = dialog.querySelector("[data-provider-trigger]");
  const provider = current();
  trigger.replaceChildren(
    el("span", { class: "select-label", text: provider ? provider.label : "Pick a provider" }),
    icon("down", 14),
  );
  trigger.setAttribute("aria-expanded", String(openMenu === "provider"));
}

function closeMenu(menu) {
  menu.classList.remove("opens-up");
  menu.style.top = "";
  menu.style.bottom = "";
  menu.style.left = "";
  menu.style.width = "";
  if (typeof menu.hidePopover === "function" && menu.matches(":popover-open")) {
    try { menu.hidePopover(); } catch (e) { /* already closed */ }
  }
}

function placeMenu(menu, trigger) {
  const rect = trigger.getBoundingClientRect();
  if (typeof menu.showPopover === "function") {
    try { menu.showPopover(); } catch (e) { /* already open */ }
  }
  const need = Math.min(220, menu.scrollHeight || 220);
  const roomBelow = window.innerHeight - rect.bottom - 12;
  const roomAbove = rect.top - 12;
  const openUp = roomBelow < need && roomAbove > roomBelow;
  menu.classList.toggle("opens-up", openUp);
  menu.style.left = `${Math.round(rect.left)}px`;
  menu.style.width = `${Math.round(rect.width)}px`;
  if (openUp) {
    menu.style.top = "auto";
    menu.style.bottom = `${Math.round(window.innerHeight - rect.top + 4)}px`;
  } else {
    menu.style.bottom = "auto";
    menu.style.top = `${Math.round(rect.bottom + 4)}px`;
  }
}

function paintProviderMenu() {
  const menu = dialog.querySelector("[data-provider-menu]");
  const trigger = dialog.querySelector("[data-provider-trigger]");
  menu.replaceChildren(...providers.map((p) => el("button", {
    class: "menu-item", type: "button", role: "option",
    "aria-selected": String(p.id === picked),
    onclick: () => {
      picked = p.id;
      keyEditing = false;
      openMenu = null;
      paint();
    },
  }, [el("span", { text: p.label })])));
  if (openMenu === "provider") placeMenu(menu, trigger);
  else closeMenu(menu);
}

function paintModelTrigger(label) {
  const trigger = dialog.querySelector("[data-model-trigger]");
  const chosen = models.find((m) => m.id === modelId);
  const text = label || (chosen ? chosen.label : (models.length ? "Pick a model" : "Connect to see models"));
  trigger.replaceChildren(
    el("span", { class: "select-label", text }),
    icon("down", 14),
  );
  trigger.disabled = !models.length;
  trigger.setAttribute("aria-expanded", String(openMenu === "model"));
}

function paintModelMenu() {
  const menu = dialog.querySelector("[data-model-menu]");
  const trigger = dialog.querySelector("[data-model-trigger]");
  menu.replaceChildren(...models.map((m) => el("button", {
    class: "menu-item", type: "button", role: "option",
    "aria-selected": String(m.id === modelId),
    onclick: () => {
      modelId = m.id;
      openMenu = null;
      paintModelTrigger();
      paintModelMenu();
    },
  }, [el("span", { text: m.label })])));
  if (openMenu === "model") placeMenu(menu, trigger);
  else closeMenu(menu);
}

function paint() {
  note("");
  paintProviderTrigger();
  paintProviderMenu();
  paintKey();
  paintConnect();
  const provider = current();
  if (provider && provider.available) loadModels(provider.id);
  else {
    models = [];
    modelId = "";
    if (openMenu === "model") openMenu = null;
    paintModelTrigger("Connect to see models");
    paintModelMenu();
  }
}

async function connect() {
  const provider = current();
  if (!provider) return;
  const input = dialog.querySelector("[data-key]");
  const key = input.value.trim();
  const hint = keyHint(provider);
  if (!key || key === hint || /•/.test(key)) {
    note("Paste a key first.");
    return;
  }
  note("Checking the key…");
  try {
    const data = await api("/api/llm/key", { provider: provider.id, key });
    rememberHint(provider.id, key);
    keyEditing = false;
    await loadProviders();
    paint();
    if (Array.isArray(data.models) && data.models.length) {
      models = data.models.map((m) => (typeof m === "string" ? { id: m, label: m } : m));
      modelId = models[0].id;
      paintModelTrigger();
      paintModelMenu();
    }
    note("Connected. Pick a model.");
  } catch (error) {
    note(error.message);
  }
}

async function disconnect() {
  const provider = current();
  if (!provider) return;
  try {
    await api("/api/llm/key", { provider: provider.id, key: "" });
    clearHint(provider.id);
    keyEditing = false;
    await loadProviders();
    paint();
  } catch (error) {
    note(error.message);
  }
}

function done() {
  const provider = current();
  if (provider && provider.available && modelId && models.some((m) => m.id === modelId)) {
    rememberChoice(provider.id, modelId, Object.fromEntries(models.map((m) => [m.id, m.label])));
    toast(`${provider.label} · ${(models.find((m) => m.id === modelId) || {}).label || modelId}`);
  }
  dialog.close();
}

function toggleMenu(which) {
  openMenu = openMenu === which ? null : which;
  paintProviderTrigger();
  paintProviderMenu();
  paintModelTrigger();
  paintModelMenu();
}

function build() {
  if (dialog) return dialog;

  const keyInput = el("input", {
    class: "input input-sm", type: "text", autocomplete: "off", spellcheck: "false",
    "data-key": "", id: "models-key",
  });
  keyInput.addEventListener("focus", () => {
    const provider = current();
    if (!provider || !provider.available || provider.source === "env") return;
    keyEditing = true;
    keyInput.value = "";
    keyInput.placeholder = `Paste your ${provider.label} key`;
  });
  keyInput.addEventListener("blur", () => {
    if (!keyInput.value.trim()) {
      keyEditing = false;
      paintKey();
    }
  });
  keyInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); connect(); }
  });

  const providerTrigger = el("button", {
    class: "select-trigger input-sm", type: "button", id: "models-provider",
    "data-provider-trigger": "", "aria-haspopup": "listbox", "aria-expanded": "false",
  });
  providerTrigger.addEventListener("click", (event) => {
    event.preventDefault();
    toggleMenu("provider");
  });

  const modelTrigger = el("button", {
    class: "select-trigger input-sm", type: "button", id: "models-model",
    "data-model-trigger": "", "aria-haspopup": "listbox", "aria-expanded": "false",
  });
  modelTrigger.addEventListener("click", (event) => {
    event.preventDefault();
    if (!models.length) return;
    toggleMenu("model");
  });

  dialog = el("dialog", { class: "modal" }, [
    el("div", { class: "modal-card" }, [
      el("header", { class: "modal-head" }, [
        el("div", { class: "modal-intro" }, [
          el("h2", { class: "card-title modal-title", text: "Model" }),
          el("p", {
            class: "meta modal-lead",
            text: "Keys stay on this machine. Pick a provider and a model.",
          }),
        ]),
        el("button", {
          class: "modal-close", type: "button", "aria-label": "Close",
          onclick: () => dialog.close(),
        }, [icon("xmark", 16)]),
      ]),
      el("hr", { class: "modal-rule" }),
      el("div", { class: "field" }, [
        el("label", { class: "label", for: "models-provider", text: "Provider" }),
        el("div", { class: "select", "data-provider-select": "" }, [
          providerTrigger,
          el("div", {
            class: "select-menu", role: "listbox", popover: "manual",
            "data-provider-menu": "",
          }),
        ]),
      ]),
      el("div", { class: "field" }, [
        el("label", { class: "label", for: "models-key", text: "Key" }),
        keyInput,
      ]),
      el("div", { class: "field" }, [
        el("label", { class: "label", for: "models-model", text: "Model" }),
        el("div", { class: "select", "data-model-select": "" }, [
          modelTrigger,
          el("div", {
            class: "select-menu", role: "listbox", popover: "manual",
            "data-model-menu": "",
          }),
        ]),
      ]),
      el("p", { class: "meta", "data-note": "" }),
      el("footer", { class: "modal-foot" }, [
        el("span", { class: "spacer" }),
        el("button", { class: "btn btn-sm", type: "button", text: "Connect", "data-connect": "" }),
        el("button", {
          class: "btn btn-sm btn-primary", type: "button", text: "Done",
          onclick: (event) => { event.preventDefault(); done(); },
        }),
      ]),
    ]),
  ]);

  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  dialog.addEventListener("close", () => {
    note("");
    openMenu = null;
    keyEditing = false;
    closeMenu(dialog.querySelector("[data-provider-menu]"));
    closeMenu(dialog.querySelector("[data-model-menu]"));
  });
  document.addEventListener("click", (event) => {
    if (!dialog || !openMenu || !dialog.open) return;
    const providerSelect = dialog.querySelector("[data-provider-select]");
    const modelSelect = dialog.querySelector("[data-model-select]");
    const providerMenu = dialog.querySelector("[data-provider-menu]");
    const modelMenu = dialog.querySelector("[data-model-menu]");
    if (providerSelect.contains(event.target) || modelSelect.contains(event.target)) return;
    if (providerMenu.contains(event.target) || modelMenu.contains(event.target)) return;
    openMenu = null;
    paintProviderTrigger();
    paintProviderMenu();
    paintModelTrigger();
    paintModelMenu();
  });
  document.body.append(dialog);
  return dialog;
}

export async function openModels() {
  build();
  try {
    await loadProviders();
  } catch (error) {
    toast(error.message, "error");
    return;
  }
  const prefs = readPrefs();
  picked = prefs.provider
    || (providers.find((p) => p.available) || providers[0] || {}).id;
  keyEditing = false;
  openMenu = null;
  paint();
  dialog.showModal();
}
