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
        item(live ? "Archive" : "Restore", () => {
          call({ action: live ? "archive" : "restore", slug: app.slug })
            .then(app.slug === open && live ? reload : load);
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
      app.job_url
        ? node("a", { class: "app-row-link", href: app.job_url, target: "_blank",
            rel: "noopener noreferrer", text: "Job post" })
        : null,
      menu,
    ]);
  }

  function paint(state) {
    open = state.current;
    const apps = state.applications || [];
    const current = apps.find((a) => a.slug === open);
    // The hub has no title of its own: the list below is the point of it.
    list.textContent = "";
    const live = apps.filter((a) => !a.archived);
    const archived = apps.filter((a) => a.archived);
    for (const app of live) list.append(row(app));
    if (archived.length) {
      list.append(node("div", { class: "app-divider", text: "Archived" }));
      for (const app of archived) list.append(row(app));
    }
  }

  async function load() {
    try {
      paint(await fetch("/api/apps").then((r) => r.json()));
    } catch (error) {
      list.textContent = `Could not read your applications: ${error.message}`;
    }
  }

  // New application: the documents come from the one you were last on, and
  // the name comes from the job description you paste on the next screen.
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
