// Step 4, Bullets: cast first, then write every on-CV role's bullets.
//
// Cast: rank roles from past lines vs frozen terms; he toggles On / Off; Next.
//
// Then, without chat accepts: one bullets turn per on role (JD + notes + voice
// + past CVs), proposals auto-applied, progress under the step strip. He
// corrects by hand here, or later in Review chat. Refresh re-runs the fill.

import { api, el, icon, stepAction, stepSkeleton, toast } from "./dom.js";

// Settled per application for this Write visit; Back from edit reopens cast.
let castSettledFor = "";

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

function title(entry) {
  return [entry.sub_left, entry.left].filter(Boolean).join(" - ") || "Role";
}

function when(entry) {
  return [entry.sub_right, entry.right].filter(Boolean).join(" · ");
}

function foot(extra) {
  return el("div", { class: "step-foot" }, [
    stepAction("a", { href: "#skills", "aria-label": "Back" }, "left"), ...extra,
  ]);
}

export async function drawBullets(ctx) {
  const host = ctx.host;
  const at = ctx.sectionIndex("EXPERIENCE");
  if (at < 0) {
    host.replaceChildren(el("div", { class: "measure-list" }, [
      el("h1", { class: "title", text: "Work History" }),
      el("p", { class: "lead", text: "This CV has no experience section." }),
      foot([stepAction("a", { href: "#additional", "aria-label": "Next" }, "right")]),
    ]));
    return;
  }

  const entries = ctx.documents.cv.sections[at].entries || [];
  if (castSettledFor !== ctx.app) {
    await drawCast(ctx, at, entries);
    return;
  }
  await drawEdit(ctx, at, entries);
}

async function drawCast(ctx, at, entries) {
  const host = ctx.host;
  const body = el("div", { class: "cast-list" });
  const nextButton = stepAction("button", { type: "button", "aria-label": "Next" }, "right");
  nextButton.disabled = true;

  host.replaceChildren(el("div", { class: "measure-list" }, [
    el("header", { class: "page-head" }, [
      el("h1", { class: "title", text: "Work History" }),
      el("p", {
        class: "lead",
        text: "Which jobs print on this CV. Ranked from your past lines against the posting.",
      }),
    ]),
    body,
    foot([nextButton]),
  ]));
  body.replaceChildren(el("p", { class: "meta", text: "Ranking roles against the posting…" }));

  let roles = [];
  try {
    const got = await api("/api/library", {
      action: "cast", app: ctx.app,
      job_description: ctx.history.job_description,
    });
    roles = (got.roles || []).map((row) => {
      const entry = entries[row.index];
      return {
        entry,
        index: row.index,
        path: row.path,
        hits: row.hits || 0,
        total: row.total || 0,
        // Start from the application's recommendation, not the last save.
        on: !row.recommend_off,
        recommend_off: !!row.recommend_off,
        was_off: !!row.off,
      };
    }).filter((role) => role.entry);
  } catch (error) {
    toast(error.message, "error");
    // Fall back to whatever is already on the CV so he is not stuck.
    roles = entries.map((entry, index) => ({
      entry, index, path: `sections.${at}.entries.${index}`,
      hits: 0, total: 0, on: !entry.off, recommend_off: !!entry.off, was_off: !!entry.off,
    }));
  }

  function paint() {
    if (!roles.length) {
      body.replaceChildren(el("p", { class: "meta", text: "No experience roles on this CV." }));
      return;
    }
    body.replaceChildren(...roles.map((role) => {
      const meta = [];
      if (role.total) {
        meta.push(`answers ${role.hits} of ${role.total}`);
      }
      const matches = role.on === !role.recommend_off;
      if (matches && role.on) meta.push("recommended");
      else if (!matches) meta.push(role.recommend_off ? "recommended off" : "recommended on");
      const toggle = el("button", {
        class: "switch", type: "button", role: "switch",
        "aria-checked": String(role.on),
        "aria-label": role.on ? `Keep ${title(role.entry)} on the CV` : `Put ${title(role.entry)} on the CV`,
        onclick: () => { role.on = !role.on; paint(); },
      });
      return el("div", { class: `cast-row${role.on ? "" : " is-off"}` }, [
        el("div", { class: "cast-body" }, [
          el("p", { class: "role-name", text: title(role.entry) }),
          el("p", {
            class: "meta",
            text: [when(role.entry), ...meta].filter(Boolean).join(" · "),
          }),
        ]),
        toggle,
      ]);
    }));
  }

  paint();
  nextButton.disabled = false;
  nextButton.addEventListener("click", async () => {
    if (roles.length && !roles.some((r) => r.on)) {
      toast("Keep at least one role on the CV.", "error");
      return;
    }
    nextButton.disabled = true;
    try {
      for (const role of roles) {
        if (role.on) delete role.entry.off;
        else role.entry.off = true;
      }
      await ctx.saveCv();
      castSettledFor = ctx.app;
      await drawBullets(ctx);
    } catch (error) {
      toast(error.message, "error");
      nextButton.disabled = false;
    }
  });
}

async function drawEdit(ctx, at, entries) {
  const host = ctx.host;
  const terms = ctx.terms && ctx.terms.current ? ctx.terms.keywords : [];
  const roles = entries
    .map((entry, index) => ({ entry, index, path: `sections.${at}.entries.${index}` }))
    .filter((role) => !role.entry.off)
    .map((role) => ({
      ...role,
      lines: [...(role.entry.bullets || [])],
      others: [],
      room: (role.entry.bullets || []).length || 3,
      pending: false,  // skeleton only while a fill turn is running for this role
      loading: false,
    }));

  let open = null;
  let dirty = false;
  const body = el("div", { class: "roles" });
  function markDirty() {
    dirty = true;
    if (ctx.touchDraft) ctx.touchDraft();
  }

  function swap(role, bullet, other) {
    const was = role.lines[bullet];
    role.lines[bullet] = other.text;
    role.others = role.others.filter((o) => o !== other);
    if (was && was.trim()) role.others.unshift({ text: was, origin: "Swapped out" });
    markDirty();
    paint();
  }

  function addBullet(role, r) {
    role.lines.push("");
    markDirty();
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
    markDirty();
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
          box.addEventListener("input", () => {
            role.lines[bullet] = box.value;
            markDirty();
          });
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

  // Same as clicking Accept on every proposal in the old chat: put each
  // returned line on its bullet path; anything else stays under Others.
  function acceptProposals(role, found) {
    const byIndex = [];
    const extras = [];
    for (const p of found) {
      const text = String(p.text || "").trim();
      if (!text || !p.path.startsWith(`${role.path}.bullets.`)) continue;
      const index = Number(p.path.slice(`${role.path}.bullets.`.length));
      if (Number.isInteger(index) && index >= 0) byIndex[index] = text;
      else extras.push(text);
    }
    // The room is fixed (Amazon 3, every other role 2); a line past it is dropped.
    const room = role.room || byIndex.length;
    while (role.lines.length < room) role.lines.push("");
    for (let i = 0; i < room; i += 1) {
      const next = byIndex[i];
      if (!next) continue;
      const was = String(role.lines[i] || "").trim();
      if (was && was !== next
          && !role.others.some((o) => o.text === was)
          && !role.lines.includes(was)) {
        role.others.unshift({ text: was, origin: "Previous" });
      }
      role.lines[i] = next;
      markDirty();
    }
    for (const text of extras) {
      if (!role.lines.includes(text) && !role.others.some((o) => o.text === text)) {
        role.others.unshift({ text, origin: "Recommended" });
      }
    }
    return byIndex.filter(Boolean).length;
  }

  function roleBlock(role, r) {
    const count = Math.max(role.room, role.lines.length);
    return el("section", { class: "role" }, [
      el("div", { class: "role-head" }, [
        el("div", { class: "role-title-row" }, [
          el("p", { class: "role-name", text: title(role.entry) }),
          role.pending ? null : el("button", {
            class: "icon-btn icon-btn-round", type: "button", "aria-label": "Add a bullet",
            onclick: () => addBullet(role, r),
          }, [icon("plus", 18)]),
        ]),
        el("p", {
          class: "meta",
          text: role.pending
            ? [when(role.entry), "writing…"].filter(Boolean).join(" · ")
            : [when(role.entry), `room for ${role.room}`].filter(Boolean).join(" · "),
        }),
      ]),
      role.pending
        ? stepSkeleton(Math.max(2, Math.min(role.room || 3, 4)))
        : el("div", { class: "bullets" }, [
          ...Array.from({ length: count }, (_, b) => row(role, r, b)),
        ]),
    ]);
  }

  function paint() {
    if (!roles.length) {
      body.replaceChildren(el("p", {
        class: "meta",
        text: "No roles are on this CV. Go back to turn one on.",
      }));
      return;
    }
    body.replaceChildren(...roles.map(roleBlock));
  }

  const backButton = stepAction("button", { type: "button", "aria-label": "Back" }, "left");
  backButton.addEventListener("click", () => {
    castSettledFor = "";
    drawBullets(ctx);
  });
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

  const hasLines = roles.some((role) => role.lines.some((line) => String(line || "").trim()));
  const lead = el("p", {
    class: "lead",
    text: hasLines
      ? "Recommended for this posting. Open a line to change it, or swap in another."
      : "Nothing written yet. Refresh to draft from your notes and past CVs.",
  });
  host.replaceChildren(el("div", { class: "measure-list" }, [
    el("header", { class: "page-head" }, [
      el("h1", { class: "title", text: "Work History" }),
      lead,
    ]),
    body,
    el("div", { class: "step-foot" }, [
      backButton,
      nextButton,
      ctx.sectionRefresh(),
      stepAction("a", { href: "#additional", "aria-label": "Skip" }, "skip"),
    ]),
  ]));
  paint();

  // Pico's accept-accept-accept, without the clicks. Only on reload or the
  // foot refresh — not when he just opens the Bullets tab.
  async function fillAll() {
    if (!roles.length) {
      lead.textContent = "No roles are on this CV.";
      return;
    }
    lead.textContent = "Writing bullets for this posting from your notes and past CVs.";
    nextButton.disabled = true;
    for (const role of roles) {
      role.pending = true;
      role.loading = false;
      role.others = [];
    }
    paint();
    if (ctx.setStepProgress) ctx.setStepProgress(0, 1);
    let placedTotal = 0;
    // Past lines for every role at once (no model), then one turn for all of
    // them: the notes, Playbook and CV are sent once, not once per role.
    await Promise.all(roles.map(async (role) => {
      try {
        const slot = await api("/api/library", {
          action: "slot", phase: "bullets", entry: role.path, app: ctx.app,
          job_description: ctx.history.job_description,
        });
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
    }));
    try {
      const found = await ctx.askSection("bullets_all", { entries: roles.map((role) => role.path) });
      for (const role of roles) {
        const placed = acceptProposals(role, found);
        placedTotal += placed;
        if (!placed) toast(`No new lines came back for ${title(role.entry)}.`, "error");
      }
    } catch (error) {
      toast(error.message, "error");
    }
    for (const role of roles) role.pending = false;
    if (ctx.setStepProgress) ctx.setStepProgress(1, 1);
    paint();
    if (placedTotal) {
      toast(`${placedTotal} ${placedTotal === 1 ? "line" : "lines"} placed across ${roles.length} ${roles.length === 1 ? "role" : "roles"}.`);
    }
    try {
      if (dirty) {
        for (const role of roles) {
          role.entry.bullets = role.lines.map((t) => String(t || "").trim()).filter(Boolean);
        }
        await ctx.saveCv();
        dirty = false;
      }
    } catch (error) {
      toast(error.message, "error");
    }
    lead.textContent = "Recommended for this posting. Open a line to change it, or swap in another.";
    nextButton.disabled = false;
    if (ctx.setStepProgress) ctx.setStepProgress(0, 0);
  }

  ctx.setRefresh(fillAll);
  const empty = roles.every((role) => !role.lines.some((line) => String(line || "").trim()));
  if (ctx.consumeAutoFill && ctx.consumeAutoFill("bullets") && empty) await fillAll();
  else {
    // Past lines for swap, no model turn.
    for (const role of roles) {
      try {
        const slot = await api("/api/library", {
          action: "slot", phase: "bullets", entry: role.path, app: ctx.app,
          job_description: ctx.history.job_description,
        });
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
    }
    paint();
  }
}
