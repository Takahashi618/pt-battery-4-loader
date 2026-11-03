"""
配布物の部品（ライセンス・はじめにお読みください・マニュアル・配布版の spec）の検査。

- 版の番号は version.py の 1 か所から（文書と spec がずれない）
- ライセンス名は Takahashi、AI（Anthropic の Claude）で作った旨がどの文書にもある
  （配布物の決まり）
- 配布する文書に、作者の名前やユーザー名、メールアドレスが入っていない
- はじめにお読みくださいの「入っているもの」が、配布物を作る道具の並びと同じ
- 配布版は作者の手元の版とバンドル ID が違い、手元の証明書で署名しない
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

# テストは日本語の文を期待する（英語は test_i18n が別のプロセスで確かめる）。
# 言語は import のときに決まるので、ここで決めないと英語の Mac で単独で動かすと落ちる
os.environ["BATTERY4_LANG"] = "ja"

import version

DOCS = os.path.join(ROOT, "docs")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8-sig") as f:
        return f.read()


def test_license_names_takahashi_and_ai():
    text = _read("LICENSE.txt")
    assert text.startswith("MIT License"), text[:40]
    assert "Copyright (c) 2026 Takahashi" in text
    assert "Anthropic" in text and "Claude" in text, "AI で作った旨が無い"
    assert version.COPYRIGHT in text
    print("PASS: ライセンスは Takahashi の MIT で、AI で作った旨がある")


def test_documents_say_ai_and_version():
    readme = _read("docs", "readme.txt")
    manual = _read("docs", "manual.html")
    manual_txt = _read("docs", "manual.txt")
    for name, text in (("はじめにお読みください", readme),
                       ("マニュアル", manual), ("マニュアル.txt", manual_txt)):
        assert "AI（Anthropic 社の Claude）" in text, f"{name} に AI の旨が無い"
        assert "Takahashi" in text, f"{name} に作者の表示が無い"
    for parts in (("docs", "readme_en.txt"), ("docs", "manual_en.html"),
                  ("docs", "manual_en.txt"), ("LICENSE.txt",)):
        text = _read(*parts)
        assert "Claude by Anthropic" in text, f"{parts} に AI の旨が無い"
        assert "Takahashi" in text, f"{parts} に作者の表示が無い"
    assert "{VERSION}" in _read("docs", "readme_en.txt")
    assert "{VERSION}" in readme, "はじめにお読みくださいの版が書き写しになっている"
    assert f"<title>{version.APP_NAME} マニュアル（{version.VERSION}）</title>" in manual
    spans = re.findall(r'<span class="version">([^<]*)</span>', manual)
    assert spans and set(spans) == {version.VERSION}, spans
    # アプリの「について」にも AI の旨と著作権者を出す
    about = version.about_text("2026-10-25 00:00", "abc1234", version.FLAVOR_RELEASE)
    assert version.AI_NOTE in about and version.COPYRIGHT in about
    assert version.VERSION in about
    print("PASS: 文書とバージョン情報に AI の旨と版がある")


def test_documents_have_no_personal_info():
    """配布する文書に、作者の名前・ユーザー名・メールアドレスが無いこと。"""
    import build_release
    words = build_release.PERSONAL + build_release.PERSONAL_TEXT
    for parts in (("LICENSE.txt",), ("docs", "readme.txt"),
                  ("docs", "manual.html"), ("docs", "manual.txt"),
                  ("docs", "readme_en.txt"), ("docs", "manual_en.html"),
                  ("docs", "manual_en.txt"), ("version.py",)):
        text = _read(*parts).lower()
        for word in words:
            assert word not in text, f"{'/'.join(parts)} に {word} がある"
    print("PASS: 配布する文書に個人の情報が無い")


def test_readme_lists_what_the_zip_contains():
    """はじめにお読みくださいの「入っているもの」と、配布物の並びが同じこと。"""
    import build_release
    readme = _read("docs", "readme.txt")
    files, apps = len(build_release.TOP_FILES), len(build_release.TOP_APPS)
    assert f"ファイルが {files} つと、アプリが {apps} つ" in readme, readme[:200]
    english = _read("docs", "readme_en.txt")
    assert f"{files} files and {apps} app" in english, english[:200]
    for name in build_release.TOP_FILES + build_release.TOP_APPS:
        assert name in readme, f"はじめにお読みくださいに {name} が無い"
        assert name in english, f"README に {name} が無い"
    assert build_release.APP_NAME == version.APP_NAME
    assert build_release.FOLDER == f"PTBattery4Loader_{version.VERSION}"
    manual = _read("docs", "manual.html")
    assert f"{version.APP_NAME}.app" in manual
    assert 'PTBattery4Loader_<span class="version">' in manual
    # 許可の一覧に同じ名前が 2 つ並ぶときの案内
    assert "同じ名前が 2 つ並んでいるときは、両方のスイッチを入れます" in manual
    assert "If the same name appears twice in the list, turn both on" in _read(
        "docs", "manual_en.html")
    print("PASS: はじめにお読みくださいと配布物の並びが同じ")


def test_release_spec_is_separate_from_local_build():
    """配布版は ad-hoc 署名で、作者の手元の版とバンドル ID が違うこと。"""
    release = _read("tools", "release.spec")
    assert 'bundle_identifier="local.takahashi.battery4loader"' in release
    assert "codesign_identity=None" in release
    assert "upx=False" in release and "upx=True" not in release
    # 動く環境の説明（macOS 14 以降・Apple シリコン）と spec を合わせる
    assert '"LSMinimumSystemVersion": "14.0"' in release
    assert 'target_arch="arm64"' in release
    manual = _read("docs", "manual.html")
    assert "macOS 14 以降" in manual and "Apple シリコン" in manual
    print("PASS: 配布版の spec は手元版と分かれている")


def _headings(html_text):
    """見出しの (階層, 飛び先の id) の並び。id の無い見出しは空文字。"""
    found = []
    for level, attrs in re.findall(r"<h([1-4])\b([^>]*)>", html_text):
        anchor = re.search(r'id="(\w+)"', attrs)
        found.append((level, anchor.group(1) if anchor else ""))
    return found


def test_headings_reads_the_ids():
    """見出しの読み取りが id の違いを見分けられること（数だけ比べていたことがある）。"""
    assert _headings('<h2 id="load">x</h2><h3>y</h3>') == [("2", "load"), ("3", "")]
    assert _headings('<h2 id="load">x</h2>') != _headings('<h2 id="other">x</h2>')
    assert _headings("<head><h1>x</h1>") == [("1", "")]
    print("PASS: 見出しの id を読める")


def test_manuals_have_the_same_chapters():
    """日本語と英語のマニュアルは、章と飛び先の id が同じこと（片方だけ直さない）。"""
    ja = _headings(_read("docs", "manual.html"))
    en = _headings(_read("docs", "manual_en.html"))
    assert ja == en, "章や見出しの並びが違う"
    assert sum(1 for _level, anchor in ja if anchor) > 20, "飛び先の id を読めていない"
    for name in ("manual.html", "manual_en.html"):
        version_found = re.findall(r'<span class="version">([^<]*)</span>',
                                   _read("docs", name))
        assert set(version_found) == {version.VERSION}, name
    print(f"PASS: 2 つのマニュアルの見出しが同じ（{len(ja)} 個）")


def test_permission_texts_in_both_languages():
    """許可の説明の日本語があり、既定（英語）と同じ項目が揃うこと。"""
    release = _read("tools", "release.spec")
    ja = _read("resources", "ja.lproj", "InfoPlist.strings")
    assert '"CFBundleDevelopmentRegion": "en"' in release
    for key in re.findall(r'"(NS\w+UsageDescription)":', release):
        assert f'"{key}"' in ja, f"日本語の説明に {key} が無い"
    assert _read("resources", "en.lproj", "InfoPlist.strings")
    print("PASS: 許可の説明は日本語と英語で揃っている")


def run_all():
    tests = [
        test_headings_reads_the_ids,
        test_manuals_have_the_same_chapters,
        test_permission_texts_in_both_languages,
        test_license_names_takahashi_and_ai,
        test_documents_say_ai_and_version,
        test_documents_have_no_personal_info,
        test_readme_lists_what_the_zip_contains,
        test_release_spec_is_separate_from_local_build,
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
