# -*- coding: utf-8 -*-
"""道路名と下り線始点から、下り線・上り線のラインデータを生成する中核処理。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

from . import geo, overpass, roads, stitch

Coord = Tuple[float, float]

ROAD_LIKE_TOKENS = ("高速道路", "自動車道", "道路", "国道", "有料道路", "バイパス")
STRUCTURE_TOKENS = ("橋", "トンネル", "隧道", "高架", "覆道", "シェルター", "洞")


@dataclass
class GenerateOptions:
    trim: bool = True
    reverse: bool = False
    directions: str = "both"  # "both" / "down" / "up"
    include_links: bool = False
    connector_max_m: float = 500.0
    connector_structure_max_m: float = 8000.0
    simplify_m: float = 15.0
    gap_m: float = 500.0
    initial_radius_deg: float = 0.15
    search_radius_deg: float = 0.9
    max_expand: int = 12
    max_bbox_deg: float = 14.0
    edge_margin_deg: float = 0.05
    expand_step_min_deg: float = 0.15
    expand_step_max_deg: float = 0.8
    bbox: Optional[Tuple[float, float, float, float]] = None
    source_json: Optional[str] = None
    cache_dir: str = ".linegen-cache"
    refresh: bool = False
    mirrors: Sequence[str] = overpass.DEFAULT_MIRRORS
    quiet: bool = False
    base_dir: str = "."


@dataclass
class RoadInfo:
    input_name: str
    name: str
    codes: List[str]
    table_name: Optional[str] = None


@dataclass
class DirectionalLine:
    direction: str  # "down" / "up"
    points: List[Coord]
    build: stitch.BuildResult
    warnings: List[str] = field(default_factory=list)
    requested_point: Coord = (0.0, 0.0)
    requested_point_is: str = "start"  # 入力地点はラインの始点か終点か

    @property
    def label(self) -> str:
        return "下り" if self.direction == "down" else "上り"


@dataclass
class GenerateResult:
    info: RoadInfo
    down: Optional[DirectionalLine]
    up: Optional[DirectionalLine]
    bbox: Tuple[float, float, float, float]
    fetch_bbox: Tuple[float, float, float, float]
    way_count: int
    primary_count: int
    connector_count: int
    used_way_ids: List[int]
    source_data: List[dict] = field(default_factory=list)

    @property
    def lines(self) -> List[DirectionalLine]:
        return [line for line in (self.down, self.up) if line is not None]

    # 旧APIとの互換（下り線優先）
    @property
    def points(self) -> List[Coord]:
        return (self.down or self.up).points

    @property
    def build(self) -> stitch.BuildResult:
        return (self.down or self.up).build

    @property
    def warnings(self) -> List[str]:
        return (self.down or self.up).warnings


class GenerateError(Exception):
    pass


def log(message: str, quiet: bool = False) -> None:
    if not quiet:
        print(message, flush=True)


# ---------------------------------------------------------------------------
# 道路名の解決
# ---------------------------------------------------------------------------

def resolve_road(road_input: str, code: Optional[str], base_dir: str) -> RoadInfo:
    code_from_name, name = roads.split_road_input(road_input)
    if code:
        codes = [code.upper()]
    elif code_from_name:
        codes = [code_from_name]
    else:
        codes = []
    table_paths = roads.default_road_table_candidates(base_dir)
    table = roads.load_road_table(table_paths)
    if not codes:
        codes = roads.guess_codes(name, table)
    if not name:
        raise GenerateError("道路名を指定してください（例: 東名高速道路）")
    return RoadInfo(input_name=road_input, name=name, codes=codes)


# ---------------------------------------------------------------------------
# ウェイの分類
# ---------------------------------------------------------------------------

def _is_road_like_name(name: str) -> bool:
    return any(token in name for token in ROAD_LIKE_TOKENS)


def _is_structure_name(name: str) -> bool:
    return any(token in name for token in STRUCTURE_TOKENS)


def classify_ways(raw_ways, info: RoadInfo, opts: GenerateOptions):
    """対象道路のウェイ（primary）と、短い橋などの接続ウェイ（connector）に分ける。"""
    dedup: Dict[int, dict] = {}
    for way in raw_ways:
        dedup[way["id"]] = way
    raw_ways = list(dedup.values())

    targets = [info.name]
    primary_raw = []
    for way in raw_ways:
        tags = way["tags"]
        if roads.name_matches(tags, targets):
            primary_raw.append(way)

    fuzzy_used = False
    if not primary_raw:
        for way in raw_ways:
            tags = way["tags"]
            if roads.name_matches(tags, targets, fuzzy=True):
                primary_raw.append(way)
        fuzzy_used = bool(primary_raw)

    primary_ids = {w["id"] for w in primary_raw}
    connector_raw = []
    for way in raw_ways:
        if way["id"] in primary_ids:
            continue
        tags = way["tags"]
        length = geo.polyline_length_m(way["coords"])
        name = tags.get("name") or ""
        highway = tags.get("highway") or ""
        is_link = highway.endswith("_link")
        ref_ok = bool(info.codes) and roads.ref_matches(tags, info.codes)
        if length > opts.connector_max_m:
            # 橋・トンネルなど、構造物名になっている同路線のウェイは長くても接続扱い
            if not (
                ref_ok
                and name
                and _is_structure_name(name)
                and length <= opts.connector_structure_max_m
            ):
                continue
        if name and _is_road_like_name(name):
            continue
        if ref_ok or (is_link and opts.include_links):
            connector_raw.append(way)

    ways: List[stitch.Way] = []
    for way in primary_raw:
        ways.append(
            stitch.Way(
                id=way["id"],
                nodes=tuple(way["nodes"]),
                coords=tuple(way["coords"]),
                tags=way["tags"],
                primary=True,
            )
        )
    for way in connector_raw:
        ways.append(
            stitch.Way(
                id=way["id"],
                nodes=tuple(way["nodes"]),
                coords=tuple(way["coords"]),
                tags=way["tags"],
                primary=False,
            )
        )
    return ways, len(primary_raw), len(connector_raw), fuzzy_used


# ---------------------------------------------------------------------------
# 取得（bbox 拡張）
# ---------------------------------------------------------------------------

def _bbox_of_ways(ways) -> Optional[Tuple[float, float, float, float]]:
    points = []
    for way in ways:
        points.extend(way["coords"])
    if not points:
        return None
    return geo.bbox_of(points)


def _collect_names(primary_raw) -> List[str]:
    names = []
    for way in primary_raw:
        for key in ("name", "name:ja"):
            value = way["tags"].get(key)
            if value and value not in names:
                names.append(value)
    return names[:24]


def _merge_way(raw: Dict[int, dict], way: dict, info: RoadInfo) -> None:
    """同じウェイの重複レスポンスを統合する。

    Overpass のミラーによってタグが古い/新しいことがあるため、
    対象道路名に一致する方を優先する。
    """
    existing = raw.get(way["id"])
    if existing is None:
        raw[way["id"]] = way
        return
    new_primary = roads.name_matches(way["tags"], [info.name])
    old_primary = roads.name_matches(existing["tags"], [info.name])
    if new_primary and not old_primary:
        raw[way["id"]] = way
    elif old_primary and not new_primary:
        return
    else:
        raw[way["id"]] = way


def _fetch(bbox, names, codes, opts: GenerateOptions, *, regex=None, include_links=False):
    ways, data = overpass.fetch_ways(
        bbox,
        names=names,
        refs=codes,
        name_regex=regex,
        include_links=include_links,
        cache_dir=opts.cache_dir,
        refresh=opts.refresh,
        mirrors=opts.mirrors,
        quiet=opts.quiet,
    )
    return ways, data


def _clipped_sides(data_bbox, fetch_bbox, edge_margin_deg: float):
    """データの端が取得範囲の端に接している方向（=道路がまだ先に続く可能性）を返す。"""
    sides = []
    if data_bbox[0] - fetch_bbox[0] <= edge_margin_deg:
        sides.append("south")
    if data_bbox[1] - fetch_bbox[1] <= edge_margin_deg:
        sides.append("west")
    if fetch_bbox[2] - data_bbox[2] <= edge_margin_deg:
        sides.append("north")
    if fetch_bbox[3] - data_bbox[3] <= edge_margin_deg:
        sides.append("east")
    return sides


def _expand_sides(bbox, sides, step_deg: float):
    south, west, north, east = bbox
    if "south" in sides:
        south -= step_deg
    if "west" in sides:
        west -= step_deg
    if "north" in sides:
        north += step_deg
    if "east" in sides:
        east += step_deg
    return (south, west, north, east)


def _fringe_rects(old_bbox, new_bbox):
    """拡張前後の差分（新しく取得すべき外側の帯）を矩形のリストで返す。"""
    s0, w0, n0, e0 = old_bbox
    s1, w1, n1, e1 = new_bbox
    rects = []
    if s1 < s0:
        rects.append((s1, w1, s0, e1))
    if n1 > n0:
        rects.append((n0, w1, n1, e1))
    if w1 < w0:
        rects.append((max(s0, s1), w1, min(n0, n1), w0))
    if e1 > e0:
        rects.append((max(s0, s1), e0, min(n0, n1), e1))
    return rects


def fetch_road_ways(info: RoadInfo, start: Coord, opts: GenerateOptions):
    """対象道路のウェイを、指定地点から bbox を広げながら取得する。

    道路が実際に伸びている方向だけを、少しずつ広げる。
    道路全体が取得範囲に収まったら、あまり余計に広げずに終了する。
    """
    canonical = roads.canonical_name(info.name)
    names = list(dict.fromkeys([info.name, canonical]))

    if opts.bbox is not None:
        fetch_bbox = tuple(opts.bbox)
        ways, data = _fetch(
            fetch_bbox, names, info.codes, opts,
            regex=canonical, include_links=opts.include_links,
        )
        return ways, fetch_bbox, [data], False

    radius = opts.initial_radius_deg
    raw: Dict[int, dict] = {}
    datas = []
    fetch_bbox = geo.bbox_around(start[0], start[1], radius)
    fuzzy_mode = False
    while True:
        ways, data = _fetch(
            fetch_bbox, names, info.codes, opts,
            regex=canonical, include_links=opts.include_links,
        )
        datas.append(data)
        for way in ways:
            _merge_way(raw, way, info)
        primary = _matched_ways(raw, info, fuzzy=False)
        if primary:
            break
        primary = _matched_ways(raw, info, fuzzy=True)
        if primary:
            fuzzy_mode = True
            break
        if radius >= opts.search_radius_deg:
            raise GenerateError(
                "指定地点の周辺で「%s」の道路データが見つかりませんでした。\n"
                "始点の緯度経度を確認するか、--bbox / --code / --name-regex を指定してください。" % info.name
            )
        radius = min(opts.search_radius_deg, radius * 2.0)
        fetch_bbox = geo.bbox_around(start[0], start[1], radius)
        log("  道路を広範囲から探しています（半径 %.2f 度）" % radius, opts.quiet)

    # 実際の OSM の名前を集めて、以降は完全一致も併用して取得する
    primary_names = _collect_names(primary)
    exact_names = list(dict.fromkeys(primary_names + [info.name, canonical]))
    regex = canonical

    truncated = False
    for iteration in range(opts.max_expand):
        # 取得範囲の判定には「対象道路のウェイ」だけを使う。
        # 同じ ref を共有する別路線（例: E1 の東名と名神）で範囲が膨らむのを防ぐ。
        primary_now = _matched_ways(raw, info, fuzzy=fuzzy_mode)
        data_bbox = _bbox_of_ways(primary_now)
        if data_bbox is None:
            break
        clipped = _clipped_sides(data_bbox, fetch_bbox, opts.edge_margin_deg)
        if not clipped:
            log("  取得範囲は道路全体をカバーしています", opts.quiet)
            break
        span = max(data_bbox[2] - data_bbox[0], data_bbox[3] - data_bbox[1])
        step = max(
            opts.expand_step_min_deg,
            min(opts.expand_step_max_deg, span * 0.5),
        )
        new_bbox = _expand_sides(fetch_bbox, clipped, step)
        if (
            new_bbox[2] - new_bbox[0] > opts.max_bbox_deg
            or new_bbox[3] - new_bbox[1] > opts.max_bbox_deg
        ):
            truncated = True
            break
        before = len(primary_now)
        fringe = _fringe_rects(fetch_bbox, new_bbox)
        ways, data = _fetch(
            fringe, exact_names, info.codes, opts,
            regex=regex, include_links=opts.include_links,
        )
        datas.append(data)
        for way in ways:
            _merge_way(raw, way, info)
        fetch_bbox = new_bbox
        log(
            "  取得範囲を拡張: %.2f,%.2f,%.2f,%.2f（%s）"
            % (new_bbox + (",".join(clipped),)),
            opts.quiet,
        )
        primary_after = _matched_ways(raw, info, fuzzy=fuzzy_mode)
        if len(primary_after) == before:
            # 広げた範囲に新しい道路がなければ、道路はここで終わり
            break
    else:
        truncated = True

    primary_final = _matched_ways(raw, info, fuzzy=fuzzy_mode)
    data_bbox = _bbox_of_ways(primary_final)
    if data_bbox is not None and _clipped_sides(data_bbox, fetch_bbox, opts.edge_margin_deg):
        truncated = True

    log(
        "  取得範囲: %.2f,%.2f,%.2f,%.2f（OSM取得 %d 回）"
        % (fetch_bbox + (len(datas),)),
        opts.quiet,
    )
    return list(raw.values()), fetch_bbox, datas, truncated


def _matched_ways(raw: Dict[int, dict], info: RoadInfo, fuzzy: bool = False):
    return [
        way
        for way in raw.values()
        if roads.name_matches(way["tags"], [info.name], fuzzy=fuzzy)
    ]


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------

def generate(road_input: str, start: Coord, code: Optional[str] = None, opts: Optional[GenerateOptions] = None) -> GenerateResult:
    opts = opts or GenerateOptions()
    info = resolve_road(road_input, code, opts.base_dir)
    log("道路: %s%s" % (info.name, (" [%s]" % ",".join(info.codes)) if info.codes else ""), opts.quiet)
    log("始点(指定): %.6f,%.6f" % start, opts.quiet)
    log("OSM データを取得しています...", opts.quiet)

    source_note = None
    truncated = False
    if opts.source_json:
        raw_ways, data = _load_osm_json(opts.source_json)
        fetcher_bbox = _bbox_of_ways(raw_ways) or (0.0, 0.0, 0.0, 0.0)
        fetch_bbox = fetcher_bbox
        datas = [data]
        source_note = "--osm-json の保存データを使用しました"
    else:
        raw_ways, fetch_bbox, datas, truncated = fetch_road_ways(info, start, opts)
    ways, primary_count, connector_count, fuzzy_used = classify_ways(raw_ways, info, opts)

    warnings: List[str] = []
    if source_note:
        warnings.append(source_note)
    if fuzzy_used:
        warnings.append("道路名は部分一致で照合しました。意図した道路か確認してください。")
    if primary_count == 0:
        raise GenerateError(
            "OSM に対象道路のウェイが見つかりませんでした。\n"
            "道路名・--code・--bbox を確認してください（取得範囲: %.4f,%.4f,%.4f,%.4f）。"
            % fetch_bbox
        )
    if connector_count:
        warnings.append("短い接続ウェイ（橋など）を %d 本使用しました。" % connector_count)
    if truncated:
        warnings.append(
            "取得範囲の端に達した可能性があります。"
            "--bbox で範囲を指定するか、--max-expand を増やしてください。"
        )
    if not info.codes:
        warnings.append(
            "道路コードが不明です。橋・トンネルなど名称の異なる区間が欠ける場合があります（--code を指定してください）。"
        )

    log("道路データ: %d 本（接続 %d 本）" % (primary_count, connector_count), opts.quiet)

    # ---- 下り線 ----
    log("下り線を組み立てています...", opts.quiet)
    down_ways = stitch.reverse_ways(ways) if opts.reverse else ways
    down_opts = stitch.BuildOptions(
        trim=opts.trim,
        reverse=opts.reverse,
        gap_m=opts.gap_m,
    )
    down_result = stitch.build_line(down_ways, start, down_opts)
    down_warnings = list(warnings) + list(down_result.warnings)
    unused_near = _find_unused_near(down_result, down_ways)
    if unused_near:
        down_warnings.append(
            "作成したラインの端点付近に未使用の道路データがあります（%s）。"
            "--gap を大きくするか --bbox を指定してください。" % unused_near
        )
    else:
        unused_far = _find_unused_near(down_result, down_ways, threshold_m=30000.0)
        if unused_far:
            down_warnings.append(
                "道路データが別区間に分かれています（%s）。"
                "未開通区間や道路名の変化がないか確認してください。" % unused_far
            )
    down_line = DirectionalLine(
        direction="down",
        points=down_result.points,
        build=down_result,
        warnings=down_warnings,
        requested_point=start,
        requested_point_is="start",
    )

    # ---- 上り線 ----
    up_line = None
    if opts.directions in ("both", "up"):
        log("上り線を組み立てています...", opts.quiet)
        # 上り線は下り線の終点から逆向きに走る。入力地点は上り線の「終点」になる。
        up_input = down_result.end
        up_ways = stitch.reverse_ways(ways) if opts.reverse else ways
        up_opts = stitch.BuildOptions(
            trim=opts.trim,
            reverse=opts.reverse,
            gap_m=opts.gap_m,
            warn_terminus_ambiguity=False,
        )
        try:
            up_result = stitch.build_line(up_ways, up_input, up_opts)
        except stitch.StitchError as exc:
            raise GenerateError(
                "上り線を作成できませんでした（%s）。"
                "OSMの上り線データが不足している可能性があります。" % exc
            )
        # 下り線の始点に相当する地点で上り線の終端を切りそろえる
        up_points = geo.truncate_at_point(up_result.points, down_result.start)
        if len(up_points) < 2:
            raise GenerateError(
                "上り線が短すぎるため作成できませんでした。指定地点が道路の端点付近の可能性があります。"
            )
        up_result = replace(
            up_result,
            points=up_points,
            end=up_points[-1],
        )
        up_warnings = list(warnings) + [
            w for w in up_result.warnings if "道路の終点寄り" not in w
        ]
        if opts.directions == "up":
            # 上り線だけを出力する場合も、入力地点に関する警告は引き継ぐ
            carried = [
                w
                for w in down_result.warnings
                if "指定地点" in w or "道路まで" in w
            ]
            up_warnings = list(dict.fromkeys(carried + up_warnings))
        end_gap = geo.haversine_m(
            up_points[-1][0], up_points[-1][1], down_result.start[0], down_result.start[1]
        )
        if end_gap > 500.0:
            up_warnings.append(
                "上り線の終点が下り線の始点から約 %.1f km 離れています。"
                "OSMのデータ状況を確認してください。" % (end_gap / 1000.0)
            )
        up_line = DirectionalLine(
            direction="up",
            points=up_points,
            build=up_result,
            warnings=up_warnings,
            requested_point=start,
            requested_point_is="end",
        )

    output_lines = []
    if opts.directions in ("both", "down"):
        output_lines.append(down_line)
    if opts.directions in ("both", "up") and up_line is not None:
        output_lines.append(up_line)

    all_points = [p for line in output_lines for p in line.points]
    bbox = geo.bbox_of(all_points) if all_points else (0.0, 0.0, 0.0, 0.0)
    used_ids = list(
        dict.fromkeys(
            way_id for line in output_lines for way_id in line.build.way_ids
        )
    )
    return GenerateResult(
        info=info,
        down=down_line if opts.directions in ("both", "down") else None,
        up=up_line if opts.directions in ("both", "up") else None,
        bbox=bbox,
        fetch_bbox=fetch_bbox,
        way_count=len(ways),
        primary_count=primary_count,
        connector_count=connector_count,
        used_way_ids=used_ids,
        source_data=datas,
    )


def _load_osm_json(path: str):
    """保存済みの Overpass レスポンス（.json / .json.gz）を読み込む。"""
    import gzip
    import json

    opener = gzip.open if path.lower().endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        data = json.load(f)
    return overpass.parse_ways(data), data


def _find_unused_near(result, ways, threshold_m: float = 1500.0) -> Optional[str]:
    """終点から進行方向に続く未使用ウェイがあるか（取りこぼし検出）。"""
    used = set(result.way_ids)
    end = result.end
    end_heading = stitch._heading(result.points, from_end=True)
    for way in ways:
        if way.id in used:
            continue
        for pos, coord in ((0, way.coords[0]), (1, way.coords[-1])):
            d = geo.haversine_m(end[0], end[1], coord[0], coord[1])
            if d > threshold_m:
                continue
            if pos == 0 and way.allows_forward:
                heading = stitch._heading(way.coords, from_end=False)
            elif pos == 1 and way.allows_backward:
                heading = stitch._heading(list(reversed(way.coords)), from_end=False)
            else:
                continue
            if geo.angle_diff_deg(heading, end_heading) <= 45.0:
                return "終点から %.1f km" % (d / 1000.0)
    return None
