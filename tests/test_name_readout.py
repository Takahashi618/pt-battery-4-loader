"""
name_readout モジュールのユニットテスト。
tkinter 非依存なので Windows 上で完全に実行できる。
"""
import sys
import os

# モジュールのあるディレクトリ（リポジトリのルート）を import パスに追加
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# テストは日本語の文を期待する（英語は test_i18n が別のプロセスで確かめる）。
# 言語は import のときに決まるので、ここで決めないと英語の Mac で単独で動かすと落ちる
os.environ["BATTERY4_LANG"] = "ja"

from name_readout import (
    readout_name, cycle_mode, mode_label, VALID_MODES, DIGIT_KANA
)


def test_off_returns_filename():
    """off: ファイル名（拡張子つき）をそのまま返す。"""
    assert readout_name("/x/01_Kick_808.wav", "off") == "01_Kick_808.wav"
    assert readout_name("Snare-Hard.aiff", "off") == "Snare-Hard.aiff"
    print("PASS: off は元のファイル名を返す")


def test_plain_separators():
    """plain: 拡張子除去 + 区切り→空白 + 連続空白圧縮。"""
    assert readout_name("/x/01_Kick_808.wav", "plain") == "01 Kick 808"
    assert readout_name("Snare-Hard.aiff", "plain") == "Snare Hard"
    assert readout_name("kick...808.wav", "plain") == "kick 808"
    assert readout_name("a__b--c.wav", "plain") == "a b c"
    print("PASS: plain は記号整形のみ")


def test_plain_no_extension():
    """拡張子が無い名前でも壊れない。"""
    assert readout_name("LoopOnly", "plain") == "LoopOnly"
    print("PASS: 拡張子なしでも動作")


def test_kana_digits():
    """kana: 数字を1桁ずつカナ化（0=マル）、英字はそのまま。"""
    assert readout_name("/x/01_Kick_808.wav", "kana") == "マルイチ Kick ハチマルハチ"
    assert readout_name("909_Snare.wav", "kana") == "キュウマルキュウ Snare"
    assert readout_name("120bpm.wav", "kana") == "イチニマルbpm"
    print("PASS: kana は数字を1桁ずつカナ化")


def test_kana_keeps_letters():
    """kana: 数字を含まない名前は plain と同じ結果。"""
    assert readout_name("Kick_Soft.wav", "kana") == "Kick Soft"
    print("PASS: kana で数字なしは記号整形のみ")


def test_unknown_mode_falls_back_to_plain():
    """未知のモードは plain 扱い。"""
    assert readout_name("01_Kick.wav", "bogus") == "01 Kick"
    print("PASS: 未知モードは plain にフォールバック")


def test_path_basename_extraction():
    """フルパスでも basename だけを対象にする。"""
    assert readout_name("/a/b/c/01_Kick_808.wav", "plain") == "01 Kick 808"
    # Windows 風パスでも os.path.basename 任せ（実行環境依存だが念のため）
    assert readout_name("snare.wav", "plain") == "snare"
    print("PASS: パスから basename を抽出")


def test_cycle_mode():
    """off -> plain -> kana -> off の巡回。"""
    assert cycle_mode("off") == "plain"
    assert cycle_mode("plain") == "kana"
    assert cycle_mode("kana") == "off"
    assert cycle_mode("invalid") == "plain"  # 不正値は先頭(off)扱い -> 次はplain
    print("PASS: cycle_mode が正しく巡回")


def test_mode_label():
    """各モードに日本語ラベルがある。"""
    for m in VALID_MODES:
        label = mode_label(m)
        assert label and "読み上げ補正" in label
    print("PASS: 全モードに日本語ラベルがある")


def test_all_digits_have_kana():
    """0-9 すべてにカナ定義がある。"""
    for d in "0123456789":
        assert d in DIGIT_KANA and DIGIT_KANA[d]
    print("PASS: 全数字にカナ定義あり")


def test_kana_full_width_digits():
    """全角の数字も読み、例外にしないこと。

    以前は str.isdigit() で数字と判定してから半角の表を引いていたため、
    全角数字（１２０）を含むファイル名で KeyError になり、数字カナの
    ときサンプル一覧が作り直せなかった（2026-10-24 のレビュー）。
    """
    assert readout_name("キック１２０.wav", "kana") == "キックイチニマル"
    assert readout_name("/x/Snare_０１.wav", "kana") == "Snare マルイチ"
    # 上付きの ² も数字として読む（NFKC で 2）
    assert readout_name("hat².wav", "kana") == "hatニ"
    print("PASS: 全角の数字も数字カナで読む")


def run_all():
    tests = [
        test_kana_full_width_digits,
        test_off_returns_filename,
        test_plain_separators,
        test_plain_no_extension,
        test_kana_digits,
        test_kana_keeps_letters,
        test_unknown_mode_falls_back_to_plain,
        test_path_basename_extraction,
        test_cycle_mode,
        test_mode_label,
        test_all_digits_have_kana,
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
