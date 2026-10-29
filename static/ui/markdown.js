// A safe markdown subset: headings, lists, paragraphs, bold, italic, code.
// Nodes are built one by one and text goes in as text, never innerHTML, so
// nothing in a file or a reply can inject markup.

import { el } from "./dom.js";

const INLINE = /(\*\*[^*]+\*\*|__[^_]+__|(?<![*\w])\*[^*\n]+\*(?!\w)|`[^`]+`)/g;

function inline(text) {
  const out = [];
  for (const part of text.split(INLINE)) {
    if (!part) continue;
    if ((part.startsWith("**") && part.endsWith("**")) || (part.startsWith("__") && part.endsWith("__"))) {
      out.push(el("strong", { text: part.slice(2, -2) }));
    } else if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
      out.push(el("em", { text: part.slice(1, -1) }));
    } else if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      out.push(el("code", { text: part.slice(1, -1) }));
    } else {
      out.push(document.createTextNode(part));
    }
  }
  return out;
}

const BULLET = /^\s*[-*•]\s+/;
const NUMBER = /^\s*\d+[.)]\s+/;
const HEADING = /^\s*(#{1,6})\s+(.*)$/;

function block(text) {
  const lines = text.split("\n");
  const heading = lines[0].match(HEADING);
  if (heading) {
    const level = Math.min(heading[1].length + 1, 4);
    const nodes = [el(`h${level}`, { class: "md-heading" }, inline(heading[2]))];
    const rest = lines.slice(1).join("\n").trim();
    if (rest) nodes.push(...block(rest));
    return nodes;
  }
  for (const [pattern, tag] of [[BULLET, "ul"], [NUMBER, "ol"]]) {
    if (lines.every((l) => pattern.test(l) || /^\s+\S/.test(l) || !l.trim())) {
      const list = el(tag, { class: "md-list" });
      let item = null;
      for (const line of lines) {
        if (!line.trim()) continue;
        if (pattern.test(line)) {
          item = el("li", {}, inline(line.replace(pattern, "")));
          list.append(item);
        } else if (item) {
          item.append(document.createTextNode(" "), ...inline(line.trim()));
        }
      }
      return [list];
    }
  }
  return [el("p", { class: "md-p" }, inline(lines.map((l) => l.trim()).join(" ")))];
}

export function renderMarkdown(text, host) {
  host.replaceChildren();
  const trimmed = text.trim();
  if (!trimmed) {
    host.append(el("p", { class: "meta", text: "Empty." }));
    return;
  }
  for (const para of trimmed.split(/\n{2,}/)) {
    if (para.trim()) host.append(...block(para.trim()));
  }
}
