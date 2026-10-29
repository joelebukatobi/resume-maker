// <app-nav current="voice">: the top bar on every redesigned page.
//
// Light DOM, so the shared atoms style it. Children marked data-slot="middle"
// sit centred (a page's search); every other child is an action on the right.
// The theme button sits last and remembers the choice in this browser.
// Models opens the provider/model dialog next to My Experience.

import { el, icon } from "./dom.js";
import { openModels } from "./models.js";

const LINKS = [
  ["applications", "/", "Applications"],
  ["voice", "/voice", "Tone"],
  ["notes", "/notes", "My Experience"],
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
      ...LINKS.map(([id, href, label]) => el("a", {
        class: "nav-link", href, text: label, "aria-current": id === here ? "page" : null,
      })),
      el("button", {
        class: "nav-link", type: "button", text: "Models",
        onclick: () => openModels(),
      }),
    ]);

    this.toggle = el("button", {
      class: "icon-btn theme-toggle", type: "button", onclick: () => this.flip(),
    });
    this.paintToggle();

    const profile = el("a", {
      class: "nav-profile",
      href: "/settings",
      text: "JO",
      title: "Settings",
      "aria-label": "Settings",
      "aria-current": here === "settings" ? "page" : null,
    });

    this.replaceChildren(
      links,
      el("div", { class: "nav-middle" }, middle),
      el("div", { class: "nav-actions" }, [...actions, this.toggle, profile]),
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
    this.toggle.replaceChildren(icon(dark ? "sun" : "moon", 14));
  }
}

customElements.define("app-nav", AppNav);
