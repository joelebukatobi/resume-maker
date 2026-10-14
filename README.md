# resume-maker

Renders a CV from JSON to DOCX and PDF, with both margins pinned exactly.

No dependencies. Python 3.12 standard library plus a Chromium-family browser
for PDF export. No LibreOffice, no `pip install`, no `npm install`, no GUI
application installed on the system.

## Quick start

```bash
python3 server.py
```

Opens the hub at `http://127.0.0.1:8000`. Edit the fields, watch the
preview, then Save or Export.

## The spine: Voice → Notes → Documents → Model → Chat

The hub at `/` walks you through five pieces, each showing whether it is
empty, started or ready:

| Pillar | Where | File | What it is |
|---|---|---|---|
| **Voice** | `/voice` | `voice.md` | How you sound on paper: method, bullet shape, banned words, punctuation. Start from a role (Product Manager, Software Engineer, Consultant, Other) and make it yours. |
| **Notes** | `/notes` | `notes.md` | Your experience bank: what you actually did, the numbers you can defend, the work a CV never captures. |
| **Documents** | `/resume`, `/cover-letter` | `cv.json`, `cover.json` | What you send, with live preview and DOCX/PDF export. |
| **Model** | Agent link in the chat | `~/.config/resume-maker/keys.json` | The provider the chat talks to. |
| **Chat** | right-hand panel | `.chat/` | Where you tailor a document to a job. |

Voice and Notes are sent with every chat turn, for both the CV and the cover
letter — **Notes are shared**, so one bank feeds both. The model is told to map
a job description against them, name the gaps, and ask before inventing
anything. Notes over 60,000 characters are trimmed in the prompt, with a marker
telling the model so.

Both files live next to `cv.json` by default; point elsewhere with
`--voice path/to/voice.md` and `--notes path/to/notes.md`. They are
git-ignored because they hold personal detail; the role starters in
`templates/` are tracked.

Command line, for the same result without the UI:

```bash
python3 -m resume_maker --format both
```

Writes `out/cv.docx` and `out/cv.pdf`. Other flags: `--format docx|pdf|html|both|all`,
`--cv`, `--theme`, `--out`, `--name`, `--check-only`.

Run the self-checks:

```bash
python3 -m unittest discover -s tests
```

Check the notes against what you actually said:

```bash
python3 -m resume_maker.audit          # one model call, reports, changes nothing
python3 -m resume_maker.audit --dry    # pair claims with evidence, no model
```

Every claim in `notes.md` is matched against your own messages, in the app's
chats and in this project's Claude Code transcripts, and a model is asked which
claims the evidence does not support. It prints what to look at. You edit.

## Chat and import

The right-hand panel talks to an LLM about the open document. Click **Connect
agent**, pick Claude, OpenAI, DeepSeek or Kimi, and paste an API key. The key is
checked against the provider, then saved to `~/.config/resume-maker/keys.json`
(owner-only, outside the repo); it is never sent back to the browser. Keys in
the environment work too and take precedence:

```bash
export ANTHROPIC_API_KEY=...   # also OPENAI_API_KEY, DEEPSEEK_API_KEY, MOONSHOT_API_KEY
python3 server.py
```

Models are listed live from each provider. Override an endpoint with
`<PROVIDER>_BASE_URL`, e.g. `MOONSHOT_BASE_URL=https://api.moonshot.cn/v1`.

- **Proposals**: text the model writes for a field appears as a before/after
  diff with Accept. Any other paragraph has **Replace…** to drop it into a field.
  Nothing changes until you accept, and nothing is written to disk until Save.
- **Job description**: paste into the left tile; it collapses to a *Pasted* card (click to view or edit).
- **Import**: click or drop a .docx, .pdf or .txt on the right tile. The result shows
  in the preview as *not applied* until you Accept. PDF import uses `pdftotext`.
- **History** is kept per document in `.chat/` (git-ignored; it holds CV text).

## The problem this solves

Word documents usually place a location or a date on the right by padding with
spaces until it looks about right. That padding is measured in the current
font. Change the font, the size, or a single word, and every right-hand column
drifts.

Here, each two-column line is one paragraph containing `left <tab> right` with
a **right-aligned tab stop at the right margin**. The stop is a coordinate on
the page — page width minus margins — and owes nothing to font metrics. The
renderer places the *last* character of the right-hand text on that coordinate
and lays the text out leftward from it.

So the left text starts on the left margin, the right text ends on the right
margin, on one line, in any font, at any size, with any wording.

The CSS side uses `justify-content: space-between` on a flex row, which
produces the same result by the same logic.

The only way this can fail is if the two texts together are wider than the text
column. Both the CLI and the editor measure every such line in the browser
before export and name any that overflow, so the failure is reported rather
than discovered later in the PDF.

## Files

| Path | Role |
| --- | --- |
| `cv.json` | Content: name, contact line, sections |
| `theme.json` | Font, sizes, margins, spacing — every measurement |
| `resume_maker/theme.py` | Geometry. The only place page numbers are defined |
| `resume_maker/model.py` | Immutable content model; parses and rejects untrusted input |
| `resume_maker/render_docx.py` | Office Open XML output |
| `resume_maker/render_html.py` | HTML output for preview and PDF |
| `resume_maker/export_pdf.py` | Headless browser: PDF and line measurement |
| `resume_maker/validate.py` | Advisory ATS checks |
| `resume_maker/build.py` | Orchestration shared by CLI and server |
| `resume_maker/fonts.py` | Curated font list with local availability |
| `resume_maker/assistant.py` | What the chat model sees (fields, Voice, Notes) and how its output is checked |
| `resume_maker/llm.py` | Provider-agnostic chat: Claude, OpenAI, DeepSeek, Kimi |
| `resume_maker/setup.py` | Readiness of each pillar and limits on what is saved |
| `templates/voice-*.md` | Role starters for Voice |
| `server.py` | Loopback-only editor |
| `static/index.html` | Editor UI, vanilla JS |
| `static/chat.js` | Chat panel |
| `static/home.html` | Hub: Voice, Notes, Applications |
| `static/voice.html`, `static/notes.html` | Setup pages, sharing `setup.css` / `setup.js` |

Both renderers read the same `Theme`, so DOCX and PDF cannot disagree about
margins, tab stops, sizes or spacing. Adding a section kind means adding a
dataclass in `model.py` and one handler per renderer; nothing existing changes.

## Changing the font

Set `font` and `font_stack` in `theme.json`, or pick from the dropdown in the
editor. Nothing else moves: the font is declared once, in the DOCX
`docDefaults` and in one CSS rule, and no measurement is derived from it. The
bullet glyph inherits it too.

Fonts that ship with Office are safest for DOCX. A font the recipient does not
have is silently substituted by Word and the document reflows on their screen.
PDF embeds its font and is unaffected — the editor marks fonts that are not
installed locally.

## ATS notes

- No tables, text boxes, columns, headers, footers or images.
- Bullets are real list items; the glyph is a hyphen in the document font.
- Hyperlinks are real, and contact links show the URL as visible text, because
  many parsers keep anchor text and discard the target.
- Section headings are checked against the vocabulary most parsers recognise.
- Date separators are normalised across the document.
- Phone numbers are checked for ten national digits.

Checks are advisory. They report; they never rewrite content or block export.

## Security

- The server binds `127.0.0.1` only and refuses cross-origin requests.
- Request bodies are capped at 1 MiB; only `application/json` is accepted.
- Static serving is a fixed filename allowlist — no path is taken from the URL.
- Saves go only to the two JSON paths the server was started with, written
  atomically via a temporary file and `os.replace`.
- Export filenames are reduced to alphanumerics, so a stem cannot traverse.
- Content is escaped for XML and for HTML; the injection tests assert it.
- Link schemes are restricted to `http`, `https` and `mailto`.
- Font names are restricted to characters that cannot terminate a CSS or XML
  context.
- The browser is launched from an argument list, never a shell string, in a
  private profile directory inside a 0700 temporary directory, so it cannot
  attach to a running browser session or touch the user's profile.
- The preview runs in an iframe sandboxed without same-origin access; it
  reports measurements by `postMessage`.
