"""Read a job description from a posting's link.

Job boards publish a schema.org JobPosting block for search engines, which is
the clean copy of the posting. A page without one falls back to its visible
text, marked so the reader knows to check it. Boards that refuse the request or
draw the posting with JavaScript raise JobPostError, and the paste box remains.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import urllib.error
import urllib.request
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

TIMEOUT = 15
MAX_PAGE = 5_000_000
MIN_TEXT = 400
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

SKIPPED = {"script", "style", "noscript", "svg", "template", "nav", "header", "footer",
           "aside", "form", "button", "select"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr"}
BLOCK = {"p", "div", "section", "article", "main", "ul", "ol", "li", "br", "tr", "table",
         "h1", "h2", "h3", "h4", "h5", "h6"}


class JobPostError(ValueError):
    """The link could not be read; the message is written for the person."""


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.blocks: list[str] = []
        self.title = ""
        self._skip = 0
        self._ld = False
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and (dict(attrs).get("type") or "").lower() == "application/ld+json":
            self._ld = True
            self.blocks.append("")
        if tag == "title":
            self._in_title = True
        if tag in SKIPPED:
            self._skip += 1
        if tag == "li" and not self._skip:
            self.text.append("\n- ")
        elif tag in BLOCK and not self._skip:
            self.text.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in BLOCK and not self._skip:
            self.text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._ld = False
        if tag == "title":
            self._in_title = False
        if tag in SKIPPED and self._skip:
            self._skip -= 1
        if tag in BLOCK and not self._skip:
            self.text.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ld:
            self.blocks[-1] += data
        elif self._in_title:
            self.title += data
        elif not self._skip:
            self.text.append(data)


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n- \n+", "\n- ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _to_text(markup: str) -> str:
    page = _Page()
    page.feed(markup)
    return _tidy("".join(page.text))


def _postings(node: Any):
    """Every JobPosting in a JSON-LD value, however it is nested."""
    if isinstance(node, list):
        for item in node:
            yield from _postings(item)
    elif isinstance(node, dict):
        kind = node.get("@type")
        if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
            yield node
        yield from _postings(node.get("@graph"))


def extract(markup: str) -> dict[str, str]:
    """The posting in a page: text, title, company, and where the text came from."""
    page = _Page()
    page.feed(markup)
    for block in page.blocks:
        try:
            found = json.loads(block, strict=False)
        except ValueError:
            continue
        for posting in _postings(found):
            # The description is HTML, sometimes escaped a second time.
            text = _to_text(unescape(str(posting.get("description") or "")))
            if len(text) >= MIN_TEXT:
                company = posting.get("hiringOrganization")
                return {
                    "text": text,
                    "title": unescape(str(posting.get("title") or "")).strip(),
                    "company": unescape(str(company.get("name") or "")).strip()
                    if isinstance(company, dict) else "",
                    "source": "posting",
                }
    text = _tidy("".join(page.text))
    if len(text) < MIN_TEXT:
        raise JobPostError("Couldn't find a job description on that page. Paste it instead.")
    return {"text": text, "title": _tidy(page.title), "company": "", "source": "page"}


def _check_public(url: str) -> None:
    """Refuse anything but a public web address, so a link can't reach this
    machine or its network."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise JobPostError("That isn't a web link.")
    try:
        found = socket.getaddrinfo(parts.hostname, parts.port or 443, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as error:
        raise JobPostError("Couldn't find that site. Check the link.") from error
    # ponytail: urllib resolves the name again, so a rebinding DNS server slips
    # through; pin the connection to this address before hosting this publicly.
    if not all(ipaddress.ip_address(item[4][0].split("%")[0]).is_global for item in found):
        raise JobPostError("That link points at a private address.")


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url: str, kinds: tuple[str, ...] = ("text/html", "application/xhtml+xml")) -> str:
    """The page at a public link, as text."""
    url = url.strip()
    _check_public(url)
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": ",".join(kinds), "Accept-Language": "en",
    })
    opener = urllib.request.build_opener(_CheckedRedirect)
    try:
        with opener.open(request, timeout=TIMEOUT) as reply:
            kind = reply.headers.get_content_type()
            if kind not in kinds:
                raise JobPostError("That link isn't a web page. Paste the description instead.")
            body = reply.read(MAX_PAGE + 1)
            charset = reply.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as error:
        if error.code == 404 or error.code == 410:
            raise JobPostError("That posting is gone. Check the link.") from error
        raise JobPostError("That site won't let us read it. Paste the description instead.") from error
    except (urllib.error.URLError, OSError) as error:
        raise JobPostError("Couldn't reach that site. Paste the description instead.") from error
    if len(body) > MAX_PAGE:
        raise JobPostError("That page is too big to read. Paste the description instead.")
    return body.decode(charset, errors="replace")


TRIM_PROMPT = """You clean up a job posting copied from a web page.

Keep only the lines about the job itself:
- the job title, location, work type and team
- a short description of the company
- what the role does and its responsibilities
- pay, salary or rate
- required, preferred and nice-to-have experience, skills and qualifications
- the heading above each of these sections, such as "Requirements" or
  "What you'll need", so every list keeps its label

Drop everything else: menus, cookie and privacy notices, apply buttons and
instructions, job alerts, benefits and perks lists, equal opportunity and
accommodation statements, legal text, links to other jobs, and footers.

Copy every line you keep exactly as written, in its original order, one per
line. Never reword, summarise, merge or add anything. Reply with the kept
lines only."""

MIN_KEPT = 0.15


def _plain(line: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"^[\s\-*•#>]+", "", line)).strip().lower()


def keep_lines(source: str, reply: str) -> str:
    """The model's trimmed posting, holding only lines copied from the source.
    A line it reworded or made up is dropped; if too little survives, the
    source comes back whole so nothing about the job is lost."""
    known = {_plain(line): line for line in source.splitlines() if _plain(line)}
    kept = [known[_plain(line)] for line in reply.splitlines() if _plain(line) in known]
    text = _tidy("\n".join(kept))
    return text if len(text) >= len(source) * MIN_KEPT and len(text) >= MIN_TEXT // 2 else source


WORKABLE = re.compile(r"https?://apply\.workable\.com/([\w-]+)/j/(\w+)")


def _workable(account: str, code: str) -> dict[str, str]:
    """Workable draws its job pages with JavaScript, but serves the same job
    as JSON to anyone."""
    url = f"https://apply.workable.com/api/v2/accounts/{account}/jobs/{code}"
    try:
        job = json.loads(fetch(url, ("application/json",)))
    except ValueError as error:
        raise JobPostError("Couldn't read that Workable job. Paste it instead.") from error
    parts = [job.get(key) or "" for key in ("description", "requirements", "benefits")]
    text = _to_text("".join(str(part) for part in parts))
    if len(text) < MIN_TEXT:
        raise JobPostError("Couldn't find a job description on that page. Paste it instead.")
    return {"text": text, "title": str(job.get("title") or "").strip(), "company": "",
            "source": "posting"}


def read(url: str) -> dict[str, str]:
    workable = WORKABLE.match(url.strip())
    post = _workable(*workable.groups()) if workable else extract(fetch(url))
    # Boards often keep the title beside the description, not in it. Naming
    # the application reads the text alone, so the title goes on top.
    title = post["title"]
    if title and title.lower() not in post["text"][:500].lower():
        post["text"] = f"{title}\n\n{post['text']}"
    return post


if __name__ == "__main__":
    import sys

    for link in sys.argv[1:]:
        try:
            post = read(link)
            print(json.dumps({"url": link, **{k: post[k] for k in ("source", "title", "company")},
                              "chars": len(post["text"])}))
            print(post["text"][:300].replace("\n", " | "))
        except JobPostError as error:
            print(json.dumps({"url": link, "error": str(error)}))
