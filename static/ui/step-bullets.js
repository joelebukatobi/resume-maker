// Step 4, Bullets: every role on the CV, every bullet, one screen.
//
// A bullet opens in place to be edited, or swapped for another line: his
// past lines for that job, as the round would show the model (POST
// /api/library slot), or new ones from one bullets turn of the round. A
// swapped-out line stays in the list so it can be swapped back. Nothing is
// saved until Next, and then the whole CV is saved once.

import { api, el, icon, toast } from "./dom.js";

// Which of the posting's frozen terms a line carries, for the line under it.
export function covers(text, terms) {
  const line = String(text || "").toLowerCase();
  return (terms || []).filter((t) => t && line.includes(String(t).toLowerCase()));
}

function meta(text, terms, origin) {
  const hit = covers(text, terms);
  const parts = [];
  if (origin) parts.push(origin);
  parts.push(hit.length ? `covers ${hit.slice(0, 3).join(", ")}` : "covers nothing in the posting");
  return parts.join(" · ");
}

export async function drawBullets(ctx) {
  const host = ctx.host;
  const at = ctx.sectionIndex("EXPERIENCE");
  const terms = ctx.terms && ctx.terms.current ? ctx.terms.keywords : [];
  const foot = (extra) => el("div", { class: "step-foot" }, [
    el("a", { class: "btn", href: "#skills", text: "Back" }), ...extra,
  ]);
  if (at < 0) {
    host.replaceChildren(el("div", { class: "measure-list" }, [
      el("h1", { class: "title", text: "Bullets" }),
      el("p", { class: "lead", text: "This CV has no experience section." }),
      foot([el("a", { class: "btn btn-primary", href: "#additional", text: "Next" })]),
    ]));
    return;
  }

  const entries = ctx.documents.cv.sections[at].entries || [];
  // The roles on this CV, each with its bullets and the lines it could use.
  const roles = entries
    .map((entry, index) => ({ entry, index, path: `sections.${at}.entries.${index}` }))
    .filter((role) => !role.entry.off)
    .map((role) => ({
      ...role,
      lines: [...(role.entry.bullets || [])],
      others: [],           // [{ text, origin }]
      room: (role.entry.bullets || []).length || 3,
      loading: true,
    }));

  let open = null;          // "role:bullet" of the bullet being edited
  let dirty = false;
  const body = el("div", { class: "roles" });

  // The frame's own fields: sub_left is the title, left the employer,
  // sub_right the dates, right the place.
  function title(entry) {
    return [entry.sub_left, entry.left].filter(Boolean).join(" — ") || "Role";
  }

  function when(entry) {
    return [entry.sub_right, entry.right].filter(Boolean).join(" · ");
  }

  function swap(role, bullet, other) {
    const was = role.lines[bullet];
    role.lines[bullet] = other.text;
    role.others = role.others.filter((o) => o !== other);
    if (was && was.trim()) role.others.unshift({ text: was, origin: "Swapped out" });
    dirty = true;
    paint();
  }

  function row(role, r, bullet) {
    const key = `${r}:${bullet}`;
    const text = role.lines[bullet] || "";
    const isOpen = open === key;
    const toggle = el("button", {
      class: "icon-btn", type: "button",
      "aria-label": isOpen ? "Close this bullet" : "Edit or swap this bullet",
      "aria-expanded": String(isOpen),
      onclick: () => { open = isOpen ? null : key; paint(); },
    }, [icon(isOpen ? "up" : "down")]);

    const head = el("div", { class: "bullet-row" }, [
      el("span", { class: text.trim() ? "marker" : "marker is-empty", "aria-hidden": "true" }),
      el("div", { class: "bullet-body" }, isOpen
        ? [(() => {
          const box = el("textarea", { class: "input", rows: String(Math.min(8, Math.max(2, Math.ceil(text.length / 80) + 1))), "aria-label": "This bullet" });
          box.value = text;
          box.addEventListener("input", () => { role.lines[bullet] = box.value; dirty = true; });
          box.addEventListener("change", () => paint());
          return box;
        })(), el("p", { class: "meta", text: meta(text, terms) })]
        : text.trim()
          ? [el("p", { class: "bullet-text", text }), el("p", { class: "meta", text: meta(text, terms) })]
          : [el("p", { class: "bullet-text is-empty", text: "Empty. Open it to pick a line or write one." })]),
      toggle,
    ]);
    if (!isOpen) return el("div", { class: "bullet" }, [head]);

    const choices = role.others.filter((o) => !role.lines.includes(o.text));
    const list = el("div", { class: "alts" }, [
      el("p", { class: "eyebrow", text: "Others for this job" }),
      ...(role.loading
        ? [el("p", { class: "meta", text: "Loading your past lines…" })]
        : choices.length
          ? choices.map((other) => el("div", { class: "alt" }, [
            el("div", { class: "alt-body" }, [
              el("p", { class: "alt-text", text: other.text }),
              el("p", { class: "meta", text: meta(other.text, terms, other.origin) }),
            ]),
            el("button", {
              class: "icon-btn", type: "button", "aria-label": "Use this line",
              onclick: () => swap(role, bullet, other),
            }, [icon("check")]),
          ]))
          : [el("p", { class: "meta", text: "No other lines for this job yet." })]),
    ]);
    return el("div", { class: "bullet is-expanded" }, [head, list]);
  }

  function roleBlock(role, r) {
    const ask = el("button", { class: "btn btn-sm", type: "button", text: "Write new lines" });
    ask.addEventListener("click", async () => {
      ask.disabled = true;
      ask.textContent = "Writing…";
      try {
        const found = (await ctx.askSection("bullets", { entry: role.path }))
          .filter((p) => typeof p.text === "string" && p.text.trim()
            && p.path.startsWith(`${role.path}.bullets.`));
        if (!found.length) throw new Error("No new lines came back. Try again.");
        for (const p of found) role.others.unshift({ text: p.text.trim(), origin: "New, from your notes" });
        toast(`${found.length} new ${found.length === 1 ? "line" : "lines"} for this job.`);
        paint();
      } catch (error) {
        toast(error.message, "error");
        ask.disabled = false;
        ask.textContent = "Write new lines";
      }
    });
    const count = Math.max(role.room, role.lines.length);
    return el("section", { class: "role" }, [
      el("div", { class: "role-head" }, [
        el("div", {}, [
          el("p", { class: "role-name", text: title(role.entry) }),
          el("p", { class: "meta", text: [when(role.entry), `room for ${role.room}`].filter(Boolean).join(" · ") }),
        ]),
        ask,
      ]),
      el("div", { class: "bullets" }, Array.from({ length: count }, (_, b) => row(role, r, b))),
    ]);
  }

  function paint() {
    body.replaceChildren(...roles.map(roleBlock));
  }

  const nextButton = el("button", { class: "btn btn-primary", type: "button", text: "Next" });
  nextButton.addEventListener("click", async () => {
    nextButton.disabled = true;
    try {
      if (dirty) {
        for (const role of roles) {
          role.entry.bullets = role.lines.map((t) => String(t || "").trim()).filter(Boolean);
        }
        await ctx.saveCv();
        dirty = false;
      }
      ctx.next("additional");
    } catch (error) {
      toast(error.message, "error");
      nextButton.disabled = false;
    }
  });
  window.onbeforeunload = (event) => { if (dirty) event.preventDefault(); };

  host.replaceChildren(el("div", { class: "measure-list" }, [
    el("header", { class: "page-head" }, [
      el("h1", { class: "title", text: "Bullets" }),
      el("p", { class: "lead", text: "Open a line to edit it, or swap in another." }),
    ]),
    body,
    foot([nextButton, el("a", { class: "btn", href: "#additional", text: "Skip" })]),
  ]));
  paint();

  // His past lines for each job, one read per job, as the round would rank them.
  for (const role of roles) {
    try {
      const slot = await api("/api/library", {
        action: "slot", phase: "bullets", entry: role.path, app: ctx.app,
        job_description: ctx.history.job_description,
      });
      // The room the round gives this job, from the base CV and its budget.
      role.room = slot.room || role.room;
      for (const text of slot.lines || []) {
        const line = text.trim();
        if (line && !role.lines.includes(line) && !role.others.some((o) => o.text === line)) {
          role.others.push({ text: line, origin: "From a past CV" });
        }
      }
    } catch (error) {
      toast(`Could not load past lines for ${title(role.entry)}: ${error.message}`, "error");
    }
    role.loading = false;
    paint();
  }
}
