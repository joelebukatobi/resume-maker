"""A new CV filled from his whole history, the posting choosing every line.

The page is filled as a whole: once a keyword is on it, the next slot goes
after what is still missing, so five bullets do not all say "roadmap" while
"go-to-market" never appears.
"""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resume_maker import fill, library  # noqa: E402


def make_cv(*, summary="Product manager.", skills=None, entries=(), additional=()):
    return {
        "name": "Joel", "contact": [],
        "sections": [
            {"heading": "PROFESSIONAL SUMMARY", "type": "paragraph", "text": summary},
            {"heading": "EDUCATION", "type": "entries",
             "entries": [{"left": "FORDHAM UNIVERSITY", "right": "2026",
                          "bullets": ["MBA coursework."]}]},
            {"heading": "SKILLS", "type": "labeled",
             "items": [{"label": k, "text": v}
                       for k, v in (skills or {"AI": "Claude, MCP"}).items()]},
            {"heading": "EXPERIENCE", "type": "entries", "entries": [dict(e) for e in entries]},
            {"heading": "ADDITIONAL INFORMATION", "type": "labeled",
             "items": [{"label": "Interests", "text": t} for t in additional]},
        ],
    }


def job(left, bullets, **extra):
    return {"left": left, "right": "2023 – 2024", "sub_left": "Developer",
            "bullets": list(bullets), **extra}


USUAL = "Coordinated with client and internal teams to deliver 15+ web applications."
COMPONENTS = "Built 20+ reusable components from usability testing insights."
ZAPIER = "Automated client onboarding using Zapier, n8n, Webflow and Airtable."
INTERESTS = ("Deep Linux user exploring agentic AI workflows via MCPs and custom "
             "SKILL.md tooling. Currently picking up Rust.")
SPONSOR = "US permanent resident, no sponsorship required."
AMBIGUITY = "Comfortable in ambiguity with a track record of charting my own path."
ROADMAP_A = "Owned the product roadmap for a data product used by 600+ teams."
ROADMAP_B = "Prioritised the roadmap with engineering leads each quarter."
GTM = "Wrote a phased go-to-market plan and a product roadmap for the capstone."

BASE = make_cv(entries=[job("AMAZON", [ROADMAP_A, ROADMAP_B], budget={"bullets": 2}),
                        job("xPathEdge LLC, IT Services", [USUAL, COMPONENTS])],
               additional=[INTERESTS])


def built(cvs, terms, *, verdicts=None, labels=None, base=BASE):
    lines = library.collect(base, cvs, {})
    index = library.Index(lines)
    verdicts = verdicts if verdicts is not None else {l.text: "pass" for l in lines}
    return fill.fill(copy.deepcopy(base), index, terms, verdicts, labels or {})


def section(cv, word):
    return next(s for s in cv["sections"] if word in s["heading"])


def texts(cv):
    out = []
    for s in cv["sections"]:
        out.append(s.get("text") or "")
        for e in s.get("entries") or []:
            out.extend(e.get("bullets") or [])
        for i in s.get("items") or []:
            out.append(i.get("text") or "")
    return out


PAST = {
    "flexport": make_cv(entries=[job("XPATHEDGE LLC", [ZAPIER, USUAL])], additional=[SPONSOR]),
    "ramp": make_cv(entries=[job("xPathEdge LLC", [ZAPIER, COMPONENTS])],
                    additional=[SPONSOR + " " + AMBIGUITY]),
    "meta": make_cv(entries=[job("xPathEdge LLC", [USUAL, COMPONENTS]),
                             job("AMAZON", [GTM])],
                    additional=[AMBIGUITY, SPONSOR]),
}
LABELS = {AMBIGUITY: "self_description", SPONSOR: "authorisation",
          SPONSOR + " " + AMBIGUITY: "self_description", INTERESTS: "interests"}


class FillTests(unittest.TestCase):
    def test_a_posting_line_from_one_cv_does_not_carry_into_the_next(self):
        cv, _ = built(PAST, ["agentic workflows", "mcp"], labels=LABELS)
        xpath = section(cv, "EXPERIENCE")["entries"][1]["bullets"]
        self.assertEqual(sorted(xpath), sorted([USUAL, COMPONENTS]))
        self.assertNotIn(ZAPIER, texts(cv))

    def test_the_interests_line_comes_back_for_agentic_work(self):
        cv, _ = built(PAST, ["agentic workflows", "mcp"], labels=LABELS)
        self.assertEqual(section(cv, "ADDITIONAL")["items"][0]["text"], INTERESTS)

    def test_a_keyword_on_the_page_stops_counting(self):
        cv, _ = built(PAST, ["product roadmap", "go-to-market"], labels=LABELS)
        amazon = section(cv, "EXPERIENCE")["entries"][0]["bullets"]
        self.assertIn(GTM, amazon)

    def test_no_line_is_used_twice(self):
        cv, _ = built(PAST, ["roadmap"], labels=LABELS)
        printed = [t for t in texts(cv) if t]
        self.assertEqual(len(printed), len(set(printed)))

    def test_no_keywords_gives_the_usual_page(self):
        cv, cards = built(PAST, [], labels=LABELS)
        kinds = {c.kind for c in cards}
        self.assertFalse(kinds & {"changed", "close", "unanswered"})
        xpath = section(cv, "EXPERIENCE")["entries"][1]["bullets"]
        self.assertEqual(sorted(xpath), sorted([USUAL, COMPONENTS]))

    def test_a_self_description_never_lands_in_additional(self):
        base = make_cv(entries=BASE["sections"][3]["entries"], additional=[SPONSOR])
        cv, _ = built(PAST, ["ambiguity", "track record"], labels=LABELS, base=base)
        for item in section(cv, "ADDITIONAL")["items"]:
            self.assertNotEqual(LABELS.get(item["text"]), "self_description")

    def test_a_held_line_gives_way_and_says_so(self):
        cvs = {"flexport": PAST["flexport"]}
        lines = library.collect(BASE, cvs, {})
        verdicts = {l.text: "pass" for l in lines}
        verdicts[ZAPIER] = "unconfirmed"
        cv, cards = built(cvs, ["zapier", "airtable"], verdicts=verdicts)
        self.assertNotIn(ZAPIER, texts(cv))
        held = [c for c in cards if c.kind == "unconfirmed"]
        self.assertEqual([c.text for c in held], [ZAPIER])
        self.assertTrue(held[0].path.startswith("sections.3.entries.1.bullets."))

    def test_a_non_usual_pick_is_a_card(self):
        cv, cards = built(PAST, ["go-to-market"], labels=LABELS)
        changed = [c for c in cards if c.kind == "changed"]
        self.assertIn(GTM, [c.text for c in changed])
        self.assertTrue(all(c.other for c in changed))

    def test_what_nothing_answers_is_one_card(self):
        _, cards = built(PAST, ["conversational ai", "mcp"], labels=LABELS)
        unanswered = [c for c in cards if c.kind == "unanswered"]
        self.assertEqual(len(unanswered), 1)
        self.assertEqual(unanswered[0].keywords, ("conversational ai",))

    def test_fixed_fields_are_the_base(self):
        cv, _ = built(PAST, ["go-to-market", "mcp"], labels=LABELS)
        for got, want in zip(section(cv, "EXPERIENCE")["entries"],
                             section(BASE, "EXPERIENCE")["entries"]):
            for key in ("left", "right", "sub_left", "budget"):
                self.assertEqual(got.get(key), want.get(key))
        self.assertEqual(section(cv, "EDUCATION"), section(BASE, "EDUCATION"))

    def test_an_entry_that_answers_more_is_turned_on(self):
        base = make_cv(entries=[job("AMAZON", [ROADMAP_A]),
                                job("FORDHAM", ["Advised students."], off=True)])
        cvs = {"a": make_cv(entries=[job("FORDHAM", ["Ran go-to-market workshops for "
                                                     "agentic workflows."])])}
        cv, cards = built(cvs, ["go-to-market", "agentic workflows"], base=base)
        entries = section(cv, "EXPERIENCE")["entries"]
        self.assertFalse(entries[1].get("off"))
        self.assertTrue(entries[0].get("off"))
        self.assertIn("cast", {c.kind for c in cards})


    def test_an_over_long_bullet_is_swapped_for_a_shorter_one(self):
        long = ("Coordinated with client and internal teams across three time zones "
                "to deliver 15+ responsive, accessible web applications on schedule.")
        base = make_cv(entries=[job("xPathEdge LLC", [long])])
        cvs = {"a": make_cv(entries=[job("xPathEdge LLC", [USUAL])])}
        lines = library.collect(base, cvs, {})
        index = library.Index(lines)
        cv, _ = fill.fill(copy.deepcopy(base), index, [], {l.text: "pass" for l in lines}, {})
        cv["sections"][3]["entries"][0]["bullets"] = [long]
        swapped = fill.shorten(cv, index, [], {l.text: "pass" for l in lines},
                               [("xPathEdge LLC", 1)])
        self.assertEqual(cv["sections"][3]["entries"][0]["bullets"], [USUAL])
        self.assertEqual(swapped, ["sections.3.entries.0.bullets.0"])


    def test_a_struck_base_bullet_does_not_come_back(self):
        base = make_cv(entries=[job("Acme", ["Shipped A widget.", "Shipped B widget.", "Ran C ops."])])
        for struck in ("Ran C ops.", "Shipped A widget."):
            with self.subTest(struck=struck):
                decisions = library.Decisions()
                decisions.struck.add(struck)
                lines = library.usable(library.collect(base, {}, {}), decisions)
                cv, _ = fill.fill(copy.deepcopy(base), library.Index(lines), [],
                                  {l.text: "pass" for l in lines}, {})
                bullets = section(cv, "EXPERIENCE")["entries"][0]["bullets"]
                self.assertNotIn(struck, bullets)
                self.assertEqual(len(bullets), len(set(bullets)))

    def test_an_unconfirmed_card_carries_its_note(self):
        cvs = {"flexport": PAST["flexport"]}
        lines = library.collect(BASE, cvs, {})
        verdicts = {l.text: "pass" for l in lines}
        verdicts[ZAPIER] = "unconfirmed"
        _, cards = fill.fill(copy.deepcopy(BASE), library.Index(lines), ["zapier"], verdicts, {},
                             evidence={ZAPIER: "I used Zapier."})
        card = next(c for c in cards if c.kind == "unconfirmed")
        self.assertEqual(card.passage, "I used Zapier.")


class BlankTests(unittest.TestCase):
    def test_a_new_cv_keeps_only_the_frame(self):
        base = make_cv(entries=[job("AMAZON", [ROADMAP_A], budget={"bullets": 2}),
                                job("FORDHAM", ["Advised."], off=True)],
                       additional=[INTERESTS])
        cv = fill.blank(copy.deepcopy(base))
        self.assertEqual(section(cv, "SUMMARY")["text"], "")
        self.assertEqual(section(cv, "EDUCATION"), section(base, "EDUCATION"))
        self.assertEqual([i["label"] for i in section(cv, "SKILL")["items"]], ["AI"])
        self.assertEqual([i["text"] for i in section(cv, "SKILL")["items"]], [""])
        self.assertEqual([i["text"] for i in section(cv, "ADDITIONAL")["items"]], [""])
        for got, want in zip(section(cv, "EXPERIENCE")["entries"],
                             section(base, "EXPERIENCE")["entries"]):
            self.assertEqual(got["bullets"], [])
            for key in ("left", "right", "sub_left", "budget", "off"):
                self.assertEqual(got.get(key), want.get(key))
        self.assertEqual([s["heading"] for s in cv["sections"]],
                         [s["heading"] for s in base["sections"]])

    def test_the_frame_still_previews(self):
        from resume_maker.build import Builder
        from resume_maker.model import CV
        from resume_maker.theme import Theme
        cv = fill.blank(copy.deepcopy(BASE))
        html = Builder(CV.from_mapping(cv), Theme.load(ROOT / "theme.json")).print_html()
        self.assertIn("ADDITIONAL INFORMATION", html.upper())


if __name__ == "__main__":
    unittest.main()
