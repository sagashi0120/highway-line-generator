# -*- coding: utf-8 -*-
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linegen import geo, stitch

DOWN_A = (35.0000, 139.0000)
DOWN_B = (35.0000, 139.0100)
DOWN_C = (35.0000, 139.0200)
UP_A = (35.0002, 139.0000)
UP_B = (35.0002, 139.0100)
UP_C = (35.0002, 139.0200)


def make_way(wid, nodes, coords, oneway="yes", name="テスト高速道路", primary=True, ref="T1"):
    return stitch.Way(
        id=wid,
        nodes=tuple(nodes),
        coords=tuple(coords),
        tags={"highway": "motorway", "name": name, "oneway": oneway, "ref": ref},
        primary=primary,
    )


def two_carriageways():
    down1 = make_way(1, (1, 2), (DOWN_A, DOWN_B))
    down2 = make_way(2, (2, 3), (DOWN_B, DOWN_C))
    up1 = make_way(3, (11, 12), (UP_C, UP_B))
    up2 = make_way(4, (12, 13), (UP_B, UP_A))
    return [down1, down2, up1, up2]


class StitchTest(unittest.TestCase):
    def test_selects_down_and_trim(self):
        ways = two_carriageways()
        res = stitch.build_line(ways, (35.0001, 139.00005))
        self.assertLess(res.snap_distance_m, 20)
        self.assertAlmostEqual(res.start[0], DOWN_A[0], places=3)
        self.assertAlmostEqual(res.start[1], DOWN_A[1], places=4)
        self.assertAlmostEqual(res.end[0], DOWN_C[0], places=4)
        self.assertAlmostEqual(res.end[1], DOWN_C[1], places=4)
        self.assertAlmostEqual(geo.polyline_length_m(res.points), 1823.2, delta=30)
        # 下り（東向き）だけを使う
        self.assertEqual(res.way_ids, [1, 2])

    def test_trim_starts_at_nearest_point(self):
        ways = two_carriageways()
        res = stitch.build_line(ways, (35.0001, 139.005))
        self.assertTrue(res.trimmed)
        self.assertAlmostEqual(res.start[1], 139.005, places=3)
        self.assertAlmostEqual(res.end[1], DOWN_C[1], places=4)

    def test_full_starts_at_road_start(self):
        ways = two_carriageways()
        res = stitch.build_line(
            ways, (35.0001, 139.005), stitch.BuildOptions(trim=False)
        )
        self.assertFalse(res.trimmed)
        self.assertAlmostEqual(res.start[1], DOWN_A[1], places=4)
        self.assertAlmostEqual(res.end[1], DOWN_C[1], places=4)

    def test_rough_point_is_tolerated(self):
        ways = two_carriageways()
        # 200m以上ずれた地点でもエラーにせず、最寄り地点から作る
        res = stitch.build_line(ways, (35.0020, 139.0010))
        self.assertGreater(res.snap_distance_m, 150)
        self.assertAlmostEqual(res.start[1], 139.001, places=3)
        self.assertAlmostEqual(res.end[1], DOWN_C[1], places=4)
        self.assertEqual(res.warnings, [])

    def test_very_far_point_still_builds_and_warns(self):
        ways = two_carriageways()
        res = stitch.build_line(ways, (35.05, 139.001))
        self.assertGreater(len(res.points), 2)
        self.assertTrue(any("離れています" in w for w in res.warnings))

    def test_long_road_midpoint_follows_down_direction(self):
        # 東京から離れる向き（東→西）が下り。途中地点でも西（下り）へ進むこと。
        down1 = make_way(1, (1, 2), ((35.0, 139.2), (35.0, 139.1)))
        down2 = make_way(2, (2, 3), ((35.0, 139.1), (35.0, 139.0)))
        up1 = make_way(3, (11, 12), ((35.0002, 139.0), (35.0002, 139.1)))
        up2 = make_way(4, (12, 13), ((35.0002, 139.1), (35.0002, 139.2)))
        res = stitch.build_line([down1, down2, up1, up2], (35.0001, 139.1))
        self.assertAlmostEqual(res.end[1], 139.0, places=4)
        self.assertEqual(res.way_ids, [1, 2])

    def test_gap_is_bridged(self):
        w1 = make_way(1, (1, 2), ((35.0, 139.0), (35.0, 139.01)))
        w2 = make_way(2, (3, 4), ((35.0, 139.012), (35.0, 139.02)))
        res = stitch.build_line([w1, w2], (35.0, 139.0))
        self.assertEqual(res.gap_jumps, 1)
        self.assertAlmostEqual(res.end[1], 139.02, places=4)
        self.assertAlmostEqual(geo.polyline_length_m(res.points), 1822.5, delta=60)

    def test_branch_prefers_straight(self):
        w1 = make_way(1, (1, 2), ((35.0, 139.0), (35.0, 139.01)))
        w2 = make_way(2, (2, 3), ((35.0, 139.01), (35.0, 139.02)))
        branch = make_way(3, (2, 4), ((35.0, 139.01), (35.02, 139.01)))
        res = stitch.build_line([w1, w2, branch], (35.0, 139.0))
        self.assertAlmostEqual(res.end[1], 139.02, places=4)
        self.assertNotIn(3, res.way_ids)

    def test_connector_bridge_allowed(self):
        w1 = make_way(1, (1, 2), ((35.0, 139.0), (35.0, 139.01)))
        bridge = make_way(
            2,
            (2, 3),
            ((35.0, 139.01), (35.00027, 139.01)),
            name="テスト橋",
            primary=False,
            ref="T1",
        )
        w2 = make_way(3, (3, 4), ((35.00027, 139.01), (35.00027, 139.02)))
        res = stitch.build_line([w1, bridge, w2], (35.0, 139.0))
        self.assertIn(2, res.way_ids)
        self.assertIn(3, res.way_ids)
        self.assertAlmostEqual(res.end[1], 139.02, places=4)

    def test_reversed_oneway(self):
        way = make_way(
            1,
            (1, 2),
            ((35.0, 139.0), (35.0, 139.01)),
            oneway="-1",
        )
        res = stitch.build_line([way], (35.0, 139.0101))
        self.assertAlmostEqual(res.start[1], 139.01, places=4)
        self.assertAlmostEqual(res.end[1], 139.0, places=4)
        self.assertGreater(res.start[1], res.end[1])

    def test_end_point_gets_ambiguity_warning(self):
        ways = two_carriageways()
        # 下り線の終点（=上り線の始点）を指定した場合
        res = stitch.build_line(ways, DOWN_C)
        self.assertTrue(any("端点" in w for w in res.warnings))
        # 上り線が選ばれ、東から西へ向かう
        self.assertGreater(res.start[1], res.end[1])

    def test_reverse_ways_flips_carriageway(self):
        ways = two_carriageways()
        res = stitch.build_line(
            stitch.reverse_ways(ways), DOWN_C, stitch.BuildOptions(reverse=True)
        )
        # 反転すると下り線（A→C）が選ばれ、指定地点 C から A へ向かう
        self.assertAlmostEqual(res.start[1], DOWN_C[1], places=3)
        self.assertAlmostEqual(res.end[1], DOWN_A[1], places=3)
        self.assertEqual(set(res.way_ids), {1, 2})
        self.assertTrue(res.reversed)
        self.assertTrue(any("--reverse" in w for w in res.warnings))
        self.assertFalse(any("端点" in w for w in res.warnings))

    def test_no_ways(self):
        with self.assertRaises(stitch.StitchError):
            stitch.build_line([], (35.0, 139.0))
