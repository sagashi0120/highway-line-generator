# -*- coding: utf-8 -*-
"""ラインデータの整形とファイル出力。"""
from __future__ import annotations

import json
import os
from typing import List, Sequence, Tuple

from . import geo

Coord = Tuple[float, float]


def finalize_points(points: Sequence[Coord], simplify_m: float = 0.0, decimals: int = 6) -> List[Coord]:
    """簡略化 → 小数丸め → 連続重複の除去。"""
    pts = [(float(p[0]), float(p[1])) for p in points]
    if simplify_m and simplify_m > 0:
        pts = geo.simplify(pts, simplify_m)
    out: List[Coord] = []
    for lat, lng in pts:
        r = (round(lat, decimals), round(lng, decimals))
        if out and abs(r[0] - out[-1][0]) < 1e-12 and abs(r[1] - out[-1][1]) < 1e-12:
            continue
        out.append(r)
    return out


def to_line_data(points: Sequence[Coord]) -> dict:
    """サイトで使われている lineData 形式。"""
    return {
        "type": "lineData",
        "version": 1,
        "points": [{"lat": lat, "lng": lng} for lat, lng in points],
    }


def to_geojson(points: Sequence[Coord], properties: dict = None) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": dict(properties or {}),
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[lng, lat] for lat, lng in points],
                },
            }
        ],
    }


def write_json(path: str, data, indent: int = 4) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
        f.write("\n")
    os.replace(tmp, path)


def write_csv(path: str, points: Sequence[Coord]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("lat,lng\n")
        for lat, lng in points:
            f.write("%.6f,%.6f\n" % (lat, lng))
    os.replace(tmp, path)


def write_line_data(path: str, points: Sequence[Coord]) -> None:
    write_json(path, to_line_data(points), indent=4)


def build_meta(info, line, points, *, fetch_bbox, used_way_ids, simplify_m: float, generator: str, version: str) -> dict:
    b = line.build
    requested = line.requested_point
    return {
        "type": "lineDataMeta",
        "version": 1,
        "road": info.name,
        "road_input": info.input_name,
        "road_code": ",".join(info.codes),
        "direction": line.direction,
        "requested_point_is": line.requested_point_is,
        "start": {"lat": round(b.start[0], 6), "lng": round(b.start[1], 6)},
        "end": {"lat": round(b.end[0], 6), "lng": round(b.end[1], 6)},
        "requested_start": {"lat": round(requested[0], 6), "lng": round(requested[1], 6)},
        "snap_distance_m": round(b.snap_distance_m, 1),
        "distance_requested_to_start_m": round(b.distance_requested_to_start_m, 1),
        "distance_requested_to_end_m": round(
            geo.haversine_m(requested[0], requested[1], b.end[0], b.end[1]), 1
        ),
        "length_m": round(geo.polyline_length_m(points), 1),
        "points": len(points),
        "simplify_m": simplify_m,
        "trimmed": b.trimmed,
        "reversed": bool(b.reversed),
        "gap_jumps": b.gap_jumps,
        "osm_ways": len(used_way_ids),
        "fetch_bbox": [round(v, 6) for v in fetch_bbox],
        "warnings": list(line.warnings),
        "source": "OpenStreetMap",
        "source_url": "https://www.openstreetmap.org/copyright",
        "license": "ODbL-1.0",
        "generator": "%s %s" % (generator, version),
    }
