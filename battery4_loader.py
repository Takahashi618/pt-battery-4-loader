#!/usr/bin/env python3
"""
battery4_loader.py — 互換ランチャー
====================================

旧 tkinter 版のエントリポイント名を残した互換用ファイル。
実体は Cocoa 版（app_main.py）を起動する。

背景:
  tkinter は macOS の NSAccessibility を十分実装しておらず、VoiceOver で
  リスト内容や Tab 移動がほとんど読み上げられないことが実機で判明した。
  そのため 2026-06 に PyObjC + Cocoa へ全面移行した。

起動方法（どちらでも同じ）:
  python3 battery4_loader.py
  python3 app_main.py
"""

from app_main import main

if __name__ == "__main__":
    main()
