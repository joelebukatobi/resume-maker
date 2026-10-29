// <app-nav current="voice">: the top bar on every redesigned page.
//
// Light DOM, so the shared atoms style it. Children marked data-slot="middle"
// sit centred (a page's search); every other child is an action on the right.
// The theme button sits last and remembers the choice in this browser.

import { el, icon } from "./dom.js";

const LINKS = [
  ["applications", "/", "Applications"],
  ["voice", "/voice", "Voice"],
  ["notes", "/notes", "Notes"],
];

function currentTheme() {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

class AppNav extends HTMLElement {
  connectedCallback() {
    if (this.built) return;
    this.built = true;
    const middle = [...this.children].filter((c) => c.dataset.slot === "middle");
    const actions = [...this.children].filter((c) => c.dataset.slot !== "middle");
    const here = this.getAttribute("current");

    this.setAttribute("role", "navigation");
    this.setAttribute("aria-label", "Main");

    const links = el("div", { class: "nav-links" }, [
      el("a", { class: "wordmark", href: "/", text: "Resume Maker" }),
      ...LINKS.map(([id, href, label]) => el("a", {
        class: "nav-link", href, text: label, "aria-current": id === here ? "page" : null,
      })),
    ]);

    this.toggle = el("button", {
      class: "icon-btn theme-toggle", type: "button", onclick: () => this.flip(),
    });
    this.paintToggle();

    this.replaceChildren(
      links,
      el("div", { class: "nav-middle" }, middle),
      el("div", { class: "nav-actions" }, [...actions, this.toggle]),
    );
  }

  flip() {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("rm-theme", next); } catch (e) { /* not kept */ }
    this.paintToggle();
  }

  paintToggle() {
    const dark = currentTheme() === "dark";
    const label = dark ? "Switch to light mode" : "Switch to dark mode";
    this.toggle.setAttribute("aria-label", label);
    this.toggle.title = label;
    this.toggle.replaceChildren(icon(dark ? "sun" : "moon"));
  }
}

customElements.define("app-nav", AppNav);
