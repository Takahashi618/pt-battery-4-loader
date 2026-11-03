"""
name_readout.py
===============
ファイル名を VoiceOver / スクリーンリーダーが読み上げやすい
日本語テキストに変換するドメインモジュール。

tkinter・pyautogui・afplay など環境依存物に一切依存しない純粋関数のみ。
Windows 上でユニットテストを完結できる。

変換モード:
  "off"   : 変換しない（元のファイル名をそのまま返す）
  "plain" : 案A。記号整形のみ。
            拡張子を除去し、区切り文字（_ - .）を空白に変換、連続空白を1つに。
            例) "01_Kick_808.wav" -> "01 Kick 808"
  "kana"  : 案B。plain に加えて数字を1桁ずつ読み仮名（カタカナ）に変換。
            ドラム名は型番（808/909 等）が多いため、数値ではなく
            1桁ずつ読む。
            例) "01_Kick_808.wav" -> "マルイチ Kick ハチマルハチ"

設計メモ:
  - 数字以外（英字・かな・漢字）はそのまま残す。VoiceOver 自身が
    英単語を読めるため、無理にローマ字をカナ化しない（誤読を避ける）。
  - 1桁ずつ読むことで "808"=ハチマルハチ、"120"=イチニーゼロ のように
    型番・連番に強い読みになる。
"""
from __future__ import annotations

import os
import re
import unicodedata

from i18n import is_english, tr

# 0-9 を1桁ずつ読むときのカタカナ。
# 0 はリズム機材の型番慣習（808=ハチマルハチ）に合わせ口語の「マル」を採用。
# 厳密な「ゼロ」にしたい場合は "0" の値を "ゼロ" に変更する。
# 「2」は標準の「ニ」。聞き取りやすさ重視なら "ニー" も可。
DIGIT_KANA = {
    "0": "マル",
    "1": "イチ",
    "2": "ニ",
    "3": "サン",
    "4": "ヨン",
    "5": "ゴ",
    "6": "ロク",
    "7": "ナナ",
    "8": "ハチ",
    "9": "キュウ",
}

# 英語で 1 桁ずつ読むときの言葉。型番の読み方（808 = eight oh eight）に合わせて 0 は oh
DIGIT_WORDS = {
    "0": "oh",
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
}

# 区切り文字として空白に変換する文字
_SEP_CHARS = r"[_\-.]+"

# 有効なモード
VALID_MODES = ("off", "plain", "kana")


def _strip_extension(filename: str) -> str:
    """拡張子を除去する。ディレクトリ部分は呼び出し側で除去済みを想定。"""
    stem, _ext = os.path.splitext(filename)
    return stem


def _normalize_separators(text: str) -> str:
    """区切り文字（_ - .）を空白へ変換し、連続空白を1つにまとめる。"""
    text = re.sub(_SEP_CHARS, " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _digits_to_kana(text: str) -> str:
    """
    連続する数字を1桁ずつカタカナに変換する。数字以外はそのまま残す。

    例) "808" -> "ハチマルハチ"（0 は口語の「マル」）
        "120" -> "イチニマル"

    全角の数字（１２０）も同じ読みにする。以前は str.isdigit() で数字かを
    見てから半角の表を引いていたため、全角数字を含むファイル名で
    KeyError になり、数字カナのときサンプル一覧が作り直せなかった。
    """
    if is_english():
        return _digits_to_words(text)
    out = []
    for ch in text:
        kana = DIGIT_KANA.get(unicodedata.normalize("NFKC", ch))
        out.append(kana if kana is not None else ch)
    return "".join(out)


def _digits_to_words(text: str) -> str:
    """連続する数字を 1 桁ずつ英語の言葉にする（"808" -> "eight oh eight"）。

    日本語と違って語の間に空白が要るので、前後の文字とも空白で分ける。
    """
    out = []
    for ch in text:
        word = DIGIT_WORDS.get(unicodedata.normalize("NFKC", ch))
        if word is None:
            out.append(ch)
            continue
        if out and out[-1] != " ":
            out.append(" ")
        out.append(word)
        out.append(" ")
    return re.sub(r" +", " ", "".join(out)).strip()


def readout_name(path_or_name: str, mode: str = "plain") -> str:
    """
    ファイルパスまたはファイル名を、読み上げ用テキストに変換する。

    Args:
        path_or_name: フルパスでもファイル名単体でも可。
        mode: "off" / "plain" / "kana"。未知の値は "plain" 扱い。

    Returns:
        読み上げ用に整形した文字列。

    例:
        readout_name("/x/01_Kick_808.wav", "off")   -> "01_Kick_808.wav"
        readout_name("/x/01_Kick_808.wav", "plain") -> "01 Kick 808"
        readout_name("/x/01_Kick_808.wav", "kana")  -> "マルイチ Kick ハチマルハチ"
    """
    name = os.path.basename(path_or_name)

    if mode == "off":
        return name
    if mode not in VALID_MODES:
        mode = "plain"

    stem = _strip_extension(name)
    plain = _normalize_separators(stem)

    if mode == "plain":
        return plain

    # mode == "kana": 数字部分のみカナ化（英単語はそのまま）
    return _digits_to_kana(plain)


def cycle_mode(current: str) -> str:
    """設定モードを off -> plain -> kana -> off の順に巡回させる。"""
    order = list(VALID_MODES)
    try:
        idx = order.index(current)
    except ValueError:
        idx = 0
    return order[(idx + 1) % len(order)]


def mode_label(mode: str) -> str:
    """モードの日本語表示名（ステータス読み上げ用）。"""
    return {
        "off":   tr("読み上げ補正：オフ", "Name reading: off"),
        "plain": tr("読み上げ補正：記号整形", "Name reading: symbols cleaned up"),
        "kana":  tr("読み上げ補正：数字カナ", "Name reading: digits as words"),
    }.get(mode, tr("読み上げ補正：記号整形", "Name reading: symbols cleaned up"))
