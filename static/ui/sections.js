// A markdown file as its "## " sections, and back, without losing a byte.
//
// The part before the first "## " heading has no heading of its own. Deeper
// headings stay inside the section they belong to. join(split(text)) is
// always text, whatever the file holds.

export function split(text) {
  const parts = [];
  let current = { heading: null, lines: [] };
  for (const line of text.split("\n")) {
    if (line.startsWith("## ")) {
      if (current.heading !== null || current.lines.length) parts.push(current);
      current = { heading: line, lines: [] };
    } else {
      current.lines.push(line);
    }
  }
  parts.push(current);
  // A heading on the file's last line has no body, not an empty one.
  return parts.map((p) => ({ heading: p.heading, body: p.lines.join("\n"), bare: !p.lines.length }));
}

export function join(parts) {
  return parts.map((p) => {
    if (p.heading === null) return p.body;
    return p.bare && p.body === "" ? p.heading : `${p.heading}\n${p.body}`;
  }).join("\n");
}

export function label(part) {
  return part.heading === null ? "Opening" : part.heading.replace(/^##\s+/, "");
}
