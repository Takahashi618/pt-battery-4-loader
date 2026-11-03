"""
Battery 4 Sample Loader — 3 つのウィンドウで共有する UI の部品
================================================================

メイン画面（app_main）・キット管理（app_kit）・設定（app_settings）の
どれからでも使う部品を置く。

**app_main から import してはいけない。** `python3 app_main.py` と .app は
app_main を `__main__` として動かすので、サブウィンドウが `import app_main`
をすると同じファイルがもう一度読み込まれ、PyObjC が
「SampleTableDataSource is overriding existing Objective-C class」で止まる。
2026-10-24 まで、設定とキット管理のボタンでリターンを押すとこれで
例外になり、何も起きなかった（.app でも同じ。テストは app_main を
ふつうのモジュールとして読むので気づけなかった）。
"""

from __future__ import annotations

from Cocoa import NSButton


def click_focused_button(window, event) -> bool:
    """フォーカスのあるボタンを Return / Enter で実行する。

    macOS の標準ではボタンはスペースで押し、リターンは既定ボタン専用。
    ただしスペースはこのアプリではプレビュー再生に使うため紛らわしく、
    利用者からリターンでも押せるようにしてほしいと要望があった。

    押したら True（イベントを消費）。ボタン以外にフォーカスがあるときは
    False を返して標準処理に任せる（テキスト欄やリストの Return は
    それぞれの既存動作のまま）。
    """
    chars = event.charactersIgnoringModifiers()
    if chars not in ("\r", "\x03"):   # Return / テンキーの Enter
        return False
    responder = window.firstResponder()
    if responder is None or not isinstance(responder, NSButton):
        return False
    try:
        if not responder.isEnabled():
            return False
        responder.performClick_(None)
        return True
    except Exception:
        return False
