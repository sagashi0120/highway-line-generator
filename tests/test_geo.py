# -*- coding: utf-8 -*-
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linegen import geo


class GeoTest(unittest.TestCase):
    def test_haversine_tokyo_osaka(self):
        # 東京駅 -> 大阪駅 はおよそ 400km
        d = geo.haversine_m(35.681236, 139.767125, 34.702485, 135.495951)
        self.assertGreater(d, 390000)
        self.assertLess(d, 410000)

    def test_nearest_on_polyline(self):
        line = [(35.0, 139.0), (35.0, 139.01), (35.0, 139.02)]
        near = geo.nearest_on_polyline(35.001, 139.005, line)
        self.assertAlmostEqual(near["dist_m"], 111.2, delta=2.0)
        self.assertAlmostEqual(near["lat"], 35.0, places=6)
        self.assertAlmostEqual(near["lng"], 139.005, places=3)
        self.assertAlmostEqual(near["along_m"], 455.4, delta=5.0)

    def test_nearest_segment_end(self):
        line = [(35.0, 139.0), (35.0, 139.01)]
        near = geo.nearest_on_polyline(35.0, 139.02, line)
        self.assertAlmostEqual(near["lng"], 139.01, places=6)

    def test_simplify_keeps_endpoints(self):
        line = [(35.0, 139.0 + i * 0.00001) for i in range(100)]
        kept = geo.simplify(line, 10.0)
        self.assertEqual(kept[0], line[0])
        self.assertEqual(kept[-1], line[-1])
        self.assertLess(len(kept), len(line))

    def test_simplify_zero(self):
        line = [(35.0, 139.0), (35.0, 139.01)]
        self.assertEqual(geo.simplify(line, 0), line)

    def test_bbox(self):
        b = geo.bbox_of([(35.0, 139.0), (36.0, 140.0)])
        self.assertEqual(b, (35.0, 139.0, 36.0, 140.0))
        self.assertTrue(geo.bbox_contains((34.0, 138.0, 37.0, 141.0), b))
        self.assertFalse(geo.bbox_contains(b, (34.0, 138.0, 37.0, 141.0)))

    def test_bearing(self):
        self.assertAlmostEqual(geo.bearing_deg(35.0, 139.0, 36.0, 139.0), 0.0, delta=0.5)
        self.assertAlmostEqual(geo.bearing_deg(35.0, 139.0, 35.0, 140.0), 90.0, delta=0.5)
