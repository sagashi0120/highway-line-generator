# -*- coding: utf-8 -*-
"""生成したラインを確認するための簡易 Leaflet プレビュー HTML。"""
from __future__ import annotations

import html
import json


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def build_preview_html(line, info, *, title: str = None, points=None) -> str:
    source_points = list(points) if points is not None else list(line.points)
    points = [[lat, lng] for lat, lng in source_points]
    start = source_points[0]
    end = source_points[-1]
    requested = line.requested_point
    from .geo import polyline_length_m

    label = line.label
    length_km = polyline_length_m(source_points) / 1000.0
    title = title or ("%s %s線" % (info.name, label))
    warnings = list(dict.fromkeys(line.warnings))
    requested_label = "指定した始点" if line.requested_point_is == "start" else "指定した始点（上り線の終点）"
    warning_html = ""
    if warnings:
        items = "".join("<li>%s</li>" % _esc(w) for w in warnings)
        warning_html = '<div class="warn"><strong>注意</strong><ul>%s</ul></div>' % items

    payload = json.dumps(points, ensure_ascii=False)
    start_js = json.dumps([start[0], start[1]])
    end_js = json.dumps([end[0], end[1]])
    req_js = json.dumps([requested[0], requested[1]])

    return """<!-- /* html */ -->
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%s</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
  * { box-sizing: border-box; }
  body { margin: 0; font-family: "Yu Gothic", "Hiragino Sans", sans-serif; background: #f4f7f4; color: #1c2b22; }
  #map { position: fixed; inset: 0 380px 0 0; }
  #panel { position: fixed; top: 0; right: 0; width: 380px; height: 100%%; overflow: auto; padding: 20px; background: #fff; box-shadow: -4px 0 18px rgba(0,0,0,.08); }
  h1 { margin: 0 0 4px; font-size: 20px; }
  .road { font-size: 13px; color: #5b7263; margin-bottom: 14px; }
  .stats { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 14px; }
  .stat { border: 1px solid #e1ebe4; border-radius: 15px; padding: 10px 12px; }
  .stat span { display: block; font-size: 11px; color: #6b8173; }
  .stat b { font-size: 16px; }
  .coord { font-family: Consolas, monospace; font-size: 12px; }
  .warn { border: 1px solid #f0d9a8; background: #fffaf0; border-radius: 15px; padding: 10px 14px; margin-top: 14px; font-size: 12px; }
  .warn ul { margin: 6px 0 0; padding-left: 18px; }
  .legend { margin-top: 14px; font-size: 12px; color: #4d6354; }
  .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%%; margin-right: 6px; }
  @media (max-width: 760px) {
    #map { inset: 0 0 46%% 0; }
    #panel { top: 54%%; width: 100%%; height: 46%%; box-shadow: 0 -4px 18px rgba(0,0,0,.08); }
  }
</style>
</head>
<body>
<div id="map"></div>
<div id="panel">
  <h1>%s</h1>
  <div class="road">OpenStreetMap から生成 / %s線</div>
  <div class="stats">
    <div class="stat"><span>延長</span><b>%.1f km</b></div>
    <div class="stat"><span>点数</span><b>%d</b></div>
    <div class="stat"><span>始点</span><b class="coord">%.6f<br>%.6f</b></div>
    <div class="stat"><span>終点</span><b class="coord">%.6f<br>%.6f</b></div>
  </div>
  %s
  <div class="legend">
    <div><span class="dot" style="background:#1f8a4c"></span>生成した%s線</div>
    <div><span class="dot" style="background:#2b6cb0"></span>%s</div>
    <div><span class="dot" style="background:#c53030"></span>ラインの始点・終点</div>
  </div>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const line = %s;
const map = L.map("map");
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors"
}).addTo(map);
const poly = L.polyline(line, { color: "#1f8a4c", weight: 5, opacity: 0.85 }).addTo(map);
L.circleMarker(%s, { radius: 8, color: "#c53030", fillColor: "#c53030", fillOpacity: 0.9 }).addTo(map).bindPopup("ライン始点");
L.circleMarker(%s, { radius: 8, color: "#c53030", fillColor: "#fff", fillOpacity: 0.9 }).addTo(map).bindPopup("ライン終点");
L.circleMarker(%s, { radius: 9, color: "#2b6cb0", fillColor: "#2b6cb0", fillOpacity: 0.65 }).addTo(map).bindPopup(%s);
map.fitBounds(poly.getBounds(), { padding: [24, 24] });
</script>
</body>
</html>
""" % (
        _esc(title),
        _esc(title),
        _esc(label),
        length_km,
        len(points),
        start[0],
        start[1],
        end[0],
        end[1],
        warning_html,
        _esc(label),
        _esc(requested_label),
        payload,
        start_js,
        end_js,
        req_js,
        json.dumps(requested_label, ensure_ascii=False),
    )
