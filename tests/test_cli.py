# -*- coding: utf-8 -*-
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from linegen import cli, geo

FIXTURE = os.path.join(HERE, "tests", "fixtures", "osm_東京外環自動車道.json")


def run_cli(args):
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(args)
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(FIXTURE):
            raise unittest.SkipTest("fixture がありません: " + FIXTURE)

    def test_line_data_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "line.json")
            code, stdout, stderr = run_cli(
                [
                    "東京外環自動車道",
                    "--start",
                    "35.7615,139.5989",
                    "--code",
                    "C3",
                    "--osm-json",
                    FIXTURE,
                    "--out",
                    out,
                    "--simplify",
                    "5",
                    "--no-meta",
                    "--quiet",
                ]
            )
            self.assertEqual(code, 0, stderr)
            down_path = os.path.join(tmp, "line-下り.json")
            up_path = os.path.join(tmp, "line-上り.json")
            self.assertTrue(os.path.isfile(down_path))
            self.assertTrue(os.path.isfile(up_path))
            with open(down_path, encoding="utf-8") as f:
                down = json.load(f)
            with open(up_path, encoding="utf-8") as f:
                up = json.load(f)
            for data in (down, up):
                self.assertEqual(data["type"], "lineData")
                self.assertEqual(data["version"], 1)
                self.assertEqual(len(str(data["points"][0]["lat"]).split(".")[1]), 6)
            dp = [(p["lat"], p["lng"]) for p in down["points"]]
            up_pts = [(p["lat"], p["lng"]) for p in up["points"]]
            self.assertGreater(len(dp), 80)
            self.assertGreater(len(up_pts), 80)
            self.assertAlmostEqual(dp[0][0], 35.7634, delta=0.002)
            self.assertAlmostEqual(dp[-1][1], 139.9452, delta=0.002)
            # 上り線は下り線の終点から始まり、下り線の始点で終わる
            self.assertAlmostEqual(up_pts[0][1], dp[-1][1], delta=0.003)
            self.assertAlmostEqual(up_pts[-1][0], dp[0][0], delta=0.003)
            self.assertGreater(geo.polyline_length_m(dp) / 1000, 47.0)
            self.assertGreater(geo.polyline_length_m(up_pts) / 1000, 47.0)

    def test_multiple_formats_and_meta(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "c3.json")
            code, stdout, stderr = run_cli(
                [
                    "東京外環自動車道",
                    "--start",
                    "35.7615,139.5989",
                    "--code",
                    "C3",
                    "--osm-json",
                    FIXTURE,
                    "--out",
                    out,
                    "--format",
                    "lineData,geojson,csv",
                    "--preview",
                    "--quiet",
                ]
            )
            self.assertEqual(code, 0, stderr)
            for label in ("下り", "上り"):
                for name in (
                    "c3-%s.json" % label,
                    "c3-%s.geojson" % label,
                    "c3-%s.csv" % label,
                    "c3-%s.meta.json" % label,
                    "c3-%s.html" % label,
                ):
                    self.assertTrue(os.path.isfile(os.path.join(tmp, name)), name)
            with open(os.path.join(tmp, "c3-下り.geojson"), encoding="utf-8") as f:
                gj = json.load(f)
            self.assertEqual(gj["type"], "FeatureCollection")
            self.assertEqual(gj["features"][0]["geometry"]["type"], "LineString")
            self.assertEqual(gj["features"][0]["properties"]["direction"], "down")
            for label, direction, role in (("下り", "down", "start"), ("上り", "up", "end")):
                with open(os.path.join(tmp, "c3-%s.meta.json" % label), encoding="utf-8") as f:
                    meta = json.load(f)
                self.assertEqual(meta["road"], "東京外環自動車道")
                self.assertEqual(meta["direction"], direction)
                self.assertEqual(meta["requested_point_is"], role)
                self.assertEqual(meta["source"], "OpenStreetMap")
                with open(os.path.join(tmp, "c3-%s.json" % label), encoding="utf-8") as f:
                    line = json.load(f)
                line_points = [(p["lat"], p["lng"]) for p in line["points"]]
                self.assertAlmostEqual(
                    meta["length_m"], geo.polyline_length_m(line_points), delta=1.0
                )

    def test_start_two_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "line.json")
            code, stdout, stderr = run_cli(
                [
                    "東京外環自動車道",
                    "--start",
                    "35.7615",
                    "139.5989",
                    "--code",
                    "C3",
                    "--osm-json",
                    FIXTURE,
                    "--out",
                    out,
                    "--no-meta",
                    "--quiet",
                ]
            )
            self.assertEqual(code, 0, stderr)
            self.assertTrue(os.path.isfile(os.path.join(tmp, "line-下り.json")))
            self.assertTrue(os.path.isfile(os.path.join(tmp, "line-上り.json")))

    def test_direction_up_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "only.json")
            code, stdout, stderr = run_cli(
                [
                    "東京外環自動車道",
                    "--start",
                    "35.7615,139.5989",
                    "--code",
                    "C3",
                    "--osm-json",
                    FIXTURE,
                    "--out",
                    out,
                    "--direction",
                    "up",
                    "--no-meta",
                    "--quiet",
                ]
            )
            self.assertEqual(code, 0, stderr)
            self.assertFalse(os.path.isfile(os.path.join(tmp, "only-下り.json")))
            self.assertTrue(os.path.isfile(os.path.join(tmp, "only-上り.json")))

    def test_direction_up_only_keeps_input_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "far.json")
            code, stdout, stderr = run_cli(
                [
                    "東京外環自動車道",
                    "--start",
                    "35.7500,139.6800",
                    "--code",
                    "C3",
                    "--osm-json",
                    FIXTURE,
                    "--out",
                    out,
                    "--direction",
                    "up",
                    "--no-meta",
                    "--quiet",
                ]
            )
            self.assertEqual(code, 0, stderr)
            self.assertIn("離れています", stdout)

    def test_reverse_both_marks_meta(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "rev.json")
            code, stdout, stderr = run_cli(
                [
                    "東京外環自動車道",
                    "--start",
                    "35.6894,139.9452",
                    "--code",
                    "C3",
                    "--osm-json",
                    FIXTURE,
                    "--out",
                    out,
                    "--reverse",
                    "--quiet",
                ]
            )
            self.assertEqual(code, 0, stderr)
            for label in ("下り", "上り"):
                with open(os.path.join(tmp, "rev-%s.meta.json" % label), encoding="utf-8") as f:
                    meta = json.load(f)
                self.assertTrue(meta["reversed"])
                self.assertNotIn("端点付近", "".join(meta["warnings"]))

    def test_unknown_road_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, stdout, stderr = run_cli(
                [
                    "存在しない道路",
                    "--start",
                    "35.7615,139.5989",
                    "--osm-json",
                    FIXTURE,
                    "--out",
                    os.path.join(tmp, "x.json"),
                    "--quiet",
                ]
            )
            self.assertEqual(code, 1)
            self.assertIn("見つかりません", stderr)
