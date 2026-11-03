"""表示の言語（日本語・英語）。**Mac の言語の設定に合わせる。**

海外の利用者向けに、画面・メニュー・読み上げを英語でも出す。言語は起動したときに
1 回だけ決める（途中で変えるなら、アプリを開き直す）。

### 決め方

1. 環境変数 `BATTERY4_LANG` が `ja` か `en` なら、それ（テストと、試しに切り替えるとき用）
2. Mac の「優先する言語」（`NSLocale.preferredLanguages`）を上から見て、
   **日本語と英語のうち先に出てくるほう**（配布版は、システム設定の「言語と地域」→
   「アプリケーション」で、このアプリだけ言語を変えたときもこれに入る）
3. どちらも無ければ英語（日本語以外の Mac は英語で出す）

**配布版の `CFBundleDevelopmentRegion` を en にしてあるのは 3 と揃えるため。** ずれると、
日本語も英語も無い Mac で、画面は英語なのに許可の説明が日本語になる。

### 書き方

文言は**日本語と英語を並べて書く**（`tr("日本語", "English")`）。f 文字列も
そのまま渡せる。訳を別の表に分けないのは、組み立てる文が多く、並べたほうが
取り違えないため。

日本語は文を詰めて並べるが、英語は間に空白が要る。文をつなぐ箇所では、英語の側の
文末か文頭に空白を書く（テスト `tests/test_i18n.py` が「done.Next」の形を探す）。

このモジュールは GUI を読まない（core.py からも読むため）。Foundation は
無い環境でも動くよう、ガードして読む。
"""

from __future__ import annotations

import os

ENV_NAME = "BATTERY4_LANG"
SUPPORTED = ("ja", "en")


def preferred_languages() -> list[str]:
    """Mac の「優先する言語」（例: ["ja-JP", "en-JP"]）。読めなければ環境変数 LANG。"""
    try:
        from Foundation import NSLocale

        langs = [str(x) for x in (NSLocale.preferredLanguages() or [])]
        if langs:
            return langs
    except Exception:
        pass
    env = os.environ.get("LANG", "")
    return [env] if env else []


def pick_language(langs: list[str]) -> str:
    """並びの上から、日本語か英語の先に出てくるほう。どちらも無ければ英語。"""
    for lang in langs:
        head = lang.strip().lower().replace("_", "-").split("-")[0]
        if head in SUPPORTED:
            return head
    return "en"


def detect_language() -> str:
    forced = os.environ.get(ENV_NAME, "").strip().lower()
    if forced in SUPPORTED:
        return forced
    return pick_language(preferred_languages())


LANG = detect_language()


def is_english() -> bool:
    return LANG == "en"


def tr(ja, en):
    """いまの言語の文言（文言以外の組にも使える）。"""
    return en if LANG == "en" else ja


# 項目を並べるときの区切り
COMMA = tr("、", ", ")


def plural(count, word: str) -> str:
    """英語の数え方（"1 sample"・"3 samples"）。日本語の文では使わない。"""
    return f"{count} {word}" if count == 1 else f"{count} {word}s"
