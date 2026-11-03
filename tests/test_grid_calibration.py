"""
grid_calibration モジュールのユニットテスト。
GUI 非依存なので単体で実行できる。

セル名は Battery 4 の実表記（行＝A〜D、列＝1〜12）。A1 の右隣は A2。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# テストは日本語の文を期待する（英語は test_i18n が別のプロセスで確かめる）。
# 言語は import のときに決まるので、ここで決めないと英語の Mac で単独で動かすと落ちる
os.environ["BATTERY4_LANG"] = "ja"

from grid_calibration import (
    cell_names, cell_to_rowcol, rowcol_to_cell,
    migrate_legacy_cell, migrate_legacy_map,
    normalize_layout, layout_cell_names, cell_ratio, cell_point,
    ratio_to_point, point_to_ratio, layout_to_coords,
    layout_from_two_cells, validate_layout, describe_layout, describe_cell,
    DEFAULT_LAYOUT, DEFAULT_ROWS, DEFAULT_COLS, LEGACY_CELL_NAMES,
)

# 実機実測の描画領域（Pro Tools 2026.4.1 / Battery 4.3.1）
REAL_VIEW = (20.0, 128.0, 1176.0, 650.0)


def test_cell_names_layout():
    names = cell_names(4, 12)
    assert len(names) == 48
    assert names[0] == "A1"
    assert names[1] == "A2"      # A1 の右隣は A2（B1 ではない）
    assert names[11] == "A12"
    assert names[12] == "B1"     # 次の行の先頭
    assert names[-1] == "D12"
    print("PASS: cell_names が Battery 表記の並びを返す")


def test_cell_to_rowcol():
    assert cell_to_rowcol("A1") == (0, 0)
    assert cell_to_rowcol("A12") == (0, 11)
    assert cell_to_rowcol("B1") == (1, 0)
    assert cell_to_rowcol("D3") == (3, 2)
    assert rowcol_to_cell(1, 2) == "B3"
    assert rowcol_to_cell(3, 11) == "D12"
    print("PASS: cell_to_rowcol / rowcol_to_cell が対応する")


def test_cell_to_rowcol_invalid():
    for bad in ("1A", "AA", "A", "", "Z3", "A0"):
        try:
            cell_to_rowcol(bad)
            assert False, f"{bad} で例外が出るべき"
        except ValueError:
            pass
    print("PASS: 不正セル名で ValueError")


def test_migrate_legacy_cell():
    """旧「列＝A〜D、行＝1〜3」から新表記へ、画面上の位置を保って変換する。"""
    assert migrate_legacy_cell("A1") == "A1"   # 左上は変わらない
    assert migrate_legacy_cell("B1") == "A2"   # 旧の「右隣」は A2
    assert migrate_legacy_cell("D1") == "A4"
    assert migrate_legacy_cell("A2") == "B1"   # 旧の「真下」は B1
    assert migrate_legacy_cell("D3") == "C4"   # 旧の右下
    assert len({migrate_legacy_cell(c) for c in LEGACY_CELL_NAMES}) == 12
    print("PASS: 旧セル名が位置を保って移行される")


def test_migrate_legacy_map():
    old = {"A1": "kick", "B1": "snare", "A2": "clap"}
    new = migrate_legacy_map(old)
    assert new == {"A1": "kick", "A2": "snare", "B1": "clap"}
    print("PASS: 旧セル名の辞書が移行される")


def test_normalize_layout_fills_defaults():
    lay = normalize_layout({"rows": 2})
    assert lay["rows"] == 2
    assert lay["cols"] == DEFAULT_COLS
    assert lay["x0"] == DEFAULT_LAYOUT["x0"]
    assert normalize_layout(None) == normalize_layout(DEFAULT_LAYOUT)
    print("PASS: normalize_layout が欠けた項目を補う")


def test_ratio_and_point_roundtrip():
    view = (100.0, 200.0, 400.0, 300.0)
    pt = (300, 350)
    ratio = point_to_ratio(pt, view)
    assert abs(ratio[0] - 0.5) < 1e-9
    assert abs(ratio[1] - 0.5) < 1e-9
    assert ratio_to_point(ratio, view) == pt
    print("PASS: 比率と画面座標が往復する")


def test_cell_point_matches_measurement():
    """既定レイアウトが実測値（A1 中心 = 386, 213）と一致する。"""
    assert cell_point(None, "A1", REAL_VIEW) == (386, 213)
    # 列間隔 70px、行間隔 56px
    assert cell_point(None, "A2", REAL_VIEW) == (456, 213)
    assert cell_point(None, "B1", REAL_VIEW) == (386, 269)
    assert cell_point(None, "B3", REAL_VIEW) == (526, 269)
    print("PASS: 既定レイアウトが実測値と一致する")


def test_cell_point_follows_window_move():
    """ウィンドウが動いても比率なので追随する。"""
    moved = (REAL_VIEW[0] + 300, REAL_VIEW[1] + 100, REAL_VIEW[2], REAL_VIEW[3])
    x0, y0 = cell_point(None, "A1", REAL_VIEW)
    x1, y1 = cell_point(None, "A1", moved)
    assert (x1 - x0, y1 - y0) == (300, 100)
    print("PASS: ウィンドウ移動にセル位置が追随する")


def test_layout_to_coords_all_cells():
    coords = layout_to_coords(None, REAL_VIEW)
    assert len(coords) == DEFAULT_ROWS * DEFAULT_COLS
    assert set(coords) == set(layout_cell_names(None))
    # 最終セルが描画領域の中に収まる
    lx, ly = coords["D12"]
    assert REAL_VIEW[0] < lx < REAL_VIEW[0] + REAL_VIEW[2]
    assert REAL_VIEW[1] < ly < REAL_VIEW[1] + REAL_VIEW[3]
    print("PASS: 全セルの座標が描画領域に収まる")


def test_layout_from_two_cells():
    """2点から逆算したレイアウトが元の比率を再現する。"""
    first = cell_ratio(None, "A1")
    last = cell_ratio(None, "D12")
    lay = layout_from_two_cells("A1", first, "D12", last, 4, 12)
    for cell in ("A1", "A12", "B3", "D12"):
        got = cell_ratio(lay, cell)
        want = cell_ratio(None, cell)
        assert abs(got[0] - want[0]) < 1e-9, cell
        assert abs(got[1] - want[1]) < 1e-9, cell
    print("PASS: 2点からレイアウトを逆算できる")


def test_layout_from_two_cells_single_row():
    """同じ行の2点でも、行間隔は既定値で埋めて壊れない。"""
    lay = layout_from_two_cells("A1", (0.3, 0.1), "A12", (0.9, 0.1), 4, 12)
    assert lay["row_step"] == DEFAULT_LAYOUT["row_step"]
    assert lay["col_step"] > 0
    print("PASS: 同一行の2点でも既定の行間隔で補える")


def test_validate_layout_ok():
    ok, msg = validate_layout(DEFAULT_LAYOUT)
    assert ok, msg
    print("PASS: 既定レイアウトは妥当")


def test_validate_layout_reversed():
    bad = dict(DEFAULT_LAYOUT, col_step=-0.05)
    ok, msg = validate_layout(bad)
    assert not ok and "列の向き" in msg
    bad = dict(DEFAULT_LAYOUT, row_step=-0.05)
    ok, msg = validate_layout(bad)
    assert not ok and "行の向き" in msg
    print("PASS: 向きの逆転を検出")


def test_validate_layout_out_of_view():
    """セルが描画領域の外に出るレイアウトを弾く。"""
    bad = dict(DEFAULT_LAYOUT, col_step=0.2)   # 12列で 2.2 まで伸びる
    ok, msg = validate_layout(bad)
    assert not ok and "範囲の外" in msg
    print("PASS: 表示範囲の外を検出")


def test_validate_layout_bad_size():
    ok, _ = validate_layout(dict(DEFAULT_LAYOUT, rows=0))
    assert not ok
    ok, _ = validate_layout(dict(DEFAULT_LAYOUT, cols=99))
    assert not ok
    print("PASS: 行数・列数の範囲外を検出")


def test_describe_layout_readable():
    text = describe_layout(DEFAULT_LAYOUT, REAL_VIEW)
    assert "4 行" in text and "12 列" in text
    assert "A1" in text and "D12" in text
    assert "間隔" in text
    for sym in ("★", "→", "↑", "♪", "×"):
        assert sym not in text
    # 描画領域なしでも説明できる
    assert "パーセント" in describe_layout(DEFAULT_LAYOUT)
    print("PASS: describe_layout が読み上げ用テキストを生成")


def test_describe_cell():
    assert describe_cell("A1") == "A行 1列"
    assert describe_cell("D12") == "D行 12列"
    print("PASS: describe_cell が行と列を説明する")


def run_all():
    tests = [
        test_cell_names_layout,
        test_cell_to_rowcol,
        test_cell_to_rowcol_invalid,
        test_migrate_legacy_cell,
        test_migrate_legacy_map,
        test_normalize_layout_fills_defaults,
        test_ratio_and_point_roundtrip,
        test_cell_point_matches_measurement,
        test_cell_point_follows_window_move,
        test_layout_to_coords_all_cells,
        test_layout_from_two_cells,
        test_layout_from_two_cells_single_row,
        test_validate_layout_ok,
        test_validate_layout_reversed,
        test_validate_layout_out_of_view,
        test_validate_layout_bad_size,
        test_describe_layout_readable,
        test_describe_cell,
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
