# -*- coding: utf-8 -*-
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linegen import output


class OutputTest(unittest.TestCase):
    def test_line_data_format(self):
        data = output.to_line_data([(35.1, 139.1), (35.2, 139.2)])
        self.assertEqual(data["type"], "lineData")
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["points"][0], {"lat": 35.1, "lng": 139.1})

    def test_finalize_rounding_and_dedupe(self):
        pts = output.finalize_points(
            [(35.12345678, 139.12345678), (35.12345678, 139.12345678), (35.2, 139.2)],
            0,
        )
        self.assertEqual(len(pts), 2)
        self.assertEqual(pts[0], (35.123457, 139.123457))

    def test_geojson_lng_lat_order(self):
        gj = output.to_geojson([(35.1, 139.1), (35.2, 139.2)])
        coords = gj["features"][0]["geometry"]["coordinates"]
        self.assertEqual(coords, [[139.1, 35.1], [139.2, 35.2]])
