# -*- coding: utf-8 -*-
"""OSM ウェイを1本のポリラインに組み立てる（下り線・上り線の共通処理）。

考え方:
- 高速道路は上下線が別々の oneway ウェイで描かれていることが多い。
- 指定地点に最も近い「走行開始点」を持つキャリッジウェイ（下り線）を選ぶ。
- 指定地点の最寄り地点から走行方向へたどって1本の線にする（既定）。
  指定地点が多少ずれていても、最寄り地点に補正するだけでエラーにしない。
- --full の場合は道路の始点（OSMの端点）までさかのぼって全体を作る。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import geo

Coord = Tuple[float, float]


@dataclass
class Way:
    id: int
    nodes: Tuple[int, ...]
    coords: Tuple[Coord, ...]
    tags: dict = field(default_factory=dict)
    primary: bool = True

    @property
    def start_node(self) -> int:
        return self.nodes[0]

    @property
    def end_node(self) -> int:
        return self.nodes[-1]

    @property
    def name(self) -> str:
        return self.tags.get("name") or ""

    @property
    def ref(self) -> str:
        return self.tags.get("ref") or ""

    @property
    def oneway(self) -> str:
        return (self.tags.get("oneway") or "").strip().lower()

    @property
    def allows_forward(self) -> bool:
        return self.oneway in ("", "yes", "true", "1")

    @property
    def allows_backward(self) -> bool:
        return self.oneway in ("", "no", "false", "0", "-1", "reverse")

    @property
    def length_m(self) -> float:
        return geo.polyline_length_m(self.coords)


@dataclass
class BuildOptions:
    trim: bool = True
    reverse: bool = False
    gap_m: float = 500.0
    max_turn_deg: float = 100.0
    max_gap_turn_deg: float = 60.0
    proximity_m: float = 500.0
    max_warn_start_distance_m: float = 5000.0
    max_warn_snap_distance_m: float = 2000.0
    min_length_m: float = 200.0
    loop_guard_m: float = 150.0
    terminus_ambiguity_m: float = 120.0
    warn_terminus_ambiguity: bool = True


@dataclass
class BuildResult:
    points: List[Coord]
    start: Coord
    end: Coord
    requested_start: Coord
    distance_requested_to_start_m: float
    snap_distance_m: float
    gap_jumps: int
    way_ids: List[int]
    warnings: List[str] = field(default_factory=list)
    trimmed: bool = False
    reversed: bool = False
    component_distance_m: float = 0.0


class StitchError(Exception):
    pass


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _heading(points: Sequence[Coord], from_end: bool = False, sample_m: float = 50.0) -> float:
    """ポリライン端（または始点）付近の進行方位を返す。"""
    if len(points) < 2:
        return 0.0
    if from_end:
        end = points[-1]
        acc = 0.0
        for i in range(len(points) - 2, -1, -1):
            acc += geo.haversine_m(points[i][0], points[i][1], points[i + 1][0], points[i + 1][1])
            if acc >= sample_m or i == 0:
                return geo.bearing_deg(points[i][0], points[i][1], end[0], end[1])
    start = points[0]
    acc = 0.0
    for i in range(1, len(points)):
        acc += geo.haversine_m(points[i - 1][0], points[i - 1][1], points[i][0], points[i][1])
        if acc >= sample_m or i == len(points) - 1:
            return geo.bearing_deg(start[0], start[1], points[i][0], points[i][1])
    return 0.0


def _heading_outward(points: Sequence[Coord], sample_m: float = 50.0) -> float:
    """末尾から手前側へ向かう方位（逆向き）。"""
    if len(points) < 2:
        return 0.0
    end = points[-1]
    acc = 0.0
    for i in range(len(points) - 2, -1, -1):
        acc += geo.haversine_m(points[i][0], points[i][1], points[i + 1][0], points[i + 1][1])
        if acc >= sample_m or i == 0:
            return geo.bearing_deg(end[0], end[1], points[i][0], points[i][1])
    return 0.0


def _dedupe_join(base: Sequence[Coord], addition: Sequence[Coord]) -> List[Coord]:
    out = list(base)
    add = list(addition)
    if out and add:
        a, b = out[-1], add[0]
        if abs(a[0] - b[0]) < 1e-12 and abs(a[1] - b[1]) < 1e-12:
            add = add[1:]
    out.extend(add)
    return out


def _prepend(prefix: Sequence[Coord], base: Sequence[Coord]) -> List[Coord]:
    pre = list(prefix)
    out = list(base)
    if pre and out:
        a, b = pre[-1], out[0]
        if abs(a[0] - b[0]) < 1e-12 and abs(a[1] - b[1]) < 1e-12:
            out = out[1:]
    return pre + out


def _near_line(coord: Coord, points: Sequence[Coord], threshold_m: float) -> bool:
    n = len(points)
    if n == 0:
        return False
    indices = list(range(0, n, 10))
    for i in range(max(0, n - 40), n):
        indices.append(i)
    for i in set(indices):
        p = points[i]
        if geo.haversine_m(coord[0], coord[1], p[0], p[1]) <= threshold_m:
            return True
    return False


# ---------------------------------------------------------------------------
# グラフ
# ---------------------------------------------------------------------------

class _Graph:
    def __init__(self, ways: Sequence[Way]):
        self.ways = list(ways)
        self.node_ways: Dict[int, List[Tuple[int, int]]] = {}
        for idx, way in enumerate(self.ways):
            self.node_ways.setdefault(way.start_node, []).append((idx, 0))
            if way.end_node != way.start_node:
                self.node_ways.setdefault(way.end_node, []).append((idx, 1))
        self.components = self._build_components()

    def _build_components(self):
        parent = list(range(len(self.ways)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        for pairs in self.node_ways.values():
            first = pairs[0][0]
            for idx, _ in pairs[1:]:
                union(first, idx)

        comps: Dict[int, List[int]] = {}
        for idx in range(len(self.ways)):
            comps.setdefault(find(idx), []).append(idx)
        return list(comps.values())

    def component_endpoints(self, comp: Sequence[int]):
        degree: Dict[int, int] = {}
        for idx in comp:
            way = self.ways[idx]
            degree[way.start_node] = degree.get(way.start_node, 0) + 1
            if way.end_node != way.start_node:
                degree[way.end_node] = degree.get(way.end_node, 0) + 1
        return [node for node, deg in degree.items() if deg == 1]

    def node_coord(self, node: int) -> Optional[Coord]:
        for idx, pos in self.node_ways.get(node, ()):
            way = self.ways[idx]
            return way.coords[0] if pos == 0 else way.coords[-1]
        return None

    def is_head(self, comp: Sequence[int], node: int) -> bool:
        comp_set = set(comp)
        for idx, pos in self.node_ways.get(node, ()):
            if idx not in comp_set:
                continue
            way = self.ways[idx]
            if pos == 0 and way.allows_forward:
                return True
            if pos == 1 and way.allows_backward:
                return True
        return False

    def is_tail(self, comp: Sequence[int], node: int) -> bool:
        comp_set = set(comp)
        for idx, pos in self.node_ways.get(node, ()):
            if idx not in comp_set:
                continue
            way = self.ways[idx]
            if pos == 1 and way.allows_forward:
                return True
            if pos == 0 and way.allows_backward:
                return True
        return False

    def component_nearest(self, point: Coord):
        result = []
        for comp in self.components:
            best = None
            for idx in comp:
                near = geo.nearest_on_polyline(point[0], point[1], self.ways[idx].coords)
                if near is None:
                    continue
                if best is None or near["dist_m"] < best["dist_m"]:
                    best = dict(near)
                    best["way_index"] = idx
            if best is not None:
                result.append((best["dist_m"], comp, best))
        return result


def _node_distance(graph: _Graph, node: int, point: Coord) -> float:
    coord = graph.node_coord(node)
    if coord is None:
        return float("inf")
    return geo.haversine_m(point[0], point[1], coord[0], coord[1])


def _terminus_ambiguity(graph: _Graph, comp, head_coord, point, opts: BuildOptions) -> bool:
    """指定地点が道路の端点にあり、下り/上りの判定が曖昧か。"""
    if geo.haversine_m(point[0], point[1], head_coord[0], head_coord[1]) > opts.terminus_ambiguity_m:
        return False
    comp_set = set(comp)
    for other in graph.components:
        if set(other) == comp_set:
            continue
        for node in graph.component_endpoints(other):
            if not graph.is_tail(other, node):
                continue
            coord = graph.node_coord(node)
            if coord is None:
                continue
            if geo.haversine_m(point[0], point[1], coord[0], coord[1]) <= opts.terminus_ambiguity_m:
                return True
    return False


# ---------------------------------------------------------------------------
# 始点の選択
# ---------------------------------------------------------------------------

@dataclass
class _StartChoice:
    comp: List[int]
    head_node: int
    head_coord: Coord
    component_distance_m: float
    head_distance_m: float
    point_is_near_tail: bool


def _choose_start(graph: _Graph, point: Coord, opts: BuildOptions) -> _StartChoice:
    comp_nearest = sorted(graph.component_nearest(point), key=lambda x: x[0])
    if not comp_nearest:
        raise StitchError("道路の形状データがありません")

    best_dist = comp_nearest[0][0]
    proximity = max(opts.proximity_m, best_dist * 0.5)
    candidates = [c for c in comp_nearest if c[0] <= best_dist + proximity]
    if not candidates:
        candidates = comp_nearest[:2]

    scored = []
    for comp_dist, comp, near in candidates:
        endpoints = graph.component_endpoints(comp)
        heads = [n for n in endpoints if graph.is_head(comp, n)]
        if not heads:
            if endpoints:
                heads = sorted(endpoints, key=lambda n: _node_distance(graph, n, point))
            else:
                heads = [graph.ways[near["way_index"]].start_node]
        head = min(heads, key=lambda n: _node_distance(graph, n, point))
        head_coord = graph.node_coord(head)
        if head_coord is None:
            continue
        head_dist = geo.haversine_m(point[0], point[1], head_coord[0], head_coord[1])
        tails = [n for n in endpoints if graph.is_tail(comp, n)]
        tail_dist = min((_node_distance(graph, n, point) for n in tails), default=float("inf"))
        scored.append((head_dist, comp_dist, comp, head, head_coord, tail_dist < head_dist))

    if not scored:
        raise StitchError("道路の始点を特定できませんでした")

    scored.sort(key=lambda x: (x[0], x[1]))
    head_dist, comp_dist, comp, head, head_coord, near_tail = scored[0]
    return _StartChoice(
        comp=comp,
        head_node=head,
        head_coord=head_coord,
        component_distance_m=comp_dist,
        head_distance_m=head_dist,
        point_is_near_tail=near_tail,
    )


# ---------------------------------------------------------------------------
# たどり
# ---------------------------------------------------------------------------

def _travel(graph: _Graph, node: int, way_index: int, pos: int):
    way = graph.ways[way_index]
    if pos == 0:
        if not way.allows_forward:
            return None
        return list(way.coords), way.end_node
    if not way.allows_backward:
        return None
    return list(reversed(way.coords)), way.start_node


def _arrival(graph: _Graph, node: int, way_index: int, pos: int):
    way = graph.ways[way_index]
    if pos == 1:
        if not way.allows_forward:
            return None
        return list(way.coords), way.start_node
    if not way.allows_backward:
        return None
    return list(reversed(way.coords)), way.end_node


def _pick_candidates(ways, cands, last_index, index_primary=None):
    """primary ウェイを優先し、connector の連鎖を防ぐ。"""
    primary = [c for c in cands if ways[c[index_primary]].primary]
    if primary:
        return primary
    if last_index is not None and not ways[last_index].primary:
        return []
    return cands


def _walk_forward_from(graph, line, node, used, opts, way_ids, gap_jumps, last_index):
    ways = graph.ways
    heading = _heading(line, from_end=True)
    while True:
        cands = []
        for widx, pos in graph.node_ways.get(node, ()):
            if widx in used:
                continue
            got = _travel(graph, node, widx, pos)
            if got is None or len(got[0]) < 2:
                continue
            c_coords, c_far = got
            turn = geo.angle_diff_deg(heading, _heading(c_coords, from_end=False))
            if turn <= opts.max_turn_deg:
                cands.append((turn, widx, c_coords, c_far))
        cands = _pick_candidates(ways, cands, last_index, index_primary=1)
        if cands:
            cands.sort(key=lambda x: x[0])
            _, widx, c_coords, c_far = cands[0]
            used.add(widx)
            way_ids.append(ways[widx].id)
            line = _dedupe_join(line, c_coords)
            node = c_far
            last_index = widx
            heading = _heading(line, from_end=True)
            continue

        gaps = _gap_candidates(graph, line[-1], heading, used, opts, backward=False)
        gaps = _pick_candidates(ways, gaps, last_index, index_primary=2)
        if gaps:
            _, _, widx, c_coords, c_far = gaps[0]
            used.add(widx)
            way_ids.append(ways[widx].id)
            line = _dedupe_join(line, c_coords)
            node = c_far
            last_index = widx
            heading = _heading(line, from_end=True)
            gap_jumps += 1
            continue
        break
    return line, node, way_ids, gap_jumps, last_index


def _walk_forward(graph, head_node, used, opts):
    ways = graph.ways
    starts = []
    for widx, pos in graph.node_ways.get(head_node, ()):
        if widx in used:
            continue
        got = _travel(graph, head_node, widx, pos)
        if got is None or len(got[0]) < 2:
            continue
        starts.append((widx, got[0], got[1]))
    if not starts:
        raise StitchError("始点から走行できる道路が見つかりません")
    primaries = [s for s in starts if ways[s[0]].primary]
    if primaries:
        starts = primaries
    starts.sort(key=lambda x: -geo.polyline_length_m(x[1]))
    widx, coords, far_node = starts[0]
    used.add(widx)
    line = list(coords)
    way_ids = [ways[widx].id]
    return _walk_forward_from(graph, line, far_node, used, opts, way_ids, 0, widx)


def _gap_candidates(graph, point, heading, used, opts, backward):
    if opts.gap_m <= 0 or point is None:
        return []
    out = []
    for way_index, way in enumerate(graph.ways):
        if way_index in used:
            continue
        for endpoint_pos in (0, 1):
            node = way.start_node if endpoint_pos == 0 else way.end_node
            coord = way.coords[0] if endpoint_pos == 0 else way.coords[-1]
            dist = geo.haversine_m(point[0], point[1], coord[0], coord[1])
            if dist > opts.gap_m:
                continue
            if backward and dist < 15.0:
                continue
            got = _arrival(graph, node, way_index, endpoint_pos) if backward else _travel(
                graph, node, way_index, endpoint_pos
            )
            if got is None or len(got[0]) < 2:
                continue
            c_coords, c_far = got
            gap_heading = (
                geo.bearing_deg(point[0], point[1], coord[0], coord[1])
                if dist > 5.0
                else heading
            )
            gap_turn = geo.angle_diff_deg(heading, gap_heading)
            cand_heading = _heading_outward(c_coords) if backward else _heading(c_coords, from_end=False)
            cand_turn = geo.angle_diff_deg(heading, cand_heading)
            if gap_turn > opts.max_gap_turn_deg or cand_turn > opts.max_gap_turn_deg:
                continue
            score = gap_turn + cand_turn + 40.0 * dist / max(opts.gap_m, 1.0)
            out.append((score, dist, way_index, c_coords, c_far))
    out.sort(key=lambda x: x[0])
    return out


def _walk_backward(graph, head_node, used, opts, forward_line):
    ways = graph.ways
    prefix: List[Coord] = []
    node = head_node
    gap_jumps = 0
    way_ids: List[int] = []

    backward_heading = (_heading(forward_line, from_end=False) + 180.0) % 360.0
    last_index = None

    while True:
        cands = []
        for widx, pos in graph.node_ways.get(node, ()):
            if widx in used:
                continue
            got = _arrival(graph, node, widx, pos)
            if got is None or len(got[0]) < 2:
                continue
            c_coords, c_far = got
            if _near_line(c_coords[0], forward_line, opts.loop_guard_m):
                continue
            turn = geo.angle_diff_deg(backward_heading, _heading_outward(c_coords))
            if turn > opts.max_turn_deg:
                continue
            cands.append((turn, widx, c_coords, c_far))
        cands = _pick_candidates(ways, cands, last_index, index_primary=1)
        if cands:
            cands.sort(key=lambda x: x[0])
            _, widx, c_coords, c_far = cands[0]
            used.add(widx)
            way_ids.append(ways[widx].id)
            prefix = _prepend(c_coords, prefix)
            node = c_far
            last_index = widx
            backward_heading = (_heading(prefix, from_end=False) + 180.0) % 360.0
            continue

        point = prefix[0] if prefix else graph.node_coord(head_node)
        gaps = _gap_candidates(graph, point, backward_heading, used, opts, backward=True)
        gaps = [g for g in gaps if not _near_line(g[3][0], forward_line, opts.loop_guard_m)]
        gaps = _pick_candidates(ways, gaps, last_index, index_primary=2)
        if gaps:
            _, _, widx, c_coords, c_far = gaps[0]
            used.add(widx)
            way_ids.append(ways[widx].id)
            prefix = _prepend(c_coords, prefix)
            node = c_far
            last_index = widx
            backward_heading = (_heading(prefix, from_end=False) + 180.0) % 360.0
            gap_jumps += 1
            continue
        break
    return prefix, way_ids, gap_jumps


# ---------------------------------------------------------------------------
# 指定地点からの途中開始（trim）
# ---------------------------------------------------------------------------

def _snap_on_component(graph, comp, point):
    best = None
    for idx in comp:
        near = geo.nearest_on_polyline(point[0], point[1], graph.ways[idx].coords)
        if near is None:
            continue
        if best is None or near["dist_m"] < best["near"]["dist_m"]:
            best = {"way_index": idx, "near": near}
    return best


def _partial_way(graph, way_index, near, head_node=None):
    """指定地点を含むウェイの、地点より先（走行方向）の座標列を返す。"""
    way = graph.ways[way_index]
    coords = list(way.coords)
    i = int(near["seg_index"])
    t = float(near["t"])
    snap = (near["lat"], near["lng"])

    forward_ok = way.allows_forward
    backward_ok = way.allows_backward
    if forward_ok and not backward_ok:
        use_backward = False
    elif backward_ok and not forward_ok:
        use_backward = True
    else:
        # 両方向（oneway なし）。道路の始点から遠ざかる向きを選ぶ。
        use_backward = False
        head_coord = graph.node_coord(head_node) if head_node is not None else None
        if head_coord is not None:
            start_coord = way.coords[0]
            end_coord = way.coords[-1]
            d_start = geo.haversine_m(
                head_coord[0], head_coord[1], start_coord[0], start_coord[1]
            )
            d_end = geo.haversine_m(
                head_coord[0], head_coord[1], end_coord[0], end_coord[1]
            )
            use_backward = d_end < d_start

    if not use_backward:
        partial = [snap] + coords[i + 1 :]
        far_node = way.end_node
    else:
        rev = list(reversed(coords))
        j = len(coords) - 2 - i
        partial = [snap] + rev[j + 1 :]
        far_node = way.start_node
    return partial, far_node, use_backward


# ---------------------------------------------------------------------------
# 全体
# ---------------------------------------------------------------------------

def build_line(ways: Iterable[Way], point: Coord, opts: BuildOptions = BuildOptions()) -> BuildResult:
    ways = [w for w in ways if len(w.coords) >= 2]
    if not ways:
        raise StitchError("対象の道路ウェイが0件です")

    graph = _Graph(ways)
    choice = _choose_start(graph, point, opts)
    used = set()
    warnings: List[str] = []
    snap_distance = 0.0
    snapped = False

    if opts.trim:
        snap = _snap_on_component(graph, choice.comp, point)
        if snap is None:
            raise StitchError("指定地点の最寄り道路が見つかりません")
        way_index = snap["way_index"]
        near = snap["near"]
        snap_distance = near["dist_m"]
        partial, far_node, _ = _partial_way(graph, way_index, near, choice.head_node)
        used.add(way_index)
        line = list(partial)
        way_ids = [graph.ways[way_index].id]
        forward, end_node, f_ids, f_jumps, _ = _walk_forward_from(
            graph, line, far_node, used, opts, way_ids, 0, way_index
        )
        points = _clean_points(forward)
        snapped = True
        b_ids = []
        b_jumps = 0
    else:
        forward, end_node, f_ids, f_jumps, _ = _walk_forward(graph, choice.head_node, used, opts)
        prefix, b_ids, b_jumps = _walk_backward(graph, choice.head_node, used, opts, forward)
        points = _clean_points(_dedupe_join(prefix, forward) if prefix else list(forward))
        snapped = False

    if len(points) < 2:
        raise StitchError("有効なラインを作成できませんでした")

    length_m = geo.polyline_length_m(points)
    distance_to_start = geo.haversine_m(point[0], point[1], points[0][0], points[0][1])

    if choice.point_is_near_tail:
        warnings.append(
            "指定地点が道路の終点寄りです。下り線の向きが正しいか確認してください。"
        )
    elif (
        opts.warn_terminus_ambiguity
        and not opts.reverse
        and _terminus_ambiguity(graph, choice.comp, choice.head_coord, point, opts)
    ):
        warnings.append(
            "指定地点は道路の端点付近です。ここが下り線の始点であることを確認してください。"
            "（終点を指定した場合は --reverse で向きを補正できます）"
        )
    if opts.reverse:
        warnings.append("--reverse により、指定地点から逆向きにラインを作成しました。")
    if snap_distance > opts.max_warn_snap_distance_m:
        warnings.append(
            "指定地点から道路まで約 %.1f km 離れています。始点を確認してください。"
            % (snap_distance / 1000.0)
        )
    if not opts.trim and distance_to_start > opts.max_warn_start_distance_m:
        warnings.append(
            "指定地点から下り線の始点まで約 %.1f km 離れています。始点を確認してください。"
            % (distance_to_start / 1000.0)
        )
    if f_jumps + b_jumps:
        warnings.append(
            "道路データの途切れを %d か所つないでいます（--gap で調整できます）。"
            % (f_jumps + b_jumps)
        )
    if length_m < opts.min_length_m:
        warnings.append(
            "作成したラインが短すぎます（%.0f m）。指定地点が終点付近の可能性があります。"
            % length_m
        )

    return BuildResult(
        points=points,
        start=points[0],
        end=points[-1],
        requested_start=point,
        distance_requested_to_start_m=distance_to_start,
        snap_distance_m=snap_distance,
        gap_jumps=f_jumps + b_jumps,
        way_ids=list(dict.fromkeys(b_ids + f_ids)),
        warnings=warnings,
        trimmed=snapped,
        reversed=bool(opts.reverse),
        component_distance_m=choice.component_distance_m,
    )


def _clean_points(points: Sequence[Coord]) -> List[Coord]:
    out: List[Coord] = []
    for p in points:
        if out:
            q = out[-1]
            if abs(p[0] - q[0]) < 1e-12 and abs(p[1] - q[1]) < 1e-12:
                continue
        out.append((p[0], p[1]))
    return out


def nearest_on_line(points, point: Coord):
    return geo.nearest_on_polyline(point[0], point[1], points)


def reverse_ways(ways: Iterable[Way]) -> List[Way]:
    """全ウェイの進行方向を反転したコピーを返す（--reverse 用）。"""
    out: List[Way] = []
    for way in ways:
        # ノード順と座標列を反転すると、oneway タグはそのままで進行方向が逆になる
        out.append(
            Way(
                id=way.id,
                nodes=tuple(reversed(way.nodes)),
                coords=tuple(reversed(way.coords)),
                tags=dict(way.tags),
                primary=way.primary,
            )
        )
    return out
