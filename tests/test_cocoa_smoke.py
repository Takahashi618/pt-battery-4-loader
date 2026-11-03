"""
Cocoa 層のスモークテスト（ウィンドウ非表示・イベントループ非起動）。

pyobjc が無い環境（Windows 等）では全テストをスキップ扱いで成功にする。
アプリを起動せずに、モジュールの import と必要なセレクタ（メソッド）の
存在を確認する。PyObjC はセレクタ名のタイプミスが実行時まで発見できない
ため、この検査で配線ミスを早期に捕まえる。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.join(ROOT, "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# テストは日本語の文を期待する（英語は test_i18n が別のプロセスで確かめる）。
# 言語は import のときに決まるので、ここで決めないと英語の Mac で単独で動かすと落ちる
os.environ["BATTERY4_LANG"] = "ja"

try:
    import objc  # noqa: F401
    import Cocoa  # noqa: F401
    PYOBJC_OK = True
except Exception:
    PYOBJC_OK = False


def test_import_app_modules():
    import app_main
    import app_kit
    import app_settings
    assert hasattr(app_main, "AppDelegate")
    assert hasattr(app_main, "DragSourceView")
    assert hasattr(app_kit, "KitManager")
    assert hasattr(app_settings, "SettingsManager")
    print("PASS: Cocoa 3 モジュールの import と主要クラス")


def test_delegate_selectors():
    import app_main
    d = app_main.AppDelegate
    for sel in (
        "applicationDidFinishLaunching_",
        "applicationWillTerminate_",
        "controlTextDidChange_",
        "scanClicked_", "scanFinished_",
        "sortChanged_", "cellChanged_",
        "loadClicked_", "loadFinished_", "loadCancelled_",
        "prepareSelfDrag_", "finishSelfDrag_",
        "openKit_", "openSettings_",
        "kitNew_", "kitSave_", "kitExport_", "kitPreviewAll_",
        "menuToggleFavorite_", "menuCycleSort_", "menuCycleReadout_",
        "menuShowHelp_", "focusFilter_", "focusList_",
        "selectCellByIndex_", "announceMessage_", "announceHighMessage_",
        "batteryChanged_", "menuCycleBattery_", "menuCycleRow_",
        # 絞り込み（作者の別のアプリと同じ AND 条件）
        "kindChanged_", "packChanged_", "favOnlyChanged_",
        "menuCycleKind_", "menuFocusPack_", "menuToggleFavOnly_",
        "menuClearFilters_", "menuCyclePackOrder_",
        "menuOpenAllBatteries_", "openAllFinished_",
        "menuCycleTargetReadout_", "menuMoveCell_", "moveCellFinished_",
        "menuSelectTrack_", "trackSwitchFinished_",
        # 合成イベント操作の下ごしらえ（排他は _run_automation が持つ）
        "_run_automation", "_prepare_load", "_load_worker",
        "_prepare_track_switch", "_prepare_open_battery",
        "_prepare_move_cell", "cancel_automation",
        "_load_targets", "_skip_note", "_note_recent",
        # 2026-10-24: 前面へ戻ってから読む・起動確認・バージョン情報・
        # スキャンの失敗・ドラッグの始点の窓
        "_announce_in_front", "announceFrontMessage_", "_init_state",
        "_show_main_window", "_build_drag_panel", "showAbout_",
        "scanFailed_", "_front_main_window", "_move_own_windows_off",
    ):
        assert hasattr(d, sel), f"AppDelegate に {sel} が無い"
    print("PASS: AppDelegate の全セレクタが存在")


def test_filter_shortcuts_are_command_keys():
    """絞り込みの操作が Cmd 付きショートカットとして登録されていること。

    単独キーは VoiceOver と競合するため使わない（確定した方式）。
    既存のショートカットと重複していないことも確かめる。
    """
    from Cocoa import NSApp
    _headless()
    # 本物のメニューを読む。シフトの有無で Cmd 単独 / Cmd+Shift を分けて数える
    plain, modified, actions = [], [], {}
    menu = NSApp.mainMenu()
    for i in range(menu.numberOfItems()):
        sub = menu.itemAtIndex_(i).submenu()
        for j in range(sub.numberOfItems()):
            item = sub.itemAtIndex_(j)
            key = str(item.keyEquivalent() or "").lower()
            if not key:
                continue
            actions[str(item.action())] = key
            shift = int(item.keyEquivalentModifierMask()) & (1 << 17)
            (modified if shift else plain).append(key)
    assert len(plain) == len(set(plain)), \
        f"Cmd 単独のショートカットが重複している: {plain}"
    assert len(modified) == len(set(modified)), \
        f"Cmd+Shift のショートカットが重複している: {modified}"
    for action, expected in (
        ("menuCycleKind:", "j"),
        ("menuFocusPack:", "g"),
        ("menuCyclePackOrder:", "g"),   # Cmd+Shift+G（Cmd+G とは別）
        ("menuToggleFavOnly:", "u"),
        ("menuClearFilters:", "r"),
        ("menuSelectTrack:", "t"),      # Cmd+Shift+T
        ("menuOpenAllBatteries:", "o"),  # Cmd+Shift+O
        ("menuCycleTargetReadout:", "b"),  # Cmd+Shift+B
        ("menuMoveCell:", "m"),
    ):
        assert actions.get(action) == expected, \
            f"{action} のキーが {expected} でない（{actions.get(action)}）"
    print(f"PASS: 絞り込みのショートカットが重複なく登録"
          f"（Cmd単独 {len(plain)} 個 / 修飾付き {len(modified)} 個）")


def test_drag_source_selectors():
    import app_main
    v = app_main.DragSourceView
    for sel in (
        "mouseDown_", "mouseDragged_",
        "draggingSession_sourceOperationMaskForDraggingContext_",
        "draggingSession_endedAtPoint_operation_",
    ):
        assert hasattr(v, sel), f"DragSourceView に {sel} が無い"
    print("PASS: DragSourceView がドラッグ元プロトコルを実装")


def test_return_key_clicks_focused_button():
    """リターンでフォーカス中のボタンを実行できること。

    macOS の標準ではボタンはスペースで押すが、このアプリはスペースを
    プレビュー再生に使うため紛らわしい。全ウィンドウでリターンでも
    押せるようにしている（利用者からの要望）。
    """
    import app_main
    import app_kit
    import app_settings
    from Cocoa import NSButton, NSMakeRect, NSTextField

    assert hasattr(app_main, "click_focused_button")

    class FakeEvent:
        def __init__(self, chars):
            self._chars = chars

        def charactersIgnoringModifiers(self):
            return self._chars

    class FakeWindow:
        def __init__(self, responder):
            self._responder = responder

        def firstResponder(self):
            return self._responder

    # performClick_ はイベントループ外では実際のアクション送信まで至らないため、
    # 「押す処理が呼ばれたか」をサブクラスで直接記録する
    clicked = []

    class SpyButton(NSButton):
        def performClick_(self, sender):
            clicked.append(True)

    button = SpyButton.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
    win = FakeWindow(button)
    # リターンとテンキーの Enter で押される
    assert app_main.click_focused_button(win, FakeEvent("\r")) is True
    assert app_main.click_focused_button(win, FakeEvent("\x03")) is True
    assert len(clicked) == 2, clicked
    # 他のキーでは押さない
    assert app_main.click_focused_button(win, FakeEvent("a")) is False
    assert app_main.click_focused_button(win, FakeEvent(" ")) is False
    # ボタン以外にフォーカスがあるときは標準処理に任せる
    # （テキスト欄やリストのリターンは既存動作のまま）
    field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
    assert app_main.click_focused_button(FakeWindow(field), FakeEvent("\r")) is False
    assert app_main.click_focused_button(FakeWindow(None), FakeEvent("\r")) is False
    # 無効なボタンは押さない
    button.setEnabled_(False)
    assert app_main.click_focused_button(win, FakeEvent("\r")) is False
    assert len(clicked) == 2, "無効なボタンが押された"

    # 3つのウィンドウすべてが配線されていること
    for mod, cls in ((app_main, "MainWindow"), (app_main, "HelpWindow"),
                     (app_kit, "KitWindow"), (app_settings, "SettingsWindow")):
        src_file = mod.__file__
        src = open(src_file, encoding="utf-8").read()
        assert "click_focused_button" in src, f"{cls} の側で配線されていない"
        assert hasattr(getattr(mod, cls), "keyDown_"), f"{cls} に keyDown_ が無い"
    print("PASS: リターンでフォーカス中のボタンを実行できる")


def test_table_columns_are_not_editable():
    """テーブルの列が編集不可であること。

    編集可のままだと、フォーカスが来たときにセルの編集ビュー
    （NSTextView）が responder を奪い、スペースや上下キーがリストに
    届かなくなる（実機で「サンプルを選択できない」と報告された不具合）。
    """
    import re
    for fname, cols in (("app_main.py", ["col"]),
                        ("app_kit.py", ["kcol", "mcol"]),
                        ("app_settings.py", ["fcol"])):
        src = open(os.path.join(ROOT, fname), encoding="utf-8").read()
        for c in cols:
            assert re.search(rf"\b{c}\.setEditable_\(False\)", src), \
                f"{fname} の列 {c} が編集不可になっていない"
    print("PASS: テーブルの列は編集不可（キーがリストに届く）")


def test_key_view_loop_is_wired():
    """Tab の移動順が明示されていること。

    既定のままだとスクロールバーにフォーカスが行き、リストを操作できない。
    """
    src = open(os.path.join(ROOT, "app_main.py"), encoding="utf-8").read()
    assert "_wire_key_view_loop" in src
    assert "setNextKeyView_" in src
    # サンプルリストが移動順に含まれること
    import re
    m = re.search(r"order = \[(.*?)\]", src, re.S)
    assert m and "self.table" in m.group(1), "リストが Tab の順序に無い"
    # サブウィンドウでも設定していること
    for fname in ("app_kit.py", "app_settings.py"):
        s2 = open(os.path.join(ROOT, fname), encoding="utf-8").read()
        assert "setNextKeyView_" in s2, f"{fname} で Tab 順が未設定"
    print("PASS: Tab の移動順が明示されている")


def test_kit_manager_selectors():
    import app_kit
    k = app_kit.KitManager
    for sel in (
        "initWithController_", "show", "stop_kit_preview",
        "assignClicked_", "newKitClicked_", "saveKitClicked_",
        "saveAsClicked_", "loadKitClicked_", "duplicateClicked_",
        "deleteClicked_", "clearCellClicked_", "exportClicked_",
        "previewAllClicked_",
        # CellMapDataSource は controller（KitManager）の cell_names を呼ぶ。
        # 2026-08-31: これが無くキット管理が開けなかった再発防止
        "cell_names",
    ):
        assert hasattr(k, sel), f"KitManager に {sel} が無い"
    print("PASS: KitManager の全セレクタが存在")


def test_settings_manager_selectors():
    import app_settings
    s = app_settings.SettingsManager
    for sel in (
        "initWithController_", "show",
        "addFolderClicked_", "removeFolderClicked_",
        "countdownChanged_", "advanceChanged_",
        "labelCellChanged_", "applyLabelClicked_",
        "standardLabelsClicked_", "clearLabelsClicked_",
        "captureFirstClicked_", "captureLastClicked_", "captureFinished_",
        "computeCoordsClicked_", "checkPermissionClicked_",
        "gridChanged_", "detectBatteryClicked_", "resetLayoutClicked_",
        "oneshotSecChanged_",
    ):
        assert hasattr(s, sel), f"SettingsManager に {sel} が無い"
    print("PASS: SettingsManager の全セレクタが存在")


def test_load_targets_checks_saved_track():
    """保存済みのロード先が無ければ、切り替えを頼まず案内で止まること。

    確かめずに切り替えを頼むと、実在しない名前を探して Battery の画面が
    全トラックを巡る（実測で1分近く、その間ずっと別のトラックが出る）。
    キットや前のセッションの名前が残っているのはふつうのこと。
    """
    import app_main
    import core

    d = app_main.AppDelegate.alloc().init()
    d.settings = {"target_track": "", "target_readout": core.READOUT_FULL}
    orig_now = core.current_battery_track
    orig_names = core.battery_track_names
    core.current_battery_track = lambda: "Inst1"
    # スロット重複は core.battery_track_names が落とす（別テストで検証）
    core.battery_track_names = lambda: ["Inst1", "Inst2"]
    try:
        # 自動（空）は表示中の画面のまま
        assert d._load_targets()[0] == [""]

        # 表示中と同じなら切り替え不要
        d.settings["target_track"] = "Inst1"
        assert d._load_targets()[0] == [""]

        # 実在するトラックは切り替えて配る
        d.settings["target_track"] = "Inst2"
        assert d._load_targets()[0] == ["Inst2"]

        # 実在しないトラックは運ばず、次の一手を案内する
        d.settings["target_track"] = "Inst9"
        targets, err, _now = d._load_targets()
        assert targets == [], targets
        assert "見つかりません" in err and "コマンド+シフト+T" in err, err

        # すべて＝表示中のトラックを先頭にして無駄な切り替えを省く
        d.settings["target_track"] = core.TARGET_ALL
        core.current_battery_track = lambda: "Inst2"
        assert d._load_targets()[0] == ["Inst2", "Inst1"]
        core.current_battery_track = lambda: "Inst1"
        assert d._load_targets()[0] == ["Inst1", "Inst2"]

        # インサートが読めないときは、そう伝える
        core.battery_track_names = lambda: []
        targets, err, _now = d._load_targets()
        assert targets == [] and "ミックスウィンドウ" in err, err
    finally:
        core.current_battery_track = orig_now
        core.battery_track_names = orig_names
    print("PASS: 保存済みのロード先を確かめてから切り替える")


def test_escape_cancels_load_but_space_does_not():
    """エスケープはロードを中断し、スペースの試聴では中断しないこと。

    以前は停止処理が共通で、配っている最中にスペースで試聴すると
    残りのトラックが黙って省かれ、しかも「切り替えられなかった」と
    誤った原因を読み上げていた。
    """
    import app_main

    src = open(app_main.__file__, encoding="utf-8").read()
    stop = src[src.index("    def stop_preview(self):"):]
    stop = stop[:stop.index("\n    # ---")]
    assert "_automation_cancel" not in stop, \
        "プレビュー停止がロードまで中断している"
    assert "self.stop_preview()\n            self.cancel_automation()" in src, \
        "リスト内のエスケープでロードを中断していない"
    assert "delegate.cancel_automation()" in src, \
        "ウィンドウのエスケープでロードを中断していない"
    print("PASS: エスケープだけがロードを中断する")


def test_load_skip_reasons_have_own_advice():
    """見送った理由ごとに、次の一手が違うこと。

    中断や画面の重なりを「切り替えられなかった」と説明すると、
    利用者は関係のない設定を疑うことになる。
    """
    import app_main

    d = app_main.AppDelegate.alloc().init()
    note = d._skip_note([
        {"where": "Inst2", "reason": "switch"},
        {"where": "Inst3", "reason": "overlap"},
        {"where": "Inst4", "reason": "resolve"},
        {"where": "Inst5", "reason": "hidden", "blocker": "確認の画面"},
        {"where": "Inst6", "reason": "no_window", "blocker": ""},
    ])
    assert "Inst2" in note and "コマンド+シフト+T" in note, note
    assert "Inst3" in note and "1 枚だけを残して" in note, note
    assert "Inst4" in note and "開いたまま" in note, note
    # セルにほかの窓が重なっていたときは、その窓の名前を言う
    assert "Inst5" in note and "確認の画面" in note, note
    # セルの位置に窓が無いときは、何かが重なっているとは言わない
    assert "Inst6" in note and "見つからなかった" in note, note
    assert set(d.SKIP_REASONS) == {"switch", "overlap", "resolve", "hidden",
                                   "no_window"}
    assert d._skip_note([{"where": "Inst1", "ok": True}]) == ""
    print("PASS: 見送った理由ごとに案内を分ける")


# ---------------------------------------------------------------------------
# 2026-10-24 のレビューで直したものの再発防止
# ---------------------------------------------------------------------------
_HEADLESS = None


def _headless():
    """窓を出さずに組み立てたアプリ（AppDelegate, KitManager, SettingsManager）。"""
    global _HEADLESS
    if _HEADLESS is None:
        import app_main
        _HEADLESS = app_main.build_headless()
    return _HEADLESS


def _run_py(code, timeout=120):
    """別の python でコードを動かす（ホームは一時フォルダ、音と読み上げなし）。"""
    import subprocess
    import tempfile
    env = dict(os.environ, HOME=tempfile.mkdtemp(prefix="b4l_sub_"),
               BATTERY4_SILENT="1", PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=timeout)


def test_subwindows_do_not_import_app_main():
    """キット管理と設定が app_main を import しないこと。

    `python3 app_main.py` と .app は app_main を __main__ として動かす。
    サブウィンドウが app_main を import すると同じファイルがもう一度
    読み込まれ、PyObjC が「SampleTableDataSource is overriding existing
    Objective-C class」で止まる。2026-10-24 まで、設定とキット管理の
    ボタンでリターンを押すとこれで何も起きなかった。
    """
    import ast
    for fname in ("app_kit.py", "app_settings.py", "ui_common.py"):
        tree = ast.parse(open(os.path.join(ROOT, fname), encoding="utf-8").read())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            assert "app_main" not in names, f"{fname} が app_main を import している"

    # 本番と同じく app_main を __main__ として読み込んだうえで、設定と
    # キット管理の窓でリターンを押す（イベントはこのプロセスの中だけ）
    code = r'''
import time
src = open("app_main.py", encoding="utf-8").read()
src = src.replace('if __name__ == "__main__":\n    main()', "")
g = {"__name__": "__main__", "__file__": "app_main.py"}
exec(compile(src, "app_main.py", "exec"), g)
from Cocoa import NSApplication, NSButton, NSEvent, NSMakeRect
NSApplication.sharedApplication()
import app_kit, app_settings
clicked = []
class Spy(NSButton):
    def performClick_(self, sender):
        clicked.append(self.title())
ev = NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
    10, (0, 0), 0, time.time(), 0, None, "\r", "\r", False, 36)
for cls in (app_settings.SettingsWindow, app_kit.KitWindow):
    win = cls.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, 200, 100), 1, 2, False)
    btn = Spy.alloc().initWithFrame_(NSMakeRect(10, 10, 80, 30))
    btn.setTitle_(cls.__name__)
    win.contentView().addSubview_(btn)
    win.makeFirstResponder_(btn)
    win.keyDown_(ev)
print("CLICKED", ",".join(clicked))
'''
    done = _run_py(code)
    assert "CLICKED SettingsWindow,KitWindow" in done.stdout, \
        (done.stdout + done.stderr)[-800:]
    print("PASS: 設定とキット管理のボタンをリターンで押せる（__main__ でも）")


def test_edit_menu_makes_text_editing_keys_work():
    """編集メニューがあり、入力欄でコマンド+V などが効くこと。

    AppKit はこれらのキーをメニュー項目として受け取る。nib を使わずに
    組み立てるこのアプリでは、作らないとフィルター欄でコマンド+A も
    コマンド+V も効かなかった（2026-10-24 に確かめた）。
    """
    from Cocoa import NSApp
    _headless()
    menu = NSApp.mainMenu()
    edit = None
    seen = {}
    for i in range(menu.numberOfItems()):
        sub = menu.itemAtIndex_(i).submenu()
        if sub.title() == "編集":
            edit = sub
        for j in range(sub.numberOfItems()):
            item = sub.itemAtIndex_(j)
            key = item.keyEquivalent()
            if not key:
                continue
            combo = (key.lower(), int(item.keyEquivalentModifierMask()))
            assert combo not in seen, \
                f"同じキーが 2 つの項目にある: {item.title()} と {seen[combo]}"
            seen[combo] = item.title()
    assert edit is not None, "編集メニューが無い"
    actions = {edit.itemAtIndex_(j).action(): edit.itemAtIndex_(j)
               for j in range(edit.numberOfItems())
               if not edit.itemAtIndex_(j).isSeparatorItem()}
    for sel, key in (("undo:", "z"), ("redo:", "z"), ("cut:", "x"),
                     ("copy:", "c"), ("paste:", "v"), ("selectAll:", "a")):
        assert sel in actions, f"編集メニューに {sel} が無い"
        assert actions[sel].keyEquivalent() == key, sel
        # 送り先は決めない（フォーカスのある入力欄が受け取る）
        assert actions[sel].target() is None, sel
    print(f"PASS: 編集メニューがあり、キーの重なりも無い（{len(seen)} 個）")


def _content_frames(window):
    """窓の中身の直下の部品を (名前, (x, y, w, h)) で返す。"""
    out = []
    for v in window.contentView().subviews():
        f = v.frame()
        name = ""
        for attr in ("title", "stringValue", "accessibilityLabel"):
            try:
                name = str(getattr(v, attr)() or "")
            except Exception:
                continue
            if name:
                break
        out.append((name or type(v).__name__,
                    (f.origin.x, f.origin.y, f.size.width, f.size.height)))
    return out


def test_windows_layout():
    """どの窓でも、部品が窓の中に収まり、互いに重ならないこと。

    キット管理ではボタン 2 つが窓の外（y が負）に置かれて見えず、
    設定ではボタンが説明文の上に、見出しが別の行の上に重なっていた
    （2026-10-24 のレビュー）。目で見ずに見つけられるよう数で確かめる。
    """
    d, km, sm = _headless()
    for label, win in (("メイン画面", d.window), ("キット管理", km.window),
                       ("設定", sm.window)):
        bounds = win.contentView().bounds()
        cw, ch = bounds.size.width, bounds.size.height
        frames = _content_frames(win)
        for name, (x, y, w, h) in frames:
            assert x >= 0 and y >= 0 and x + w <= cw and y + h <= ch, \
                f"{label}: {name} が窓からはみ出している（{x}, {y}, {w}, {h}）"
        for i, (n1, a) in enumerate(frames):
            for n2, b in frames[i + 1:]:
                overlap = (min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]) > 0
                           and min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]) > 0)
                assert not overlap, f"{label}: {n1} と {n2} が重なっている"
    print("PASS: 3 つの窓とも、部品が窓に収まり重ならない")


def test_texts_fit_in_their_frames():
    """日本語の文字が部品の枠に収まること（英語は test_i18n が確かめる）。"""
    from layout_check import clipped_texts
    d, km, sm = _headless()
    sm._refresh_coord_info()
    for label, win in (("メイン画面", d.window), ("キット管理", km.window),
                       ("設定", sm.window)):
        problems = clipped_texts(win)
        assert not problems, f"{label}: {problems}"
    print("PASS: 文字が部品の枠に収まる")


def test_battery_popup_stays_in_step_with_tracks():
    """同じ名前のトラックがあっても、選択肢とロード先の対応がずれないこと。

    addItemWithTitle_ は同じ題の項目を消して末尾へ移す。「自動」という名前の
    トラックを選ぶと、すべての Battery へ配る設定になっていた
    （2026-11-03 のレビュー。別のトラックのセルを上書きする）。
    """
    import core
    d, _km, _sm = _headless()
    saved = dict(d.settings)
    original = core.battery_windows
    d._save_settings = lambda: None     # 利用者の設定に書かない
    d._set_status = lambda *a, **k: None
    try:
        for mode, name in ((core.READOUT_FULL, "自動"), (core.READOUT_SHORT, "すべて")):
            d.settings["target_readout"] = mode
            d.settings["target_track"] = ""
            core.battery_windows = lambda: [{"track": name, "plugin": "Battery 4",
                                             "view": (0, 0, 10, 10)}]
            d._populate_battery_popup()
            assert d.battery_popup.numberOfItems() == len(d._battery_tracks) + 1, \
                list(d.battery_popup.itemTitles())
            assert d.battery_popup.indexOfSelectedItem() == 0
            # 最後の項目がそのトラック
            d.battery_popup.selectItemAtIndex_(d.battery_popup.numberOfItems() - 1)
            d.batteryChanged_(d.battery_popup)
            assert d.settings["target_track"] == name, d.settings["target_track"]
    finally:
        core.battery_windows = original
        del d._save_settings, d._set_status
        d.settings.clear()
        d.settings.update(saved)
        d._populate_battery_popup()
    print("PASS: 同じ名前のトラックでも選択肢とロード先がずれない")


def test_move_cancel_is_not_reported_as_failure():
    """セル移動の中断は失敗の案内を出さないこと。"""
    d, _km, _sm = _headless()
    said = []
    d._set_status = lambda text, *a, **k: said.append(text)
    try:
        d.moveCellFinished_({"ok": False, "cancelled": True, "msg": "移動を中断しました"})
        assert said == ["移動を中断しました"], said
        said.clear()
        d.moveCellFinished_({"ok": False, "cancelled": False, "msg": "失敗"})
        assert "確認してください" in said[0], said
    finally:
        del d._set_status
    print("PASS: セル移動の中断を失敗と言わない")


def test_clipboard_is_left_alone_when_silent():
    """テストとビルド（BATTERY4_SILENT）ではクリップボードに触らないこと。

    ホームを一時フォルダにしても、クリップボードは本物のまま。英語の検査が
    ロードの失敗の報告を通して、ビルドのたびに書き換えていた（2026-11-03）。
    """
    import shutil
    import stat
    import tempfile
    folder = tempfile.mkdtemp(prefix="b4l_pb_")
    marker = os.path.join(folder, "called")
    fake = os.path.join(folder, "pbcopy")
    with open(fake, "w", encoding="utf-8") as f:
        f.write(f'#!/bin/sh\ncat > "{marker}"\n')
    os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
    code = (
        "import os, core\n"
        "core.copy_to_clipboard('silent')\n"
        "assert not os.path.exists(os.environ['B4L_MARKER'])\n"
        "del os.environ['BATTERY4_SILENT']\n"
        "core.copy_to_clipboard('not silent')\n"
        "assert open(os.environ['B4L_MARKER']).read() == 'not silent'\n"
    )
    import subprocess
    env = dict(os.environ, BATTERY4_SILENT="1", B4L_MARKER=marker,
               PATH=folder + os.pathsep + os.environ.get("PATH", ""),
               HOME=folder, PYTHONDONTWRITEBYTECODE="1")
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=60)
    shutil.rmtree(folder, ignore_errors=True)
    assert done.returncode == 0, done.stderr[-600:]
    print("PASS: テストとビルドではクリップボードに触らない")


def test_tab_order_follows_layout():
    """Tab の順が、見た目の順（上から、行の中は左から）と同じこと。

    設定ではポップアップを全部回ってからボタンへ行く順で、ラベルを入力して
    Tab を押すと「このセルに設定」ではなくグリッドの行数へ飛んでいた。
    """
    d, km, sm = _headless()
    for label, order in (("メイン画面", d._key_view_order),
                         ("キット管理", km._key_view_order),
                         ("設定", sm._key_view_order)):
        def place(view):
            outer = view.enclosingScrollView() or view
            f = outer.frame()
            return (-round(f.origin.y + f.size.height), round(f.origin.x))
        expected = sorted(order, key=place)
        assert [id(v) for v in order] == [id(v) for v in expected], \
            f"{label}: Tab の順が見た目の順と違う"
        for cur, nxt in zip(order, order[1:] + order[:1]):
            assert cur.nextKeyView() is nxt, f"{label}: Tab の鎖が切れている"
    print("PASS: Tab の順は見た目の順")


def test_button_labels_start_with_visible_text():
    """ボタンの読み上げ名は、画面の文字で始まること。

    VoiceOver はボタンの文字ではなく、付けた読み上げ名を読む。設定の窓では
    「選択を削除」が「選択中のフォルダを検索対象から削除します」と読まれ、
    マニュアルや手伝う人の言う名前と聞こえる名前が食い違っていた
    （2026-10-25）。「画面の文字。説明」の形にそろえる。
    """
    d, km, sm = _headless()
    checked = 0
    for label, order in (("メイン画面", d._key_view_order),
                         ("キット管理", km._key_view_order),
                         ("設定", sm._key_view_order)):
        for view in order:
            try:
                title = str(view.title() or "")
            except Exception:
                continue          # ボタンでない部品
            if not title or view.cell().accessibilityRole() not in (
                    "AXButton", "AXCheckBox"):
                continue
            spoken = str(view.accessibilityLabel() or title)
            assert spoken.startswith(title), \
                f"{label}: 「{title}」の読み上げ名が「{spoken}」"
            checked += 1
    assert checked >= 20, checked
    print(f"PASS: ボタンの読み上げ名は画面の文字で始まる（{checked} 個）")


def test_help_mentions_every_menu_key():
    """操作一覧（ヘルプ）に、メニューのキーがすべて書いてあること。

    マニュアルのキーの表はメニューから作るが、ヘルプは手で書いている。
    キーを足したのにヘルプに無い、を防ぐ。
    """
    import app_main
    from Cocoa import NSApp
    _headless()
    text = app_main.help_text()
    names = {",": "カンマ", ";": "セミコロン"}
    menu = NSApp.mainMenu()
    missing = []
    for i in range(menu.numberOfItems()):
        sub = menu.itemAtIndex_(i).submenu()
        for j in range(sub.numberOfItems()):
            item = sub.itemAtIndex_(j)
            key = str(item.keyEquivalent() or "")
            if not key:
                continue
            shift = bool(int(item.keyEquivalentModifierMask()) & (1 << 17))
            if key.isdigit():
                spoken = "コマンド+1から9"
            else:
                spoken = ("コマンド+シフト+" if shift else "コマンド+") + \
                    names.get(key, key.upper())
            if spoken not in text:
                missing.append(f"{item.title()}（{spoken}）")
    assert not missing, f"ヘルプに無いキー: {missing}"
    print("PASS: ヘルプにメニューのキーがすべて書いてある")


def test_manual_matches_code():
    """マニュアル（docs/manual.html と .txt）が、いまのコードと合っていること。

    キー・メニュー・タブの順・設定の初めの値は、本物の画面から作る
    （tools/doc_parts.py）。作り直し忘れをここで落とす。
    """
    import subprocess
    done = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "doc_parts.py"), "--check"],
        cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, (done.stdout + done.stderr)[-500:]
    print("PASS: マニュアルはいまのコードと合っている")


def test_drag_source_is_a_topmost_panel():
    """ドラッグの始点は、最前面の層の小さな窓に載っていること。

    以前はメイン画面の右下にあり、Pro Tools の浮いたプラグイン画面（層 3）の
    下に隠れると、マウスを押した合成イベントが Battery の画面に落ちた。
    """
    import app_main
    d, _km, _sm = _headless()
    panel = d.drag_panel
    assert panel.level() >= 100, panel.level()
    assert not panel.canBecomeKeyWindow() and not panel.canBecomeMainWindow()
    assert panel.styleMask() & app_main.NSWindowStyleMaskNonactivatingPanel
    assert d.drag_view.window() is panel
    assert d.drag_view not in d._key_view_order
    assert hasattr(app_main.DragSourceView, "draggingSession_willBeginAtPoint_")
    d.drag_view.session_began = True
    d.drag_view.prepare_for_path("/x/kick.wav")
    assert d.drag_view.session_began is False
    print("PASS: ドラッグの始点は最前面の層の小さな窓")


def test_results_of_front_stealing_actions_are_read_in_front():
    """Pro Tools や Finder を前に出す操作の結果は、前面へ戻ってから読むこと。

    VoiceOver は前面にいないアプリの読み上げを読まない。トラック切替の
    結果などが黙って消えていた（2026-10-24 のレビュー）。
    """
    import app_main
    src = open(app_main.__file__, encoding="utf-8").read()
    for name in ("trackSwitchFinished_", "openAllFinished_",
                 "moveCellFinished_", "loadCancelled_", "loadFinished_"):
        start = src.index(f"    def {name}(")
        end = src.index("\n    def ", start + 10)
        body = src[start:end]
        assert "front=True" in body, f"{name} が前面へ戻らずに読んでいる"
        assert "speak=True" not in body, f"{name} に前面へ戻らない読み上げがある"
    print("PASS: ほかのアプリを前に出す操作の結果は前面へ戻ってから読む")


def test_selftest_runs_from_source():
    """`python3 app_main.py --selftest` が通ること（ビルドの起動確認と同じ道筋）。"""
    import json
    import subprocess
    import tempfile
    env = dict(os.environ, HOME=tempfile.mkdtemp(prefix="b4l_sub_"),
               BATTERY4_SILENT="1")
    done = subprocess.run([sys.executable, "app_main.py", "--selftest"],
                          cwd=ROOT, env=env, capture_output=True, text=True,
                          timeout=120)
    report = json.loads(done.stdout.strip().splitlines()[-1])
    assert done.returncode == 0 and report["ok"], report
    assert report["app_main_reimported"] is False, report
    print(f"PASS: --selftest が通る（版 {report['version']}）")


def test_kit_asks_before_delete_and_overwrite():
    """キットの削除と、別のキットへの上書きは確かめてからにすること。"""
    import core
    d, km, _sm = _headless()
    saved = []
    orig_save = core.save_kits
    core.save_kits = lambda kits: saved.append(dict(kits))
    original_kits = d.kits
    try:
        d.kits = {"A": {"cells": {"A1": "/x/a.wav"}, "track": ""},
                  "B": {"cells": {"A1": "/x/b.wav"}, "track": ""}}
        km._refresh_kit_list()
        from Foundation import NSIndexSet
        km.kit_table.selectRowIndexes_byExtendingSelection_(
            NSIndexSet.indexSetWithIndex_(0), False)     # A

        km._confirm = lambda *a: False                     # キャンセル
        km.deleteClicked_(None)
        assert "A" in d.kits and not saved, "確かめずに削除した"
        km.current_kit_name = "A"
        km.current_kit_cells = {"A1": "/x/new.wav"}
        km.name_field.setStringValue_("B")
        km.saveKitClicked_(None)
        assert d.kits["B"]["cells"] == {"A1": "/x/b.wav"}, "確かめずに上書きした"
        # 編集中のキットへの保存は確かめない
        km.name_field.setStringValue_("A")
        km.saveKitClicked_(None)
        assert d.kits["A"]["cells"] == {"A1": "/x/new.wav"}

        km._confirm = lambda *a: True                      # 「削除」
        km._refresh_kit_list()
        km.kit_table.selectRowIndexes_byExtendingSelection_(
            NSIndexSet.indexSetWithIndex_(1), False)     # B
        km.deleteClicked_(None)
        assert "B" not in d.kits
    finally:
        core.save_kits = orig_save
        d.kits = original_kits
        km.__dict__.pop("_confirm", None)
    print("PASS: キットの削除と上書きは確かめてから")


def test_launcher_shim():
    """battery4_loader.py（互換ランチャー）が Cocoa 版を指している。"""
    src = open(os.path.join(ROOT, "battery4_loader.py"), encoding="utf-8").read()
    assert "from app_main import main" in src
    assert "import tkinter" not in src and "from tkinter" not in src, \
        "ランチャーが tkinter を import している"
    print("PASS: battery4_loader.py は Cocoa 版へのランチャー")


def run_all():
    if not PYOBJC_OK:
        print("SKIP: pyobjc が無い環境のため Cocoa スモークテストをスキップ")
        return 0, 0
    tests = [
        test_import_app_modules,
        test_delegate_selectors,
        test_filter_shortcuts_are_command_keys,
        test_return_key_clicks_focused_button,
        test_table_columns_are_not_editable,
        test_key_view_loop_is_wired,
        test_drag_source_selectors,
        test_kit_manager_selectors,
        test_settings_manager_selectors,
        test_load_targets_checks_saved_track,
        test_escape_cancels_load_but_space_does_not,
        test_load_skip_reasons_have_own_advice,
        test_launcher_shim,
        test_subwindows_do_not_import_app_main,
        test_edit_menu_makes_text_editing_keys_work,
        test_windows_layout,
        test_texts_fit_in_their_frames,
        test_battery_popup_stays_in_step_with_tracks,
        test_move_cancel_is_not_reported_as_failure,
        test_clipboard_is_left_alone_when_silent,
        test_tab_order_follows_layout,
        test_button_labels_start_with_visible_text,
        test_help_mentions_every_menu_key,
        test_manual_matches_code,
        test_drag_source_is_a_topmost_panel,
        test_results_of_front_stealing_actions_are_read_in_front,
        test_selftest_runs_from_source,
        test_kit_asks_before_delete_and_overwrite,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"FAIL: {t.__name__}: {e}")
        except Exception as e:
            print(f"ERROR: {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(tests)} テストが PASS")
    return passed, len(tests)


if __name__ == "__main__":
    run_all()
