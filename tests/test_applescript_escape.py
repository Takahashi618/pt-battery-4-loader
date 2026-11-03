"""
core._escape_applescript のエスケープ処理を検証するテスト。
AppleScript 文字列リテラルとして安全になっているかを確認する。
GUI を起動しないモジュール関数テスト。
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


def test_plain_text_unchanged():
    """記号を含まないテキストはそのまま。"""
    assert core._escape_applescript("Kick 808") == "Kick 808"
    print("PASS: 通常テキストは変化しない")


def test_double_quote_escaped():
    """二重引用符が \\\" にエスケープされる。"""
    out = core._escape_applescript('Kick "Hard".wav')
    assert out == 'Kick \\"Hard\\".wav', out
    print("PASS: 二重引用符をエスケープ")


def test_backslash_escaped():
    """バックスラッシュが二重化される。"""
    out = core._escape_applescript("path\\to\\file")
    assert out == "path\\\\to\\\\file", out
    print("PASS: バックスラッシュをエスケープ")


def test_backslash_before_quote_order():
    """バックスラッシュ→引用符の順でエスケープされ二重適用されない。"""
    # 入力 \"  ->  \\ + \"  =  \\\"
    out = core._escape_applescript('\\"')
    assert out == '\\\\\\"', repr(out)
    print("PASS: エスケープ順序が正しい")


def test_control_chars_to_space():
    """改行・タブが空白に正規化される。"""
    out = core._escape_applescript("a\nb\tc")
    assert "\n" not in out and "\t" not in out
    assert out == "a b c", repr(out)
    print("PASS: 制御文字を空白へ正規化")


def test_none_safe():
    """None を渡しても落ちない。"""
    assert core._escape_applescript(None) == ""
    print("PASS: None 安全")


def test_generated_script_balanced_quotes():
    """
    問題のあるファイル名で実際に生成される AppleScript 断片の
    二重引用符が偶数（＝閉じている）ことを確認する。
    """
    bad = 'Kick "Hard".wav'
    safe = core._escape_applescript(bad)
    fragment = f'set announcement to "{safe}"'
    unescaped_quotes = 0
    i = 0
    while i < len(fragment):
        if fragment[i] == "\\":
            i += 2  # エスケープシーケンスを飛ばす
            continue
        if fragment[i] == '"':
            unescaped_quotes += 1
        i += 1
    assert unescaped_quotes == 2, f"開きと閉じの2個のはずが {unescaped_quotes}"
    print("PASS: 生成スクリプトの引用符が閉じている")


def run_all():
    tests = [
        test_plain_text_unchanged,
        test_double_quote_escaped,
        test_backslash_escaped,
        test_backslash_before_quote_order,
        test_control_chars_to_space,
        test_none_safe,
        test_generated_script_balanced_quotes,
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
