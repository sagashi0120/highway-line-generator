# -*- coding: utf-8 -*-
"""地理計算ユーティリティ（標準ライブラリのみ）。

座標はすべて (lat, lng) のタプルで扱う。距離の単位はメートル。
"""
from __future__ import annotations

import math

EARTH_RADIUS_M = 6371008.8

# 緯度1度あたりの距離（m）。道路スケールでは十分な精度。
_M_PER_DEG_LAT = 111132.954
_M_PER_DEG_LNG_EQ = 111319.49079327358


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """2点間の大円距離（m）。"""
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    h = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def bearing_deg(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """1点目から2点目への方位（北=0, 東=90, 南=180, 西=270）。"""
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dl = math.radians(lng2 - lng1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def angle_diff_deg(a: float, b: float) -> float:
    """2方位の差（0〜180度）。"""
    d = abs((a - b) % 360.0)
    return 360.0 - d if d > 180.0 else d


def _m_per_deg(lat: float):
    """その緯度での緯度方向・経度方向の1度あたりの距離（m）。"""
    lat = max(-89.9, min(89.9, lat))
    m_lat = _M_PER_DEG_LAT - 559.822 * math.cos(2.0 * math.radians(lat))
    m_lng = _M_PER_DEG_LNG_EQ * math.cos(math.radians(lat))
    return m_lat, m_lng


def polyline_length_m(points) -> float:
    """ポリラインの道なり距離（m）。"""
    total = 0.0
    for (lat1, lng1), (lat2, lng2) in zip(points, points[1:]):
        total += haversine_m(lat1, lng1, lat2, lng2)
    return total


def cumulative_lengths_m(points):
    """各頂点までの道なり距離のリスト（先頭は0）。"""
    acc = [0.0]
    for (lat1, lng1), (lat2, lng2) in zip(points, points[1:]):
        acc.append(acc[-1] + haversine_m(lat1, lng1, lat2, lng2))
    return acc


def _project(lat: float, lng: float, lat0: float, lng0: float):
    """緯度経度を、基準点まわりのローカル平面座標（m）に変換する。"""
    m_lat, m_lng = _m_per_deg(lat0)
    return (lng - lng0) * m_lng, (lat - lat0) * m_lat


def nearest_on_polyline(lat: float, lng: float, points, nearest_only: bool = False):
    """点からポリラインへの最短点を求める。

    Returns:
        dict: dist_m, along_m, lat, lng, seg_index, t, bearing（その区間の進行方位）。
        頂点が2未満のときは None。
    """
    if len(points) < 2:
        return None
    best = None
    along = 0.0
    for i in range(len(points) - 1):
        a = points[i]
        b = points[i + 1]
        seg_len = haversine_m(a[0], a[1], b[0], b[1])
        ax, ay = 0.0, 0.0
        bx, by = _project(b[0], b[1], a[0], a[1])
        px, py = _project(lat, lng, a[0], a[1])
        dx, dy = bx - ax, by - ay
        denom = dx * dx + dy * dy
        t = 0.0 if denom <= 1e-12 else (px * dx + py * dy) / denom
        t = max(0.0, min(1.0, t))
        qx, qy = ax + t * dx, ay + t * dy
        dist = math.hypot(px - qx, py - qy)
        if best is None or dist < best["dist_m"]:
            best = {
                "dist_m": dist,
                "along_m": along + seg_len * t,
                "lat": a[0] + (b[0] - a[0]) * t,
                "lng": a[1] + (b[1] - a[1]) * t,
                "seg_index": i,
                "t": t,
                "bearing": bearing_deg(a[0], a[1], b[0], b[1])
                if seg_len > 0
                else 0.0,
            }
        along += seg_len
        if nearest_only and best is not None and best["dist_m"] <= 0.01:
            break
    return best


def spacing_stats_m(points):
    """連続する頂点間距離の統計（確認用）。"""
    if len(points) < 2:
        return {"count": 0, "min": 0.0, "mean": 0.0, "max": 0.0}
    gaps = [
        haversine_m(a[0], a[1], b[0], b[1])
        for a, b in zip(points, points[1:])
    ]
    return {
        "count": len(gaps),
        "min": min(gaps),
        "mean": sum(gaps) / len(gaps),
        "max": max(gaps),
    }


def _perpendicular_distance_m(p, a, b) -> float:
    """点 p から線分 ab への垂線距離（m）。"""
    m_lat, m_lng = _m_per_deg(a[0])
    px = (p[1] - a[1]) * m_lng
    py = (p[0] - a[0]) * m_lat
    bx = (b[1] - a[1]) * m_lng
    by = (b[0] - a[0]) * m_lat
    denom = bx * bx + by * by
    if denom <= 1e-12:
        return math.hypot(px, py)
    t = max(0.0, min(1.0, (px * bx + py * by) / denom))
    return math.hypot(px - t * bx, py - t * by)


def simplify(points, tolerance_m: float):
    """Douglas-Peucker 法で間引く。tolerance_m<=0 ならそのまま返す。"""
    n = len(points)
    if tolerance_m <= 0.0 or n <= 2:
        return list(points)

    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi <= lo + 1:
            continue
        worst_i = -1
        worst_d = -1.0
        a = points[lo]
        b = points[hi]
        for i in range(lo + 1, hi):
            d = _perpendicular_distance_m(points[i], a, b)
            if d > worst_d:
                worst_d = d
                worst_i = i
        if worst_d > tolerance_m and worst_i > 0:
            keep[worst_i] = True
            stack.append((lo, worst_i))
            stack.append((worst_i, hi))
    return [points[i] for i in range(n) if keep[i]]


def bbox_of(points):
    """(south, west, north, east) を返す。"""
    lats = [p[0] for p in points]
    lngs = [p[1] for p in points]
    return (min(lats), min(lngs), max(lats), max(lngs))


def bbox_around(lat: float, lng: float, radius_deg: float):
    return (lat - radius_deg, lng - radius_deg, lat + radius_deg, lng + radius_deg)


def bbox_union(a, b):
    return (
        min(a[0], b[0]),
        min(a[1], b[1]),
        max(a[2], b[2]),
        max(a[3], b[3]),
    )


def bbox_expand(bbox, margin_deg: float):
    return (
        bbox[0] - margin_deg,
        bbox[1] - margin_deg,
        bbox[2] + margin_deg,
        bbox[3] + margin_deg,
    )


def bbox_contains(outer, inner) -> bool:
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
    )


def truncate_at_point(points, point):
    """ポリラインを、指定地点の最寄り地点で切り取る（始点側は保持）。"""
    if len(points) < 2:
        return list(points)
    near = nearest_on_polyline(point[0], point[1], points)
    if near is None:
        return list(points)
    index = int(near["seg_index"])
    t = float(near["t"])
    out = list(points[: index + 1])
    if t > 0.0:
        out.append((near["lat"], near["lng"]))
    if len(out) >= 2:
        a, b = out[-1], out[-2]
        if abs(a[0] - b[0]) < 1e-12 and abs(a[1] - b[1]) < 1e-12:
            out.pop()
    return out


def format_coord(lat: float, lng: float, decimals: int = 6) -> str:
    return f"{lat:.{decimals}f},{lng:.{decimals}f}"
