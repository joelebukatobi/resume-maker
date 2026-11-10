import json
import unittest
from unittest import mock

from resume_maker import jobpost

BODY = "<p>You will build things.</p><ul><li>Python</li><li>SQL</li></ul>" + "<p>More detail.</p>" * 40


def page(*blocks, body="<p>short</p>"):
    scripts = "".join(f'<script type="application/ld+json">{b}</script>' for b in blocks)
    return f"<html><head><title>Job</title>{scripts}</head><body><nav>Menu</nav>{body}</body></html>"


class ExtractTest(unittest.TestCase):
    def test_reads_the_posting_block(self):
        block = json.dumps({"@type": "JobPosting", "title": "Engineer",
                            "hiringOrganization": {"name": "Acme"}, "description": BODY})
        post = jobpost.extract(page(block))
        self.assertEqual((post["source"], post["title"], post["company"]),
                         ("posting", "Engineer", "Acme"))
        self.assertIn("- Python", post["text"])
        self.assertNotIn("<p>", post["text"])

    def test_finds_it_in_a_graph_and_unescapes_it(self):
        block = json.dumps({"@graph": [{"@type": "WebSite"}, {
            "@type": ["JobPosting"], "description": BODY.replace("<", "&lt;").replace(">", "&gt;"),
        }]})
        post = jobpost.extract(page("{broken", block))
        self.assertEqual(post["source"], "posting")
        self.assertNotIn("<p>", post["text"])

    def test_falls_back_to_visible_text_without_the_menu(self):
        post = jobpost.extract(page(body=BODY))
        self.assertEqual(post["source"], "page")
        self.assertIn("You will build things.", post["text"])
        self.assertNotIn("Menu", post["text"])

    def test_a_page_drawn_by_javascript_is_refused(self):
        with self.assertRaises(jobpost.JobPostError):
            jobpost.extract(page(body='<div id="root"></div>'))


class WorkableTest(unittest.TestCase):
    def test_a_workable_link_reads_the_job_data(self):
        job = json.dumps({"title": "Engineer", "description": BODY, "requirements": "<p>Go</p>"})
        with mock.patch.object(jobpost, "fetch", return_value=job) as fetch:
            post = jobpost.read("https://apply.workable.com/acme-1/j/0594E67655/")
        self.assertEqual(fetch.call_args.args[0],
                         "https://apply.workable.com/api/v2/accounts/acme-1/jobs/0594E67655")
        self.assertEqual((post["title"], post["source"]), ("Engineer", "posting"))
        self.assertTrue(post["text"].startswith("Engineer\n\n"))  # the title, for naming
        self.assertTrue(post["text"].endswith("Go"))


class TrimTest(unittest.TestCase):
    SOURCE = "\n".join(["Skip to main content", "Engineer", "- Python for five years",
                        "Salary: $100,000"] + [f"Responsibility {n} in detail here" for n in range(20)]
                       + ["We are an equal opportunity employer."])

    def test_keeps_copied_lines_and_drops_made_up_ones(self):
        reply = "Engineer\n* python for five  years\nYou will love it here\n" + "\n".join(
            f"Responsibility {n} in detail here" for n in range(20)) + "\nSalary: $100,000"
        text = jobpost.keep_lines(self.SOURCE, reply)
        self.assertIn("- Python for five years", text)              # the source's own wording
        self.assertNotIn("love it", text)
        self.assertNotIn("Skip to main", text)
        self.assertNotIn("equal opportunity", text)

    def test_a_reply_that_keeps_almost_nothing_returns_the_source(self):
        self.assertEqual(jobpost.keep_lines(self.SOURCE, "Engineer"), self.SOURCE)


class AddressTest(unittest.TestCase):
    def test_only_public_web_links_are_fetched(self):
        for url in ("file:///etc/passwd", "ftp://example.com/x", "http://127.0.0.1:8000/",
                    "http://localhost/", "http://169.254.169.254/latest/", "http://[::1]/",
                    "http://10.0.0.1/", "http://192.168.1.1/"):
            with self.subTest(url), self.assertRaises(jobpost.JobPostError):
                jobpost.fetch(url)

    def test_a_redirect_to_a_private_address_is_refused(self):
        handler = jobpost._CheckedRedirect()
        with self.assertRaises(jobpost.JobPostError):
            handler.redirect_request(mock.Mock(), None, 302, "", {}, "http://127.0.0.1/admin")


if __name__ == "__main__":
    unittest.main()
