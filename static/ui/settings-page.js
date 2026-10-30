// Settings: bio, contacts, paper theme, and Models.
// JO in the nav opens this page. Keys stay on the server; Models reuses the
// existing dialog. Bio writes to both CV and letter so header/footer picks
// share one list.

import { api, el, icon, toast } from "./dom.js";
import { openModels } from "./models.js";
import { bespokeSelect } from "./select.js";

const LETTER = { w: 8.5, h: 11 };
const A4 = { w: 210 / 25.4, h: 297 / 25.4 };

function $(id) {
  return document.getElementById(id);
}

function pageSizeOf(theme) {
  const w = Number(theme.page_width_in);
  const h = Number(theme.page_height_in);
  if (Math.abs(w - A4.w) < 0.05 && Math.abs(h - A4.h) < 0.05) return "a4";
  return "letter";
}

function applyPageSize(theme, size) {
  const dims = size === "a4" ? A4 : LETTER;
  theme.page_width_in = dims.w;
  theme.page_height_in = dims.h;
}

function field(label, control) {
  return el("label", { class: "settings-field" }, [
    el("span", { class: "label", text: label }),
    control,
  ]);
}

function numberInput(value, attrs = {}) {
  const box = el("input", {
    class: "input", type: "number", step: "0.5", min: "6", ...attrs,
  });
  box.value = value == null ? "" : String(value);
  return box;
}

function initials(name) {
  const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "JO";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function paintProfile(name) {
  const node = document.querySelector(".nav-profile");
  if (node) node.textContent = initials(name);
}

function rule() {
  return el("hr", { class: "settings-rule" });
}

async function main() {
  const host = $("settings");
  const status = $("status");
  let doc;
  let fonts = [];
  try {
    [doc, fonts] = await Promise.all([
      api("/api/document"),
      api("/api/fonts"),
    ]);
  } catch (error) {
    status.textContent = error.message;
    status.dataset.state = "error";
    return;
  }
  status.remove();

  const cv = doc.cv;
  const cover = doc.cover;
  const theme = doc.theme;
  const coverTheme = doc.cover_theme;
  if (!Array.isArray(cv.contact)) cv.contact = [];
  if (!Array.isArray(cover.contact)) cover.contact = [];
  // CV owns the list and header ticks; the letter's footer ticks live on
  // cover.contact. Match by text (or href) so Settings shows what already
  // prints under the sign-off: email and phone today.
  function coverMatch(item) {
    const text = String(item.text || "").trim();
    const href = String(item.href || "").trim();
    return cover.contact.find((c) => {
      const ct = String(c.text || "").trim();
      const ch = String(c.href || "").trim();
      return (text && ct === text) || (href && ch === href);
    }) || null;
  }
  let contact = cv.contact.map((item) => {
    const onLetter = coverMatch(item);
    return {
      text: item.text || "",
      href: item.href || "",
      header: item.header !== false,
      footer: onLetter ? !!onLetter.footer : !!item.footer,
    };
  });
  let dirty = false;

  const contactBody = el("div", { class: "settings-contacts" });
  const paperBody = el("div", { class: "settings-stack" });
  const saveButton = el("button", {
    class: "btn btn-primary", type: "button", text: "Save",
  });
  const addContact = el("button", {
    class: "icon-btn", type: "button", "aria-label": "Add contact item",
  }, [icon("plus", 18)]);

  const nameBox = el("input", {
    class: "input", type: "text", id: "settings-name",
    "aria-label": "Name",
  });
  nameBox.value = cv.name || "";
  nameBox.addEventListener("input", () => {
    dirty = true;
    paintProfile(nameBox.value);
  });

  function markDirty() { dirty = true; }

  function placeSwitch(label, on, set) {
    return el("button", {
      class: "switch",
      type: "button",
      role: "switch",
      "aria-checked": String(on),
      "aria-label": label,
      onclick: () => { set(!on); markDirty(); paintContacts(); },
    });
  }

  function contactHead() {
    return el("div", {
      class: "settings-contact is-head",
      "aria-hidden": "true",
    }, [
      el("span", { class: "meta", text: "Text" }),
      el("span", { class: "meta", text: "Link" }),
      el("span", { class: "meta settings-contact-place", text: "CV" }),
      el("span", { class: "meta settings-contact-place", text: "Letter" }),
      el("span", { class: "settings-contact-gap" }),
    ]);
  }

  function contactRow(item, index) {
    const text = el("input", {
      class: "input", type: "text", placeholder: "email, phone, …",
      "aria-label": "Contact text",
    });
    text.value = item.text;
    text.addEventListener("input", () => { item.text = text.value; markDirty(); });

    const href = el("input", {
      class: "input", type: "url", placeholder: "https://…",
      "aria-label": "Contact link",
    });
    href.value = item.href || "";
    href.addEventListener("input", () => {
      item.href = href.value.trim();
      markDirty();
    });

    const onCv = item.header !== false;
    const onLetter = !!item.footer;
    return el("div", { class: "settings-contact" }, [
      text,
      href,
      placeSwitch(
        onCv ? "Hide from CV" : "Show on CV",
        onCv,
        (v) => { item.header = v; },
      ),
      placeSwitch(
        onLetter ? "Hide from Letter" : "Show on Letter",
        onLetter,
        (v) => { item.footer = v; },
      ),
      el("button", {
        class: "icon-btn", type: "button", "aria-label": "Remove contact item",
        onclick: () => {
          contact.splice(index, 1);
          markDirty();
          paintContacts();
        },
      }, [icon("xmark", 16)]),
    ]);
  }

  function paintContacts() {
    contactBody.replaceChildren(contactHead(), ...contact.map(contactRow));
  }

  addContact.addEventListener("click", () => {
    contact.push({ text: "", href: "", header: true, footer: false });
    markDirty();
    paintContacts();
  });

  const fontOptions = fonts.map((f) => ({
    id: f.name,
    label: f.name + (f.ships_with_office ? "" : " (PDF only)"),
  }));
  if (theme.font && !fontOptions.some((o) => o.id === theme.font)) {
    fontOptions.push({ id: theme.font, label: theme.font });
  }
  const fontSelect = bespokeSelect({
    label: "Font",
    options: fontOptions,
    value: theme.font || "Cambria",
    onChange: (name) => {
      theme.font = name;
      const font = fonts.find((f) => f.name === name);
      if (font) theme.font_stack = font.stack;
      markDirty();
    },
  });

  const pageSelect = bespokeSelect({
    label: "Paper Size",
    options: [
      { id: "letter", label: "US Letter (8.5 × 11 in)" },
      { id: "a4", label: "A4 (210 × 297 mm)" },
    ],
    value: pageSizeOf(theme),
    onChange: (size) => {
      applyPageSize(theme, size);
      markDirty();
    },
  });

  const sizeName = numberInput(theme.size_name_pt, { "aria-label": "Name size" });
  const sizeContact = numberInput(theme.size_contact_pt, { "aria-label": "Contact size" });
  const sizeHeading = numberInput(theme.size_heading_pt, { "aria-label": "Heading size" });
  const sizeBody = numberInput(theme.size_body_pt, { "aria-label": "Body size" });
  const margin = numberInput(theme.margin_in, {
    step: "0.05", min: "0", "aria-label": "Margin inches",
  });

  for (const [box, key] of [
    [sizeName, "size_name_pt"],
    [sizeContact, "size_contact_pt"],
    [sizeHeading, "size_heading_pt"],
    [sizeBody, "size_body_pt"],
    [margin, "margin_in"],
  ]) {
    box.addEventListener("input", () => {
      const value = parseFloat(box.value);
      if (!Number.isNaN(value)) {
        theme[key] = value;
        markDirty();
      }
    });
  }

  paperBody.replaceChildren(
    el("div", { class: "settings-grid settings-grid-3" }, [
      field("Paper Size", pageSelect.root),
      field("Font", fontSelect.root),
      field("Name (pt)", sizeName),
    ]),
    el("div", { class: "settings-grid" }, [
      field("Contact (pt)", sizeContact),
      field("Heading (pt)", sizeHeading),
      field("Body (pt)", sizeBody),
      field("Margin (in)", margin),
    ]),
  );

  function mirrorPaperToCover() {
    for (const key of [
      "font", "font_stack",
      "size_name_pt", "size_contact_pt", "size_heading_pt", "size_body_pt",
      "page_width_in", "page_height_in", "margin_in",
    ]) {
      coverTheme[key] = theme[key];
    }
  }

  function contactPayload() {
    return contact.map((item) => {
      const out = {
        text: String(item.text || "").trim(),
        header: item.header !== false,
        footer: !!item.footer,
      };
      const href = String(item.href || "").trim();
      if (href) out.href = href;
      return out;
    }).filter((item) => item.text);
  }

  async function save() {
    saveButton.disabled = true;
    try {
      const name = nameBox.value.trim();
      cv.name = name;
      cover.name = name;
      const items = contactPayload();
      cv.contact = items;
      cover.contact = items;
      mirrorPaperToCover();
      await api("/api/save", {
        cv, theme, cover, cover_theme: coverTheme, build: false,
      });
      dirty = false;
      paintProfile(name);
      toast("Settings saved.");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      saveButton.disabled = false;
    }
  }

  saveButton.addEventListener("click", () => save());
  window.onbeforeunload = (event) => { if (dirty) event.preventDefault(); };

  paintContacts();
  paintProfile(nameBox.value);

  host.replaceChildren(
    el("header", { class: "page-head" }, [
      el("h1", { class: "title", text: "Settings" }),
      el("p", {
        class: "lead",
        text: "Your name, contact details, and the paper the CV and letter print on.",
      }),
    ]),

    el("section", { class: "settings-block", "aria-labelledby": "settings-bio" }, [
      el("h2", { class: "eyebrow", id: "settings-bio", text: "Bio" }),
      field("Name", nameBox),
    ]),

    rule(),

    el("section", { class: "settings-block", "aria-labelledby": "settings-contacts" }, [
      el("div", { class: "settings-block-head" }, [
        el("h2", { class: "eyebrow", id: "settings-contacts", text: "Contacts" }),
        addContact,
      ]),
      el("p", {
        class: "meta settings-lead",
        text: "Toggle CV or Letter on each line to choose where it prints: under your name on the resume, or under the sign-off on the cover letter.",
      }),
      contactBody,
    ]),

    rule(),

    el("section", { class: "settings-block", "aria-labelledby": "settings-paper" }, [
      el("h2", { class: "eyebrow", id: "settings-paper", text: "Paper" }),
      paperBody,
    ]),

    rule(),

    el("section", { class: "settings-block", "aria-labelledby": "settings-models" }, [
      el("div", { class: "settings-block-head" }, [
        el("h2", { class: "eyebrow", id: "settings-models", text: "Models" }),
        el("button", {
          class: "doc-action", type: "button", text: "Open Models",
          onclick: () => openModels(),
        }),
      ]),
      el("p", {
        class: "meta settings-lead",
        text: "Provider, key, and model. Keys stay on this machine and never reach the browser.",
      }),
    ]),

    el("div", { class: "settings-foot" }, [saveButton]),
  );
}

main();
