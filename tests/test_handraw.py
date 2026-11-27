"""Handraw style integration tests (skip when optional skill is absent)."""
from __future__ import annotations

import unittest

import handraw


@unittest.skipUnless(handraw.package_root(), "optional handraw-style skill not installed")
class HandrawTests(unittest.TestCase):
    def test_catalog(self):
        self.assertGreaterEqual(len(handraw.styles()), 300)
        self.assertGreaterEqual(len(handraw.colors()), 30)

    def test_build_prompt(self):
        result = handraw.build_prompt(["FA-001"], ["C-01"], "雨夜街角的猫")
        self.assertIn("FA-001", result["prompt"])
        self.assertIn("雨夜街角的猫", result["prompt"])
        self.assertIn("Klein", result["prompt"])

    def test_unknown_style(self):
        with self.assertRaises(ValueError):
            handraw.build_prompt(["ZZ-999"], [], "x")
