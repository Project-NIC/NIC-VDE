"""Tests for LocalBackend against a throwaway temp directory tree."""
import os
import shutil
import tempfile
import unittest

from tests import helpers
from tests.helpers import make_temp_mla

from volkov_core.local import LocalBackend
from volkov_core.mla import MlaBackend
from volkov_core.backend import BackendError


class LocalBackendTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vde_test_")
        os.mkdir(os.path.join(self.root, "sub"))
        with open(os.path.join(self.root, "b.txt"), "wb") as f:
            f.write(b"hello")
        with open(os.path.join(self.root, "a.bin"), "wb") as f:
            f.write(b"\x00\x01\x02")
        # a real, valid .mla copied into the tree
        tmp_mla = make_temp_mla()
        self.mla_path = os.path.join(self.root, "data.mla")
        shutil.move(tmp_mla, self.mla_path)
        self.b = LocalBackend(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def names(self):
        return [e.name for e in self.b.list()]

    # ── listing ──────────────────────────────────────────────────────────────
    def test_list_has_updir_first(self):
        self.assertEqual(self.b.list()[0].name, "..")

    def test_dirs_before_files_each_sorted(self):
        names = self.names()
        self.assertEqual(names[0], "..")
        self.assertEqual(names[1], "sub")          # the only dir
        self.assertEqual(names[2:], ["a.bin", "b.txt", "data.mla"])  # files, sorted

    def test_mla_classified_as_enterable(self):
        mla = next(e for e in self.b.list() if e.name == "data.mla")
        self.assertEqual(mla.kind, "mla")
        self.assertTrue(mla.is_container)

    # ── entering ───────────────────────────────────────────────────────────────
    def test_enter_subdir(self):
        sub = next(e for e in self.b.list() if e.name == "sub")
        child = self.b.enter(sub)
        self.assertIsInstance(child, LocalBackend)
        self.assertEqual(os.path.basename(child.location), "sub")

    def test_enter_updir(self):
        up = self.b.enter(self.b.list()[0])
        self.assertIsInstance(up, LocalBackend)
        self.assertEqual(up.location, os.path.dirname(self.root))

    def test_enter_mla_returns_mla_backend(self):
        mla = next(e for e in self.b.list() if e.name == "data.mla")
        child = self.b.enter(mla)
        self.assertIsInstance(child, MlaBackend)

    def test_enter_plain_file_is_none(self):
        f = next(e for e in self.b.list() if e.name == "b.txt")
        self.assertIsNone(self.b.enter(f))

    # ── reading ────────────────────────────────────────────────────────────────
    def test_read_file(self):
        f = next(e for e in self.b.list() if e.name == "b.txt")
        self.assertEqual(self.b.read(f), b"hello")

    def test_read_directory_raises(self):
        sub = next(e for e in self.b.list() if e.name == "sub")
        with self.assertRaises(BackendError):
            self.b.read(sub)

    # ── mutating ─────────────────────────────────────────────────────────────
    def test_mkdir_and_exists(self):
        self.assertFalse(self.b.exists("new"))
        self.b.mkdir("new")
        self.assertTrue(self.b.exists("new"))
        self.assertIn("new", self.names())

    def test_put_file_roundtrip(self):
        self.b.put_file("c.dat", b"payload")
        f = next(e for e in self.b.list() if e.name == "c.dat")
        self.assertEqual(self.b.read(f), b"payload")

    def test_rename(self):
        f = next(e for e in self.b.list() if e.name == "b.txt")
        self.b.rename(f, "renamed.txt")
        self.assertIn("renamed.txt", self.names())
        self.assertNotIn("b.txt", self.names())

    def test_delete_file(self):
        f = next(e for e in self.b.list() if e.name == "a.bin")
        self.b.delete(f)
        self.assertNotIn("a.bin", self.names())

    def test_delete_dir_recursive(self):
        sub = next(e for e in self.b.list() if e.name == "sub")
        self.b.delete(sub)
        self.assertNotIn("sub", self.names())

    def test_mkdir_failure_raises_backend_error(self):
        with self.assertRaises(BackendError):
            self.b.mkdir("sub")  # already exists


if __name__ == "__main__":
    unittest.main()
