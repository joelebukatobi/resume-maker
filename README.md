# resume-maker

A local tool that tailors your CV and cover letter to each job posting, using a
language model you bring your own key for. It renders to DOCX and PDF with both
margins pinned exactly.

Python 3.12 standard library plus a Chromium-family browser for PDF export.
One optional dependency, `fastembed`, runs a small local model (bge-base,
about 200MB, CPU only) that finds your evidence by meaning on Review. Without
it everything still works, and the gap check falls back to word search.

## Quick start

```bash
git clone <this repo>
cd resume-maker
python3 server.py
```

To turn on the local embedding model (recommended), install it once into a
project venv and start the server with that Python instead:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python server.py
```

The model downloads on first start to `~/.cache/resume-maker/fastembed`.

The editor opens at `http://127.0.0.1:8000`. On the first run the server
creates `cv.json` and `cover.json` from the example files. Everything you add
after that stays on your machine: your CV, letter, notes, Playbook and
applications are all git-ignored, so a commit or a push only ever carries the
tool and its examples.

## Setting up, once

Work through these in order. The model only knows what you give it here, so the
more of this you fill in, the better every application reads.

### 1. Your base CV and letter: Settings

Click **Settings** in the menu. The base CV is what every application starts
from; each application gets its own copy, so editing one never touches another.

- **Bio**: your name, contact details (email, phone, LinkedIn, site, city) and
  which of them print on the CV and the letter. Paper size lives here too.
- **Education**: each school, degree and dates.
- **Work History**: every role you might ever want on a CV, with its
  organisation, location, title, dates and bullets. Put your most recent or
  strongest role first: the first role gets three bullets, every other role
  gets two. Add more roles than fit; the tool picks the four that answer each
  posting best.
- **Base Files**: upload an existing CV or letter (.docx, .pdf or .txt) instead
  of typing it, or replace your notes or Playbook from a file. Each upload
  warns first and offers a backup of what it replaces.

The company, job title, dates and headings are the CV's structure. The model
never changes them; only Settings does.

### 2. Your experience: My Experience

`notes.md`, opened from **My Experience**. This is the bank the model writes
from, and the only place it may take facts from. Write down what you actually
did in each job, the numbers you can defend, the tools you used and the work a
CV never captures. Use one `##` section per job.

A `## Skills` section (and an optional `## Technical skills`) feeds the Skills
line on the CV: one tool per entry, grouped under the labels your CV uses.

Notes are experience only. How the CV should be built belongs in the Playbook.

### 3. Your rules: Playbook

`playbook.md`, opened from **Playbook**. Start from a role starter (Product
Manager, Software Engineer, Consultant, Other) and make it yours. It goes into
every model turn and holds:

- what ranks first when the posting, the notes and the Playbook disagree;
- how to choose lines from the notes;
- the CV's shape: which roles may appear, which must always be on, page length;
- how you write: tone, bullet shape, banned words, punctuation.

The only rules fixed in code are the bullet counts (first role three, others
two), the four roles on a one-page CV and the CV's structure fields. Everything
else is yours to set here.

### 4. A model: Models

Click **Models** in the menu, pick a provider (Claude, OpenAI, DeepSeek or
Kimi), paste an API key and choose a model. The key is checked against the
provider, then saved to `~/.config/resume-maker/keys.json` (owner-only, outside
the repo); it is never sent back to the browser. Keys in the environment work
too and take precedence:

```bash
export ANTHROPIC_API_KEY=...   # also OPENAI_API_KEY, DEEPSEEK_API_KEY, MOONSHOT_API_KEY
python3 server.py
```

Model calls are billed by the provider. Override an endpoint with
`<PROVIDER>_BASE_URL`, e.g. `MOONSHOT_BASE_URL=https://api.moonshot.cn/v1`.

Optional: set `TYPESAFE_API_KEY` to let TypeSafe's Jev check each past CV line
against your notes, so lines your notes do not back are never reused.

## Making an application

The **Hub** lists your applications. Click **Start Application**.

1. **Job Description**: paste the posting's link or its text. A link is read
   when the board allows it; otherwise paste the text. **Next** names the
   application (Company | Role) and reads the posting's key terms.
2. **Professional Summary**, **Skills**, **Work History**, **Additional**: each
   step writes its section from your notes, past lines and Playbook. Read it,
   edit it, ask for changes in the chat, then **Next** to accept and move on.
   Work History picks which roles print and writes all their bullets at once.
3. **Review**: the whole CV against the posting. Gaps the notes cannot cover
   come back as questions; an answer can go straight into your notes or skills.
   **Overall Fit** reruns the check. **Save** writes the CV as PDF and DOCX
   into the application's `out/` folder.
4. **Cover letter**: switch with **Cover letter** in the menu. It is written
   from the same posting, notes and Playbook, then gets its own Review with a
   Checks card for the opening, claims, tone and length.

Nothing is sent to a model until you press a button that needs one, and
nothing is written to your notes or Playbook until you accept it.

## Where your data lives

| File | What it holds | In git |
| --- | --- | --- |
| `cv.json`, `cover.json` | Your base CV and letter | No (`*.example.json` are) |
| `notes.md` | Your experience | No |
| `playbook.md` | Your rules and voice | No (`templates/` starters are) |
| `library.json` | What you decided about past lines | No |
| `applications/` | Each job: its CV, letter, posting and chat | No |
| `~/.config/resume-maker/keys.json` | API keys | Outside the repo |

Point at other paths with `--cv`, `--cover`, `--playbook` and `--notes`.

## Command line

```bash
python3 -m resume_maker --format both
```

Writes `out/cv.docx` and `out/cv.pdf` from `cv.json` (start the server once
first, or copy `cv.example.json` to `cv.json`). Other flags:
`--format docx|pdf|html|both|all`, `--cv`, `--theme`, `--out`, `--name`,
`--check-only`.

Check the notes against what you actually said:

```bash
python3 -m resume_maker.audit          # one model call, reports, changes nothing
python3 -m resume_maker.audit --dry    # pair claims with evidence, no model
```

Run the self-checks:

```bash
python3 -m unittest discover -s tests
```

## The problem the renderer solves

Word documents usually place a location or a date on the right by padding with
spaces until it looks about right. That padding is measured in the current
font. Change the font, the size, or a single word, and every right-hand column
drifts.

Here, each two-column line is one paragraph containing `left <tab> right` with
a right-aligned tab stop at the right margin. The stop is a coordinate on the
page (page width minus margins) and owes nothing to font metrics. So the left
text starts on the left margin and the right text ends on the right margin, on
one line, in any font, at any size, with any wording. The HTML side uses
`justify-content: space-between` for the same result.

It can only fail if the two texts together are wider than the text column.
The editor measures every such line before export and names any that overflow.

Both renderers read the same `Theme`, so DOCX and PDF cannot disagree about
margins, tab stops, sizes or spacing. To change the font, set `font` and
`font_stack` in `theme.json`. Fonts that ship with Office are safest for DOCX;
PDF embeds its font.

## ATS notes

- No tables, text boxes, columns, headers, footers or images.
- Bullets are real list items; the glyph is a hyphen in the document font.
- Hyperlinks are real, and contact links show the URL as visible text.
- Section headings are checked against the vocabulary most parsers recognise.
- Date separators are normalised across the document.

Checks are advisory. They report; they never rewrite content or block export.

## Security

- The server binds `127.0.0.1` only and refuses cross-origin requests.
- Request bodies are capped; only `application/json` is accepted.
- Static serving is a fixed allowlist; no path is taken from the URL.
- Saves are written atomically via a temporary file and `os.replace`.
- Content is escaped for XML and for HTML; the injection tests assert it.
- Link schemes are restricted to `http`, `https` and `mailto`.
- The browser for PDF export runs from an argument list, never a shell string,
  in a private profile inside a 0700 temporary directory.
