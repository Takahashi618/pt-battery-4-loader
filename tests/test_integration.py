"""
統合スモークテスト（GUI 非起動）：
core のソート・リスト表示と name_readout の読み上げ補正が
組み合わせて正しく動くかを検証する。
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
import name_readout


def test_recent_sort_via_core():
    """recent ソート：最新→既存→名前順。"""
    recent = ["/x/c.wav", "/x/a.wav"]  # c が最新
    paths = ["/x/a.wav", "/x/b.wav", "/x/c.wav", "/x/d.wav"]
    result = core.sort_samples(paths, "recent", recent=recent)
    assert result == ["/x/c.wav", "/x/a.wav", "/x/b.wav", "/x/d.wav"], result
    print("PASS: recent ソートが最新→既存→名前順で並ぶ")


def test_readout_modes():
    """読み上げ補正 3 モードの変換結果。"""
    p = "/x/01_Kick_808.wav"
    assert name_readout.readout_name(p, "off") == "01_Kick_808.wav"
    assert name_readout.readout_name(p, "plain") == "01 Kick 808"
    assert name_readout.readout_name(p, "kana") == "マルイチ Kick ハチマルハチ"
    print("PASS: 読み上げ補正 off / plain / kana")


def test_readout_cycle_persist_shape():
    """モード巡回と設定キーの形（app_main が保存する形式）を確認。"""
    mode = "off"
    seen = [mode]
    for _ in range(3):
        mode = name_readout.cycle_mode(mode)
        seen.append(mode)
    assert seen == ["off", "plain", "kana", "off"]
    s = core.default_settings()
    assert s["readout_mode"] in name_readout.VALID_MODES
    print("PASS: モード巡回 off→plain→kana→off と設定既定値")


def test_list_row_composition():
    """app_main._refresh_list 相当の行文字列合成：
    お気に入りタグ + 読み上げ補正名。"""
    path = "/x/808_Clap-Hard.wav"
    fav = {path}
    spoken = name_readout.readout_name(path, "kana")
    row = ("[お気に入り] " if path in fav else "") + spoken
    assert row == "[お気に入り] ハチマルハチ Clap Hard", row
    assert "★" not in row
    print("PASS: リスト行の合成（お気に入りタグ＋補正名）")


def test_filter_then_sort_pipeline():
    """フィルター→ソートのパイプライン（_refresh_list の流れ）。"""
    paths = ["/x/Kick_1.wav", "/x/Kick_2.wav", "/y/Snare.wav"]
    matched = core.filter_samples(paths, "kick")
    ordered = core.sort_samples(matched, "name_desc")
    assert ordered == ["/x/Kick_2.wav", "/x/Kick_1.wav"], ordered
    print("PASS: フィルター→ソートのパイプライン")


def run_all():
    tests = [
        test_recent_sort_via_core,
        test_readout_modes,
        test_readout_cycle_persist_shape,
        test_list_row_composition,
        test_filter_then_sort_pipeline,
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
