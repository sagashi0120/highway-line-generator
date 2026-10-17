# テスト用フィクスチャ

`osm_東京外環自動車道.json` は OpenStreetMap の Overpass API から取得した
東京外環自動車道のウェイデータです（テストをネットワークなしで実行するため）。

- 出典: © OpenStreetMap contributors
- ライセンス: Open Database License (ODbL) 1.0
- 取得方法の例:

```
[out:json][timeout:180];
(
  way["highway"~"^(motorway|trunk)$"]["name"~"東京外環自動車道"](35.55,139.45,35.90,139.98);
  way["highway"~"^(motorway|trunk)$"]["ref"="C3"](35.55,139.45,35.90,139.98);
);
out body geom;
```

テストは次のように実行します。

```powershell
py -m unittest discover -s tests -v
```
