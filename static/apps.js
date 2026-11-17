"use strict";
/* The application switcher in the top bar, on every page.
 *
 * An application is one job you are applying to: its own CV, cover letter,
 * themes and chat, under its own URL — /meta-rotational-pm/resume. Switching
 * is a navigation; the server also records which one was opened last, so the
 * bare /resume and /cover-letter paths know where to send you.
 *
 * Self-contained: no page's globals are used, because the hub has none.
 */

(() => {
  const node = (tag, attrs = {}, kids = []) => {
    const element = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === "text") element.textContent = value;
      else if (key === "class") element.className = value;
      else if (key.startsWith("on")) element.addEventListener(key.slice(2), value);
      else if (value != null && value !== false) element.setAttribute(key, value);
    }
    for (const kid of kids) if (kid) element.append(kid);
    return element;
  };

  // Iconoir "more-vert" (MIT, https://iconoir.com), drawn here because the
  // hub carries no icon sprite of its own.
  function kebab() {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", "ico");
    svg.setAttribute("aria-hidden", "true");
    for (const y of ["5", "12", "19"]) {
      const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      dot.setAttribute("cx", "12");
      dot.setAttribute("cy", y);
      dot.setAttribute("r", "1.5");
      dot.setAttribute("fill", "currentColor");
      svg.append(dot);
    }
    return svg;
  }

  const list = document.getElementById("app-list");
  if (!list) return;
  let open = null; // the application currently being worked on

  async function call(payload) {
    const response = await fetch("/api/apps", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error((await response.text()).trim() || "request failed");
    return response.json();
  }

  // Each application has its own URL, so switching is a navigation.
  const go = (slug) => { window.location.href = `/${encodeURIComponent(slug)}`; };
  const reload = () => window.location.reload();

  function row(app) {
    const live = !app.archived;
    // The row itself is the primary action: open the application. Everything
    // else is rare or destructive, so it sits behind one menu with words on
    // it — four unlabelled icons per row would be a guessing game.
    const item = (text, onclick) => node("button", { class: "menu-item", text, onclick });
    const menu = node("details", { class: "row-menu" }, [
      node("summary", { "aria-label": `More actions for ${app.name}`, title: "More" }, [kebab()]),
      node("div", { class: "menu-sheet" }, [
        item("Rename", () => {
          const name = window.prompt("Name this application", app.name);
          if (name) call({ action: "rename", slug: app.slug, name }).then(load);
        }),
        item("Duplicate", () => {
          const name = window.prompt("Name the copy", `${app.name} (copy)`);
          if (name) call({ action: "create", name, source: app.slug })
            .then((r) => go(r.application.slug));
        }),
        item(`Track: ${app.track || "untagged"}`, () => {
          const chosen = window.prompt(
            "Track: Product, Engineering, Data, Design or Other", app.track || "");
          if (chosen === null) return;
          call({ action: "track", slug: app.slug, track: chosen.trim() })
            .then(load)
            .catch((error) => window.alert(error.message));
        }),
        item(live ? "Archive" : "Restore", () => {
          call({ action: live ? "archive" : "restore", slug: app.slug })
            .then(app.slug === open && live ? reload : load);
        }),
        item("Delete", () => {
          // Irreversible: archive is the reversible choice, so say exactly
          // what goes, and send the hub home if it was the open one.
          if (!window.confirm(`Delete "${app.name}" for good? Its CV, cover letter and chat are removed and cannot be recovered.`)) {
            return;
          }
          call({ action: "delete", slug: app.slug }).then(() => {
            if (app.slug === open) window.location.href = "/";
            else load();
          });
        }),
      ]),
    ]);
    const label = live
      ? node("a", { class: "app-row-name", href: `/${encodeURIComponent(app.slug)}`,
          onclick: () => call({ action: "open", slug: app.slug }) }, [
          document.createTextNode(app.name)])
      : node("span", { class: "app-row-name", text: app.name });
    return node("div", { class: `app-row${app.slug === open ? " current" : ""}` }, [
      label,
      app.track ? node("span", { class: "app-row-track", text: app.track }) : null,
      app.job_url
        ? node("a", { class: "app-row-link", href: app.job_url, target: "_blank",
            rel: "noopener noreferrer", text: "Job post" })
        : null,
      menu,
    ]);
  }

  // The list shows as many rows as fit the space the hub leaves it, and
  // pages through the rest. The page survives a reload within the tab.
  const pager = document.getElementById("app-pager");
  let rows = [];
  let page = 0;
  try { page = Number(sessionStorage.getItem("apps-page")) || 0; } catch { /* page one */ }

  function remember() {
    try { sessionStorage.setItem("apps-page", String(page)); } catch { /* not kept */ }
  }

  function perPage() {
    if (!pager || !rows.length) return rows.length || 1;
    list.textContent = "";
    list.append(rows[0]);
    const gap = parseFloat(getComputedStyle(list).rowGap) || 0;
    const tall = rows[0].getBoundingClientRect().height + gap;
    list.textContent = "";
    return Math.max(1, Math.floor((list.clientHeight + gap) / (tall || 1)));
  }

  function showPage() {
    // Measured with the pager showing: it takes its height from the list, and
    // measuring without it left the last row of the first page cut off.
    if (pager) pager.hidden = false;
    const size = perPage();
    const pages = Math.max(1, Math.ceil(rows.length / size));
    page = Math.min(Math.max(0, page), pages - 1);
    remember();
    list.textContent = "";
    for (const r of rows.slice(page * size, (page + 1) * size)) list.append(r);
    if (!pager) return;
    pager.hidden = pages <= 1;
    pager.textContent = "";
    const step = (text, to, disabled) => node("button", {
      class: "secondary", text, disabled: disabled || null,
      onclick: () => { page = to; showPage(); } });
    pager.append(step("Previous", page - 1, page === 0),
                 node("span", { text: `Page ${page + 1} of ${pages}` }),
                 step("Next", page + 1, page >= pages - 1));
  }

  function paint(state) {
    open = state.current;
    const apps = state.applications || [];
    // The hub has no title of its own: the list below is the point of it.
    const live = apps.filter((a) => !a.archived);
    const archived = apps.filter((a) => a.archived);
    rows = live.map(row);
    if (archived.length) {
      rows.push(node("div", { class: "app-divider", text: "Archived" }));
      rows.push(...archived.map(row));
    }
    showPage();
  }

  let resizing;
  window.addEventListener("resize", () => {
    clearTimeout(resizing);
    resizing = setTimeout(() => { if (rows.length) showPage(); }, 120);
  });

  async function load() {
    try {
      paint(await fetch("/api/apps").then((r) => r.json()));
    } catch (error) {
      list.textContent = `Could not read your applications: ${error.message}`;
    }
  }

  // New application: it starts from the base CV and letter, and the job
  // description you paste on the next screen names it and builds its CV.
  document.getElementById("app-new").addEventListener("click", async () => {
    try {
      const made = await call({ action: "create" });
      go(made.application.slug);
    } catch (error) {
      list.textContent = `Could not create it: ${error.message}`;
    }
  });

  // Native <details> has no "close the others" behaviour of its own.
  document.addEventListener("click", (event) => {
    for (const open of document.querySelectorAll("details.row-menu[open]")) {
      if (!open.contains(event.target)) open.removeAttribute("open");
    }
  });

  load();
})();
