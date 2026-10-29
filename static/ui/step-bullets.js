// Step 4, Bullets: every role on the CV, every bullet, one screen.
//
// A bullet opens in place to be edited, or swapped for another line: his
// past lines for that job, as the round would show the model (POST
// /api/library slot), or new ones from one bullets turn of the round. A
// swapped-out line stays in the list so it can be swapped back. Nothing is
// saved until Next, and then the whole CV is saved once.

import { api, el, icon, stepAction, toast } from "./dom.js";

// Which of the posting's frozen terms a line carries, for the line under it.
export function covers(text, terms) {
  const line = String(text || "").toLowerCase();
  return (terms || []).filter((t) => t && line.includes(String(t).toLowerCase()));
}

function coverageMeta(text, terms, origin) {
  const hit = covers(text, terms);
  const kids = [];
  if (origin) kids.push(el("span", { class: "meta", text: origin }));
  if (hit.length) {
    kids.push(el("span", { class: "meta", text: `covers ${hit.slice(0, 3).join(", ")}` }));
  } else {
    kids.push(el("span", {
      class: "meta is-gap",
      text: "covers nothing in the posting",
    }));
  }
  return el("p", { class: "bullet-meta" }, kids.flatMap((node, i) => (
    i ? [el("span", { class: "meta", text: "·" }), node] : [node]
  )));
}

export async function drawBullets(ctx) {
  const host = ctx.host;
  const at = ctx.sectionIndex("EXPERIENCE");
  const terms = ctx.terms && ctx.terms.current ? ctx.terms.keywords : [];
  const foot = (extra) => el("div", { class: "step-foot" }, [
    stepAction("a", { href: "#skills", "aria-label": "Back" }, "left"), ...extra,
  ]);
  if (at < 0) {
    host.replaceChildren(el("div", { class: "measure-list" }, [
      el("h1", { class: "title", text: "Bullets" }),
      el("p", { class: "lead", text: "This CV has no experience section." }),
      foot([stepAction("a", { href: "#additional", "aria-label": "Next" }, "right")]),
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
    return [entry.sub_left, entry.left].filter(Boolean).join(" - ") || "Role";
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

  function addBullet(role, r) {
    role.lines.push("");
    dirty = true;
    open = `${r}:${role.lines.length - 1}`;
    paint();
  }

  function moveBullet(role, r, bullet, dir) {
    const to = bullet + dir;
    if (to < 0 || to >= role.lines.length) return;
    const lines = role.lines;
    const held = lines[bullet];
    lines[bullet] = lines[to];
    lines[to] = held;
    dirty = true;
    if (open === `${r}:${bullet}`) open = `${r}:${to}`;
    else if (open === `${r}:${to}`) open = `${r}:${bullet}`;
    paint();
  }

  function row(role, r, bullet) {
    const key = `${r}:${bullet}`;
    const text = role.lines[bullet] || "";
    const isOpen = open === key;
    const toggle = el("button", {
      class: "icon-btn icon-btn-round", type: "button",
      "aria-label": isOpen ? "Close this bullet" : "Edit or swap this bullet",
      "aria-expanded": String(isOpen),
      onclick: () => { open = isOpen ? null : key; paint(); },
    }, [icon(isOpen ? "up" : "down", 18)]);

    const actions = el("div", { class: "bullet-actions" }, [
      el("button", {
        class: "icon-btn icon-btn-round", type: "button", "aria-label": "Move bullet up",
        disabled: bullet === 0,
        onclick: () => moveBullet(role, r, bullet, -1),
      }, [icon("arrow-up", 13, 2)]),
      el("button", {
        class: "icon-btn icon-btn-round", type: "button", "aria-label": "Move bullet down",
        disabled: bullet >= role.lines.length - 1,
        onclick: () => moveBullet(role, r, bullet, 1),
      }, [icon("arrow-down", 13, 2)]),
      toggle,
    ]);

    const head = el("div", { class: "bullet-row" }, [
      el("span", { class: text.trim() ? "marker" : "marker is-empty", "aria-hidden": "true" }),
      el("div", { class: "bullet-body" }, isOpen
        ? [(() => {
          const box = el("textarea", {
            class: "input bullet-own",
            rows: String(Math.min(8, Math.max(2, Math.ceil(text.length / 80) + 1))),
            "aria-label": "This bullet",
          });
          box.value = text;
          box.addEventListener("input", () => { role.lines[bullet] = box.value; dirty = true; });
          box.addEventListener("change", () => paint());
          return box;
        })(), coverageMeta(text, terms)]
        : text.trim()
          ? [el("p", { class: "bullet-text", text }), coverageMeta(text, terms)]
          : [el("p", { class: "bullet-text is-empty", text: "Empty. Open it to pick a line or write one." })]),
      actions,
    ]);
    if (!isOpen) return el("div", { class: "bullet" }, [head]);

    const choices = role.others.filter((o) => !role.lines.includes(o.text));
    const list = el("div", { class: "alts" }, [
      el("p", { class: "eyebrow", text: "Others for this job" }),
      ...(role.loading
        ? [el("p", { class: "meta", text: "Loading your past lines…" })]
        : choices.length
          ? choices.map((other) => el("div", { class: "alt" }, [
            el("span", { class: "alt-gutter", "aria-hidden": "true" }),
            el("div", { class: "alt-body" }, [
              el("p", { class: "alt-text", text: other.text }),
              coverageMeta(other.text, terms, other.origin),
            ]),
            el("div", { class: "alt-actions" }, [
              el("button", {
                class: "icon-btn icon-btn-round", type: "button",
                "aria-label": "Use this line",
                onclick: () => swap(role, bullet, other),
              }, [icon("check", 18)]),
            ]),
          ]))
          : [el("p", { class: "meta", text: "No other lines for this job yet." })]),
    ]);
    return el("div", { class: "bullet is-expanded" }, [head, list]);
  }

  function takeProposals(role, found, origin) {
    let placed = 0;
    for (const p of found) {
      const text = String(p.text || "").trim();
      if (!text || !p.path.startsWith(`${role.path}.bullets.`)) continue;
      const index = Number(p.path.slice(`${role.path}.bullets.`.length));
      if (Number.isInteger(index) && index >= 0) {
        while (role.lines.length <= index) role.lines.push("");
        if (!String(role.lines[index] || "").trim()) {
          role.lines[index] = text;
          placed += 1;
          dirty = true;
          continue;
        }
      }
      if (!role.lines.includes(text) && !role.others.some((o) => o.text === text)) {
        role.others.unshift({ text, origin });
      }
    }
    return placed;
  }

  async function proposeRole(role) {
    try {
      const found = await ctx.askSection("bullets", { entry: role.path });
      const placed = takeProposals(role, found, "Recommended");
      if (!found.length) throw new Error("No new lines came back. Try again.");
      if (placed) toast(`${placed} ${placed === 1 ? "line" : "lines"} recommended for this job.`);
      else toast(`${found.length} other ${found.length === 1 ? "line" : "lines"} for this job.`);
      paint();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function roleBlock(role, r) {
    const count = Math.max(role.room, role.lines.length);
    return el("section", { class: "role" }, [
      el("div", { class: "role-head" }, [
        el("div", { class: "role-title-row" }, [
          el("p", { class: "role-name", text: title(role.entry) }),
          el("button", {
            class: "icon-btn icon-btn-round", type: "button", "aria-label": "Add a bullet",
            onclick: () => addBullet(role, r),
          }, [icon("plus", 18)]),
        ]),
        el("p", { class: "meta", text: [when(role.entry), `room for ${role.room}`].filter(Boolean).join(" · ") }),
      ]),
      el("div", { class: "bullets" }, [
        ...Array.from({ length: count }, (_, b) => row(role, r, b)),
      ]),
    ]);
  }

  function paint() {
    body.replaceChildren(...roles.map(roleBlock));
  }

  const nextButton = stepAction("button", { type: "button", "aria-label": "Next" }, "right");
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
      el("p", { class: "lead", text: "Recommended for this posting. Open a line to change it, or swap in another." }),
    ]),
    body,
    foot([
      nextButton,
      stepAction("a", { href: "#additional", "aria-label": "Skip" }, "skip"),
    ]),
  ]));
  paint();

  // His past lines for each job, then the same bullets turn the chat ran on
  // an empty role. Lines stay off the saved CV until Next.
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
          role.others.push({ text: line, origin: "Past" });
        }
      }
    } catch (error) {
      toast(`Could not load past lines for ${title(role.entry)}: ${error.message}`, "error");
    }
    role.loading = false;
    paint();
    if (!role.lines.some((line) => String(line || "").trim())) await proposeRole(role);
  }
}
