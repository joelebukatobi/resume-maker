import unittest

from resume_maker import setup


class AtomicSkillsTest(unittest.TestCase):
    def test_every_tool_stands_alone(self):
        self.assertEqual(setup.atomic("SQL & Python"), ["SQL", "Python"])
        self.assertEqual(setup.atomic("JavaScript (Node, React & Fastify)"),
                         ["JavaScript", "Node", "React", "Fastify"])
        self.assertEqual(setup.atomic("Model Context Protocol (MCPs)"), ["MCP"])
        self.assertEqual(setup.atomic("Testing and Evaluation"), ["Testing and Evaluation"])

    def test_the_same_tool_is_listed_once(self):
        have = ["Node.js", "LLMs", "Product Strategy"]
        setup._merge(have, ["Node", "Large Language Models", "Prompt Strategy"])
        self.assertEqual(have, ["Node.js", "LLMs", "Product Strategy", "Prompt Strategy"])


if __name__ == "__main__":
    unittest.main()
