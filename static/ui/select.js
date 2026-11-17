// Bespoke select: trigger + popover menu, same language as the Models dialog.
// options are { id, label }. onChange(id) fires when a choice is confirmed.

import { el, icon } from "./dom.js";

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

export function bespokeSelect({ label, options, value, onChange }) {
  let open = false;
  let current = value;
  let items = options.slice();

  const trigger = el("button", {
    class: "select-trigger",
    type: "button",
    "aria-label": label,
    "aria-haspopup": "listbox",
    "aria-expanded": "false",
  });
  const menu = el("div", {
    class: "select-menu",
    role: "listbox",
    popover: "manual",
  });
  const root = el("div", { class: "select" }, [trigger, menu]);

  function chosen() {
    return items.find((o) => o.id === current) || null;
  }

  function paintTrigger() {
    const pick = chosen();
    trigger.replaceChildren(
      el("span", { class: "select-label", text: pick ? pick.label : "Pick…" }),
      icon("down", 14),
    );
    trigger.setAttribute("aria-expanded", String(open));
  }

  function paintMenu() {
    menu.replaceChildren(...items.map((o) => el("button", {
      class: "menu-item",
      type: "button",
      role: "option",
      "aria-selected": String(o.id === current),
      onclick: () => {
        current = o.id;
        open = false;
        paint();
        if (onChange) onChange(current);
      },
    }, [el("span", { text: o.label })])));
    if (open) placeMenu(menu, trigger);
    else closeMenu(menu);
  }

  function paint() {
    paintTrigger();
    paintMenu();
  }

  trigger.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    open = !open;
    paint();
  });

  document.addEventListener("click", (event) => {
    if (!open) return;
    if (root.contains(event.target) || menu.contains(event.target)) return;
    open = false;
    paint();
  });

  document.addEventListener("keydown", (event) => {
    if (!open || event.key !== "Escape") return;
    open = false;
    paint();
  });

  paint();

  return {
    root,
    get value() { return current; },
    set value(next) {
      current = next;
      paint();
    },
    setOptions(next) {
      items = next.slice();
      if (!items.some((o) => o.id === current) && items[0]) current = items[0].id;
      paint();
    },
  };
}
