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
        self.assertEqual(app.name, "Meta — Rotational Product Manager")
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
