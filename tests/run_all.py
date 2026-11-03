"""
全テストを順に実行するランナー。

使い方:
  python3 tests/run_all.py

各テストモジュールは run_all() を持ち、(passed, total) を返すか、
または None を返す（後方互換）。1つでも失敗があれば終了コード 1。
"""
import io
import os
import sys
import tempfile
import importlib

# Windows コンソールでも文字化けしないよう UTF-8 を強制する
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# **利用者の設定ファイル・キット・ログに触らない。** core は読み込んだ時点の
# ホームフォルダから置き場所を決めるので、テストを読み込む前に一時フォルダへ
# 向けておく（各テストも置き場所を差し替えているが、漏れたときの安全網）。
# 音と読み上げも止める
os.environ["HOME"] = tempfile.mkdtemp(prefix="b4l_test_home_")
os.environ["BATTERY4_SILENT"] = "1"
# テストは日本語の文を期待する（英語は test_i18n が別のプロセスで確かめる）
os.environ["BATTERY4_LANG"] = "ja"

# リポジトリのルートと tests/ を import パスに追加（実行場所に依存しない）
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
for p in (ROOT, TESTS_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

TEST_MODULES = [
    "test_core",
    "test_name_readout",
    "test_integration",
    "test_applescript_escape",
    "test_grid_calibration",
    "test_wizard_integration",
    "test_cocoa_smoke",
    "test_release",
    "test_i18n",
]
# 一覧に書き忘れたテストも流す
TEST_MODULES += sorted(
    os.path.splitext(name)[0] for name in os.listdir(TESTS_DIR)
    if name.startswith("test_") and name.endswith(".py")
    and os.path.splitext(name)[0] not in TEST_MODULES
)


def main():
    print("=" * 50)
    print(" PT Battery 4 Loader — 全テスト実行")
    print("=" * 50)
    grand_passed = 0
    grand_total = 0
    broken = []
    for name in TEST_MODULES:
        print(f"\n----- {name} -----")
        try:
            mod = importlib.import_module(name)
        except Exception as e:
            print(f"ERROR: {name} を import できません: {type(e).__name__}: {e}")
            broken.append(name)
            continue
        if not hasattr(mod, "run_all"):
            print(f"(run_all なし: {name})")
            continue
        try:
            result = mod.run_all()
        except Exception as e:
            print(f"ERROR: {name}.run_all() が例外: {type(e).__name__}: {e}")
            broken.append(name)
            continue
        if isinstance(result, tuple) and len(result) == 2:
            passed, total = result
            grand_passed += passed
            grand_total += total
            if passed != total:
                broken.append(name)

    print("\n" + "=" * 50)
    print(f" 合計: {grand_passed}/{grand_total} テストが PASS")
    if broken:
        print(f" 失敗のあるモジュール: {', '.join(broken)}")
        print("=" * 50)
        sys.exit(1)
    print(" すべてのテストが成功しました")
    print("=" * 50)


if __name__ == "__main__":
    main()
