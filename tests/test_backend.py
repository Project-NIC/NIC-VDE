"""Tests for the backend abstraction (VdeEntry, VdeBackend defaults)."""
import unittest

from tests import helpers  # noqa: F401  — path bootstrap

from volkov_core.backend import VdeBackend, VdeEntry, VdeUnsupported, VdeBackendError


class _Stub(VdeBackend):
    """Minimal concrete backend to exercise the base-class defaults."""

    @property
    def location(self) -> str:
        return "/a/b/c"

    def list(self):
        return []

    def enter(self, entry):
        return None


class EntryTests(unittest.TestCase):
    def test_defaults(self):
        e = VdeEntry("foo")
        self.assertFalse(e.is_container)
        self.assertEqual(e.size, 0)
        self.assertIsNone(e.mtime)
        self.assertEqual(e.kind, "file")
        self.assertEqual(e.meta, {})

    def test_meta_is_per_instance(self):
        a, b = VdeEntry("a"), VdeEntry("b")
        a.meta["x"] = 1
        self.assertEqual(b.meta, {})  # no shared mutable default


class BackendDefaultTests(unittest.TestCase):
    def setUp(self):
        self.b = _Stub()

    def test_label_is_last_path_component(self):
        self.assertEqual(self.b.label, "c")

    def test_default_info_file(self):
        rows = self.b.info(VdeEntry("f.txt", size=42))
        self.assertIn(("Name", "f.txt"), rows)
        self.assertIn(("Size", "42 B"), rows)

    def test_default_info_container_has_no_size(self):
        rows = self.b.info(VdeEntry("d", is_container=True, kind="dir"))
        self.assertFalse(any(label == "Size" for label, _ in rows))

    def test_mutating_ops_unsupported_by_default(self):
        for call in (
            lambda: self.b.mkdir("x"),
            lambda: self.b.delete(VdeEntry("x")),
            lambda: self.b.rename(VdeEntry("x"), "y"),
            lambda: self.b.put_file("x", b""),
            lambda: self.b.read(VdeEntry("x")),
        ):
            with self.assertRaises(VdeBackendError):
                call()

    def test_unsupported_is_backend_error(self):
        self.assertTrue(issubclass(VdeUnsupported, VdeBackendError))

    def test_exists_default_false(self):
        self.assertFalse(self.b.exists("anything"))

    def test_close_is_idempotent(self):
        self.b.close()
        self.b.close()  # must not raise


if __name__ == "__main__":
    unittest.main()
