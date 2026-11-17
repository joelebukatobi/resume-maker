"""One folder per job application.

Each application you work on — Meta's Rotational PM, Osmo's Software TPM —
owns its CV, its cover letter, both themes and the whole chat about it. They
are copies, made from the files in the project root when the application is
created, so tailoring one never touches another and the root files stay the
clean base everything forks from.

Voice (``voice.md``) and the experience notes (``notes.md``) are *not*
copied. They describe the candidate, not the job, and every application draws
on the same pair.

Finished applications are archived rather than deleted: the same company often
comes back with a different role, and the cover letter you wrote is the best
possible starting point for it. Delete exists too, but it is the deliberate
hard option for an application that should never be opened again.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

ARCHIVE = "_archive"
CURRENT = "current"
META = "app.json"
MAX_NAME = 120
# What kind of role an application is for. A new application forks its documents
# from the newest live application of the same track, so a Product CV and an
# Engineering CV stay separate lineages.
TRACKS = ("Product", "Engineering", "Data", "Design", "Other")
MAX_TRACK = 40
# What a new application is called until the job description names it.
UNNAMED = "Untitled application"
# The files an application owns. The key is how the rest of the program asks
# for one; the value is the file inside the application's folder.
FILES = {
    "cv": "cv.json",
    "cover": "cover.json",
    "theme": "theme.json",
    "cover_theme": "cover-theme.json",
    "chat": "chat.json",
    "keywords": "keywords.json",
}
# A new application inherits the documents, never the conversation: the
# tailoring is a head start, the chat about another job is noise. Nor the
# keyword list, which was read from the last posting and would count the next
# application against the wrong job.
# Nor the cards from the last page build, which were about that page.
COPIED = tuple(k for k in FILES if k not in ("chat", "keywords"))
_SLUG_OK = re.compile(r"\A[a-z0-9][a-z0-9-]{0,63}\Z")


class ApplicationError(ValueError):
    """The application could not be created, found, or moved."""


def slugify(name: str) -> str:
    """``Meta — Rotational PM`` -> ``meta-rotational-pm``.

    The result is used as a directory name, so it is restricted to a shape that
    cannot climb out of the applications folder: lowercase, digits, hyphens.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64].strip("-")
    return slug or "application"


@dataclass(frozen=True)
class Application:
    slug: str
    name: str
    created: str
    job_url: str = ""
    # A fingerprint of the page last read from ``job_url``, so a refresh can
    # tell a changed posting from the same one read again.
    job_page: str = ""
    archived: bool = False
    track: str = ""
    # Stable across renames, and never reused. The slug is neither: naming an
    # application moves its folder, and deleting one frees its slug for the
    # next application made from the same posting, which then inherited the
    # dead one's unsaved draft and its fit score from the browser.
    id: str = ""
    # When this application was last opened, as a unix timestamp. ``created``
    # is a date, so every application made on the same day ties on it and the
    # slug broke the tie: after deleting the open application, "the one you
    # were last on" became whichever slug sorted last.
    opened: float = 0.0


class Workspace:
    """The set of applications on disk, and which one is open.

    ``base`` maps the keys in :data:`FILES` to the project-root files a new
    application is seeded from.
    """

    def __init__(self, root: Path, base: dict[str, Path]) -> None:
        self.root = Path(root)
        self.base = base

    # -- locating ------------------------------------------------------

    def folder(self, slug: str, *, archived: bool = False) -> Path:
        """The folder for ``slug``, checked to be directly inside the root.

        ``slug`` reaches here from the browser, so it is validated against the
        slug shape and then confirmed to resolve to a direct child. A value
        like ``../..`` fails the first test; a symlink fails the second.
        """
        if not _SLUG_OK.match(slug or ""):
            raise ApplicationError(f"not an application: {slug!r}")
        parent = self.root / ARCHIVE if archived else self.root
        folder = (parent / slug).resolve()
        if folder.parent != parent.resolve():
            raise ApplicationError(f"not an application: {slug!r}")
        return folder

    def find(self, slug: str) -> tuple[Path, bool]:
        """``(folder, archived)`` for an application that exists."""
        for archived in (False, True):
            folder = self.folder(slug, archived=archived)
            if (folder / META).is_file():
                return folder, archived
        raise ApplicationError(f"no application called {slug!r}")

    def paths(self, slug: str) -> dict[str, Path]:
        """Where this application's documents live, by :data:`FILES` key."""
        folder, _ = self.find(slug)
        return {key: folder / name for key, name in FILES.items()}

    # -- listing -------------------------------------------------------

    def _read(self, folder: Path, archived: bool) -> Application | None:
        try:
            data = json.loads((folder / META).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            opened = float(data.get("opened") or 0.0)
        except (TypeError, ValueError):
            opened = 0.0
        return Application(
            slug=folder.name,
            name=str(data.get("name") or folder.name),
            created=str(data.get("created") or ""),
            job_url=str(data.get("job_url") or ""),
            job_page=str(data.get("job_page") or ""),
            archived=archived,
            track=str(data.get("track") or ""),
            id=str(data.get("id") or ""),
            opened=opened,
        )

    def list(self) -> list[Application]:
        """Live applications first, then archived; newest first within each."""
        found = []
        for parent, archived in ((self.root, False), (self.root / ARCHIVE, True)):
            if not parent.is_dir():
                continue
            for folder in parent.iterdir():
                if folder.is_dir() and folder.name != ARCHIVE:
                    app = self._read(folder, archived)
                    if app:
                        found.append(app)
        found.sort(key=lambda a: (a.archived, a.created, a.slug), reverse=True)
        found.sort(key=lambda a: a.archived)
        return found

    # -- current -------------------------------------------------------

    @property
    def current(self) -> str:
        """The open application, falling back to the one opened most recently.

        The pointer goes when the open application is deleted, and what
        replaces it seeds the next application's documents. Falling back to
        the first of ``list()`` meant falling back to a date-then-slug sort,
        so deleting today's open application handed the next one an
        alphabetical winner from earlier in the day rather than the CV the
        user had actually been working on.
        """
        try:
            slug = (self.root / CURRENT).read_text(encoding="utf-8").strip()
            self.find(slug)
            return slug
        except (OSError, ApplicationError):
            pass
        live = [a for a in self.list() if not a.archived]
        if not live:
            raise ApplicationError("no applications yet")
        live.sort(key=lambda a: (a.opened, a.created, a.slug), reverse=True)
        self.set_current(live[0].slug)
        return live[0].slug

    def set_current(self, slug: str) -> Application:
        folder, archived = self.find(slug)
        if archived:
            raise ApplicationError("restore this application before opening it")
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / CURRENT).write_text(slug + "\n", encoding="utf-8")
        self._patch(folder, opened=time.time())
        app = self._read(folder, False)
        assert app is not None  # find() just read the same file
        return app

    def _patch(self, folder: Path, **fields: Any) -> dict[str, Any]:
        """Change some of an application's metadata, leaving the rest alone."""
        try:
            meta = json.loads((folder / META).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        meta.update(fields)
        (folder / META).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        return meta

    # -- changing ------------------------------------------------------

    def create(self, name: str, *, job_url: str = "", source: str | None = None,
               track: str | None = None, keep_letter: bool = True) -> Application:
        """A new application, with documents copied from ``source``.

        ``source`` is normally the application you were last working on: the
        CV and cover letter you have already tailored are a better starting
        point than the untouched base, and the same company's next role starts
        from the letter you wrote for the first one. The chat does not come
        along — the new job gets a clean conversation.

        ``track`` is usually left to be detected from the job description; when
        it is given (Duplicate inherits it), the new application starts tagged.
        The hashes of the copied documents are recorded so identify can tell an
        untouched copy from one the user has since edited.

        ``keep_letter`` is the exception to all of that. A CV carries forward
        because tailoring compounds, but a cover letter is an argument made to
        one posting, and inheriting it meant a letter that pitched design and
        Figma work at a company that had not asked for any. Chaining from the
        last application you were on therefore starts the letter from the base
        file; Duplicate, which asks for a source by name, keeps it.
        """
        name = " ".join(str(name).split())[:MAX_NAME]
        if not name:
            raise ApplicationError("give the application a name")
        slug = self._free_slug(slugify(name))
        folder = self.folder(slug)
        origin = self.paths(source) if source else dict(self.base)
        folder.mkdir(parents=True)
        for key in COPIED:
            start = origin.get(key)
            if key == "cover" and not keep_letter:
                start = self.base.get("cover")
            if start and Path(start).is_file():
                shutil.copyfile(start, folder / FILES[key])
        if track is None and source:
            track = self._track_of(source)
        seed = {key: _sha(folder / FILES[key])
                for key in ("cv", "cover") if (folder / FILES[key]).is_file()}
        meta = {"id": uuid.uuid4().hex[:12], "name": name,
                "created": date.today().isoformat(), "opened": time.time(),
                "job_url": str(job_url)[:2000], "track": _clean_track(track), "seed": seed}
        (folder / META).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        return self.set_current(slug)

    def _free_slug(self, slug: str) -> str:
        """A slug no folder uses, live or archived. Any folder counts, even one
        without its app.json: moving into it would nest one application inside
        another (it happened to Shopify when two namings ran at once)."""
        candidate, n = slug, 2
        while (self.folder(candidate).exists()
               or self.folder(candidate, archived=True).exists()):
            candidate, n = f"{slug}-{n}", n + 1
        return candidate

    def rename(self, slug: str, name: str, *, reslug: bool = False) -> Application:
        """A new label.

        The folder normally keeps its slug, so any link to it still works.
        ``reslug`` moves the folder to match the new name instead: that is for
        an application that was created unnamed and has just been identified
        from its job description, where nothing can be pointing at the
        placeholder slug yet.
        """
        name = " ".join(str(name).split())[:MAX_NAME]
        if not name:
            raise ApplicationError("give the application a name")
        folder, archived = self.find(slug)
        meta = json.loads((folder / META).read_text(encoding="utf-8"))
        meta["name"] = name
        (folder / META).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        if reslug:
            wanted = self._free_slug(slugify(name))
            if wanted != slug:
                target = self.folder(wanted, archived=archived)
                # os.rename, not shutil.move: shutil.move puts the folder
                # inside an existing one instead of failing.
                if target.exists():
                    raise ApplicationError(f"{wanted!r} already exists")
                os.rename(folder, target)
                folder, slug = target, wanted
                if not archived:
                    self.set_current(slug)
        app = self._read(folder, archived)
        assert app is not None
        return app

    def archive(self, slug: str, *, archived: bool = True) -> Application:
        """Move an application in or out of the archive."""
        folder, already = self.find(slug)
        if already == archived:
            app = self._read(folder, already)
            assert app is not None
            return app
        target = self.folder(slug, archived=archived)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ApplicationError(f"{slug!r} already exists there")
        shutil.move(str(folder), str(target))
        if archived and self._stored_current() == slug:
            (self.root / CURRENT).unlink(missing_ok=True)
        app = self._read(target, archived)
        assert app is not None
        return app

    def delete(self, slug: str) -> None:
        """Remove an application and everything it owns, for good.

        Archive is the reversible option; this is the hard one, for an
        application that should never come back. If it was the open one, the
        pointer is cleared so the bare paths fall through to another.
        """
        folder, _ = self.find(slug)
        shutil.rmtree(folder)
        if self._stored_current() == slug:
            (self.root / CURRENT).unlink(missing_ok=True)

    def set_track(self, slug: str, track: str) -> Application:
        """Label an application's role type, by hand or from the posting.

        The known :data:`TRACKS` are the usual choices (and what identify
        writes). The edit dialog may also keep a short custom label so a
        niche role type is not forced into Other.
        """
        track = " ".join(str(track).split())[:MAX_TRACK]
        if not track:
            raise ApplicationError("give the application a track")
        folder, archived = self.find(slug)
        meta = json.loads((folder / META).read_text(encoding="utf-8"))
        meta["track"] = track
        (folder / META).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        app = self._read(folder, archived)
        assert app is not None
        return app

    def set_link(self, slug: str, url: str, page: str = "") -> Application:
        """Keep the posting's link, and the fingerprint of the page read from it."""
        url = str(url).strip()[:2000]
        if url and not url.startswith(("http://", "https://")):
            raise ApplicationError("that isn't a web link")
        folder, archived = self.find(slug)
        meta = json.loads((folder / META).read_text(encoding="utf-8"))
        meta["job_url"] = url
        meta["job_page"] = str(page)[:64]
        (folder / META).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        app = self._read(folder, archived)
        assert app is not None
        return app

    def untouched(self, slug: str, key: str = "cv") -> bool:
        """Whether the document is still byte for byte what :meth:`create` wrote.

        An edit he saved before the posting arrived is his, and nothing built
        from the library may write over it.

        A save of the unchanged copy can still change its bytes (the editor
        writes its own formatting), so a CV whose content equals the base it
        was copied from counts as untouched too.
        """
        folder, _ = self.find(slug)
        try:
            meta = json.loads((folder / META).read_text(encoding="utf-8"))
            if _sha(folder / FILES[key]) == (meta.get("seed") or {}).get(key):
                return True
        except (OSError, ValueError, AttributeError):
            pass
        base = self.base.get(key)
        return bool(base) and _load(folder / FILES[key]) == _load(Path(base)) != {}

    def reseed(self, slug: str, track: str) -> bool:
        """Fill the CV from the newest live application of ``track``.

        Only a CV whose bytes still match what :meth:`create` wrote is
        replaced, so an application the user has already saved or edited is
        never overwritten. Returns whether anything moved.

        The CV only. The letter used to come too, which meant the posting that
        named an application also handed it the previous application's finished
        letter, body and all, with the company and the role swapped in. A
        letter is written to one posting.
        """
        folder, _ = self.find(slug)
        source = self._latest_of_track(track, exclude=slug)
        if source is None:
            return False
        meta = json.loads((folder / META).read_text(encoding="utf-8"))
        seed = meta.get("seed") if isinstance(meta.get("seed"), dict) else {}
        moved = False
        for key in ("cv",):
            target, incoming = folder / FILES[key], source / FILES[key]
            if not incoming.is_file() or not target.is_file() or _sha(target) != seed.get(key):
                continue
            shutil.copyfile(incoming, target)
            seed[key] = _sha(target)
            moved = True
        if moved:
            meta["seed"] = seed
            (folder / META).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        return moved

    def _latest_of_track(self, track: str, *, exclude: str = "") -> Path | None:
        """The live application of ``track`` whose documents changed last."""
        if not track:
            return None
        best: Path | None = None
        newest = -1.0
        for app in self.list():
            if app.archived or app.slug == exclude or app.track != track:
                continue
            folder = self.folder(app.slug)
            stamps = [(folder / FILES[k]).stat().st_mtime
                      for k in ("cv", "cover") if (folder / FILES[k]).is_file()]
            if stamps and max(stamps) > newest:
                best, newest = folder, max(stamps)
        return best

    def _track_of(self, slug: str) -> str:
        try:
            folder, archived = self.find(slug)
        except ApplicationError:
            return ""
        app = self._read(folder, archived)
        return app.track if app else ""

    def _stored_current(self) -> str:
        try:
            return (self.root / CURRENT).read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    # -- first run -----------------------------------------------------

    def ensure(self, *, name: str = "", chat_dir: Path | None = None) -> Application:
        """Guarantee one application exists, migrating the old layout if needed.

        Before applications there was a single CV, cover letter and chat at the
        project root. The first run moves that work into an application named
        after whoever the cover letter was addressed to, so nothing is lost and
        nothing has to be re-tailored.
        """
        self.adopt_ids()
        live = [a for a in self.list() if not a.archived]
        if live:
            folder, archived = self.find(self.current)
            return self._read(folder, archived) or live[0]
        app = self.create(name or self._base_name())
        if chat_dir is not None:
            self._adopt_chats(app.slug, Path(chat_dir))
        return app

    def adopt_ids(self) -> int:
        """Give the applications made before ids one each, and a last-opened.

        Both are what the browser keys its unsaved draft and its fit reading
        on, so an application without them is one whose work a later
        application with the same slug could still inherit. ``opened`` starts
        from when the documents were last written, which is the closest thing
        on disk to when the application was last worked on.
        """
        changed = 0
        for app in self.list():
            folder = self.folder(app.slug, archived=app.archived)
            fields: dict[str, Any] = {}
            if not app.id:
                fields["id"] = uuid.uuid4().hex[:12]
            if not app.opened:
                stamps = [(folder / FILES[k]).stat().st_mtime
                          for k in ("cv", "cover") if (folder / FILES[k]).is_file()]
                fields["opened"] = max(stamps) if stamps else 0.0
            if fields:
                self._patch(folder, **fields)
                changed += 1
        return changed

    def _base_name(self) -> str:
        """``Meta — Rotational Product Manager``, from the base cover letter."""
        data = _load(self.base.get("cover"))
        company = str(data.get("company") or "").strip()
        role = str(data.get("role") or "").strip()
        return " | ".join(p for p in (company, role) if p) or "First application"

    def _adopt_chats(self, slug: str, chat_dir: Path) -> None:
        """Fold the old per-document chat files into one conversation.

        The CV chat runs first, then the cover letter's; each message is tagged
        with the document it was about, which is what keeps a proposal pointed
        at the right field after the conversation becomes shared.
        """
        messages: list[dict[str, Any]] = []
        job_description = ""
        for kind in ("cv", "cover"):
            old = _load(chat_dir / f"{kind}.json")
            for message in old.get("messages") or []:
                if isinstance(message, dict):
                    messages.append({**message, "document": kind})
            job_description = job_description or str(old.get("job_description") or "")
        if not messages and not job_description:
            return
        chat = self.paths(slug)["chat"]
        chat.write_text(
            json.dumps({"messages": messages, "job_description": job_description},
                       ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def _clean_track(track: str | None) -> str:
    """A known track, or "" for one that was never set."""
    track = str(track or "")
    return track if track in TRACKS else ""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path | None) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))  # type: ignore[arg-type]
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def as_json(app: Application) -> dict[str, Any]:
    return asdict(app)
