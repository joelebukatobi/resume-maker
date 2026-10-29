// Small helpers every page module shares. Nodes are built, never parsed from
// strings: nothing a file or a model wrote is ever assigned as HTML.

export function el(tag, attrs = {}, kids = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === "text") node.textContent = value;
    else if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const kid of kids) if (kid != null) node.append(kid);
  return node;
}

const SVG = "http://www.w3.org/2000/svg";

// Iconoir paths, 24px grid, stroke 1.5, drawn in currentColor.
const ICONS = {
  moon: ["M3 11.5066C3 16.7497 7.25034 21 12.4934 21C16.2209 21 19.4466 18.8518 21 15.7259C12.4934 15.7259 8.27411 11.5066 8.27411 3C5.14821 4.55344 3 7.77915 3 11.5066Z"],
  sun: ["M12 18C15.3137 18 18 15.3137 18 12C18 8.68629 15.3137 6 12 6C8.68629 6 6 8.68629 6 12C6 15.3137 8.68629 18 12 18Z",
    "M22 12L23 12M12 2V1M12 23V22M20 20L19 19M20 4L19 5M4 20L5 19M4 4L5 5M1 12L2 12"],
  download: ["M6 20L18 20M12 4V16M12 16L15.5 12.5M12 16L8.5 12.5"],
  send: ["M22.1525 3.55321L11.1772 21.0044L9.50686 12.4078L2.00002 7.89795L22.1525 3.55321Z",
    "M9.45557 12.4436L22.1524 3.55321"],
  down: ["M6 9L12 15L18 9"],
  up: ["M6 15L12 9L18 15"],
  // Straight arrows — reorder a bullet; chevrons stay for expand/collapse.
  "arrow-up": ["M12 20V4M12 4L18 10M12 4L6 10"],
  "arrow-down": ["M12 4V20M12 20L18 14M12 20L6 14"],
  check: ["M5 13L9 17L19 7"],
  stop: ["M21 3.6V20.4C21 20.7314 20.7314 21 20.4 21H3.6C3.26863 21 3 20.7314 3 20.4V3.6C3 3.26863 3.26863 3 3.6 3H20.4C20.7314 3 21 3.26863 21 3.6Z"],
  xmark: ["M6.75827 17.2426L12.0009 12M17.2435 6.75736L12.0009 12M12.0009 12L6.75827 6.75736M12.0009 12L17.2435 17.2426"],
  left: ["M15 6L9 12L15 18"],
  right: ["M9 6L15 12L9 18"],
  // Double chevron — skip ahead without committing this step.
  skip: ["M6 6L12 12L6 18", "M12 6L18 12L12 18"],
  plus: ["M6 12H12M12 12H18M12 12V6M12 12V18"],
  minus: ["M6 12H18"],
  mic: [
    "M12 15C10.3431 15 9 13.6569 9 12V6C9 4.34315 10.3431 3 12 3C13.6569 3 15 4.34315 15 6V12C15 13.6569 13.6569 15 12 15Z",
    "M19 12C19 15.866 15.866 19 12 19M12 19C8.13401 19 5 15.866 5 12M12 19V22M12 22H9M12 22H15",
  ],
  // Magnifying glass with + / − — preview zoom, matching the old editor.
  "zoom-in": [
    "M15.5 15.5L19 19",
    "M5 11C5 7.13401 8.13401 4 12 4C15.866 4 19 7.13401 19 11C19 14.866 15.866 18 12 18C8.13401 18 5 14.866 5 11Z",
    "M8 11H16",
    "M12 8V14",
  ],
  "zoom-out": [
    "M15.5 15.5L19 19",
    "M5 11C5 7.13401 8.13401 4 12 4C15.866 4 19 7.13401 19 11C19 14.866 15.866 18 12 18C8.13401 18 5 14.866 5 11Z",
    "M8 11H16",
  ],
  refresh: [
    "M21.8883 13.5C21.1645 18.3113 17.013 22 12 22C6.47715 22 2 17.5228 2 12C2 6.47715 6.47715 2 12 2C16.1006 2 19.5948 4.46819 21.1716 8",
    "M17 8H21.4C21.7314 8 22 7.73137 22 7.4V3",
  ],
  search: [
    "M17 17L21 21",
    "M3 11C3 15.4183 6.58172 19 11 19C13.213 19 15.2161 18.1015 16.6644 16.6493C18.1128 15.197 19 13.194 19 11C19 6.58172 15.4183 3 11 3C6.58172 3 3 6.58172 3 11Z",
  ],
  edit: [
    "M13.0207 5.82839L15.8491 2.99996L20.7988 7.94971L17.9704 10.7781M13.0207 5.82839L3.41405 15.435C3.22652 15.6225 3.12116 15.8768 3.12116 16.142V19.6776H6.65685C6.9221 19.6776 7.17645 19.5723 7.36398 19.3847L16.9706 9.77808M13.0207 5.82839L16.9706 9.77808",
  ],
  archive: [
    "M7 6L17 6M7 6L5.5 10M7 6L8.5 10M17 6L18.5 10M17 6L15.5 10M5.5 10L4 18C4 19.1046 4.89543 20 6 20H18C19.1046 20 20 19.1046 20 18L18.5 10M5.5 10H18.5M10 14V16M14 14V16",
  ],
  trash: [
    "M20 9L18.005 20.3463C17.8369 21.3026 17.0062 22 16.0353 22H7.96474C6.99379 22 6.1631 21.3026 5.99486 20.3463L4 9",
    "M20 9H4M9 6V3.5C9 2.67157 9.67157 2 10.5 2H13.5C14.3284 2 15 2.67157 15 3.5V6M9 6H15",
  ],
  copy: [
    "M19.4 20H9.6C9.26863 20 9 19.7314 9 19.4V9.6C9 9.26863 9.26863 9 9.6 9H19.4C19.7314 9 20 9.26863 20 9.6V19.4C20 19.7314 19.7314 20 19.4 20Z",
    "M15 9V4.6C15 4.26863 14.7314 4 14.4 4H4.6C4.26863 4 4 4.26863 4 4.6V14.4C4 14.7314 4.26863 15 4.6 15H9",
  ],
  eye: [
    "M12 14C13.1046 14 14 13.1046 14 12C14 10.8954 13.1046 10 12 10C10.8954 10 10 10.8954 10 12C10 13.1046 10.8954 14 12 14Z",
    "M21 12C19.1114 14.991 15.7183 18 12 18C8.2817 18 4.88856 14.991 3 12C4.88856 9.00948 8.2817 6 12 6C15.7183 6 19.1114 9.00948 21 12Z",
  ],
  code: [
    "M13.5 6L10 18",
    "M6.8 9L3 12.5L6.8 16",
    "M17.2 9L21 12.5L17.2 16",
  ],
  more: [
    "M12 5C12.5523 5 13 5.44772 13 6C13 6.55228 12.5523 7 12 7C11.4477 7 11 6.55228 11 6C11 5.44772 11.4477 5 12 5Z",
    "M12 11C12.5523 11 13 11.4477 13 12C13 12.5523 12.5523 13 12 13C11.4477 13 11 12.5523 11 12C11 11.4477 11.4477 11 12 11Z",
    "M12 17C12.5523 17 13 17.4477 13 18C13 18.5523 12.5523 19 12 19C11.4477 19 11 18.5523 11 18C11 17.4477 11.4477 17 12 17Z",
  ],
};

// Same 32px control the posting foot uses: icon-only Back / Next / Skip.
export function stepAction(tag, attrs = {}, name = "right") {
  return el(tag, { class: "posting-action", ...attrs }, [icon(name, 18)]);
}

export function icon(name, size = 18, stroke = 1.5) {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("aria-hidden", "true");
  for (const d of ICONS[name]) {
    const path = document.createElementNS(SVG, "path");
    path.setAttribute("d", d);
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", String(stroke));
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    svg.append(path);
  }
  return svg;
}

export async function api(path, body) {
  const options = body === undefined
    ? {}
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

let toastTimer;

export function toast(text, state = "done") {
  let node = document.querySelector(".toast");
  if (!node) {
    node = el("div", { class: "toast", role: "status", "aria-live": "polite" });
    document.body.append(node);
  }
  clearTimeout(toastTimer);
  node.dataset.state = state;
  node.textContent = text;
  node.hidden = false;
  if (state === "done") toastTimer = setTimeout(() => { node.hidden = true; }, 1800);
}

export function download(filename, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=utf-8" }));
  const link = el("a", { href: url, download: filename });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
