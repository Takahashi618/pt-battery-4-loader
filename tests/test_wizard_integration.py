"""
セル位置合わせの統合テスト（GUI 非起動）。

app_settings の位置合わせが使う流れ
（2点をマウスで取得 → 比率化 → layout_from_two_cells → validate → 保存）と、
core 側の座標解決・設定移行を検証する。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# テストは日本語の文を期待する（英語は test_i18n が別のプロセスで確かめる）。
# 言語は import のときに決まるので、ここで決めないと英語の Mac で単独で動かすと落ちる
os.environ["BATTERY4_LANG"] = "ja"

import core
import grid_calibration

REAL_VIEW = (20.0, 128.0, 1176.0, 650.0)


def test_capture_to_layout_pipeline():
    """マウスで取った2点が、そのままレイアウトとして保存できる形になる。"""
    # 実機で A1 と D12 のセル中心にポインタを置いた場合の画面座標
    first_pt = (386, 213)
    last_pt = (386 + 11 * 70, 213 + 3 * 56)
    first = grid_calibration.point_to_ratio(first_pt, REAL_VIEW)
    last = grid_calibration.point_to_ratio(last_pt, REAL_VIEW)
    layout = grid_calibration.layout_from_two_cells(
        "A1", first, "D12", last, 4, 12
    )
    ok, msg = grid_calibration.validate_layout(layout)
    assert ok, msg
    # 取得した2点にきちんと戻る
    assert grid_calibration.cell_point(layout, "A1", REAL_VIEW) == first_pt
    assert grid_calibration.cell_point(layout, "D12", REAL_VIEW) == last_pt
    print("PASS: 2点取得 → 比率 → レイアウト保存の流れ")


def test_layout_survives_json_roundtrip():
    """レイアウトは JSON を通しても壊れない（保存形式が素の数値のみ）。"""
    import json
    layout = dict(core.DEFAULT_CELL_LAYOUT)
    restored = json.loads(json.dumps(layout))
    assert grid_calibration.normalize_layout(restored) == \
        grid_calibration.normalize_layout(layout)
    print("PASS: レイアウトが JSON 往復に耐える")


def test_resolve_cell_point_uses_layout():
    """core.resolve_cell_point が描画領域と比率から座標を出す。

    AX を呼ばずに済むよう find_battery_window を差し替えて検証する。
    """
    original = core.find_battery_window
    core.find_battery_window = lambda track="": {
        "track": "Inst2", "plugin": "Battery 4",
        "window": REAL_VIEW, "view": REAL_VIEW,
    }
    try:
        s = core.default_settings()
        got = core.resolve_cell_point(s, "A1")
        assert got == (386, 213, "Inst2 の Battery 4"), got
        got = core.resolve_cell_point(s, "B3")
        assert got[:2] == (526, 269), got
    finally:
        core.find_battery_window = original
    print("PASS: resolve_cell_point が比率から実座標を返す")


def test_resolve_cell_point_without_window():
    """Battery が開いていなければ None を返す（黙って誤った座標を使わない）。"""
    original = core.find_battery_window
    core.find_battery_window = lambda track="": None
    try:
        assert core.resolve_cell_point(core.default_settings(), "A1") is None
    finally:
        core.find_battery_window = original
    print("PASS: ウィンドウ未検出なら None")


def test_resolve_cell_points_uses_only_visible_window():
    """開いている画面が複数でも、配り先は「いま見えている1枚」だけ。

    かつては「すべての Battery」で全ウィンドウ分の座標を返していた。
    しかし Pro Tools はプラグイン画面を**完全に同じ位置に重ねて開く**ので、
    座標を分けても実際のドロップは手前の1枚に落ちる。3つに配ったと
    読み上げながら1つのセルを3回上書きし、利用者のサンプルが消えた
    （2026-08-31）。現行は1枚運用＋トラック切替なので、ここでは
    常に1件しか返さない。
    """
    same = (20.0, 128.0, 1176.0, 650.0)
    fake = [
        {"track": t, "plugin": "Battery 4", "window": same, "view": same}
        for t in ("Inst1", "Inst2", "Inst4")
    ]
    original = core.battery_windows
    core.battery_windows = lambda: list(fake)
    try:
        s = core.default_settings()
        for track in (core.TARGET_ALL, ""):
            points = core.resolve_cell_points(s, "A1", track)
            assert len(points) == 1, (track, points)
            assert points[0]["where"] == "Inst1 の Battery 4", points
        # 単数版も同じ1枚を返す
        assert core.resolve_cell_point(s, "A1", core.TARGET_ALL) == (
            386, 213, "Inst1 の Battery 4")
        # トラック指定は従来どおり選べる
        one = core.resolve_cell_points(s, "A1", "Inst2")
        assert len(one) == 1 and one[0]["where"] == "Inst2 の Battery 4"
    finally:
        core.battery_windows = original
    print("PASS: 配り先は見えている1枚だけ（重なった画面へ配らない）")


def test_battery_track_names_dedupes_slots():
    """同じトラックに複数スロットで挿さっていても、配り先は1回だけ。

    インサートをそのまま並べると Inst1 が2回出てきて、同じセルへ
    二重にドロップし、件数も多く読み上げてしまう。
    """
    original = core.battery_inserts
    core.battery_inserts = lambda: [
        {"track": "Inst1", "slot": "A", "plugin": "Battery 4",
         "point": (10, 10), "open": True},
        {"track": "Inst1", "slot": "B", "plugin": "Battery 4",
         "point": (10, 20), "open": False},
        {"track": "Inst2", "slot": "A", "plugin": "Battery 4",
         "point": (20, 10), "open": False},
        {"track": "", "slot": "A", "plugin": "Battery 4",
         "point": None, "open": False},
    ]
    try:
        assert core.battery_track_names() == ["Inst1", "Inst2"], \
            core.battery_track_names()
    finally:
        core.battery_inserts = original
    print("PASS: 配り先のトラックはスロット重複を落とす")


def test_select_battery_track_refuses_unknown_target():
    """並び順に無いトラック名では、メニューを1度も開かないこと。

    保存済みのロード先が消えているとき（キットや前のセッションの名前）、
    当てもなくメニューを送ると Battery の画面が全トラックを巡る。
    実測で1分近くかかり、その間ずっと別のトラックが表示される。
    """
    if not (core._AX_QUERY_OK and core.QUARTZ_OK):
        print("SKIP: AX / Quartz が無い環境")
        return
    keys = []
    orig_header = core._front_plugin_header
    orig_key = core._cg_key
    orig_click = core._cg_click
    orig_attr = core._ax_attr
    core._front_plugin_header = lambda: {
        "selector": object(), "geometry": (30.0, 40.0, 120.0, 20.0),
        "track": "Inst1", "plugin": "Battery 4",
    }
    core._ax_attr = lambda el, name: "Inst1"
    core._cg_key = lambda code: keys.append(code)
    core._cg_click = lambda x, y: keys.append(("click", x, y))
    try:
        # 並び順に無い名前
        assert core.select_battery_track(
            "Inst9", order=["Inst1", "Inst2"]) is False
        # 並び順そのものが読めないとき（Mix が閉じている）も実行しない
        assert core.select_battery_track("Inst2", order=[]) is False
        assert keys == [], keys
    finally:
        core._front_plugin_header = orig_header
        core._cg_key = orig_key
        core._cg_click = orig_click
        core._ax_attr = orig_attr
    print("PASS: 実在しないトラックへは総当たりしない")


def test_overlapping_plugin_window_blocks_targeting():
    """Battery の画面に別プラグインの画面が重なっていたら中止すること。

    Pro Tools はプラグイン画面を同じ位置に重ねて開く。Track Selector が
    重なっていると合成クリックは手前の画面へ落ちるので、Battery の
    つもりで EQ のトラックを動かしてしまう（利用者は気づけない）。
    """
    same = (20.0, 25.0, 1176.0, 753.0)
    sel = (30.0, 40.0, 120.0, 20.0)
    orig_win = core.battery_windows
    orig_headers = core._plugin_headers
    core.battery_windows = lambda: [
        {"track": "Inst1", "plugin": "Battery 4",
         "window": same, "view": same}
    ]
    try:
        # Battery だけなら狙える
        core._plugin_headers = lambda: [
            {"selector": None, "geometry": sel,
             "track": "Inst1", "plugin": "Battery 4"},
        ]
        assert core.overlapping_plugin_names() == []
        ok, _why = core.can_target_reliably()
        assert ok is True

        # 同じ位置に EQ の画面があると狙えない
        core._plugin_headers = lambda: [
            {"selector": None, "geometry": sel,
             "track": "Inst1", "plugin": "Battery 4"},
            {"selector": None, "geometry": sel,
             "track": "Audio 1", "plugin": "EQ3 7-Band"},
        ]
        assert core.overlapping_plugin_names() == ["EQ3 7-Band"]
        ok, why = core.can_target_reliably()
        assert ok is False and "EQ3 7-Band" in why, why

        # 離れた位置なら邪魔しない
        core._plugin_headers = lambda: [
            {"selector": None, "geometry": sel,
             "track": "Inst1", "plugin": "Battery 4"},
            {"selector": None, "geometry": (900.0, 600.0, 120.0, 20.0),
             "track": "Audio 1", "plugin": "EQ3 7-Band"},
        ]
        assert core.overlapping_plugin_names() == []
        ok, _why = core.can_target_reliably()
        assert ok is True
    finally:
        core.battery_windows = orig_win
        core._plugin_headers = orig_headers
    print("PASS: 別プラグインの画面が重なっていたら中止する")


def test_front_plugin_header_does_not_guess():
    """Battery が見つからないとき、2枚以上あるなら選ばないこと。

    切り替えの途中で no insert のトラックに乗ると Battery 名が消える。
    そこで最初の1枚に落とすと、EQ など別の画面のトラックを動かす。
    """
    sel = (30.0, 40.0, 120.0, 20.0)
    original = core._plugin_headers
    try:
        core._plugin_headers = lambda: [
            {"selector": "eq", "geometry": sel,
             "track": "Audio 1", "plugin": "EQ3 7-Band"},
            {"selector": "bat", "geometry": sel,
             "track": "Inst1", "plugin": "Battery 4"},
        ]
        assert core._front_plugin_header()["selector"] == "bat"
        # Battery が無く、画面が2枚 → 選ばない
        core._plugin_headers = lambda: [
            {"selector": "eq", "geometry": sel,
             "track": "Audio 1", "plugin": "EQ3 7-Band"},
            {"selector": "other", "geometry": sel,
             "track": "Inst1", "plugin": "no insert"},
        ]
        assert core._front_plugin_header() is None
        # 1枚だけなら、それが no insert でも追える
        core._plugin_headers = lambda: [
            {"selector": "other", "geometry": sel,
             "track": "Inst1", "plugin": "no insert"},
        ]
        assert core._front_plugin_header()["selector"] == "other"
    finally:
        core._plugin_headers = original
    print("PASS: Battery が無いとき、当て推量で画面を選ばない")


def test_activate_host_needs_running_host():
    """Pro Tools が起動していなければ、activate を送らないこと。

    AppleScript の activate は起動していないアプリを立ち上げてしまう。
    「開いていない Battery は操作しない」方針と食い違う。
    """
    orig_pids = core.host_app_pids
    orig_run = core.subprocess.run
    calls = []
    core.host_app_pids = lambda names=core.PLUGIN_HOST_APPS: []
    core.subprocess.run = lambda *a, **k: calls.append(a)
    try:
        assert core._activate_host() is False
        assert calls == [], calls
    finally:
        core.host_app_pids = orig_pids
        core.subprocess.run = orig_run
    print("PASS: Pro Tools が起動していなければ前面化しない")


def test_resolve_cell_points_without_windows():
    """Battery が1つも開いていなければ空リスト（誤った座標へ配らない）。"""
    original = core.battery_windows
    core.battery_windows = lambda: []
    try:
        s = core.default_settings()
        assert core.resolve_cell_points(s, "A1", core.TARGET_ALL) == []
        assert core.resolve_cell_points(s, "A1", "") == []
        assert core.resolve_cell_point(s, "A1", core.TARGET_ALL) is None
    finally:
        core.battery_windows = original
    print("PASS: Battery 未検出なら配り先も空")


def test_short_track_name():
    """トラック名を短い日本語にできること（Cmd+B で何度も聞くため）。"""
    assert core.short_track_name("Inst1") == "インスト1"
    assert core.short_track_name("Inst 12") == "インスト12"
    assert core.short_track_name("Audio 3") == "オーディオ3"
    assert core.short_track_name("Master1") == "マスター1"
    assert core.short_track_name("Aux2") == "アックス2"
    # 知らない名前は壊さずそのまま
    assert core.short_track_name("Drums") == "Drums"
    # 語の途中で切らない（続きが数字か空白のときだけ言い換える）
    assert core.short_track_name("Instrument 1") == "Instrument 1"
    assert core.short_track_name("Mastering") == "Mastering"
    assert core.short_track_name("Inst") == "インスト"
    assert core.short_track_name("") == ""
    # ロード先の表示名（自動・すべて）も短くなる
    assert core.target_label("", "short") == "自動"
    assert core.target_label(core.TARGET_ALL, "short") == "すべて"
    assert core.target_label("Inst1", "short") == "インスト1"
    assert core.target_label(core.TARGET_ALL) == core.TARGET_ALL_LABEL
    print("PASS: トラック名を短い日本語で読み上げられる")


def test_window_label_readout_modes():
    """ロード先の読み上げ方を切り替えられること（利用者の要望）。"""
    win = {"track": "Inst1", "plugin": "Battery 4"}
    assert core.window_label(win, core.READOUT_FULL) == "Inst1 の Battery 4"
    assert core.window_label(win, core.READOUT_TRACK) == "Inst1"
    assert core.window_label(win, core.READOUT_SHORT) == "インスト1"
    # トラック名が読めないインスタンスは、どのモードでもプラグイン名
    noname = {"track": "", "plugin": "Battery 4"}
    assert core.window_label(noname, core.READOUT_FULL) == "Battery 4"
    assert core.window_label(noname, core.READOUT_TRACK) == "Battery 4"
    assert core.window_label(noname, core.READOUT_SHORT) == "Battery 4"
    # 3通りとも選べること
    assert [k for k, _ in core.TARGET_READOUT_OPTIONS] == [
        core.READOUT_FULL, core.READOUT_TRACK, core.READOUT_SHORT]
    # 設定で座標解決の読み上げにも反映される
    view = (20.0, 128.0, 1176.0, 650.0)
    fake = [{"track": "Inst2", "plugin": "Battery 4",
             "window": view, "view": view}]
    original = core.battery_windows
    core.battery_windows = lambda: list(fake)
    try:
        s = core.default_settings()
        assert s["target_readout"] == core.READOUT_FULL
        full = core.resolve_cell_points(s, "A1", core.TARGET_ALL)
        assert full[0]["where"] == "Inst2 の Battery 4", full[0]
        s["target_readout"] = core.READOUT_TRACK
        short = core.resolve_cell_points(s, "A1", core.TARGET_ALL)
        assert short[0]["where"] == "Inst2", short[0]
        # 座標は読み上げ方に影響されない
        assert (full[0]["x"], full[0]["y"]) == (short[0]["x"], short[0]["y"])
    finally:
        core.battery_windows = original
    print("PASS: ロード先の読み上げ方を切り替えられる")


def test_front_track_selector_survives_no_insert():
    """プラグインが挿さっていないトラックでも追跡できること。

    トラックを送っている途中で `no insert` のトラックを通ると、
    ウィンドウのタイトルが `Plug-in:` で始まらなくなる。タイトルで
    絞ると見失って切り替えが止まる（実機で発生した不具合）。
    ここでは実機の状態に依存しないよう、関数の存在と安全性だけ確かめる。
    """
    # Pro Tools が無い環境でも例外を出さない
    assert isinstance(core.current_battery_track(), str)
    assert isinstance(core.front_plugin_name(), str)
    assert isinstance(core.host_track_order(), list)
    # 実装がタイトルで絞っていないこと（no insert を見失わないため）
    import inspect
    src = inspect.getsource(core._front_track_selector)
    assert "_PLUGIN_WINDOW_PREFIX" not in src, \
        "タイトルで絞ると no insert のトラックを見失う"
    assert "Track Selector" in src
    print("PASS: no insert のトラックでも Track Selector を追える")


def test_can_target_reliably():
    """画面が複数開いていたら「確実に狙えない」と判定すること。

    重なった画面はどれに入るか特定できず、別のトラックのセルを
    上書きして利用者のサンプルを消す（実際に起きた事故）。
    この判定がロードを止める最後の砦。
    """
    view = (20.0, 128.0, 1176.0, 650.0)

    def fake(n):
        return lambda: [
            {"track": f"Inst{i+1}", "plugin": "Battery 4",
             "window": view, "view": view}
            for i in range(n)
        ]

    original = core.battery_windows
    orig_headers = core._plugin_headers
    core._plugin_headers = lambda: []   # 重なり判定は別テストで見る
    try:
        core.battery_windows = fake(1)
        ok, why = core.can_target_reliably()
        assert ok is True and why == "", (ok, why)

        core.battery_windows = fake(3)
        ok, why = core.can_target_reliably()
        assert ok is False, "複数枚でも安全と判定している"
        assert "3 枚" in why and "閉じて" in why, why

        core.battery_windows = fake(0)
        ok, why = core.can_target_reliably()
        assert ok is False and "開いていません" in why, why
    finally:
        core.battery_windows = original
        core._plugin_headers = orig_headers
    print("PASS: 画面が複数なら確実に狙えないと判定する")


def test_move_cell_sample_guards():
    """セル間の移動が、危ない状況では実行されないこと。

    移動は Battery の画面上のドラッグなので、対象が定まらない状態で
    実行すると別のセルを壊しかねない（ロードで起きたデータ消失事故と
    同型）。ロードと同じ守り（画面1枚運用の確認）を通ること。
    """
    s = core.default_settings()
    view = (20.0, 128.0, 1176.0, 650.0)

    def fake(n):
        return lambda: [
            {"track": f"Inst{i+1}", "plugin": "Battery 4",
             "window": view, "view": view}
            for i in range(n)
        ]

    # 同じセルへの移動は何もしない
    ok, msg = core.move_cell_sample(s, "A2", "A2")
    assert ok is False and "同じセル" in msg, msg

    orig_win = core.battery_windows
    orig_find = core.find_battery_window
    orig_trusted = core.accessibility_trusted
    orig_headers = core._plugin_headers
    core.accessibility_trusted = lambda prompt=False: True
    core._plugin_headers = lambda: []
    try:
        # 画面が1枚も無ければ実行しない（can_target_reliably）
        core.battery_windows = lambda: []
        ok, msg = core.move_cell_sample(s, "A2", "A6")
        assert ok is False and "開いていません" in msg, msg

        # 画面が2枚以上でも実行しない（重なると入れ先を特定できない）
        core.battery_windows = fake(3)
        ok, msg = core.move_cell_sample(s, "A2", "A6")
        assert ok is False and "3 枚" in msg, msg

        # 1枚でもウィンドウ情報が取れなければ実行しない
        core.battery_windows = fake(1)
        core.find_battery_window = lambda track="": None
        ok, msg = core.move_cell_sample(s, "A2", "A6")
        assert ok is False and "見つかりません" in msg, msg

        # TARGET_ALL はトラック名ではない。切り替えを試みて暴走せず、
        # 「表示中の画面」への移動として扱われる（切替エラーにならない）
        called = []
        orig_select = core.select_battery_track
        core.select_battery_track = lambda *a, **k: called.append(a) or False
        try:
            ok, msg = core.move_cell_sample(s, "A2", "A6", core.TARGET_ALL)
            assert not called, "TARGET_ALL で select_battery_track が呼ばれた"
            assert ok is False and "見つかりません" in msg, msg
        finally:
            core.select_battery_track = orig_select
    finally:
        core.battery_windows = orig_win
        core.find_battery_window = orig_find
        core.accessibility_trusted = orig_trusted
        core._plugin_headers = orig_headers

    # 権限が無ければ実行しない
    core.accessibility_trusted = lambda prompt=False: False
    try:
        ok, msg = core.move_cell_sample(s, "A2", "A6")
        assert ok is False and "権限" in msg, msg
    finally:
        core.accessibility_trusted = orig_trusted
    print("PASS: セル移動は前提が揃わなければ実行しない")


class _Patch:
    """core の関数を一時的に差し替える（終わったら必ず戻す）。"""

    def __init__(self, **repl):
        self.repl = repl
        self.saved = {}

    def __enter__(self):
        for name, value in self.repl.items():
            self.saved[name] = getattr(core, name)
            setattr(core, name, value)
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(core, name, value)
        return False


def test_move_cell_sample_can_be_cancelled():
    """セル移動はエスケープで止まること。

    読み上げは「エスケープで中断」と言うのに、トラックの切り替えが無いと
    cancel を一度も見ず、ドラッグを送っていた（2026-11-03 のレビュー）。
    切り替え中の中断も「切り替えられなかった」と失敗扱いにしない。
    """
    view = (20.0, 128.0, 1176.0, 650.0)
    one = {"track": "Inst1", "plugin": "Battery 4", "window": view, "view": view}
    sent = []
    press_escape = []
    common = dict(
        accessibility_trusted=lambda prompt=False: True,
        can_target_reliably=lambda: (True, ""),
        find_battery_window=lambda track="": one,
        host_window_at=lambda x, y, layer=None, infos=None, pids=None: (True, ""),
        cg_drag=lambda *a, **k: (sent.append(a), True)[1],
        _activate_host=lambda timeout=2.0: True,
    )
    s = core.default_settings()
    with _Patch(**common):
        # 始める前に押されていた
        ok, msg = core.move_cell_sample(s, "A1", "A2", "", cancel=lambda: True)
        assert ok is False and "中断" in msg and not sent, (msg, sent)
    # 前面化を待っている間に押された
    with _Patch(**dict(common, _activate_host=lambda timeout=2.0:
                       (press_escape.append(1), True)[1])):
        ok, msg = core.move_cell_sample(s, "A1", "A2", "",
                                        cancel=lambda: bool(press_escape))
        assert ok is False and "中断" in msg and not sent, (msg, sent)
    with _Patch(**dict(common, select_battery_track=lambda t, cancel=None: False)):
        # 切り替えの途中で押された（失敗ではない）
        ok, msg = core.move_cell_sample(s, "A1", "A2", "Inst2", cancel=lambda: True)
        assert ok is False and "中断" in msg, msg
        # 押していないのに切り替えられなかった（失敗）
        ok, msg = core.move_cell_sample(s, "A1", "A2", "Inst2", cancel=lambda: False)
        assert ok is False and "切り替えられません" in msg, msg
    assert not sent, sent
    # 押さなければ動く
    with _Patch(**common):
        ok, msg = core.move_cell_sample(s, "A1", "A2", "", cancel=lambda: False)
        assert ok is True and len(sent) == 1, (msg, sent)
    # セルの位置に窓が無いときは、重なっていると言わない
    sent.clear()
    with _Patch(**dict(common, host_window_at=lambda x, y, layer=None, infos=None,
                       pids=None: (False, ""))):
        ok, msg = core.move_cell_sample(s, "A1", "A2", "")
        assert ok is False and "見つからない" in msg and "重なって" not in msg, msg
        assert not sent, sent
    print("PASS: セル移動はエスケープで止まり、中断を失敗と言わない")


def test_synthetic_events_check_the_window_under_the_point():
    """合成クリック・合成ドラッグは、行き先の窓を確かめてから送ること。

    Pro Tools のプラグイン画面は浮いた窓で、ほかのアプリの窓（確認の画面・
    操作盤など）も上に重なりうる。確かめずに送ると、
    その窓を押したり動かしたりしてしまう（2026-10-24 のレビュー）。
    """
    view = (20.0, 128.0, 1176.0, 650.0)
    one = {"track": "Inst1", "plugin": "Battery 4", "window": view, "view": view}
    sent = []

    # セル移動: セルの上に別の窓があれば動かさない
    with _Patch(
        accessibility_trusted=lambda prompt=False: True,
        can_target_reliably=lambda: (True, ""),
        find_battery_window=lambda track="": one,
        _activate_host=lambda timeout=2.0: True,
        host_window_at=lambda x, y, layer=None, infos=None, pids=None:
            (False, "確認の画面"),
        cg_drag=lambda *a, **k: sent.append(("drag", a)) or True,
    ):
        ok, msg = core.move_cell_sample(core.default_settings(), "A2", "A6")
    assert ok is False and "確認の画面" in msg, msg
    assert not sent, sent

    # トラック切替: Track Selector の上に別の窓があれば押さない
    header = {"selector": object(), "geometry": (300.0, 40.0, 80.0, 20.0),
              "track": "Inst1", "plugin": "Battery 4"}
    with _Patch(
        _front_plugin_header=lambda: header,
        _ax_attr=lambda el, name: "Inst1",
        _activate_host=lambda timeout=2.0: True,
        host_window_at=lambda x, y, layer=None, infos=None, pids=None:
            (False, "Floating Panel"),
        _cg_click=lambda x, y, shift=False: sent.append(("click", x, y)),
        _cg_key=lambda code: sent.append(("key", code)),
    ):
        assert core.select_battery_track(
            "Inst2", order=["Inst1", "Inst2"], now="Inst1") is False
    assert not sent, sent

    # 画面を開く: インサート欄に浮いた画面が重なっていれば押さない
    ins = [{"track": "Inst1", "slot": "A", "plugin": "Battery 4",
            "point": (100, 500), "open": False}]
    with _Patch(
        accessibility_trusted=lambda prompt=False: True,
        battery_windows=lambda: [],
        battery_inserts=lambda: ins,
        host_app_pids=lambda names=core.PLUGIN_HOST_APPS: [10],
        AXUIElementCreateApplication=lambda pid: None,
        _activate_host=lambda timeout=2.0: True,
        _plugin_windows_of=lambda app: [],
        host_window_at=lambda x, y, layer=None, infos=None, pids=None:
            (False, "Pro Tools の別の画面"),
        _cg_click=lambda x, y, shift=False: sent.append(("click", x, y)),
    ):
        res = core.open_closed_battery_windows()
    assert "重なっていて押せません" in res["error"], res

    # 画面を開く: インサート欄の位置に窓が無いときは、重なっていると言わない
    with _Patch(
        accessibility_trusted=lambda prompt=False: True,
        battery_windows=lambda: [],
        battery_inserts=lambda: ins,
        host_app_pids=lambda names=core.PLUGIN_HOST_APPS: [10],
        AXUIElementCreateApplication=lambda pid: None,
        _activate_host=lambda timeout=2.0: True,
        _plugin_windows_of=lambda app: [],
        host_window_at=lambda x, y, layer=None, infos=None, pids=None: (False, ""),
        _cg_click=lambda x, y, shift=False: sent.append(("click", x, y)),
    ):
        res = core.open_closed_battery_windows()
    assert "窓が見つからない" in res["error"] and "重なって" not in res["error"], res
    assert not sent, sent
    print("PASS: 合成イベントは行き先の窓を確かめてから送る")


def test_describe_inventory_names_each_track_once():
    """1 つのトラックに 2 か所挿さっていても、名前は 1 回だけ言うこと。"""
    inv = {"inserted": 3, "open": 1, "closed_tracks": ["Inst1", "Inst1"]}
    text = core.describe_battery_inventory(inv, 1)
    assert text.count("Inst1") == 1, text
    print("PASS: 閉じているトラックの名前は 1 回だけ")


def test_select_battery_track_rejects_target_all():
    """select_battery_track が TARGET_ALL を拒否すること。

    "__all__" は実在しないトラック名なので、そのまま送り続けると
    Track Selector を延々と回してしまう。入口で守る。
    """
    assert core.select_battery_track(core.TARGET_ALL) is False
    assert core.select_battery_track("") is False
    print("PASS: select_battery_track は TARGET_ALL と空文字を拒否する")


def test_find_battery_window_accepts_target_all():
    """find_battery_window が TARGET_ALL を「自動」として扱うこと。

    ロード先を「すべての Battery」にしたまま設定ウィンドウの
    位置合わせを使うと、"__all__" がそのまま照合されて全滅していた
    （2026-08-31 のレビューで発覚）。表示中の1枚を返すのが正しい。
    """
    view = (20.0, 128.0, 1176.0, 650.0)
    fake = [{"track": "Inst1", "plugin": "Battery 4",
             "window": view, "view": view}]
    original = core.battery_windows
    core.battery_windows = lambda: list(fake)
    try:
        win = core.find_battery_window(core.TARGET_ALL)
        assert win is not None and win["track"] == "Inst1", win
        # resolve_cell_point 側も同様に解決できる
        got = core.resolve_cell_point(core.default_settings(), "A1",
                                      core.TARGET_ALL)
        assert got is not None and got[:2] == (386, 213), got
    finally:
        core.battery_windows = original
    print("PASS: find_battery_window が TARGET_ALL を自動として扱う")


def test_describe_battery_inventory():
    """「挿さっているのに見えない」状況を説明できること。

    Pro Tools に挿さっていても画面を開いていない Battery は AX から
    見えない。利用者が「3つ挿したのに1つしか出ない」と困ったため、
    数の食い違いと、どのトラックが閉じているかを伝える。
    """
    # 3つ挿さっていて1つだけ開いている（実機で起きた状況）
    inv = {"inserted": 3, "open": 1, "closed_tracks": ["Inst1", "Inst2"]}
    msg = core.describe_battery_inventory(inv, detected=1)
    assert "3 個挿さって" in msg, msg
    assert "1 個" in msg, msg
    assert "Inst1" in msg and "Inst2" in msg, msg
    # 全部開いていれば、その旨だけ
    inv_all = {"inserted": 3, "open": 3, "closed_tracks": []}
    msg2 = core.describe_battery_inventory(inv_all, detected=3)
    assert "すべて画面が開いて" in msg2, msg2
    # 1つも挿さっていなければ何も言わない（余計な読み上げを避ける）
    assert core.describe_battery_inventory(
        {"inserted": 0, "closed_tracks": []}, 0) == ""
    print("PASS: 挿さっている数と見えている数の食い違いを説明できる")


def test_battery_inserts_is_safe():
    """battery_inserts は Pro Tools が無くても例外を出さない（読み取りのみ）。"""
    inserts = core.battery_inserts()
    assert isinstance(inserts, list)
    for i in inserts:
        assert set(i) >= {"track", "slot", "plugin", "point", "open"}
        assert i["plugin"].startswith(core.TARGET_PLUGIN_PREFIX)
        assert isinstance(i["open"], bool)
    inv = core.battery_inventory()
    assert set(inv) >= {"inserted", "open", "closed_tracks", "inserts"}
    assert inv["inserted"] == len(inv["inserts"])
    assert inv["open"] + len(inv["closed_tracks"]) == inv["inserted"]
    print(f"PASS: インサート走査が安全に動く（{inv['inserted']} 個検出）")


def test_target_label():
    """ロード先の読み上げ名。"""
    assert core.target_label(core.TARGET_ALL) == core.TARGET_ALL_LABEL
    assert core.target_label("Inst1") == "Inst1"
    assert core.target_label("") == "自動"
    # トラック名と衝突しない値であること
    assert core.TARGET_ALL.startswith("__")
    print("PASS: ロード先の読み上げ名")


def test_find_battery_window_matches_plugin_fallback():
    """Track Selector が読めない Battery はプラグイン名でも照合できる。

    2026-08-31: ロード先ポップアップは track が空だとプラグイン名を表示・
    保存するのに、照合が track 完全一致のみで、選ぶと以後のロードが全て
    「見つかりません」になっていた再発防止。
    """
    fake = [
        {"track": "Inst1", "plugin": "Battery 4",
         "window": REAL_VIEW, "view": REAL_VIEW},
        {"track": "", "plugin": "Battery 4",
         "window": REAL_VIEW, "view": REAL_VIEW},
    ]
    original = core.battery_windows
    core.battery_windows = lambda: list(fake)
    try:
        assert core.find_battery_window("Inst1") is fake[0]
        # トラック名で見つからなければ、track が空のものをプラグイン名で照合
        assert core.find_battery_window("Battery 4") is fake[1]
        assert core.find_battery_window("Inst9") is None
        assert core.find_battery_window("") is fake[0]
    finally:
        core.battery_windows = original
    print("PASS: find_battery_window がプラグイン名でも照合する")


def test_migrate_settings_from_legacy():
    """旧設定（12セル・絶対座標）が現行形式へ移る。"""
    legacy = {
        "search_folders": ["/tmp"],
        "cell_coords": {"A1": [400, 300], "B1": [460, 300]},
        "cell_labels": {"A1": "Kick", "B1": "Snare", "A2": "Clap"},
        "favorites": [],
        "recent": [],
    }
    migrated, changed = core.migrate_settings(dict(legacy))
    assert changed
    assert "cell_coords" not in migrated
    assert migrated["schema_version"] == core.SCHEMA_VERSION
    # ラベルは位置を保って移る（旧 B1 は新 A2、旧 A2 は新 B1）
    assert migrated["cell_labels"]["A1"] == "Kick"
    assert migrated["cell_labels"]["A2"] == "Snare"
    assert migrated["cell_labels"]["B1"] == "Clap"
    # 検索フォルダなど他の設定は保たれる
    assert migrated["search_folders"] == ["/tmp"]
    assert migrated["cell_layout"]["cols"] == 12
    print("PASS: 旧設定が現行形式へ移行される")


def test_migrate_settings_is_idempotent():
    once, _ = core.migrate_settings(core.default_settings())
    twice, changed = core.migrate_settings(dict(once))
    assert not changed
    assert once == twice
    print("PASS: 移行を繰り返しても変わらない")


def test_migrate_kits_from_legacy():
    """旧キット（セルマップ直置き・旧セル名）が現行形式へ移る。"""
    legacy = {"my kit": {"A1": "/a/kick.wav", "B1": "/a/snare.wav"}}
    kits, changed = core.migrate_kits(legacy)
    assert changed
    assert kits["my kit"]["track"] == ""
    cells = kits["my kit"]["cells"]
    assert cells["A1"] == "/a/kick.wav"
    assert cells["A2"] == "/a/snare.wav"   # 旧 B1 は位置的に A2
    # 現行形式はそのまま通る
    again, changed2 = core.migrate_kits(kits)
    assert not changed2 and again == kits
    print("PASS: 旧キットが現行形式へ移行される")


def test_kit_accessors():
    assert core.kit_cells({"cells": {"A1": "x"}, "track": "Inst1"}) == {"A1": "x"}
    assert core.kit_track({"cells": {}, "track": "Inst1"}) == "Inst1"
    # 旧形式でも読める
    assert core.kit_cells({"A1": "x"}) == {"A1": "x"}
    assert core.kit_track({"A1": "x"}) == ""
    print("PASS: キットのアクセサが新旧どちらでも読める")


def test_app_settings_wires_calibration():
    """app_settings が比率方式の位置合わせと権限確認を実装している。"""
    src = open(os.path.join(ROOT, "app_settings.py"), encoding="utf-8").read()
    for needed in (
        "layout_from_two_cells",
        "validate_layout",
        "describe_layout",
        "point_to_ratio",
        "current_mouse_position",
        "accessibility_trusted",
        "find_battery_window",
    ):
        assert needed in src, needed
    # 絶対座標に戻っていないこと
    assert "cell_coords" not in src
    print("PASS: app_settings が比率方式の位置合わせを配線")


def test_app_main_wires_battery_target():
    """app_main がロード先 Battery の選択と座標解決を配線している。"""
    src = open(os.path.join(ROOT, "app_main.py"), encoding="utf-8").read()
    for needed in (
        "resolve_cell_point",
        "target_track",
        "menuCycleBattery_",
        "_populate_battery_popup",
    ):
        assert needed in src, needed
    assert "DEFAULT_CELL_COORDS" not in src
    print("PASS: app_main がロード先 Battery を配線")


def run_all():
    tests = [
        test_capture_to_layout_pipeline,
        test_layout_survives_json_roundtrip,
        test_resolve_cell_point_uses_layout,
        test_resolve_cell_point_without_window,
        test_resolve_cell_points_uses_only_visible_window,
        test_resolve_cell_points_without_windows,
        test_battery_track_names_dedupes_slots,
        test_select_battery_track_refuses_unknown_target,
        test_overlapping_plugin_window_blocks_targeting,
        test_front_plugin_header_does_not_guess,
        test_activate_host_needs_running_host,
        test_short_track_name,
        test_window_label_readout_modes,
        test_front_track_selector_survives_no_insert,
        test_can_target_reliably,
        test_move_cell_sample_guards,
        test_move_cell_sample_can_be_cancelled,
        test_synthetic_events_check_the_window_under_the_point,
        test_describe_inventory_names_each_track_once,
        test_select_battery_track_rejects_target_all,
        test_find_battery_window_accepts_target_all,
        test_describe_battery_inventory,
        test_battery_inserts_is_safe,
        test_target_label,
        test_find_battery_window_matches_plugin_fallback,
        test_migrate_settings_from_legacy,
        test_migrate_settings_is_idempotent,
        test_migrate_kits_from_legacy,
        test_kit_accessors,
        test_app_settings_wires_calibration,
        test_app_main_wires_battery_target,
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
