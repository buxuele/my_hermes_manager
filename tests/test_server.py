"""服务端文件浏览的自测：非递归与递归列目录。

跑法：python3 -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402


class ContentFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "a", "b"), exist_ok=True)
        for rel in ("root.md", "a/one.md", "a/b/two.md", "a/skip.png"):
            with open(os.path.join(self.tmp, rel), "w", encoding="utf-8") as fh:
                fh.write("# x")

    def test_non_recursive_only_root(self):
        files = server.content_files(self.tmp)
        self.assertEqual([f["rel"] for f in files], ["root.md"])

    def test_recursive_depth_two(self):
        files = server.content_files(self.tmp, recursive=True, depth=2)
        self.assertEqual(sorted(f["rel"] for f in files), ["a/b/two.md", "a/one.md", "root.md"])

    def test_recursive_depth_one(self):
        files = server.content_files(self.tmp, recursive=True, depth=1)
        self.assertEqual(sorted(f["rel"] for f in files), ["a/one.md", "root.md"])

    def test_in_roots_blocks_outside_paths(self):
        root = os.path.realpath(self.tmp)
        self.assertTrue(server.in_roots(os.path.join(root, "root.md"), [root]))
        self.assertFalse(server.in_roots(os.path.expanduser("~/.hermes/auth.json"), [root]))

    def test_static_path_confined(self):
        self.assertIsNotNone(server.static_path("favicon.svg"))
        self.assertIsNone(server.static_path("../hub.db"))
        self.assertIsNone(server.static_path("..%2F..%2Fserver.py"))
        self.assertIsNone(server.static_path("../../AGENTS.md"))

    def test_host_allowlist(self):
        for ok in ("127.0.0.1:8899", "localhost:8899", "[::1]:8899"):
            self.assertTrue(server.HOST_RE.match(ok), ok)
        for bad in ("evil.com:8899", "127.0.0.1.evil.com", "10.0.0.2:8899", ""):
            self.assertIsNone(server.HOST_RE.match(bad), bad)

    def test_content_status_reports_missing(self):
        info = server.content_status(os.path.join(self.tmp, "nope"))
        self.assertFalse(info["exists"])
        self.assertEqual(info["error"], "目录不存在")
        self.assertEqual(info["file_count"], 0)


if __name__ == "__main__":
    unittest.main()
