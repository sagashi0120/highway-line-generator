# -*- coding: utf-8 -*-
"""OpenStreetMap Overpass API から道路のウェイを取得する。

- 取得結果はディスクにキャッシュする（同じクエリは再取得しない）
- サーバ混雑・一時エラー時はミラーを切り替えてリトライする
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)

USER_AGENT = "highway-linegen/1.0 (https://github.com/; line data generator)"
# メインラインとして扱う highway クラス
MAIN_HIGHWAYS = ("motorway", "trunk")
LINK_HIGHWAYS = ("motorway_link", "trunk_link")


class FetchError(Exception):
    """Overpass API からの取得に失敗した。"""


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def query_hash(query: str) -> str:
    return hashlib.sha1(query.encode("utf-8")).hexdigest()


def _overpass_quote(value: str) -> str:
    """Overpass の文字列リテラル用にエスケープする。"""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _regex_quote(value: str) -> str:
    """Overpass の正規表現用にメタ文字をエスケープする。"""
    special = ".[]{}()*+?^$|" + chr(92)
    return "".join((chr(92) + ch) if ch in special else ch for ch in value)


def normalize_bboxes(bbox):
    """単一bbox / bboxのリストを、bboxのリストに正規化する。"""
    if not bbox:
        raise ValueError("bbox が空です")
    if isinstance(bbox[0], (int, float)):
        return [tuple(float(v) for v in bbox)]
    return [tuple(float(v) for v in b) for b in bbox]


def build_query(
    bbox,
    names=None,
    refs=None,
    name_regex=None,
    include_links: bool = False,
    timeout: int = 180,
) -> str:
    """Overpass QL のクエリ文字列を組み立てる。

    bbox は (south, west, north, east) もしくはそのリスト。
    複数指定時は、それぞれの範囲をまとめて1クエリで取得する。
    names は完全一致、name_regex は部分一致（正規表現）で名前を探す。
    refs は道路コード（例: E1）の完全一致。
    """
    bboxes = normalize_bboxes(bbox)
    bbox_strs = ["%.6f,%.6f,%.6f,%.6f" % b for b in bboxes]
    highways = list(MAIN_HIGHWAYS)
    if include_links:
        highways.extend(LINK_HIGHWAYS)
    hw_regex = "^(%s)$" % "|".join(re.escape(h) for h in highways)

    clauses = []
    for bbox_str in bbox_strs:
        for name in names or ():
            clauses.append(
                'way["highway"~"%s"]["name"="%s"](%s);' % (hw_regex, _overpass_quote(name), bbox_str)
            )
            clauses.append(
                'way["highway"~"%s"]["official_name"="%s"](%s);'
                % (hw_regex, _overpass_quote(name), bbox_str)
            )
        for ref in refs or ():
            clauses.append(
                'way["highway"~"%s"]["ref"="%s"](%s);' % (hw_regex, _overpass_quote(ref), bbox_str)
            )
        if name_regex:
            clauses.append(
                'way["highway"~"%s"]["name"~"%s"](%s);'
                % (hw_regex, _regex_quote(name_regex), bbox_str)
            )
    if not clauses:
        raise ValueError("names / refs / name_regex のいずれかが必要です")

    return (
        "[out:json][timeout:%d];\n(\n  %s\n);\nout body geom;"
        % (timeout, "\n  ".join(clauses))
    )


def _cache_file(cache_dir: str, query: str) -> str:
    return os.path.join(cache_dir, "overpass", query_hash(query) + ".json")


def _read_cache(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "elements" in data:
            return data
    except (OSError, ValueError):
        return None
    return None


def _write_cache(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def _http_get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": "ja,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def execute_query(
    query: str,
    cache_dir: str,
    refresh: bool = False,
    mirrors=None,
    timeout: float = 300.0,
    retries: int = 3,
    quiet: bool = False,
) -> dict:
    """Overpass クエリを実行して JSON を返す（キャッシュ優先）。"""
    mirrors = tuple(mirrors or DEFAULT_MIRRORS)
    path = _cache_file(cache_dir, query)
    if not refresh:
        cached = _read_cache(path)
        if cached is not None:
            if not quiet:
                _log("  [cache] Overpass 結果を再利用")
            return cached

    last_error = None
    for attempt in range(retries):
        for mirror in mirrors:
            url = mirror + "?data=" + urllib.parse.quote(query, safe="")
            try:
                if not quiet:
                    _log("  [osm] 取得中... (%s)" % mirror.split("/")[2])
                raw = _http_get(url, timeout)
                data = json.loads(raw.decode("utf-8"))
                if "elements" not in data:
                    last_error = FetchError(
                        "Overpass の応答に elements がありません: %s"
                        % str(data)[:300]
                    )
                    continue
                remark = data.get("remark")
                if remark:
                    low = str(remark).lower()
                    if "timed out" in low or "out of memory" in low or "error" in low:
                        # 部分的にしか返っていない可能性があるため保存しない
                        last_error = FetchError("Overpass エラー: %s" % remark)
                        continue
                _write_cache(path, data)
                return data
            except urllib.error.HTTPError as exc:
                last_error = exc
                if not quiet:
                    _log("  [osm] HTTP %s (%s)" % (exc.code, mirror.split("/")[2]))
                if exc.code in (400, 401, 403):
                    # クエリ自体の問題。他のミラーでも同じなので中断
                    raise FetchError("Overpass がクエリを拒否しました (HTTP %s)" % exc.code)
            except urllib.error.URLError as exc:
                last_error = exc
                if not quiet:
                    _log("  [osm] 接続エラー (%s)" % exc.reason)
            except (ValueError, OSError) as exc:
                last_error = exc
                if not quiet:
                    _log("  [osm] 応答エラー: %s" % exc)
        if attempt + 1 < retries:
            wait = 3.0 * (attempt + 1)
            if not quiet:
                _log("  [osm] %d 秒待ってリトライします" % int(wait))
            time.sleep(wait)
    raise FetchError(
        "Overpass API から取得できませんでした（%s）。\n"
        "ネットワーク接続を確認するか、--osm-json で保存済みデータを指定してください。"
        % last_error
    )


def parse_ways(data) -> list:
    """Overpass のレスポンスからウェイだけを正規化して返す。"""
    ways = []
    for el in data.get("elements", ()):
        if el.get("type") != "way":
            continue
        nodes = el.get("nodes")
        geom = el.get("geometry")
        if not nodes or not geom or len(nodes) != len(geom):
            continue
        coords = []
        for point in geom:
            if "lat" not in point or "lon" not in point:
                coords = []
                break
            coords.append((float(point["lat"]), float(point["lon"])))
        if len(coords) < 2:
            continue
        ways.append(
            {
                "id": el.get("id"),
                "nodes": [int(n) for n in nodes],
                "coords": coords,
                "tags": el.get("tags") or {},
            }
        )
    return ways


def fetch_ways(
    bbox,
    names=None,
    refs=None,
    name_regex=None,
    include_links: bool = False,
    cache_dir: str = ".linegen-cache",
    refresh: bool = False,
    mirrors=None,
    timeout: float = 300.0,
    retries: int = 3,
    quiet: bool = False,
):
    """条件に合う OSM ウェイを取得する。"""
    query = build_query(
        bbox,
        names=names,
        refs=refs,
        name_regex=name_regex,
        include_links=include_links,
    )
    data = execute_query(
        query,
        cache_dir=cache_dir,
        refresh=refresh,
        mirrors=mirrors,
        timeout=timeout,
        retries=retries,
        quiet=quiet,
    )
    return parse_ways(data), data
