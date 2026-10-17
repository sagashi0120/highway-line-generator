#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""高速道路の下り線・上り線ラインデータ生成ツール（エントリポイント）。

使い方:
    python make_line.py 東名高速道路 --start 35.6283,139.6187

詳しくは README.md を参照してください。
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from linegen.cli import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main())
