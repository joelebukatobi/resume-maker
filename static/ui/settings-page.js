// Settings: Bio (name, contacts, paper), Education, Work History, Base Files.
// JO opens this page. Tabs mirror Write. Structure editors change the current
// application's CV skeleton (employer, school, dates); Write still owns
// posting-driven wording. Models lives in the main nav.

import { agent } from "./agent.js";
import { api, download, el, icon, toast } from "./dom.js";
import { bespokeSelect } from "./select.js";

const LETTER = { w: 8.5, h: 11 };
const A4 = { w: 210 / 25.4, h: 297 / 25.4 };

function $(id) {
  return document.getElementById(id);
}

const BASE_FILES = {
  cv: {
    label: "CV",
    backup: "cv-backup.json",
    warning: "This replaces your base CV and can't be undone. Applications you've already made keep their copies.",
  },
  cover: {
    label: "Cover Letter",
    backup: "cover-letter-backup.json",
    warning: "This replaces your base letter and can't be undone. Applications you've already made keep their copies.",
  },
  voice: {
    label: "Playbook",
    backup: "playbook-backup.md",
    warning: "This replaces your current Playbook and can't be undone.",
  },
  notes: {
    label: "Notes",
    backup: "notes-backup.md",
    warning: "This replaces your current notes, including your saved answers, and can't be undone.",
  },
};

function readBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.onerror = () => reject(new Error("Could not read that file."));
    reader.readAsDataURL(file);
  });
}

// Back up or skip, then the new file goes in. Resolves true once replaced.
function confirmReplace(key, current, apply) {
  const { label, backup, warning } = BASE_FILES[key];
  return new Promise((resolve) => {
    const error = el("p", { class: "meta" });
    const close = (done) => { dialog.close(); dialog.remove(); resolve(done); };
    const replace = async (button) => {
      button.disabled = true;
      try {
        await apply();
        close(true);
      } catch (failure) {
        button.disabled = false;
        error.textContent = failure.message;
      }
    };
    const cancel = el("button", {
      class: "btn btn-sm", type: "button", text: "Cancel", onclick: () => close(false),
    });
    const dialog = el("dialog", { class: "modal" }, [
      el("div", { class: "modal-card" }, [
        el("header", { class: "modal-head" }, [
          el("div", { class: "modal-intro" }, [
            el("h2", { class: "card-title modal-title", text: `Replace ${label}` }),
          ]),
          el("button", {
            class: "modal-close", type: "button", "aria-label": "Close",
            onclick: () => close(false),
          }, [icon("xmark", 16)]),
        ]),
        el("hr", { class: "modal-rule" }),
        el("p", { class: "meta", text: warning }),
        error,
        el("footer", { class: "modal-foot" }, [
          el("span", { class: "spacer" }),
          cancel,
          el("button", {
            class: "btn btn-sm", type: "button", text: "Skip",
            onclick: (event) => replace(event.currentTarget),
          }),
          el("button", {
            class: "btn btn-sm btn-primary", type: "button", text: "Download Backup",
            onclick: (event) => {
              download(backup, current);
              replace(event.currentTarget);
            },
          }),
        ]),
      ]),
    ]);
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(false); });
    document.body.append(dialog);
    dialog.showModal();
    cancel.focus();
  });
}

// A CV or letter is turned into a document by one model call, made only once
// the warning is answered. Notes and voice are plain text and cost nothing.
async function uploadFor(key, file) {
  if (key === "cv" || key === "cover") {
    const model = await agent().catch(() => null);
    if (!model) throw new Error("Connect a model first.");
    const current = (await api("/api/setup/base", { action: "read" }))[key];
    const encoded = await readBase64(file);
    return {
      current: JSON.stringify(current, null, 2),
      apply: async () => {
        const { doc } = await api("/api/import", {
          document: key, doc: current, filename: file.name, file: encoded,
          provider: model.provider, model: model.model,
        });
        await api("/api/setup/base", { document: key, doc });
      },
    };
  }
  // Plain text is read here: the extractor stops at 60,000 characters.
  const { text } = /\.(md|txt)$/i.test(file.name)
    ? { text: await file.text() }
    : await api("/api/setup/extract", { filename: file.name, file: await readBase64(file) });
  if (!text.trim()) throw new Error("That file has no text in it.");
  return {
    current: (await api("/api/setup"))[key] || "",
    apply: () => api(`/api/setup/${key}`, { text }),
  };
}

function baseFileRow(key) {
  const { label } = BASE_FILES[key];
  const input = el("input", {
    type: "file",
    accept: key === "cv" || key === "cover" ? ".txt,.docx,.pdf" : ".md,.txt,.docx,.pdf",
    hidden: true,
    "aria-label": `Upload ${label}`,
  });
  const button = el("button", {
    class: "btn btn-sm", type: "button", text: "Upload", onclick: () => input.click(),
  });
  input.addEventListener("change", async () => {
    const file = input.files[0];
    input.value = "";
    if (!file) return;
    button.disabled = true;
    try {
      // Read first, so a file that cannot be used never reaches the warning.
      const { current, apply } = await uploadFor(key, file);
      if (current.trim()) {
        if (!await confirmReplace(key, current, apply)) return;
      } else {
        await apply();
      }
      toast(`${label} replaced.`);
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  });
  return el("div", {}, [el("span", { text: label }), el("span", {}, [input, button])]);
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

  function placeSwitch(label, on, set, after) {
    return el("button", {
      class: "switch",
      type: "button",
      role: "switch",
      "aria-checked": String(on),
      "aria-label": label,
      onclick: () => {
        set(!on);
        markDirty();
        if (after) after();
      },
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
        paintContacts,
      ),
      placeSwitch(
        onLetter ? "Hide from Letter" : "Show on Letter",
        onLetter,
        (v) => { item.footer = v; },
        paintContacts,
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

  async function save(button) {
    if (button) button.disabled = true;
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
      if (button) button.disabled = false;
    }
  }

  window.onbeforeunload = (event) => { if (dirty) event.preventDefault(); };

  paintContacts();
  paintProfile(nameBox.value);

  function sectionIndex(word) {
    return (cv.sections || []).findIndex(
      (s) => String(s.heading || "").toUpperCase().includes(word));
  }

  function entryFields(entry, labels, onInput = markDirty) {
    const box = (label, key) => {
      const input = el("input", {
        class: "input", type: "text", "aria-label": label,
      });
      input.value = entry[key] || "";
      input.addEventListener("input", () => {
        entry[key] = input.value;
        onInput();
      });
      return field(label, input);
    };
    // Two rows, two page-height inputs each (same .input atom as Bio).
    return el("div", { class: "settings-entry" }, [
      el("div", { class: "settings-grid" }, [
        box(labels[0], "left"),
        box(labels[1], "right"),
      ]),
      el("div", { class: "settings-grid" }, [
        box(labels[2], "sub_left"),
        box(labels[3], "sub_right"),
      ]),
    ]);
  }

  function entriesPanel(word, empty, labels) {
    const at = sectionIndex(word);
    const body = el("div", { class: "settings-entries" });
    const blank = () => ({ left: "", right: "", sub_left: "", sub_right: "", bullets: [] });
    // The empty card at the end. It joins the CV only when its check is pressed.
    let draft = blank();

    function draftCard(section) {
      const add = el("button", {
        class: "icon-btn", type: "button", "aria-label": "Add entry", hidden: true,
        onclick: () => {
          section.entries.push(draft);
          draft = blank();
          markDirty();
          paint();
        },
      }, [icon("check", 16)]);
      const filled = () => ["left", "right", "sub_left", "sub_right"]
        .every((key) => String(draft[key] || "").trim());
      return el("div", { class: "card settings-entry-card" }, [
        el("div", { class: "settings-entry-head" }, [
          el("h3", { class: "eyebrow", text: "New entry" }),
          el("div", { class: "settings-entry-actions" }, [add]),
        ]),
        entryFields(draft, labels, () => { add.hidden = !filled(); }),
      ]);
    }

    function paint() {
      if (at < 0) {
        body.replaceChildren(el("p", { class: "meta", text: empty }));
        return;
      }
      const section = cv.sections[at];
      if (!Array.isArray(section.entries)) section.entries = [];
      const rows = section.entries.map((entry, index) => el("div", { class: "card settings-entry-card" }, [
        el("div", { class: "settings-entry-head" }, [
          el("h3", {
            class: "eyebrow",
            text: entry.left || entry.sub_left || `Entry ${index + 1}`,
          }),
          el("div", { class: "settings-entry-actions" }, [
            el("button", {
              class: "icon-btn", type: "button", "aria-label": "Remove entry",
              onclick: () => {
                section.entries.splice(index, 1);
                markDirty();
                paint();
              },
            }, [icon("xmark", 16)]),
          ]),
        ]),
        entryFields(entry, labels),
      ]));
      body.replaceChildren(...rows, draftCard(section));
    }
    paint();
    return body;
  }

  const educationBody = entriesPanel(
    "EDUCATION",
    "This CV has no Education section yet.",
    ["School", "Location", "Degree", "Dates"],
  );
  const workBody = entriesPanel(
    "EXPERIENCE",
    "This CV has no Experience section yet.",
    ["Organisation", "Location", "Role", "Dates"],
  );

  // Same strip slot as Write's Job Description / Professional Summary / …
  const TABS = [
    ["bio", "Bio"],
    ["education", "Education"],
    ["work", "Work History"],
    ["base", "Base Files"],
  ];
  const panel = el("div", { class: "settings-panel" });

  function tabId() {
    const wanted = location.hash.slice(1);
    return TABS.some(([id]) => id === wanted) ? wanted : "bio";
  }

  function paintSteps(here) {
    const at = TABS.findIndex(([id]) => id === here);
    const links = el("div", { class: "steps-links" }, TABS.map(([id, label], index) => el("a", {
      class: index < at ? "step is-done" : "step",
      href: `#${id}`,
      "aria-current": id === here ? "step" : null,
      text: `${index + 1} ${label}`,
    })));
    const saveBtn = el("button", {
      class: "steps-score-item", type: "button", text: "Save",
      "aria-label": "Save settings",
      onclick: (event) => save(event.currentTarget),
    });
    $("steps").replaceChildren(links, el("div", { class: "steps-aside" }, [saveBtn]));
  }

  function paintTab() {
    const here = tabId();
    paintSteps(here);
    if (here === "bio") {
      panel.replaceChildren(
        el("header", { class: "page-head" }, [
          el("h1", { class: "title", text: "Bio" }),
          el("p", {
            class: "lead",
            text: "Your name and the contact lines that print on the CV and letter.",
          }),
        ]),
        field("Name", nameBox),
        el("div", { class: "settings-block-head settings-contacts-head" }, [
          el("h2", { class: "eyebrow", text: "Contacts" }),
          addContact,
        ]),
        el("p", {
          class: "meta settings-lead",
          text: "Toggle CV or Letter on each line to choose where it prints: under your name on the resume, or under the sign-off on the cover letter.",
        }),
        contactBody,
        el("div", { class: "settings-block-head settings-contacts-head" }, [
          el("h2", { class: "eyebrow", text: "Paper" }),
        ]),
        paperBody,
      );
    } else if (here === "education") {
      panel.replaceChildren(
        el("header", { class: "page-head" }, [
          el("h1", { class: "title", text: "Education" }),
          el("p", {
            class: "lead",
            text: "Schools and degrees on this application's CV. Bullet wording still happens in Write.",
          }),
        ]),
        educationBody,
      );
    } else if (here === "base") {
      panel.replaceChildren(
        el("header", { class: "page-head" }, [
          el("h1", { class: "title", text: "Base Files" }),
          el("p", { class: "lead", text: "Upload a file to replace what the model works from." }),
        ]),
        el("div", { class: "rows" }, Object.keys(BASE_FILES).map(baseFileRow)),
      );
    } else {
      panel.replaceChildren(
        el("header", { class: "page-head" }, [
          el("h1", { class: "title", text: "Work History" }),
          el("p", {
            class: "lead",
            text: "Employers, titles, and dates. Write still fills the bullets.",
          }),
        ]),
        workBody,
      );
    }
  }

  window.addEventListener("hashchange", paintTab);
  host.replaceChildren(panel);
  paintTab();
}

main();
