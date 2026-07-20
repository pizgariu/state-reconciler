"""DriftItem value object: name and message access, repr, slots guard, and str form."""
import unittest

from state_reconciler import DriftItem


class DriftItemTests(unittest.TestCase):
    def test_exposes_name_and_message(self):
        d = DriftItem("n", "f")
        self.assertEqual((d.name, d.message), ("n", "f"))

    def test_repr(self):
        self.assertEqual(repr(DriftItem("n", "f")), "DriftItem('n', 'f')")

    def test_slots_forbid_stray_attributes(self):
        d = DriftItem("n", "f")
        with self.assertRaises(AttributeError):
            d.extra = 1  # __slots__ has no __dict__

    def test_str_renders_name_and_message(self):
        self.assertEqual(str(DriftItem("a", "b")), "a: b")
