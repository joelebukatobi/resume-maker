"""How much of a job description the document actually answers.

This is deliberately mechanical. It counts which words and phrases the posting
leans on and checks whether they appear in the document, so the number costs
nothing, never varies between runs, and can be explained by pointing at a list
of missing terms. It is a coverage measure, not a judgement: a CV can score
well and still be a bad application, and a model is the right tool for that
question. What this removes is the other half, the mechanical half, that was
being asked of a model turn after turn.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

# Keeps "c++", "node.js" and "a/b" whole; drops the full stop at the end of
# a sentence, which otherwise arrives glued to the last word.
_WORD = re.compile(r"[a-z][a-z0-9+#]*(?:[./][a-z0-9+#]+)*")
# The words every posting uses, which say nothing about whether you fit.
STOPWORDS = frozenset("""
a about above across after against all also am an and any are as at be because been
before being below between both but by can cannot could did do does doing down during
each few for from further had has have having he her here hers him his how i if in into
is it its itself just me more most my no nor not of off on once only or other our out
over own same she should so some such than that the their them then there these they
this those through to too under until up very was we were what when where which while
who whom why will with would you your yours
ability able across addition additional applicant apply applying april august benefits
candidate candidates company culture day days december dear december degree description
employee employee's employer employment equal experience february friday full gender
help here hire hiring hour hours including job january july june keywords legal level
like look looking make march may minimum monday month months must need needed new
november october offer offers one opportunity part pay people per please position
preferred provide qualifications read required requirements responsibilities role
salary september skills start strong team teams thursday time tuesday understand
us use used using wednesday week weeks what work working year years you'll
across along already also always another anyone around back best better big both
bring build building built come comes doing done don drive driven else enough etc
every everything find first get give go going good great happen help helps high
however idea ideas keep know known large later lead learn least less let life little
long lot made many matter mean means might move much never next often open order
others own part particular people person place point put quickly really right say
see seek seeking set several small something sometimes soon still take taken tell
thing things think together took turn want way well whether within without
asking rather meet meets whole moving runs run doesn isn aren didn won wasn
weren hasn haven couldn wouldn shouldn e.g i.e etc.
compensation salaries bonus equity insurance dental vision 401k pto holiday
holidays leave parental diversity inclusive inclusion veteran disability
accommodation accommodations background check eeo employer discrimination
regardless race color colour religion sex orientation identity national origin
status protected law laws applicable base range annually hourly posting apply
application applications resume cv cover letter candidate recruiter interview
process stage stages offer onboarding remote hybrid onsite office
world society perspectives member members deep class qualified applicants
employees spouses children usd requisition unique trusted exceptional
passionate proficiency expertise familiarity fluency
""".split())
MAX_TERMS = 40

# The benefits table, the salary line and the equal-opportunity paragraph are
# not the job. Counted with the rest, they dominated: on one consulting posting
# the forty terms included "employees spouses", "usd", "cost", "coverage" and
# "qualified applicants", and the heaviest term of all was the company's own
# acronym. A CV cannot honestly contain any of it, so the missing-terms list
# was asking for words that could not help.
#
# Unambiguous markers. One is enough to drop the line it sits on.
_BOILERPLATE = re.compile(r"""\b(?:
  equal\s+(?:employment\s+)?opportunit\w* | qualified\s+applicant\w*
| without\s+regard(?:less)?\s+(?:to|of) | regardless\s+of\s+race
| affirmative\s+action | protected\s+veteran\w* | criminal\s+histor\w*
| e-?verify | base\s+(?:salary|compensation|pay) | salar\w*\s+(?:range\w*|start\w*)
| compensation\s+for\s+this\s+role | usd | 401\s?\(?k\)? | copay\w*
| orthodontia | egg\s+freezing | surrogacy | profit\s+sharing | paid\s+time\s+off
| parental\s+leave | gym\s+membership\w* | reasonable\s+accommodation\w*
| tuition\s+reimbursement | requisition\w* | fair\s+chance | conviction\s+record\w*
| benefit\w*\s+(?:program\w*|package\w*)
# Where you must live and which pay band applies. Not the job, and on one
# posting a single sentence about pay zones produced three of the heaviest
# terms in the count: "called geo", "geo covering", "metropolitan area".
| pay\s+(?:zone\w*|transparency|range\w*) | geographic\s+pay | labor\s+market\w*
| geo-?[12]\b | metropolitan\s+area\w* | commutable\s+distance | work\s+mode\w*
| relocation | commuting\s+distance
# The job board's own furniture, which travels with a pasted posting.
| job\s+requisition | requisition\s+id | remote\s+type | time\s+type | posted\s+on
| continue\s+application | keywords?\s+matched | improve\s+my\s+match
| (?:fair|good|strong|weak)\s+match | save\s+job | apply\s+now | share\s+this\s+job
)\b | \$\s?\d""", re.I | re.X)
# Words that are the benefits table in one posting and the industry in the
# next, so "a leading insurance company" survives and "health, dental and
# vision coverage" does not. Two distinct hits on one line to drop it.
_PERKS = re.compile(r"""\b(?:
  insurance | dental | retirement | benefits? | premiums? | vacation | wellness
| holidays? | disability | flexible\s+time | pto | discretionary | bonus
)\b""", re.I | re.X)
_LOCATIONS = re.compile(r"^\s*locations?\s*[:\-]", re.I)
# Below this much of the posting left, the filter has misfired rather than
# found an unusually administrative posting, and less trimming is used.
_KEEP_AT_LEAST = 0.15
_KEEP_AT_LEAST_CHARS = 200
# A line longer than this is a wall of prose rather than a list item, so it is
# filtered by sentence: one marker in one sentence should not take the rest.
_LONG_LINE = 240


def _lines(job_description: str) -> Iterator[str]:
    for line in job_description.splitlines():
        if len(line) <= _LONG_LINE:
            yield line
        else:
            yield from re.split(r"(?<=[.!?])\s+", line)


# "posted on", "Posted Yesterday": a job board's own line about the listing.
_POSTED = re.compile(r"^\s*posted\b", re.I)


def _is_boilerplate(line: str) -> bool:
    if _LOCATIONS.search(line) or _BOILERPLATE.search(line):
        return True
    if _POSTED.search(line) and len(line.split()) <= 4:
        return True
    return len({m.group(0).lower() for m in _PERKS.finditer(line)}) >= 2


# A heading introducing a part of the posting that is not the job: who the
# company is, what it pays, where you must sit, how to apply, and the law.
# Counted with the rest, that material won: BCG's heaviest terms came from its
# benefits table and its own blurb, and Etsy's from its pay zones.
_NOT_THE_JOB = re.compile(r"""^\W{0,3}(?:
  compan(?:y|ies)\s+(?:description|overview|profile) | who\s+we\s+are
| about\s+(?:us|the\s+\w+|\w+)$ | our\s+(?:story|promise|mission|values|culture|commitment|team)\b
| life\s+at\b | why\s+(?:join|work) | benefits? | perks | compensation | total\s+rewards
| pay\s+(?:range|transparency|zones?) | salary | location | work\s+(?:mode|arrangement|environment)s?
| how\s+to\s+apply | what'?s?\s+next | application\s+process | equal\s+(?:employment\s+)?opportunity
| eeo | diversity | legal | disclaimer | privacy | additional\s+info
| note\s+to\s+(?:applicants|candidates)
)\b""", re.I | re.X)
# A heading is short, is not a sentence, and reads as a label rather than as
# prose. Without the last part every bullet in a benefits list counted as a
# heading, which ended the section it belonged to and kept the rest of the
# list: "Vision insurance with coverage for both glasses and contact lenses"
# became a heading of its own and survived.
_HEADING_WORDS = 10
_HEADING_CHARS = 72
_LABEL_WORDS = 6


def _is_heading(line: str) -> bool:
    text = line.strip().lstrip("#•●-* ").rstrip(":?").strip()
    if not text or len(text) > _HEADING_CHARS or len(text.split()) > _HEADING_WORDS:
        return False
    if text.endswith((".", ",", ";")):
        return False
    if line.rstrip().endswith((":", "?")) or text.isupper():
        return True
    words = text.split()
    if len(words) > _LABEL_WORDS or not text[0].isupper():
        return False
    long = [w for w in words if len(w) > 3]
    capitals = [w for w in long if w[0].isupper()]
    return not long or len(capitals) * 2 >= len(long)


def _is_not_the_job(head: str) -> bool:
    """Whether a heading introduces something other than the job.

    The name is matched at the start, so a role called "Benefits Platform PM"
    keeps its requirements; a heading that is itself a pay line, such as "The
    first year base compensation for this role is:", is caught by the markers.
    """
    text = head.strip().lstrip("#•●-* ")
    return bool(_NOT_THE_JOB.search(text) or _BOILERPLATE.search(text))


def _sections(text: str) -> Iterator[tuple[str, list[str]]]:
    """``(heading, lines)`` for each part of the posting, heading first."""
    head, body = "", []
    for line in text.splitlines():
        if _is_heading(line) and (head or body):
            yield head, body
            head, body = line, []
        elif _is_heading(line) and not head:
            head = line
        else:
            body.append(line)
    yield head, body


# A job board's own header sits above the posting: the title, "Partially
# Remote", "locations Brooklyn, New York", "Posted Yesterday", a requisition
# code, and on one site a keyword-match widget. It arrives as a run of one-line
# fragments rather than as a block, and pasted along with the posting it was
# counted as requirements: "partially", "brooklyn", "jr6037", "final", "gap".
#
# The run is everything before the first section that contains a sentence. It
# is only dropped when there are several of them, so a posting that opens with
# a short line or two keeps them.
_HEADER_RUN = 3
_SENTENCE_CHARS = 80


def _header_run(sections: Sequence[tuple[str, list[str]]]) -> int:
    """How many leading sections are the job board's own furniture."""
    for index, (_, body) in enumerate(sections):
        if any(len(line.strip()) >= _SENTENCE_CHARS for line in body):
            return index if index >= _HEADER_RUN else 0
    return 0


def job_text(job_description: str) -> str:
    """The posting cut back to the part that describes the job.

    Two passes. Whole sections go when their heading says they are not the job,
    which is what removes a company's blurb and its pay bands. Then the
    surviving lines go through the marker filter, which catches the same
    material where a posting has no headings to go by.

    A posting that ends up too short has defeated the heuristics rather than
    been unusually administrative, so the trimming is given up a stage at a
    time: sections and lines, then lines alone, then the posting as pasted.
    """
    parts = list(_sections(job_description))
    by_section = "\n".join(
        line
        for head, body in parts[_header_run(parts):]
        if not _is_not_the_job(head)
        for line in (head, *body)
        if line.strip() and not _is_boilerplate(line)
    )
    by_line = "\n".join(line for line in _lines(job_description)
                        if not _is_boilerplate(line))
    for kept in (by_section, by_line):
        if len(kept) >= _KEEP_AT_LEAST_CHARS and len(kept) >= len(job_description) * _KEEP_AT_LEAST:
            return kept
    return job_description
# Endings stripped before comparing, longest first. A posting asking for
# "tracking" and a CV that says "tracked" are talking about the same thing,
# and counting that as a miss made the number say less than it should.
# "e" last, and only on a word long enough to survive it. Without it the
# plural and the singular of the same word stemmed differently: "pipelines"
# lost "es" to leave "pipelin" while "pipeline" kept its "e", so a posting
# asking for data pipelines counted a CV that says "data pipeline" as a miss.
_ENDINGS = ("ization", "isation", "ational", "ments", "ingly", "ment", "ions",
            "ings", "ion", "edly", "ies", "ied", "ive", "ers", "est", "ing",
            "ed", "es", "er", "ly", "al", "s", "e")


def stem(word: str) -> str:
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 4:
            return word[: -len(ending)]
    return word


@dataclass(frozen=True, slots=True)
class Match:
    score: int
    matched: tuple[str, ...]
    missing: tuple[str, ...]

    def as_json(self) -> dict[str, object]:
        return {"score": self.score, "matched": list(self.matched),
                "missing": list(self.missing)}


def _words(text: str) -> list[str]:
    """Every word, with a slash-joined pair counted as its parts as well.

    The pattern keeps "node.js", "ci/cd" and "a/b" whole on purpose, which also
    kept "React/TypeScript" whole: it could never match a CV that lists React
    and TypeScript separately, so the posting's two clearest keywords counted
    as one term nobody could answer. Only parts of three characters or more are
    split out, which leaves the short pairs alone.
    """
    out = []
    for word in _WORD.findall(text.lower()):
        parts = word.split("/")
        if len(parts) > 1 and all(len(p) >= 3 for p in parts):
            # Each side counts on its own, with a separator between them so no
            # phrase is built across the slash. "React/TypeScript" as a pair
            # swallowed both singles and then matched nothing, because a CV
            # lists React and TypeScript in a skills line rather than side by
            # side in that order.
            for part in parts:
                out.extend((part, "/"))
            out.pop()
        else:
            out.append(word)
    return out


# Where a posting lists the skills it wants: "in languages such as PHP,
# Python, Ruby, Java, Go, or JavaScript/TypeScript". Each is named once, so by
# frequency alone they ranked 150th and never reached the count, while the
# culture paragraph's third "believe" did. A comma list is the one place a
# posting reliably writes down what it is actually asking for.
_LIST_COMMAS = 3
_LIST_WORDS = 2
_LIST_CHARS = 24
_LIST_WEIGHT = 2
# How many of the budget's places the listed skills may take, and how many
# items a line needs before it counts as a list at all.
MAX_LISTED = 20
_LIST_DENSITY = 4


def _listed(text: str) -> Counter[str]:
    """Short items from the posting's comma-separated lists.

    Only from a line that is mostly a list. A sentence with commas in it
    ("Of course, this is just a sample of the kinds of work…") yields two or
    three fragments and would otherwise spend the budget that the line naming
    six languages needs.
    """
    found: Counter[str] = Counter()
    for line in text.splitlines():
        if line.count(",") < _LIST_COMMAS:
            continue
        items = []
        lead = re.split(r"\b(?:such\s+as|including|e\.g\.?|like)\b", line, 1)[-1]
        for item in re.split(r",|\bor\b|\band\b|/", lead.split(":", 1)[-1]):
            words = [w for w in _words(item) if w not in STOPWORDS and len(w) > 2]
            term = " ".join(words)
            if 1 <= len(words) <= _LIST_WORDS and len(term) <= _LIST_CHARS:
                items.append(term)
        if len(items) >= _LIST_DENSITY:
            found.update({item: _LIST_WEIGHT for item in items})
    return found


def _terms(job_description: str) -> list[tuple[str, int]]:
    """The posting's own vocabulary, as ``(term, weight)``, heaviest first.

    Two word phrases are kept when the posting repeats them, because "product
    management" and "data pipeline" say more than either word alone.
    """
    words = _words(job_description)
    singles = Counter(w for w in words if w not in STOPWORDS and len(w) > 2)
    pairs = Counter(
        f"{a} {b}" for a, b in zip(words, words[1:])
        if a not in STOPWORDS and b not in STOPWORDS and len(a) > 2 and len(b) > 2
    )
    terms: Counter[str] = Counter()
    for phrase, count in pairs.items():
        if count >= 2:
            terms[phrase] = count * 2  # a repeated phrase is the posting's own language
            for word in phrase.split():
                singles.pop(word, None)  # counted once, as the phrase
    terms.update(singles)
    # The listed skills come first and the frequency ranking fills what is
    # left. A skill named once lost its place to a culture paragraph's third
    # "believe" otherwise, and raising the budget for everything instead
    # diluted every score with prose the CV was never meant to answer.
    listed = _listed(job_description)
    # Shortest first, because a technology is a word and a fragment of prose
    # is a phrase. Every item here carries the same weight, so without an
    # order of its own the budget went to whichever list appeared first in the
    # posting, which on one of them was the team's job titles.
    order = sorted(listed, key=lambda term: (len(term.split()), len(term)))
    first = [(term, listed[term]) for term in order[:MAX_LISTED]]
    taken = {term for term, _ in first}
    rest = [(term, weight) for term, weight in terms.most_common()
            if term not in taken]
    return first + rest[: max(0, MAX_TERMS - len(first))]


def _document_text(parts: Iterable[str]) -> tuple[str, set[str]]:
    """The document reduced to stems, as one string and as a set of words."""
    words = [stem(w) for w in _words(" ".join(parts))]
    return " ".join(words), set(words)


def score_against(parts: Sequence[str], terms: Sequence[str]) -> Match:
    """Coverage of a list of requirements, each counted once.

    The counted terms in :func:`score` come out of the posting's own
    repetitions, which cannot tell a requirement from the team's org chart: on
    one engineering posting "marketplace makers" and "star seller" weighed 6
    each while "ruby", a requirement the CV lacked, weighed 2. Given a list of
    requirements instead, every one counts the same, and a miss is something
    the candidate could actually go and have.

    A phrase counts when all of its words appear in one line of the document,
    in any order. "low-code tooling" is answered by a bullet about "onboarding
    tooling on low code platforms"; the exact-phrase match could not see it.
    One line, not the whole document, so a stray "tooling" in another bullet
    does not complete it.
    """
    # Normalised and original kept together: filtering one list and zipping
    # it against the other shifted every keyword after an empty one.
    pairs = [(" ".join(_words(term)), term) for term in terms]
    pairs = [(norm, term) for norm, term in pairs if norm.replace("/", "").strip()]
    if not pairs:
        return Match(0, (), ())
    _, words = _document_text(parts)
    lines = [set(stem(w) for w in _words(part)) - {"/"} for part in parts]
    matched, missing = [], []
    for term, original in pairs:
        # The split leaves a "/" between the halves of a longer pair. It is a
        # separator, not a word, and counting it let any slash complete a pair.
        stems = {stem(part) for part in term.split() if part != "/"}
        if len(stems) == 1:
            found = stems <= words
        else:
            found = any(stems <= line for line in lines)
        (matched if found else missing).append(original)
    return Match(round(100 * len(matched) / len(pairs)), tuple(matched), tuple(missing))


def score(parts: Sequence[str], job_description: str, company: str = "") -> Match:
    """Coverage of the posting's vocabulary, 0 to 100, with what is missing.

    ``parts`` is every piece of text in the document, and ``company`` is the
    hiring company, whose own name is dropped from the vocabulary. An empty
    posting scores zero rather than a hundred, because nothing has been
    matched against.
    """
    terms = _terms(job_text(job_description or ""))
    # The company's own name is not a skill, and nobody lists it on a CV. Nor
    # is the acronym it calls itself, which outweighed every real term on one
    # posting because the name it was stripped by was spelled out in full.
    hidden = {w for w in _words(company) if len(w) > 2}
    initials = "".join(word[0] for word in _words(company) if len(word) > 2)
    if len(initials) > 1:
        hidden.add(initials)
    if hidden:
        terms = [(t, w) for t, w in terms if not hidden.intersection(t.split())]
    if not terms:
        return Match(0, (), ())
    text, words = _document_text(parts)
    matched, missing, hit, total = [], [], 0, 0
    for term, weight in terms:
        total += weight
        key = " ".join(stem(part) for part in term.split())
        found = key in text if " " in term else key in words
        if found:
            hit += weight
            matched.append(term)
        else:
            missing.append(term)
    return Match(round(100 * hit / total), tuple(matched), tuple(missing[:12]))
