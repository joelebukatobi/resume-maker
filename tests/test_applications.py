"""One folder per job application: creating, switching, archiving, migrating."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from resume_maker.applications import ApplicationError, Workspace, slugify


class WorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="apps-test-"))
        self.base = {
            "cv": self.tmp / "cv.json",
            "cover": self.tmp / "cover.json",
            "theme": self.tmp / "theme.json",
            "cover_theme": self.tmp / "cover-theme.json",
        }
        self.base["cv"].write_text(json.dumps({"name": "Joel Onwuanaku", "sections": []}))
        self.base["cover"].write_text(json.dumps(
            {"name": "Joel Onwuanaku", "company": "Meta", "role": "Rotational Product Manager"}))
        self.base["theme"].write_text(json.dumps({"font": "Cambria"}))
        self.base["cover_theme"].write_text(json.dumps({"font": "Cambria"}))
        self.space = Workspace(self.tmp / "applications", self.base)

    def test_slugs_are_safe_directory_names(self):
        self.assertEqual(slugify("Meta — Rotational PM"), "meta-rotational-pm")
        self.assertEqual(slugify("../../etc"), "etc")
        self.assertEqual(slugify("!!!"), "application")

    def test_create_copies_the_base_documents(self):
        app = self.space.create("Osmo — Software TPM", job_url="https://osmo.example/jobs/1")
        self.assertEqual(app.slug, "osmo-software-tpm")
        paths = self.space.paths(app.slug)
        self.assertEqual(json.loads(paths["cv"].read_text())["name"], "Joel Onwuanaku")
        self.assertTrue(paths["theme"].is_file())
        self.assertEqual(self.space.current, app.slug)
        self.assertEqual(self.space.list()[0].job_url, "https://osmo.example/jobs/1")

    def test_editing_one_application_leaves_the_others_alone(self):
        first = self.space.create("Meta — RPM")
        self.space.paths(first.slug)["cv"].write_text(json.dumps({"name": "Tailored for Meta"}))
        second = self.space.create("Osmo — TPM")
        self.assertEqual(json.loads(self.space.paths(second.slug)["cv"].read_text())["name"],
                         "Joel Onwuanaku")
        self.assertEqual(json.loads(self.base["cv"].read_text())["name"], "Joel Onwuanaku")

    def test_duplicate_starts_from_another_application(self):
        first = self.space.create("Meta — RPM")
        self.space.paths(first.slug)["cover"].write_text(json.dumps({"company": "Meta"}))
        copy = self.space.create("Meta — PMT", source=first.slug)
        self.assertEqual(json.loads(self.space.paths(copy.slug)["cover"].read_text())["company"],
                         "Meta")

    def test_a_new_application_inherits_documents_but_not_the_chat(self):
        first = self.space.create("Meta — RPM")
        paths = self.space.paths(first.slug)
        paths["cv"].write_text(json.dumps({"name": "Tailored for Meta"}))
        paths["chat"].write_text(json.dumps({"messages": [{"role": "user", "content": "hi"}]}))
        second = self.space.create("Osmo — TPM", source=first.slug)
        copied = self.space.paths(second.slug)
        self.assertEqual(json.loads(copied["cv"].read_text())["name"], "Tailored for Meta")
        self.assertFalse(copied["chat"].exists())

    def test_track_is_recorded_and_validated(self):
        app = self.space.create("Meta — RPM")
        self.assertEqual(app.track, "")                    # untagged until set
        self.assertEqual(self.space.list()[0].track, "")
        tagged = self.space.set_track(app.slug, "Product")
        self.assertEqual(tagged.track, "Product")
        self.assertEqual(self.space.list()[0].track, "Product")
        custom = self.space.set_track(app.slug, "Wizard")
        self.assertEqual(custom.track, "Wizard")
        with self.assertRaises(ApplicationError):
            self.space.set_track(app.slug, "  ")

    def test_a_copy_inherits_the_source_track(self):
        first = self.space.create("Meta — RPM")
        self.space.set_track(first.slug, "Product")
        copy = self.space.create("Meta — PMT", source=first.slug)
        self.assertEqual(copy.track, "Product")

    def test_reseed_pulls_the_newest_same_track_cv(self):
        product = self.space.create("Meta — RPM")
        self.space.set_track(product.slug, "Product")
        self.space.paths(product.slug)["cv"].write_text(json.dumps({"name": "Product CV"}))
        fresh = self.space.create("Osmo — TPM")            # from the base, untouched
        self.space.set_track(fresh.slug, "Product")
        self.assertTrue(self.space.reseed(fresh.slug, "Product"))
        self.assertEqual(json.loads(self.space.paths(fresh.slug)["cv"].read_text())["name"],
                         "Product CV")

    def test_reseed_leaves_an_edited_cv_alone(self):
        product = self.space.create("Meta — RPM")
        self.space.set_track(product.slug, "Product")
        self.space.paths(product.slug)["cv"].write_text(json.dumps({"name": "Product CV"}))
        fresh = self.space.create("Osmo — TPM")
        self.space.set_track(fresh.slug, "Product")
        self.space.paths(fresh.slug)["cv"].write_text(json.dumps({"name": "My own edit"}))
        self.space.reseed(fresh.slug, "Product")
        self.assertEqual(json.loads(self.space.paths(fresh.slug)["cv"].read_text())["name"],
                         "My own edit")

    def test_reseed_never_brings_another_postings_letter(self):
        """What it used to do. Naming an application handed it the previous
        application's finished letter with the company and role swapped in, so
        a veterinary marketplace was pitched a letter about Figma and design
        systems. A letter is written to one posting."""
        product = self.space.create("Fizz — Design PM")
        self.space.set_track(product.slug, "Product")
        self.space.paths(product.slug)["cover"].write_text(
            json.dumps({"company": "Fizz", "paragraphs": ["Design has been part of my work."]}))
        fresh = self.space.create("Vetcove — APM")
        self.space.set_track(fresh.slug, "Product")
        self.space.reseed(fresh.slug, "Product")
        letter = json.loads(self.space.paths(fresh.slug)["cover"].read_text())
        self.assertNotIn("paragraphs", letter)           # the base letter's shape, not Fizz's

    def test_a_plain_new_application_starts_the_letter_from_the_base(self):
        first = self.space.create("Fizz — Design PM")
        self.space.paths(first.slug)["cv"].write_text(json.dumps({"name": "Tailored CV"}))
        self.space.paths(first.slug)["cover"].write_text(
            json.dumps({"company": "Fizz", "role": "Design PM"}))
        chained = self.space.create("Vetcove — APM", source=first.slug, keep_letter=False)
        # The CV carries forward, because tailoring compounds ...
        self.assertEqual(json.loads(self.space.paths(chained.slug)["cv"].read_text())["name"],
                         "Tailored CV")
        # ... the letter does not, because it argued another posting's case.
        self.assertEqual(json.loads(self.space.paths(chained.slug)["cover"].read_text())["company"],
                         "Meta")
        # Duplicate asks for a source by name, and keeps the letter.
        copy = self.space.create("Fizz — Design PM 2", source=first.slug)
        self.assertEqual(json.loads(self.space.paths(copy.slug)["cover"].read_text())["company"],
                         "Fizz")

    def test_reseed_does_nothing_without_a_same_track_source(self):
        solo = self.space.create("Meta — RPM")
        self.assertFalse(self.space.reseed(solo.slug, "Engineering"))

    def test_an_archived_application_is_never_a_source(self):
        product = self.space.create("Meta — RPM")
        self.space.set_track(product.slug, "Product")
        self.space.paths(product.slug)["cv"].write_text(json.dumps({"name": "Product CV"}))
        self.space.create("Osmo — TPM")                    # one live app remains
        self.space.archive(product.slug)
        fresh = self.space.create("Acme — TPM")
        self.assertFalse(self.space.reseed(fresh.slug, "Product"))

    def test_names_may_repeat_without_colliding_on_disk(self):
        first = self.space.create("Meta")
        second = self.space.create("Meta")
        self.assertNotEqual(first.slug, second.slug)
        self.assertEqual(second.slug, "meta-2")

    def test_archive_hides_an_application_without_losing_it(self):
        app = self.space.create("Meta — RPM")
        self.space.create("Osmo — TPM")
        self.space.archive(app.slug)
        listed = {a.slug: a.archived for a in self.space.list()}
        self.assertTrue(listed[app.slug])
        # The cover letter is still there for the company's next role.
        self.assertTrue(self.space.paths(app.slug)["cover"].is_file())
        self.space.archive(app.slug, archived=False)
        self.assertFalse(self.space.list()[0].archived)

    def test_archiving_the_open_application_moves_on_to_another(self):
        open_app = self.space.create("Meta — RPM")
        other = self.space.create("Osmo — TPM")
        self.space.set_current(open_app.slug)
        self.space.archive(open_app.slug)
        self.assertEqual(self.space.current, other.slug)

    def test_an_archived_application_cannot_be_opened_until_restored(self):
        app = self.space.create("Meta — RPM")
        self.space.create("Osmo — TPM")
        self.space.archive(app.slug)
        with self.assertRaises(ApplicationError):
            self.space.set_current(app.slug)

    def test_deleting_the_open_application_falls_back_to_the_last_one_opened(self):
        """``created`` is a date, so applications made the same day tie on it
        and the slug broke the tie. Deleting the open application then fell
        back to an alphabetical winner rather than the one last worked on."""
        zulu = self.space.create("Zulu — PM")            # sorts first, reversed
        alpha = self.space.create("Alpha — PM")
        doomed = self.space.create("Doomed — PM")
        self.space.set_current(alpha.slug)               # worked on ...
        self.space.set_current(doomed.slug)              # ... then this one, now deleted
        self.space.delete(doomed.slug)
        self.assertEqual(self.space.current, alpha.slug)
        self.assertNotEqual(self.space.current, zulu.slug)

    def test_a_new_application_starts_from_the_one_opened_most_recently(self):
        older = self.space.create("Zulu — PM")
        self.space.paths(older.slug)["cv"].write_text(json.dumps({"name": "Older"}))
        recent = self.space.create("Alpha — PM")
        self.space.paths(recent.slug)["cv"].write_text(json.dumps({"name": "What I use now"}))
        self.space.set_current(recent.slug)
        self.space.delete(recent.slug)                   # the pointer goes with it
        fresh = self.space.create("New — PM", source=self.space.current)
        self.assertEqual(json.loads(self.space.paths(fresh.slug)["cv"].read_text())["name"],
                         "Older")

    def test_an_id_is_unique_and_survives_a_rename(self):
        """The browser keys the unsaved draft and the fit reading on the id.
        A slug is reused by the next application made from the same posting,
        which would otherwise open with the deleted one's work in it."""
        first = self.space.create("Meta — RPM")
        self.assertTrue(first.id)
        renamed = self.space.rename(first.slug, "Meta — Rotational PM", reslug=True)
        self.assertEqual(renamed.id, first.id)
        self.space.delete(renamed.slug)
        again = self.space.create("Meta — Rotational PM")
        self.assertEqual(again.slug, renamed.slug)       # the slug comes back ...
        self.assertNotEqual(again.id, first.id)          # ... the identity does not

    def test_applications_made_before_ids_get_one(self):
        app = self.space.create("Meta — RPM")
        folder = self.space.folder(app.slug)
        meta = json.loads((folder / "app.json").read_text())
        del meta["id"], meta["opened"]
        (folder / "app.json").write_text(json.dumps(meta))
        self.assertEqual(self.space.adopt_ids(), 1)
        back = self.space.list()[0]
        self.assertTrue(back.id)
        self.assertGreater(back.opened, 0)               # from when the documents were written
        self.assertEqual(self.space.adopt_ids(), 0)      # once, not on every start

    def test_identifying_an_unnamed_application_moves_it_to_a_real_slug(self):
        app = self.space.create("Untitled application")
        self.assertEqual(app.slug, "untitled-application")
        named = self.space.rename(app.slug, "Osmo — Software TPM", reslug=True)
        self.assertEqual(named.slug, "osmo-software-tpm")
        self.assertEqual(self.space.current, "osmo-software-tpm")
        # The documents moved with it; the placeholder is gone.
        self.assertTrue(self.space.paths(named.slug)["cv"].is_file())
        with self.assertRaises(ApplicationError):
            self.space.find("untitled-application")

    def test_naming_never_moves_one_application_inside_a_folder_already_there(self):
        app = self.space.create("Untitled application")
        (self.space.folder("osmo-software-tpm")).mkdir(parents=True)  # a stray folder, no app.json
        named = self.space.rename(app.slug, "Osmo — Software TPM", reslug=True)
        self.assertEqual(named.slug, "osmo-software-tpm-2")
        self.assertTrue(self.space.paths(named.slug)["cv"].is_file())
        self.assertEqual(list(self.space.folder("osmo-software-tpm").iterdir()), [])

    def test_rename_keeps_the_folder(self):
        app = self.space.create("Meta")
        renamed = self.space.rename(app.slug, "Meta — Rotational PM")
        self.assertEqual(renamed.name, "Meta — Rotational PM")
        self.assertEqual(renamed.slug, app.slug)

    def test_a_slug_cannot_escape_the_applications_folder(self):
        for bad in ("../secrets", "..", "/etc", "a/b", "", "Meta"):
            with self.assertRaises(ApplicationError):
                self.space.folder(bad)

    def test_first_run_adopts_the_existing_work(self):
        chat = self.tmp / ".chat"
        chat.mkdir()
        (chat / "cv.json").write_text(json.dumps(
            {"messages": [{"role": "user", "content": "tighten my summary"}],
             "job_description": "RPM at Meta"}))
        (chat / "cover.json").write_text(json.dumps(
            {"messages": [{"role": "user", "content": "draft the opening"}],
             "job_description": ""}))
        app = self.space.ensure(chat_dir=chat)
        self.assertEqual(app.name, "Meta | Rotational Product Manager")
        history = json.loads(self.space.paths(app.slug)["chat"].read_text())
        self.assertEqual([m["document"] for m in history["messages"]], ["cv", "cover"])
        self.assertEqual(history["job_description"], "RPM at Meta")

    def test_ensure_is_a_no_op_once_an_application_exists(self):
        first = self.space.ensure()
        again = self.space.ensure()
        self.assertEqual(first.slug, again.slug)
        self.assertEqual(len(self.space.list()), 1)


if __name__ == "__main__":
    unittest.main()
