# -*- coding: utf-8 -*-
"""道路名の正規化・道路コード表・座標文字列の解析。"""
from __future__ import annotations

import csv
import os
import re
import unicodedata

# 道路名のゆれを吸収するための置換
_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uff0d"


def normalize_name(text) -> str:
    """全角半角・空白・ダッシュのゆれを吸収する。"""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    for ch in _DASHES:
        s = s.replace(ch, "-")
    s = s.replace("\u3000", " ")
    s = re.sub(r"\s+", "", s)
    return s


def canonical_name(text) -> str:
    """末尾の括弧書きを落とした名前を返す。

    例: 中央自動車道(富士吉田線) -> 中央自動車道
    """
    s = normalize_name(text)
    while True:
        start = s.find("(")
        if start < 0:
            break
        end = s.rfind(")")
        if end <= start:
            break
        s = s[:start] + s[end + 1 :]
    s = s.replace("・", "")
    return s


def way_names(tags: dict):
    """OSM タグから比較対象の名前を取り出す。"""
    names = []
    for key in ("name", "name:ja", "official_name"):
        value = tags.get(key)
        if value:
            names.append(str(value))
    return names


def name_matches(tags: dict, targets, fuzzy: bool = False) -> bool:
    """ウェイのタグが対象の道路名に一致するか。"""
    target_norms = set()
    target_canons = set()
    for t in targets:
        n = normalize_name(t)
        if n:
            target_norms.add(n)
            target_canons.add(canonical_name(t))
    for raw in way_names(tags):
        n = normalize_name(raw)
        c = canonical_name(raw)
        if n in target_norms or c in target_canons:
            return True
        if fuzzy:
            for tn in target_norms:
                if tn and (tn in n or n in tn):
                    return True
            for tc in target_canons:
                if tc and (tc in c or c in tc):
                    return True
    return False


def ref_list(tags: dict):
    raw = tags.get("ref") or ""
    return [part.strip() for part in re.split(r"[;,/]", raw) if part.strip()]


def ref_matches(tags: dict, codes) -> bool:
    if not codes:
        return False
    refs = {r.upper() for r in ref_list(tags)}
    return bool(refs & {c.upper() for c in codes})


def load_road_table(paths):
    """road_list.csv (code,name) を読み、名前 -> コードの辞書を作る。"""
    table = {}
    for path in paths:
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                for row in csv.reader(f):
                    if len(row) < 2:
                        continue
                    code = row[0].strip()
                    name = row[1].strip()
                    if not code or not name:
                        continue
                    for key in {normalize_name(name), canonical_name(name)}:
                        table.setdefault(key, set()).add(code)
        except OSError:
            continue
    return table


def guess_codes(name: str, table: dict):
    codes = set()
    for key in {normalize_name(name), canonical_name(name)}:
        codes.update(table.get(key, set()))
    return sorted(codes)


def default_road_table_candidates(base_dir: str):
    """このスクリプトの位置から road_list.csv を探す候補パス。"""
    here = os.path.abspath(base_dir)
    candidates = []
    for up in (here, os.path.dirname(here), os.path.dirname(os.path.dirname(here))):
        candidates.append(os.path.join(up, "data", "public", "road_list.csv"))
        candidates.append(os.path.join(up, "road_list.csv"))
    # 重複を除く
    seen = set()
    unique = []
    for path in candidates:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def split_road_input(text: str):
    """E1-東名高速道路 / E1 東名高速道路 のような入力を code, name に分ける。"""
    text = (text or "").strip()
    m = re.match(r"^([A-Za-z]{1,2}[0-9]{1,2}[A-Za-z]?)[\s\-:：]+(.+)$", text)
    if m:
        return m.group(1).upper(), m.group(2).strip()
    return None, text


def parse_coord(text: str):
    """緯度経度の文字列を (lat, lng) に変換する。

    受け付ける形式:
      35.6283,139.6187
      35.6283 139.6187
      lat=35.6283 lng=139.6187
    """
    if text is None:
        raise ValueError("座標が指定されていません")
    s = str(text).strip()
    s = s.replace("、", ",").replace("；", ";")
    if "lat" in s.lower() or "lng" in s.lower():
        lat_m = re.search(r"lat\s*[=:]\s*(-?[0-9.]+)", s, re.IGNORECASE)
        lng_m = re.search(r"(?:lng|lon)\s*[=:]\s*(-?[0-9.]+)", s, re.IGNORECASE)
        if lat_m and lng_m:
            return float(lat_m.group(1)), float(lng_m.group(1))
    parts = [p for p in re.split(r"[\s,;]+", s) if p]
    if len(parts) < 2:
        raise ValueError(
            "座標は「緯度,経度」の形式で指定してください（例: 35.6283,139.6187）"
        )
    lat = float(parts[0])
    lng = float(parts[1])
    if not (-90.0 <= lat <= 90.0):
        raise ValueError("緯度が範囲外です: %s" % lat)
    if not (-180.0 <= lng <= 180.0):
        raise ValueError("経度が範囲外です: %s" % lng)
    return lat, lng
