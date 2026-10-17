# -*- coding: utf-8 -*-
import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from linegen import core, geo


def make_road_way(wid, start_lng, end_lng, lat=35.0, name="テスト高速道路"):
    return {
        "id": wid,
        "nodes": [wid * 2, wid * 2 + 1],
        "coords": [(lat, start_lng), (lat, end_lng)],
        "tags": {"highway": "motorway", "name": name, "oneway": "yes"},
    }


def fake_road_ways(count=20):
    ways = []
    for i in range(count):
        ways.append(make_road_way(i + 1, 139.0 + i * 0.01, 139.0 + (i + 1) * 0.01))
    return ways


def in_bbox(way, bbox):
    south, west, north, east = bbox
    for lat, lng in way["coords"]:
        if south <= lat <= north and west <= lng <= east:
            return True
    return False


def bbox_list(bbox):
    if len(bbox) == 4 and isinstance(bbox[0], (int, float)):
        return [tuple(bbox)]
    return [tuple(b) for b in bbox]


def in_any_bbox(way, bbox):
    return any(in_bbox(way, b) for b in bbox_list(bbox))


class CoreTest(unittest.TestCase):
    def test_resolve_road_guesses_code(self):
        info = core.resolve_road("東名高速道路", None, HERE)
        self.assertIn("E1", info.codes)

    def test_resolve_road_splits_code_prefix(self):
        info = core.resolve_road("E1-東名高速道路", None, HERE)
        self.assertEqual(info.name, "東名高速道路")
        self.assertEqual(info.codes, ["E1"])

    def test_classify_primary_and_connector(self):
        info = core.resolve_road("テスト高速道路", "T1", HERE)
        opts = core.GenerateOptions(base_dir=HERE, quiet=True)
        raw = [
            make_road_way(1, 139.0, 139.01),
            # 名前の異なる短い橋（ref 一致）→ connector
            {
                "id": 2,
                "nodes": [3, 4],
                "coords": [(35.0, 139.01), (35.00027, 139.01)],
                "tags": {"highway": "motorway", "name": "テスト橋", "ref": "T1", "oneway": "yes"},
            },
            # 長い別道路（ref 一致でも connector にしない）
            {
                "id": 3,
                "nodes": [5, 6],
                "coords": [(35.0, 139.02), (35.0, 139.2)],
                "tags": {"highway": "motorway", "name": "別高速道路", "ref": "T1", "oneway": "yes"},
            },
        ]
        ways, n_primary, n_connector, fuzzy = core.classify_ways(raw, info, opts)
        self.assertEqual(n_primary, 1)
        self.assertEqual(n_connector, 1)
        self.assertFalse(fuzzy)
        self.assertEqual(sum(1 for w in ways if w.primary), 1)

    def test_bbox_expansion_fetches_whole_road(self):
        road = fake_road_ways()
        calls = []
        original = core._fetch

        def fake_fetch(bbox, names, codes, opts, regex=None, include_links=False):
            calls.append(bbox)
            selected = [way for way in road if in_any_bbox(way, bbox)]
            return selected, {"elements": []}

        core._fetch = fake_fetch
        try:
            opts = core.GenerateOptions(
                base_dir=HERE,
                quiet=True,
                directions="down",
                initial_radius_deg=0.15,
                cache_dir=os.path.join(HERE, "_core_test_cache"),
            )
            result = core.generate("テスト高速道路", (35.0, 139.0), code=None, opts=opts)
        finally:
            core._fetch = original

        self.assertGreaterEqual(len(calls), 2)
        # 2回目以降は初回範囲の外側（フリンジ）だけを取得する
        first = bbox_list(calls[0])[0]
        self.assertTrue(
            any(not geo.bbox_contains(first, rect) for rect in bbox_list(calls[1]))
        )
        points = result.points
        self.assertGreater(len(points), 5)
        self.assertAlmostEqual(points[-1][1], 139.2, places=3)
        self.assertGreater(geo.polyline_length_m(points) / 1000.0, 18.0)

    def test_bbox_expansion_long_road_is_bounded(self):
        # 約3度（約270km）にわたる道路でも、余計に広げすぎないこと
        road = []
        for i in range(30):
            west = 139.0 - (i + 1) * 0.1
            # 東から西へ走る道路（始点 139.0 から進行方向へ）
            road.append(make_road_way(i + 1, west + 0.1, west))
        calls = []
        original = core._fetch

        def fake_fetch(bbox, names, codes, opts, regex=None, include_links=False):
            calls.append(bbox)
            return [way for way in road if in_any_bbox(way, bbox)], {"elements": []}

        core._fetch = fake_fetch
        try:
            opts = core.GenerateOptions(
                base_dir=HERE,
                quiet=True,
                directions="down",
                cache_dir=os.path.join(HERE, "_core_test_cache"),
            )
            result = core.generate("テスト高速道路", (35.0, 139.0), code=None, opts=opts)
        finally:
            core._fetch = original

        self.assertGreaterEqual(len(calls), 2)
        self.assertLessEqual(len(calls), 12)
        # 道路の東西幅は約3度。取得した矩形全体が道路より極端に広がっていないこと
        rects = [rect for call in calls for rect in bbox_list(call)]
        west = min(r[1] for r in rects)
        east = max(r[3] for r in rects)
        self.assertLessEqual(east - west, 3.0 + 2 * 0.9)
        self.assertAlmostEqual(result.points[-1][1], 136.0, places=2)

    def test_bbox_expansion_stops(self):
        road = fake_road_ways(3)
        calls = []
        original = core._fetch

        def fake_fetch(bbox, names, codes, opts, regex=None, include_links=False):
            calls.append(bbox)
            return [way for way in road if in_any_bbox(way, bbox)], {"elements": []}

        core._fetch = fake_fetch
        try:
            opts = core.GenerateOptions(
                base_dir=HERE,
                quiet=True,
                directions="down",
                cache_dir=os.path.join(HERE, "_core_test_cache"),
            )
            core.generate("テスト高速道路", (35.0, 139.0), code=None, opts=opts)
        finally:
            core._fetch = original
        # 短い道路は1〜2回の取得で終わる
        self.assertLessEqual(len(calls), 3)
