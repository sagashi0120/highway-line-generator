#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成した lineData と既存 lineData を比較する補助スクリプト。

使い方:
    py tools/compare_with_existing.py out/E14-京葉道路-下り.json ../test_app/latlng/E14-京葉道路.json

複数の既存ファイルを渡すと、それらをまとめて比較します。
"""
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from linegen import geo  # noqa: E402


def load_points(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("type") != "lineData":
        raise SystemExit("lineData 形式ではありません: %s" % path)
    return [(float(p["lat"]), float(p["lng"])) for p in data["points"]]


def distances(points, line):
    values = []
    for lat, lng in points:
        near = geo.nearest_on_polyline(lat, lng, line)
        values.append(near["dist_m"])
    values.sort()
    return values


def pct(values, ratio):
    if not values:
        return 0.0
    index = int(len(values) * ratio)
    index = min(index, len(values) - 1)
    return values[index]


def report(name, points, line):
    ds = distances(points, line)
    print("  %-40s 点数=%5d 平均=%6.1fm p95=%7.1fm 最大=%7.1fm"
          % (name, len(points), sum(ds) / len(ds), pct(ds, 0.95), ds[-1]))


def main(argv=None):
    argv = argv or sys.argv[1:]
    if len(argv) < 2:
        print(__doc__)
        return 2
    generated_path = argv[0]
    existing_paths = argv[1:]
    generated = load_points(generated_path)
    existing_groups = []
    all_existing = []
    print("生成ライン: %s" % generated_path)
    print("  延長 %.2f km / 点数 %d" % (geo.polyline_length_m(generated) / 1000.0, len(generated)))
    for path in existing_paths:
        pts = load_points(path)
        existing_groups.append((os.path.basename(path), pts))
        all_existing.extend(pts)
    print("既存ライン点 -> 生成ライン点 の最短距離:")
    for name, pts in existing_groups:
        report(name, pts, generated)
    print("生成ライン点 -> 既存ライン点 の最短距離:")
    report("(既存データ全体)", generated, all_existing)
    return 0


if __name__ == "__main__":
    sys.exit(main())
