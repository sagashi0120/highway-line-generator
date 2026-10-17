# -*- coding: utf-8 -*-
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linegen import roads


class RoadsTest(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(roads.normalize_name("東名 高速道路"), "東名高速道路")
        self.assertEqual(roads.normalize_name("Ｅ１"), "E1")

    def test_canonical(self):
        self.assertEqual(roads.canonical_name("中央自動車道（富士吉田線）"), "中央自動車道")
        self.assertEqual(roads.canonical_name("東名高速道路"), "東名高速道路")

    def test_name_matches_route_suffix(self):
        tags = {"name": "東名高速道路（左ルート）"}
        self.assertTrue(roads.name_matches(tags, ["東名高速道路"]))
        self.assertFalse(roads.name_matches(tags, ["名神高速道路"]))

    def test_name_does_not_cross_match(self):
        tags = {"name": "新東名高速道路"}
        self.assertFalse(roads.name_matches(tags, ["東名高速道路"]))
        self.assertTrue(roads.name_matches(tags, ["新東名高速道路"]))

    def test_ref(self):
        self.assertTrue(roads.ref_matches({"ref": "E1;E2"}, ["E2"]))
        self.assertFalse(roads.ref_matches({"ref": "E1"}, ["E4"]))

    def test_parse_coord(self):
        self.assertEqual(roads.parse_coord("35.6283,139.6187"), (35.6283, 139.6187))
        self.assertEqual(roads.parse_coord("35.6283 139.6187"), (35.6283, 139.6187))
        self.assertEqual(roads.parse_coord("lat=35.6283 lng=139.6187"), (35.6283, 139.6187))
        with self.assertRaises(ValueError):
            roads.parse_coord("35.6")

    def test_split_road_input(self):
        self.assertEqual(roads.split_road_input("E1-東名高速道路"), ("E1", "東名高速道路"))
        self.assertEqual(roads.split_road_input("東名高速道路"), (None, "東名高速道路"))
