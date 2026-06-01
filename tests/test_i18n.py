"""Tests for the translation layer."""
import unittest

from tests import helpers  # noqa: F401  — path bootstrap

from volkov_i18n import VdeTranslator, TABLES, VDE_LANGUAGES


class TranslatorTests(unittest.TestCase):
    def test_default_is_english_identity(self):
        t = VdeTranslator()
        self.assertEqual(t.lang, "en")
        self.assertEqual(t.tr("Files"), "Files")  # identity fallback

    def test_switch_language(self):
        t = VdeTranslator()
        t.set_lang("cs")
        self.assertEqual(t.tr("Files"), "Soubory")

    def test_unknown_string_falls_back_to_source(self):
        t = VdeTranslator("cs")
        self.assertEqual(t.tr("No such key here"), "No such key here")

    def test_unknown_language_falls_back_to_english(self):
        t = VdeTranslator("zz")
        self.assertEqual(t.lang, "en")

    def test_languages_all_have_tables(self):
        for code, _native in VDE_LANGUAGES:
            self.assertIn(code, TABLES)

    def test_translation_keys_consistent(self):
        # every non-English table should translate the same set of keys as cs
        # (the reference fully-translated table) — guards against a missed string
        reference = set(TABLES["cs"])
        for code in ("fr", "es", "ru"):
            self.assertEqual(set(TABLES[code]), reference,
                             f"language '{code}' key set differs from 'cs'")


if __name__ == "__main__":
    unittest.main()
