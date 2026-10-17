# -*- coding: utf-8 -*-
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linegen import overpass


class OverpassTest(unittest.TestCase):
    def test_build_query_contains_conditions(self):
        q = overpass.build_query(
            (35.0, 139.0, 35.1, 139.1),
            names=["東名高速道路"],
            refs=["E1"],
            name_regex="東名高速道路",
        )
        self.assertIn('["name"="東名高速道路"]', q)
        self.assertIn('["ref"="E1"]', q)
        self.assertIn('["name"~"東名高速道路"]', q)
        self.assertIn("35.000000,139.000000,35.100000,139.100000", q)
        self.assertIn("out body geom;", q)
        self.assertIn("motorway", q)

    def test_build_query_multiple_bboxes(self):
        q = overpass.build_query(
            [(35.0, 139.0, 35.1, 139.1), (35.1, 139.0, 35.2, 139.1)],
            names=["テスト道路"],
        )
        self.assertIn("35.000000,139.000000,35.100000,139.100000", q)
        self.assertIn("35.100000,139.000000,35.200000,139.100000", q)

    def test_regex_escape(self):
        self.assertEqual(overpass._regex_quote("中央自動車道(富士吉田線)"), "中央自動車道\\(富士吉田線\\)")

    def test_parse_ways(self):
        data = {
            "elements": [
                {
                    "type": "way",
                    "id": 10,
                    "nodes": [1, 2],
                    "geometry": [
                        {"lat": 35.0, "lon": 139.0},
                        {"lat": 35.0, "lon": 139.1},
                    ],
                    "tags": {"highway": "motorway"},
                },
                {"type": "node", "id": 1},
                {"type": "way", "id": 11, "nodes": [], "geometry": []},
            ]
        }
        ways = overpass.parse_ways(data)
        self.assertEqual(len(ways), 1)
        self.assertEqual(ways[0]["id"], 10)
        self.assertEqual(ways[0]["coords"], [(35.0, 139.0), (35.0, 139.1)])
