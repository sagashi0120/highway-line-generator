# -*- coding: utf-8 -*-
"""コマンドラインインターフェース。"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import List, Optional, Tuple

from . import __version__, core, geo, output, preview, roads

ATTRIBUTION = "データ: © OpenStreetMap contributors (ODbL) / https://www.openstreetmap.org/copyright"


def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _parse_bbox(text: str):
    parts = [p for p in re.split(r"[, ]+", text.strip()) if p]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("--bbox は 南,西,北,東 の4つの数値で指定してください")
    return tuple(float(p) for p in parts)


def _parse_start(args):
    if args.lat is not None or args.lng is not None:
        if args.lat is None or args.lng is None:
            raise SystemExit("--lat と --lng は両方指定してください")
        return (float(args.lat), float(args.lng))
    if not args.start:
        raise SystemExit(
            "始点を指定してください。例: --start 35.6283,139.6187"
        )
    if len(args.start) == 1:
        return roads.parse_coord(args.start[0])
    if len(args.start) == 2:
        return (float(args.start[0]), float(args.start[1]))
    raise SystemExit("--start は「緯度,経度」または「緯度 経度」で指定してください")


def _safe_filename(name: str) -> str:
    name = name.strip().replace("/", "_").replace("\\", "_")
    name = re.sub(r'[<>:"|?*]', "_", name)
    name = re.sub(r"\s+", "", name)
    return name or "line"


def _split_formats(text: str) -> List[str]:
    return [part.strip().lower() for part in text.split(",") if part.strip()]


def _strip_direction_suffix(stem: str) -> str:
    for suffix in ("-下り", "-上り"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def _base_output_path(args, info) -> Tuple[str, str]:
    """出力のベースパス（方向サフィックスと拡張子を除いたもの）と拡張子。"""
    if args.out:
        root, ext = os.path.splitext(args.out)
        return _strip_direction_suffix(root), (ext.lower() or ".json")
    code_part = (info.codes[0] + "-") if info.codes else ""
    return os.path.join(args.out_dir, code_part + _safe_filename(info.name)), ".json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="make_line.py",
        description="OpenStreetMap から高速道路の下り線・上り線ラインデータを自動生成します。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例:\n"
            "  python make_line.py 東名高速道路 --start 35.6283,139.6187\n"
            "  python make_line.py 名神高速道路 --start 35.3031,136.9072 --code E1\n"
            "  python make_line.py 京葉道路 --start 35.7024,139.9006 --full\n"
            "  python make_line.py 京葉道路 --start 35.7024,139.9006 --direction down\n"
            "\n"
            "指定地点は「下り線の始点のおおよその位置」です。\n"
            "上り線も同時に生成し、入力地点は上り線の終点になります。\n"
            "多少ずれていても近い地点に補正して作成します（エラーにはしません）。\n"
            "\n"
            + ATTRIBUTION
        ),
    )
    parser.add_argument("road_pos", nargs="?", metavar="道路名", help="道路名（例: 東名高速道路）")
    parser.add_argument("--road", dest="road_opt", help="道路名（--road でも指定可）")
    parser.add_argument("--code", help="道路コード（例: E1, C3）。省略時は road_list.csv から推定")
    parser.add_argument("--start", nargs="+", metavar="緯度,経度", help="下り線の始点（おおよそ）。上り線では終点になる")
    parser.add_argument("--lat", type=float, help="始点の緯度")
    parser.add_argument("--lng", type=float, help="始点の経度")
    parser.add_argument("--full", action="store_true", help="指定地点ではなく道路の始点から全体を出力する")
    parser.add_argument("--reverse", action="store_true", help="指定地点を終点側とみなして逆方向に作る（向き補正用）")
    parser.add_argument("--direction", choices=("both", "down", "up"), default="both", help="出力する線 both=下り+上り（既定） / down / up")
    parser.add_argument("--out", help="出力のベースパス（-下り / -上り を付けて出力）")
    parser.add_argument("--out-dir", default="out", help="出力ディレクトリ（--out 省略時。既定: out）")
    parser.add_argument("--format", help="出力形式 lineData,geojson,csv のカンマ区切り（既定: lineData）")
    parser.add_argument("--simplify", type=float, default=10.0, metavar="メートル", help="間引き許容誤差（既定: 10m、0で無効）")
    parser.add_argument("--gap", type=float, default=500.0, metavar="メートル", help="道路データの途切れを補う最大距離（既定: 500m）")
    parser.add_argument("--max-expand", type=int, default=12, metavar="回数", help="OSM取得範囲の自動拡張の最大回数（既定: 12）")
    parser.add_argument("--connector-max", type=float, default=500.0, metavar="メートル", help="橋などの接続ウェイとして使う最大長（既定: 500m）")
    parser.add_argument("--include-links", action="store_true", help="立体交差のリンク道路も取得対象にする")
    parser.add_argument("--bbox", help="取得範囲 南,西,北,東（例: 34.6,136.8,35.8,139.8）")
    parser.add_argument("--cache-dir", help="OSM 応答のキャッシュ先（既定: このスクリプトの .linegen-cache）")
    parser.add_argument("--osm-json", help="保存済みの Overpass レスポンス（.json / .json.gz）を使う（オフライン用）")
    parser.add_argument("--refresh", action="store_true", help="キャッシュを使わず再取得する")
    parser.add_argument("--mirror", action="append", help="Overpass API のミラー URL（複数指定可）")
    parser.add_argument("--preview", nargs="?", const="auto", metavar="パス", help="Leaflet プレビュー HTML を出力")
    parser.add_argument("--no-meta", action="store_true", help="メタ情報 JSON を出力しない")
    parser.add_argument("--quiet", action="store_true", help="進捗表示を抑制")
    parser.add_argument("--version", action="version", version="highway-linegen " + __version__)
    return parser


def main(argv=None) -> int:
    _ensure_utf8_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)

    road_input = args.road_opt or args.road_pos
    if not road_input:
        parser.error("道路名を指定してください（例: python make_line.py 東名高速道路 --start 35.63,139.62）")
    try:
        start = _parse_start(args)
    except ValueError as exc:
        print("エラー: %s" % exc, file=sys.stderr)
        return 1

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache_dir = args.cache_dir or os.path.join(base_dir, ".linegen-cache")

    bbox = None
    if args.bbox:
        try:
            bbox = _parse_bbox(args.bbox)
        except (ValueError, argparse.ArgumentTypeError) as exc:
            parser.error(str(exc))

    opts = core.GenerateOptions(
        trim=not args.full,
        reverse=args.reverse,
        directions=args.direction,
        include_links=args.include_links,
        connector_max_m=args.connector_max,
        simplify_m=args.simplify,
        gap_m=args.gap,
        max_expand=args.max_expand,
        bbox=bbox,
        source_json=args.osm_json,
        cache_dir=cache_dir,
        refresh=args.refresh,
        mirrors=args.mirror or None,
        quiet=args.quiet,
        base_dir=base_dir,
    )

    try:
        result = core.generate(road_input, start, code=args.code, opts=opts)
    except (core.GenerateError, ValueError) as exc:
        print("エラー: %s" % exc, file=sys.stderr)
        return 1
    except Exception as exc:  # 想定外でも親切に
        print("エラー: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1

    final_points = {}
    for line in result.lines:
        pts = output.finalize_points(line.points, args.simplify)
        if len(pts) < 2:
            print("エラー: %s線の有効なラインを作成できませんでした" % line.label, file=sys.stderr)
            return 1
        final_points[line.direction] = pts

    base_path, out_ext = _base_output_path(args, result.info)
    if args.format:
        formats = _split_formats(args.format) or ["linedata"]
    elif out_ext == ".geojson":
        formats = ["geojson"]
    elif out_ext == ".csv":
        formats = ["csv"]
    else:
        formats = ["linedata"]

    written = []
    for line in result.lines:
        points = final_points[line.direction]
        base = "%s-%s" % (base_path, line.label)
        for fmt in formats:
            if fmt in ("linedata", "json"):
                path = base + ".json"
                output.write_line_data(path, points)
            elif fmt in ("geojson", "geojson-lines", "json-geojson"):
                path = base + ".geojson"
                output.write_json(
                    path,
                    output.to_geojson(
                        points,
                        {
                            "road": result.info.name,
                            "road_code": ",".join(result.info.codes),
                            "direction": line.direction,
                        },
                    ),
                )
            elif fmt == "csv":
                path = base + ".csv"
                output.write_csv(path, points)
            else:
                print("警告: 不明な形式 %s を無視しました" % fmt, file=sys.stderr)
                continue
            written.append(path)

        if not args.no_meta:
            meta_path = base + ".meta.json"
            meta = output.build_meta(
                result.info,
                line,
                points,
                fetch_bbox=result.fetch_bbox,
                used_way_ids=line.build.way_ids,
                simplify_m=args.simplify,
                generator="highway-linegen",
                version=__version__,
            )
            output.write_json(meta_path, meta)
            written.append(meta_path)

        if args.preview:
            if args.preview == "auto":
                preview_path = base + ".html"
            else:
                root, ext = os.path.splitext(args.preview)
                preview_path = root + "-" + line.label + (ext or ".html")
            html = preview.build_preview_html(line, result.info, points=points)
            os.makedirs(os.path.dirname(os.path.abspath(preview_path)), exist_ok=True)
            with open(preview_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(html)
            written.append(preview_path)

    print()
    print("=== 完了 ===")
    print("道路      : %s%s" % (result.info.name, (" [" + ",".join(result.info.codes) + "]") if result.info.codes else ""))
    for line in result.lines:
        points = final_points[line.direction]
        b = line.build
        length_km = geo.polyline_length_m(points) / 1000.0
        spacing = geo.spacing_stats_m(points)
        print()
        print("[%s線]" % line.label)
        print("  始点    : %.6f,%.6f" % (b.start[0], b.start[1]))
        print("  終点    : %.6f,%.6f" % (b.end[0], b.end[1]))
        print("  延長    : %.2f km" % length_km)
        print("  点数    : %d（平均間隔 %.1f m）" % (len(points), spacing["mean"]))
        if b.trimmed:
            if line.direction == "down":
                print("  指定地点からの補正: %.0f m" % b.snap_distance_m)
            else:
                print("  指定地点は上り線の終点です（下り線の始点）")
        if b.reversed:
            print("  向き    : --reverse（指定地点から逆方向）")
    print()
    print("OSMウェイ: %d 本" % len(result.used_way_ids))
    print("出力      : %s" % ", ".join(written))

    for line in result.lines:
        all_warnings = list(dict.fromkeys(line.warnings))
        if all_warnings:
            print()
            print("--- 注意（%s線） ---" % line.label)
            for warning in all_warnings:
                print(" * " + warning)
    print()
    print(ATTRIBUTION)
    return 0
