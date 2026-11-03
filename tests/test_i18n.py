"""表示の言語（i18n.py）の確認。

- 言語の決め方（Mac の「優先する言語」の上から、日本語か英語の先に出るほう。
  どちらも無ければ英語）
- **ソースの文言がすべて日本語と英語の組になっていること**（ast で見る）
- 組の f 文字列が同じ値を使っていること（片方にだけ入れ忘れない）
- **英語で起動したとき、画面・メニュー・読み上げに日本語が混ざらないこと**
  （別のプロセスで英語にして集める。tests/en_probe.py。言語は起動したときに
  決まるので、このプロセスの中では切り替えられない）
"""
import ast
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# テストは日本語で動かす。言語は import のときに決まるので、import の前に決める
os.environ["BATTERY4_LANG"] = "ja"

import i18n  # noqa: E402

JAPANESE = re.compile(r"[　-ヿ一-鿿＀-￯]")
SOURCES = ("app_main.py", "app_kit.py", "app_settings.py", "core.py",
           "grid_calibration.py", "name_readout.py", "version.py", "ui_common.py")
# 文言ではない日本語を持つ変数（カタカナの読みの表、ファイル名から種別を推定する言葉）
NOT_TEXT_NAMES = {"DIGIT_KANA", "LOOP_HINTS"}
# 日本語のときだけ使う関数と、文言を持たない関数
# （日本語のヘルプ、フォルダ名の全角の置き換え、起動確認の見本のファイル名）
JAPANESE_ONLY_FUNCTIONS = {"_help_text_ja", "kit_folder_name", "run_selftest"}


def _tree(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return ast.parse(f.read())


def _tr_calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "tr":
            yield node


def _skipped(tree) -> set:
    """文言として調べないノード（説明文と、上の変数・関数の中）。"""
    skip = set()
    for node in ast.walk(tree):
        if (isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
                and node.body and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)):
            skip.add(id(node.body[0].value))
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        named = any(getattr(t, "id", "") in NOT_TEXT_NAMES for t in targets)
        function = (isinstance(node, ast.FunctionDef)
                    and node.name in JAPANESE_ONLY_FUNCTIONS)
        if named or function:
            skip.update(id(c) for c in ast.walk(node))
    return skip


def test_pick_language():
    for langs, want in (
        (["ja-JP", "en-JP"], "ja"),
        (["en-US", "ja-JP"], "en"),
        (["en-GB"], "en"),
        (["fr-FR", "ja-JP"], "ja"),   # 日本語と英語のうち、先に出るほう
        (["fr-FR", "de-DE"], "en"),   # どちらも無ければ英語
        ([], "en"),
        (["ja_JP.UTF-8"], "ja"),      # 環境変数 LANG の書き方
        (["zh-Hans-CN", "en-CN"], "en"),
    ):
        assert i18n.pick_language(langs) == want, (langs, want)
    print("PASS: 言語の決め方")


def test_tests_run_in_japanese():
    assert i18n.LANG == "ja"
    assert i18n.tr("日本語", "English") == "日本語"
    assert i18n.plural(1, "sample") == "1 sample"
    assert i18n.plural(0, "sample") == "0 samples"
    assert i18n.plural(3, "sample") == "3 samples"
    print("PASS: テストは日本語で動いている")


def test_every_string_is_paired():
    """tr(日本語, 英語) の外に日本語の文字列が無く、英語の側に日本語が無いこと。"""
    problems = []
    checked = 0
    for name in SOURCES:
        tree = _tree(name)
        skip = _skipped(tree)
        for call in _tr_calls(tree):
            checked += 1
            ja, en = call.args
            skip.update(id(c) for c in ast.walk(ja))
            for child in ast.walk(en):
                if (isinstance(child, ast.Constant)
                        and isinstance(child.value, str)
                        and JAPANESE.search(child.value)):
                    problems.append(f"{name}:{call.lineno} 英語の側に日本語: "
                                    f"{child.value[:40]}")
                skip.add(id(child))
        for node in ast.walk(tree):
            if (id(node) not in skip and isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and JAPANESE.search(node.value)):
                problems.append(f"{name}:{node.lineno} {node.value[:40]}")
    assert not problems, "\n".join(problems[:20])
    assert checked > 400, checked
    print(f"PASS: 文言はすべて日本語と英語の組（{checked} 組）")


def _names_in(node) -> set:
    """f 文字列の差し込みで使っている名前。"""
    found = set()
    for value in ast.walk(node):
        if isinstance(value, ast.FormattedValue):
            found |= {n.id for n in ast.walk(value.value)
                      if isinstance(n, ast.Name) and n.id != "plural"}
            found |= {n.attr for n in ast.walk(value.value)
                      if isinstance(n, ast.Attribute)}
    return found


def test_pairs_use_the_same_values():
    """日本語と英語で、差し込む値が同じこと（片方の文にだけ件数が無い、などを防ぐ）。"""
    problems = []
    for name in SOURCES:
        for call in _tr_calls(_tree(name)):
            ja, en = (_names_in(arg) for arg in call.args)
            if ja != en:
                problems.append(f"{name}:{call.lineno} {sorted(ja ^ en)}")
    assert not problems, "\n".join(problems[:20])
    print("PASS: 組の f 文字列は同じ値を差し込む")


_PROBE = None


def _probe() -> dict:
    """英語の文言を別のプロセスで集める（en_probe.py が環境を自分で整える）。

    pbcopy は記録するだけの偽物に差し替えて動かす（呼ばれたら印のファイルができる）。
    検査そのものが本物のクリップボードを書き換えていたことがある。
    """
    global _PROBE
    if _PROBE is None:
        folder = tempfile.mkdtemp(prefix="b4l_en_pb_")
        marker = os.path.join(folder, "called")
        fake = os.path.join(folder, "pbcopy")
        with open(fake, "w", encoding="utf-8") as f:
            f.write(f'#!/bin/sh\ncat > "{marker}"\n')
        os.chmod(fake, 0o755)
        # 単独で動かしたときの本物のホームの代わり。en_probe はここに書かないはず
        home = os.path.join(folder, "home")
        os.mkdir(home)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", HOME=home,
                   PATH=folder + os.pathsep + os.environ.get("PATH", ""))
        done = subprocess.run(
            [sys.executable, "-X", "utf8",
             os.path.join(ROOT, "tests", "en_probe.py")],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
        assert done.returncode == 0, done.stderr[-2000:]
        _PROBE = json.loads(done.stdout.strip().splitlines()[-1])
        _PROBE["clipboard_used"] = ["yes" if os.path.exists(marker) else "no"]
        _PROBE["home_files"] = os.listdir(home)
    return _PROBE


def _all_texts():
    for group, texts in _probe().items():
        for text in texts:
            yield group, text


def test_probe_leaves_the_clipboard_alone():
    """英語の検査がロードの失敗の報告を通しても、pbcopy を呼ばないこと。"""
    assert _probe()["clipboard_used"] == ["no"]
    print("PASS: 英語の検査はクリップボードに触らない")


def test_probe_leaves_the_home_folder_alone():
    """英語の検査を単独で動かしても、本物の設定を書き換えないこと。"""
    assert _probe()["home_files"] == [], _probe()["home_files"]
    print("PASS: 英語の検査はホームに書かない")


def test_english_text_fits():
    """英語の文字が部品の枠に収まること（幅は日本語に合わせて決めてあった）。"""
    assert _probe()["clipped"] == [], _probe()["clipped"]
    print("PASS: 英語の文字が部品の枠に収まる")


def test_english_button_names_start_with_screen_text():
    """英語でも、読み上げ名が画面の文字で始まること（日本語は test_cocoa_smoke）。"""
    pairs = [line.split(" / ", 1) for line in _probe()["buttons"]]
    assert len(pairs) > 20, pairs
    bad = [pair for pair in pairs if not pair[1].startswith(pair[0])]
    assert not bad, bad
    print(f"PASS: 英語の読み上げ名は画面の文字で始まる（{len(pairs)} 個）")


def test_english_has_no_japanese():
    bad = [(g, t[:80]) for g, t in _all_texts() if JAPANESE.search(t)]
    assert not bad, bad[:10]
    assert _probe()["lang"] == ["en"]
    print(f"PASS: 英語の画面に日本語が混ざらない（{sum(1 for _ in _all_texts())} 件）")


def test_every_group_was_collected():
    empty = [g for g, texts in _probe().items()
             if g not in ("clipped", "home_files") and not [t for t in texts if t]]
    assert not empty, empty
    print("PASS: 英語の文言を全部の組から集めた")


def test_english_singular_forms():
    """「1 samples」のような形で読まない。"""
    wrong = re.compile(r"\b1 (?:sample|cell|second|minute|pack|row|column|folder"
                       r"|item|Battery instance|inserted Battery instance)s\b"
                       r"|\b1 are\b|\b1 inserted Batteries\b")
    bad = [t[:80] for _g, t in _all_texts() if wrong.search(t)]
    assert not bad, bad[:10]
    print("PASS: 英語の数え方")


def test_english_sentences_are_spaced():
    """日本語は文を詰めて並べるので、英語でも詰めたままになりやすい（「done.Next」）。"""
    joined = re.compile(r"[a-z]\.[A-Z]|[a-z]\)\.[A-Z]")
    bad = [t[:120] for _g, t in _all_texts() if joined.search(t)]
    assert not bad, bad[:10]
    print("PASS: 英語の文の間が空いている")


def test_english_names():
    out = _probe()
    assert "Actions" in out["menus"] and "Edit" in out["menus"]
    assert out["main_window"][0] == "PT Battery 4 Loader", out["main_window"][0]
    assert f"About {out['main_window'][0]}" in out["menus"]
    assert out["name_readout"][2] == "oh one Kick eight oh eight", out["name_readout"]
    assert out["labels"][3:6] == ["Inst 1", "Audio 3", "Instrument 1"], out["labels"]
    assert out["labels"][6] == "Battery 4 on Inst1", out["labels"]
    assert out["durations"][0] == "Length unknown"
    assert "1 minute 1 second" in out["durations"], out["durations"]
    print("PASS: 英語の読み方の見本")


def test_english_help_mentions_every_menu_key():
    """英語のヘルプにも、日本語と同じコマンドキーがあること。"""
    import app_main
    ja = app_main._help_text_ja()
    en = _probe()["help"][0]
    names = {"カンマ": "Comma", "セミコロン": "Semicolon"}
    for found in set(re.findall(r"コマンド\+(?:シフト\+)?(?:[A-Z0-9]\b|カンマ|セミコロン)", ja)):
        want = found.replace("コマンド", "Command").replace("シフト", "Shift")
        for jp, word in names.items():
            want = want.replace(jp, word)
        assert want in en, f"英語のヘルプに {want} が無い"
    print("PASS: 英語のヘルプにもキーがすべてある")


def test_english_composed_sentences():
    """組み立てた文の見本（途中まで入った中断・1 件のときの言い方）。"""
    said = _probe()["load_results"]
    assert "Load stopped. Before that, it went into Inst1." in said, said
    assert "Move stopped" in said, said
    assert any("showing 1 sample." in t for t in said), said
    assert any("1 inserted Battery instance gets" in t for t in said), said
    grid = _probe()["grid"]
    assert any(t.startswith("The grid is 1 row by 1 column, 1 cell in all") for t in grid), grid
    assert any(t.startswith("Grid changed to 1 row by 1 column") for t in grid), grid
    print("PASS: 英語の組み立てた文の見本")


def run_all():
    tests = [
        test_pick_language,
        test_tests_run_in_japanese,
        test_every_string_is_paired,
        test_pairs_use_the_same_values,
        test_probe_leaves_the_clipboard_alone,
        test_probe_leaves_the_home_folder_alone,
        test_english_text_fits,
        test_english_button_names_start_with_screen_text,
        test_english_composed_sentences,
        test_english_has_no_japanese,
        test_every_group_was_collected,
        test_english_singular_forms,
        test_english_sentences_are_spaced,
        test_english_names,
        test_english_help_mentions_every_menu_key,
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
