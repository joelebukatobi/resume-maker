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
from typing import Iterable, Sequence

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
""".split())
MAX_TERMS = 40
# Endings stripped before comparing, longest first. A posting asking for
# "tracking" and a CV that says "tracked" are talking about the same thing,
# and counting that as a miss made the number say less than it should.
_ENDINGS = ("ization", "isation", "ational", "ments", "ingly", "ment", "ions",
            "ings", "ion", "edly", "ies", "ied", "ive", "ers", "est", "ing",
            "ed", "es", "er", "ly", "al", "s")


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
    return [w for w in _WORD.findall(text.lower())]


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
    return terms.most_common(MAX_TERMS)


def _document_text(parts: Iterable[str]) -> tuple[str, set[str]]:
    """The document reduced to stems, as one string and as a set of words."""
    words = [stem(w) for w in _words(" ".join(parts))]
    return " ".join(words), set(words)


def score(parts: Sequence[str], job_description: str, company: str = "") -> Match:
    """Coverage of the posting's vocabulary, 0 to 100, with what is missing.

    ``parts`` is every piece of text in the document, and ``company`` is the
    hiring company, whose own name is dropped from the vocabulary. An empty
    posting scores zero rather than a hundred, because nothing has been
    matched against.
    """
    terms = _terms(job_description or "")
    # The company's own name is not a skill, and nobody lists it on a CV.
    hidden = {w for w in _words(company) if len(w) > 2}
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
