"""
Battery 4 Sample Loader — コアロジック（UI非依存）
===================================================

tkinter / Cocoa など特定の GUI ツールキットに依存しない純粋なロジックを集約する。
スキャン・ソート・フィルター・ドラッグ自動化・キット管理・永続化・VoiceOver通知など、
GUI 層から呼び出される処理をここに置く。

設計方針:
- この層は UI を一切 import しない（tkinter / AppKit を参照しない）。
- 状態を持たない純粋関数を基本とし、状態が必要な処理は引数で受け取る。
- macOS 固有の処理（osascript / afplay / mdfind）はこの層に閉じ込める。
"""

from __future__ import annotations

import os
import re
import sys
import json
import time
import shutil
import random
import hashlib
import datetime
import subprocess
from pathlib import Path
from typing import Iterable, Sequence

import grid_calibration
import version
from i18n import COMMA, plural, tr


# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------
SUPPORTED_EXT: set[str] = {".wav", ".aif", ".aiff", ".rex", ".rx2"}

SETTINGS_FILE: str = os.path.expanduser("~/.battery4_loader_settings.json")
LOADER_KITS_FILE: str = os.path.expanduser("~/.battery4_loader_kits.json")
BATTERY4_KITS_DIR: str = os.path.expanduser(
    "~/Documents/Native Instruments/Battery 4/Kits"
)
RECENT_MAX: int = 20

# セル名は Battery 4 の実表記（行＝A〜D、列＝1〜12）。A1 の右隣は A2。
GRID_ROWS: int = grid_calibration.DEFAULT_ROWS
GRID_COLS: int = grid_calibration.DEFAULT_COLS
CELL_NAMES: list[str] = grid_calibration.cell_names(GRID_ROWS, GRID_COLS)

# セル位置は「プラグイン描画領域からの比率」で持つ（絶対座標ではない）。
# 初期値は実機実測。
DEFAULT_CELL_LAYOUT: dict = dict(grid_calibration.DEFAULT_LAYOUT)

# 設定ファイルの形式版。1 = 旧12セル・絶対座標、2 = 比率レイアウト
SCHEMA_VERSION: int = 2

# プラグインウィンドウを探すホストアプリ名
PLUGIN_HOST_APPS: tuple[str, ...] = ("Pro Tools",)
# 対象プラグイン名（Plugin Selector の値がこれで始まるもの）
TARGET_PLUGIN_PREFIX: str = "Battery"

# ロード先の特別な指定。開いているすべての Battery の同じセルへ配る。
# トラック名と衝突しないよう、Pro Tools が付けない形の文字列にしている。
TARGET_ALL: str = "__all__"
TARGET_ALL_LABEL: str = tr("すべての Battery", "All Batteries")


def target_label(track: str, mode: str = "full") -> str:
    """ロード先の読み上げ用の表示名（純粋関数）。

    mode が "short" なら、トラック名も日本語の短い形にする
    （「インスト1」「自動」「すべて」）。Cmd+B で何度も聞くので、
    短く言い切れることを優先する。
    """
    short = mode == "short"
    if track == TARGET_ALL:
        return tr("すべて", "All") if short else TARGET_ALL_LABEL
    if not track:
        return tr("自動", "Auto")
    return short_track_name(track) if short else track

# システムサウンド
SND_OK = "/System/Library/Sounds/Tink.aiff"
SND_ERROR = "/System/Library/Sounds/Basso.aiff"
SND_TICK = "/System/Library/Sounds/Pop.aiff"
SND_READY = "/System/Library/Sounds/Glass.aiff"

# ソート種別: (key, 表示ラベル)
SORT_OPTIONS: list[tuple[str, str]] = [
    ("name_asc", tr("名前 昇順", "Name ascending")),
    ("name_desc", tr("名前 降順", "Name descending")),
    ("ext", tr("形式（拡張子）", "Format (extension)")),
    ("size_asc", tr("サイズ 小さい順", "Size, smallest first")),
    ("size_desc", tr("サイズ 大きい順", "Size, largest first")),
    ("date_new", tr("日時 新しい順", "Date, newest first")),
    ("date_old", tr("日時 古い順", "Date, oldest first")),
    ("folder", tr("フォルダ別", "By folder")),
    ("dur_short", tr("長さ 短い順", "Length, shortest first")),
    ("dur_long", tr("長さ 長い順", "Length, longest first")),
    ("fav_first", tr("お気に入り優先", "Favorites first")),
    ("recent", tr("最近使用", "Recently used")),
    ("random", tr("ランダム", "Random")),
]


# ---------------------------------------------------------------------------
# 永続化（設定 / キット）
# ---------------------------------------------------------------------------
def default_settings() -> dict:
    """設定ファイルが無い場合のデフォルト設定を返す。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "search_folders": [],
        # セル位置は描画領域からの比率で持つ（絶対座標ではない）
        "cell_layout": dict(DEFAULT_CELL_LAYOUT),
        "cell_labels": {c: "" for c in CELL_NAMES},
        # ロード先の Battery（Pro Tools のトラック名）。空なら自動選択
        "target_track": "",
        "favorites": [],
        "recent": [],
        "countdown_sec": 3,
        "auto_advance_cell": True,
        # ファイル名読み上げ補正: off / plain / kana
        "readout_mode": "plain",
        # 前回の並び替えキー（SORT_OPTIONS の key）
        "sort_key": "name_asc",
        # 長さがこれ以下ならワンショットと見なす（秒）
        "oneshot_max_sec": DEFAULT_ONESHOT_MAX_SEC,
        # パックの並び順（count / name / mtime / added）。
        # **既定は中身の多い順。** 実測で 553 パック中 389 個が 1 件だけ
        # だったので、名前順だと目当ての大きなパックが埋もれる。
        # 作者の別のアプリと揃えてある（2026-09-05）。
        "pack_order": PACK_ORDER_COUNT,
        # ロード先の読み上げ方（full = トラック名とプラグイン名 / track = トラック名のみ）
        "target_readout": READOUT_FULL,
    }


def migrate_settings(settings: dict) -> tuple[dict, bool]:
    """旧形式の設定を現行形式へ移し替える。

    旧形式（schema_version が無い）は「列＝A〜D、行＝1〜3」の12セルと
    絶対座標 cell_coords を持っていた。セル名は物理位置を保って変換し、
    絶対座標は捨てて比率レイアウトの初期値を入れる（旧座標は描画領域が
    分からないため比率に直せない。実測との差も大きく、そのまま使えない）。

    Returns:
        (移し替えた設定, 変更したか)
    """
    changed = False
    # 版の判定は、既定値で穴埋めする前に行う
    # （先に埋めると schema_version が入って旧形式を見落とす）
    version = int(settings.get("schema_version", 1))

    if version < SCHEMA_VERSION:
        settings["cell_labels"] = grid_calibration.migrate_legacy_map(
            settings.get("cell_labels", {}) or {}
        )
        settings["cell_layout"] = dict(DEFAULT_CELL_LAYOUT)
        settings["schema_version"] = SCHEMA_VERSION
        changed = True

    # 版に関わらず、絶対座標が残っていれば捨てる（使わない上に誤解を招く）
    if "cell_coords" in settings:
        settings.pop("cell_coords", None)
        changed = True

    for k, v in default_settings().items():
        if k not in settings:
            settings[k] = v
            changed = True

    # 表示するセルの集合が変わっていたらラベルを揃える
    names = grid_calibration.layout_cell_names(settings.get("cell_layout"))
    labels = settings.get("cell_labels") or {}
    fixed = {c: labels.get(c, "") for c in names}
    if fixed != labels:
        settings["cell_labels"] = fixed
        changed = True
    return settings, changed


def migrate_kits(kits: dict) -> tuple[dict, bool]:
    """旧形式のキット定義を現行形式へ移し替える。

    旧: {"kit名": {"A1": path, ...}}          （セル名が旧表記）
    新: {"kit名": {"cells": {...}, "track": ""}}  （ロード先トラックを持つ）
    """
    changed = False
    out: dict = {}
    for name, body in (kits or {}).items():
        if isinstance(body, dict) and "cells" in body:
            cells = dict(body.get("cells") or {})
            track = body.get("track", "") or ""
        else:
            cells = grid_calibration.migrate_legacy_map(dict(body or {}))
            track = ""
            changed = True
        out[name] = {"cells": cells, "track": track}
    return out, changed


def kit_cells(kit: dict) -> dict:
    """キット定義からセルマップを取り出す（新旧どちらの形でも）。"""
    if isinstance(kit, dict) and "cells" in kit:
        return dict(kit.get("cells") or {})
    return dict(kit or {})


def kit_track(kit: dict) -> str:
    """キット定義からロード先トラック名を取り出す（無ければ空文字）。"""
    if isinstance(kit, dict):
        return kit.get("track", "") or ""
    return ""


def load_settings() -> dict:
    """設定 JSON を読み込む。壊れている / 無い場合はデフォルトを返す。

    旧形式であれば現行形式へ移し替えて返す（保存は呼び出し側の責任）。
    """
    p = Path(SETTINGS_FILE)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            settings, _ = migrate_settings(data)
            return settings
        except Exception:
            pass
    return default_settings()


def save_settings(settings: dict) -> None:
    """設定 JSON を書き込む。"""
    Path(SETTINGS_FILE).write_text(
        json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def load_kits() -> dict:
    """キット定義 JSON を読み込む。無い場合は空 dict。

    旧形式（セル名が旧表記・トラック指定なし）なら移し替えて返す。
    """
    p = Path(LOADER_KITS_FILE)
    if p.exists():
        try:
            kits, _ = migrate_kits(json.loads(p.read_text(encoding="utf-8")))
            return kits
        except Exception:
            pass
    return {}


def save_kits(kits: dict) -> None:
    """キット定義 JSON を書き込む。"""
    Path(LOADER_KITS_FILE).write_text(
        json.dumps(kits, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# サウンド / 通知
# ---------------------------------------------------------------------------
def silent() -> bool:
    """音と読み上げを止めるか（環境変数 BATTERY4_SILENT）。

    ビルドの起動確認やマニュアルの生成でアプリを組み立てるとき、
    利用者の作業中に音や VoiceOver の読み上げが割り込まないようにする。
    クリップボードにも触らない（copy_to_clipboard）。
    """
    return bool(os.environ.get("BATTERY4_SILENT"))


def copy_to_clipboard(text: str) -> None:
    """クリップボードへ写す。写せなければ例外。

    **音と読み上げを止めているとき（テストとビルド）は何もしない。**
    ホームを一時フォルダにしても、クリップボードは本物のままなので、
    ロードの失敗の報告を通すと、利用者がコピーした内容が消える
    （2026-11-03 に、英語の検査がビルドのたびに実際に消していた）。
    """
    if silent():
        return
    process = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
    process.communicate(text.encode("utf-8"))


def beep(sound: str = SND_OK) -> None:
    """afplay でシステムサウンドを非ブロッキング再生する。"""
    if silent():
        return
    try:
        subprocess.Popen(
            ["afplay", sound],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# AppleScript ユーティリティ / VoiceOver
# ---------------------------------------------------------------------------
def _escape_applescript(text: str | None) -> str:
    """
    AppleScript の文字列リテラルに安全に埋め込めるようエスケープする。

    二重引用符文字列では、バックスラッシュと二重引用符を
    バックスラッシュでエスケープする必要がある。エスケープ漏れがあると、
    ファイル名に " を含む場合（例: 'Kick "Hard".wav'）に文字列が途中で
    閉じてスクリプトが構文エラーになる。

    改行・タブなどの制御文字は読み上げに不要かつスクリプトを壊しうるため
    空白に正規化する。
    """
    if text is None:
        return ""
    # 制御文字（改行・タブ等）を空白へ
    text = "".join(ch if ch >= " " else " " for ch in text)
    # バックスラッシュを先にエスケープ（順序が重要）
    text = text.replace("\\", "\\\\")
    text = text.replace('"', '\\"')
    return text


def vo_announce(message: str, high_priority: bool = False) -> None:
    """
    VoiceOver にアナウンスを送る（AXAnnouncement 属性、osascript 経由）。
    ツールキット非依存のフォールバック。Cocoa 層では app_main.announce()
    （NSAccessibilityPostNotification）を使う。

    **macOS 15 ではこの方法は -10006 で断られ、何も読まれない**
    。pyobjc が使えないときの最後の控え。
    """
    if silent():
        return
    try:
        safe = _escape_applescript(message)
        script = (
            'tell application "System Events"\n'
            f'    set announcement to "{safe}"\n'
            "    tell (first application process whose frontmost is true)\n"
            '        set value of attribute "AXAnnouncement" to announcement\n'
            "    end tell\n"
            "end tell"
        )
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# サンプル検索（スキャン）
# ---------------------------------------------------------------------------
def is_sample_name(name: str) -> bool:
    """スキャンで拾うファイル名か（純粋関数）。

    対応する拡張子で、**先頭がドットでないもの。** exFAT など Mac 以外の
    形式の外付けドライブでは、macOS が `._kick.wav` のような
    リソースフォーク（AppleDouble）を横に作る。拡張子は .wav でも中身は
    音ではないので、拾うと同じ名前が二重に並び、ロードもできない。
    作者の別のアプリと同じ規則（2 つのアプリで
    同じサンプルが同じ件数で見えるように）。
    """
    if not name or name.startswith("."):
        return False
    return os.path.splitext(name)[1].lower() in SUPPORTED_EXT


def find_samples_walk(folders: Iterable[str]) -> list[str]:
    """登録フォルダを再帰スキャンし、対応拡張子のパスを名前昇順で返す。"""
    results: list[str] = []
    for folder in folders:
        for root, _dirs, files in os.walk(folder):
            for f in files:
                if is_sample_name(f):
                    results.append(os.path.join(root, f))
    return sorted(results)


def find_samples_spotlight() -> list[str]:
    """フォルダ未登録時に Spotlight(mdfind) で Mac 全体から音声ファイルを検索する。"""
    query = 'kMDItemContentTypeTree == "public.audio"'
    try:
        out = subprocess.check_output(["mdfind", query], text=True, timeout=15)
        return sorted(
            {
                line
                for line in out.strip().splitlines()
                if is_sample_name(os.path.basename(line))
            }
        )
    except Exception:
        return []


# ---------------------------------------------------------------------------
# ソート / フィルター（純粋関数）
# ---------------------------------------------------------------------------
def _cached_stat(path: str, cache: dict | None = None):
    """os.stat の結果を返す（失敗時 None）。

    cache（{path: os.stat_result | False}）を渡すと結果を再利用する。
    サイズ／日時ソートはフィルター1文字ごとに全件へ stat を発行するため、
    大量サンプル（外付けSSD 上など）ではキャッシュが無いと
    キーストロークごとにメインスレッドが止まる。
    """
    if cache is None:
        try:
            return os.stat(path)
        except OSError:
            return None
    st = cache.get(path)
    if st is None:
        try:
            st = os.stat(path)
        except OSError:
            st = False  # 取得失敗も覚える（毎回失敗し直さない）
        cache[path] = st
    return st if st else None


def _safe_stat(path: str, attr: str, cache: dict | None = None) -> float:
    """os.stat の属性を安全に取得する（失敗時 0）。"""
    st = _cached_stat(path, cache)
    return getattr(st, attr) if st is not None else 0


def sort_samples(
    paths: Sequence[str],
    key: str,
    favorites: set[str] | None = None,
    recent: Sequence[str] | None = None,
    stat_cache: dict | None = None,
    random_order: dict | None = None,
    durations: dict | None = None,
) -> list[str]:
    """
    ソートキーに応じてパスリストを並び替えて返す純粋関数。

    Args:
        paths: 対象パス列
        key: SORT_OPTIONS の key
        favorites: お気に入りパスの集合（fav_first で使用）
        recent: 最近使用パス列（左が最新。recent で使用）
        stat_cache: サイズ／日時ソート用の stat 結果キャッシュ（省略可）
        random_order: ランダム順を安定させる {path: 順位}（省略時は毎回シャッフル。
            渡すとフィルター中も並びが変わらず、選択維持と読み上げが安定する）
    """
    favorites = favorites or set()
    recent = list(recent or [])

    def basename_key(p: str) -> str:
        return os.path.basename(p).lower()

    if key == "name_asc":
        return sorted(paths, key=basename_key)
    if key == "name_desc":
        return sorted(paths, key=basename_key, reverse=True)
    if key == "ext":
        return sorted(paths, key=lambda p: (os.path.splitext(p)[1].lower(), basename_key(p)))
    if key == "size_asc":
        return sorted(paths, key=lambda p: _safe_stat(p, "st_size", stat_cache))
    if key == "size_desc":
        return sorted(paths, key=lambda p: _safe_stat(p, "st_size", stat_cache), reverse=True)
    if key == "date_new":
        return sorted(paths, key=lambda p: _safe_stat(p, "st_mtime", stat_cache), reverse=True)
    if key == "date_old":
        return sorted(paths, key=lambda p: _safe_stat(p, "st_mtime", stat_cache))
    if key == "folder":
        return sorted(paths, key=lambda p: (os.path.dirname(p).lower(), basename_key(p)))
    if key in ("dur_short", "dur_long"):
        durs = durations or {}
        # 長さが分からないものは末尾へ（短い順でも長い順でも最後）
        def dur_key(p):
            d = durs.get(p)
            return (1, 0.0, basename_key(p)) if d is None else (0, d, basename_key(p))
        if key == "dur_short":
            return sorted(paths, key=dur_key)
        known = [p for p in paths if durs.get(p) is not None]
        unknown = [p for p in paths if durs.get(p) is None]
        known.sort(key=lambda p: (-durs[p], basename_key(p)))
        unknown.sort(key=basename_key)
        return known + unknown
    if key == "fav_first":
        return sorted(paths, key=lambda p: (0 if p in favorites else 1, basename_key(p)))
    if key == "recent":
        recent_order = {p: i for i, p in enumerate(recent)}
        big = len(recent_order)
        return sorted(paths, key=lambda p: (recent_order.get(p, big), basename_key(p)))
    if key == "random":
        if random_order is not None:
            big = len(random_order)
            return sorted(paths, key=lambda p: (random_order.get(p, big), basename_key(p)))
        result = list(paths)
        random.shuffle(result)
        return result
    return list(paths)


# ---------------------------------------------------------------------------
# 絞り込み（作者の別のアプリと同じ規則）
# ---------------------------------------------------------------------------
# 判定規則は作者の別のアプリに合わせてある。同じサンプルを
# 2つのアプリで同じ言葉で絞り込めるようにするため、ヒント語や
# パック名の取り出し方を勝手に変えないこと。
#
# そのアプリとの意図的な違い:
#   - 向こうは Return で確定、こちらは1文字ごとの即時絞り込み（確定仕様）
#   - 向こうは SQLite、こちらはパスの配列を毎回絞る（DB を持たない）
#   - 種別・チャンネル数などのうち、パスから分からないもの
#     （カテゴリ・タグ・チャンネル数）は扱わない

# ファイル名からループ素材を推定するヒント語（作者の別のアプリと同一）
LOOP_HINTS: tuple[str, ...] = ("loop", "ループ", "_lp", "groove", "beat")

# Splice のライブラリは packs/<パック名>/… という構造になっている
PACKS_DIR: str = "packs"

KIND_ONESHOT: str = "oneshot"
KIND_LOOP: str = "loop"

# 種別の絞り込み候補: (値, 表示ラベル)。空文字は「条件なし」
KIND_OPTIONS: list[tuple[str, str]] = [
    ("", tr("種別 すべて", "Type: all")),
    (KIND_ONESHOT, tr("ワンショット", "One-shot")),
    (KIND_LOOP, tr("ループ", "Loop")),
]

# 長さから種別を判断する境目（秒）。これ以下ならワンショット。
# 実測（Splice 3119件）では、ファイル名だけの判定でワンショットとされた
# 2241 件のうち 1270 件が 3 秒以上あり、当てにならなかった。
# ドラムの単発音はおおむね 2 秒に収まるのでここを初期値にする。
# 設定 oneshot_max_sec で変えられる。
DEFAULT_ONESHOT_MAX_SEC: float = 2.0

# パックの並び順: (値, 表示ラベル)
#
# **作者の別のアプリと同じ 4 つ・同じ順・同じ言葉にしてある**（2026-09-05）。
# 3 つのアプリを持ち替えるので、Cmd+Shift+G を押したときに同じ順で
# 同じ言葉が返らないと、そのつど覚え直すことになる。
#
# 名前に**向きを入れてある。** 「日時順」だけでは新旧どちらが上か
# 分からず、読み上げで聞いても確かめようがない。
PACK_ORDER_NAME: str = "name"
PACK_ORDER_COUNT: str = "count"
PACK_ORDER_MTIME: str = "mtime"
PACK_ORDER_ADDED: str = "added"
PACK_ORDER_OPTIONS: list[tuple[str, str]] = [
    (PACK_ORDER_COUNT, tr("中身の多い順", "Most samples first")),
    (PACK_ORDER_NAME, tr("名前順", "By name")),
    (PACK_ORDER_MTIME, tr("更新の新しい順", "Recently updated first")),
    (PACK_ORDER_ADDED, tr("追加した順。古いものから", "Order added, oldest first")),
]


def guess_kind(path: str) -> str:
    """ファイル名から種別（ワンショット / ループ）を推定する純粋関数。

    作者の別のアプリと同じ規則。ファイル名だけを見る
    （フォルダ名は見ない）。**ファイル名だけでは当てにならない**ので、
    長さが分かるときは resolve_kind() を使うこと。
    """
    lower = os.path.basename(path).lower()
    if any(hint in lower for hint in LOOP_HINTS):
        return KIND_LOOP
    return KIND_ONESHOT


def read_duration(path: str) -> float | None:
    """音声ファイルの長さ（秒）を返す。分からなければ None。

    ヘッダだけを読むので速い（実測 3119 件で 0.6 秒 = 1件 0.2ms）。
    標準の wave モジュールで読めない形式（Float32 WAV・AIFF など）は
    afinfo に落とす。afinfo は外部プロセスで遅いが、実測では全体の
    0.2% 程度しか該当しない。
    """
    try:
        import wave
        with wave.open(path, "rb") as w:
            rate = w.getframerate()
            if rate:
                return w.getnframes() / float(rate)
    except Exception:
        pass
    text = afinfo_report(path)
    if text:
        return parse_duration(text)
    return None


def parse_duration(afinfo_text: str) -> float | None:
    """afinfo の出力から長さ（秒）を取り出す純粋関数。"""
    m = re.search(r"estimated duration:\s*([0-9.]+)\s*sec", afinfo_text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def read_durations(paths: Sequence[str], cache: dict | None = None) -> dict:
    """複数ファイルの長さをまとめて取得する（{path: 秒 or None}）。

    cache を渡すと既に調べたものを再利用する。スキャン時にワーカー
    スレッドから呼ぶことを想定（メインスレッドを止めない）。

    **読めなかったもの（None）は覚えない。** ダウンロード途中の
    ファイルは一度失敗するが、次のスキャンでは読めるようになる。
    覚えてしまうと再スキャンしても「長さ不明」のままで、種別の判定も
    ファイル名頼みに戻ってしまう。読めない形式は実測で全体の 0.2%
    程度しかないので、毎回読み直しても負担にならない。
    """
    out = cache if cache is not None else {}
    for p in paths:
        if out.get(p) is None:
            out[p] = read_duration(p)
    return out


def kind_from_duration(
    duration: float | None, oneshot_max_sec: float = DEFAULT_ONESHOT_MAX_SEC
) -> str | None:
    """長さから種別を判断する純粋関数。判断できなければ None。"""
    if duration is None:
        return None
    return KIND_ONESHOT if duration <= oneshot_max_sec else KIND_LOOP


def resolve_kind(
    path: str,
    duration: float | None = None,
    oneshot_max_sec: float = DEFAULT_ONESHOT_MAX_SEC,
) -> str:
    """種別を決める。長さが分かればそれを優先する。

    規則:
      1. 長さが境目を超える → ループ（ファイル名が何であれ長い音）
      2. 長さが境目以下 → ファイル名にループのヒント語があればループ、
         無ければワンショット
      3. 長さが分からない → ファイル名から推定（guess_kind と同じ）

    1 があるので「ワンショットのつもりが長いサンプルだった」を防げる。
    2 でファイル名の情報も捨てない（短いドラムループなどを拾う）。
    """
    by_duration = kind_from_duration(duration, oneshot_max_sec)
    if by_duration == KIND_LOOP:
        return KIND_LOOP
    if by_duration == KIND_ONESHOT:
        return guess_kind(path)
    return guess_kind(path)


def format_duration(duration: float | None) -> str:
    """長さを読み上げやすいテキストにする（純粋関数）。

    VoiceOver が読むので記号を使わず「1.5秒」の形にする。
    1分以上は「1分30秒」。分からなければ「長さ不明」。

    **丸めてから分と秒に分ける。** 先に分を取ると 119.6 秒が「1分60秒」、
    59.96 秒が「60.0秒」になっていた。
    """
    if duration is None:
        return tr("長さ不明", "Length unknown")
    if round(duration, 1) < 60:
        return tr(f"{duration:.1f}秒", f"{duration:.1f} seconds")
    minutes, seconds = divmod(int(round(duration)), 60)
    return tr(f"{minutes}分{seconds}秒",
              f"{plural(minutes, 'minute')} {plural(seconds, 'second')}")


def extract_pack(path: str, root: str = "") -> str:
    """Splice のパス構造からパック名を取り出す純粋関数。

    `…/packs/<パック名>/…/音.wav` の <パック名> を返す。
    packs 階層が無ければ空文字（パックなし）。

    パック名の直下にあるファイル（`packs/SAUCE.WAV` のような
    パック階層を持たないもの）は空文字になる。
    root を渡すと、そこからの相対パスだけを見る（フォルダ名に
    たまたま packs があるときの誤検出を防ぐ）。
    """
    target = path
    if root:
        try:
            target = os.path.relpath(path, root)
        except ValueError:
            target = path
    parts = [p for p in target.split(os.sep) if p]
    for i, part in enumerate(parts):
        if part.lower() != PACKS_DIR:
            continue
        # packs の次が最後の要素（＝ファイル名）ならパック無し
        if i + 2 <= len(parts) - 1:
            return parts[i + 1]
        return ""
    return ""


def pack_counts(paths: Sequence[str], root: str = "") -> dict:
    """パック名ごとのサンプル数を数える純粋関数（パックなしは除く）。"""
    counts: dict[str, int] = {}
    for p in paths:
        name = extract_pack(p, root)
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def pack_times(
    paths: Sequence[str], root: str = "", stat_cache: dict | None = None
) -> tuple[dict, dict]:
    """パックごとの日時を 2 種類そろえる純粋関数。戻り値は (更新, 追加)。

    | どちら | 何を採るか | 何が分かるか |
    | --- | --- | --- |
    | 更新 | そのパックで**最も新しい**素材 | 最近さわったパック |
    | 追加 | そのパックで**最も古い**素材 | Splice に入れた順 |

    **2 つは意味が違う。** Splice は素材を 1 つずつ落とすので、
    買い足し続けているパックでは離れる。「新しい順」だけだと
    「入れた順に辿る」ができない。

    日時が取れないものは 0 として扱う（並べ替えでは末尾へ）。
    `stat_cache` は `sort_samples` と共有できる（1 文字ごとの絞り込みで
    全件に stat を発行しないため）。
    """
    newest: dict[str, float] = {}
    oldest: dict[str, float] = {}
    for p in paths:
        name = extract_pack(p, root)
        if not name:
            continue
        when = _safe_stat(p, "st_mtime", stat_cache)
        if when <= 0:
            continue
        if when > newest.get(name, 0.0):
            newest[name] = when
        if name not in oldest or when < oldest[name]:
            oldest[name] = when
    return newest, oldest


def list_packs(
    paths: Sequence[str], root: str = "", order: str = PACK_ORDER_COUNT,
    counts: dict | None = None, stat_cache: dict | None = None,
) -> list[str]:
    """絞り込み候補にするパック名を、重複なく並べて返す。

    order:
      PACK_ORDER_COUNT … 中身の多い順（同数なら名前順。よく使う大きな
                         パックが先頭に来るので、目当てが分からないときに探しやすい）
      PACK_ORDER_NAME  … 名前順（頭文字で目的のパックへ飛べる）
      PACK_ORDER_MTIME … 更新の新しい順（最近さわったパックから）
      PACK_ORDER_ADDED … 追加した順（古いものから。入れた順に辿れる）

    **同じ値のときは必ず名前順へ落とす。** 並びが毎回同じにならないと、
    覚えた位置が使えなくなる。

    counts: pack_counts() の結果を渡すと走査を省略する。
        呼び出し側が件数表示にも counts を使う場合、二重走査を避けられる
        （1文字ごとの絞り込みで毎回呼ばれるため）。
    stat_cache: 日時順のときだけ使う。sort_samples と共有できる。
    """
    if counts is None:
        counts = pack_counts(paths, root)
    if order == PACK_ORDER_NAME:
        return sorted(counts, key=str.lower)
    if order in (PACK_ORDER_MTIME, PACK_ORDER_ADDED):
        newest, oldest = pack_times(paths, root, stat_cache)
        if order == PACK_ORDER_MTIME:
            return sorted(
                counts, key=lambda n: (-newest.get(n, 0.0), n.lower())
            )
        # **古い順。** 日時が取れないものは末尾へ（先頭に来ると邪魔）。
        return sorted(
            counts,
            key=lambda n: (
                oldest.get(n, 0.0) <= 0,
                oldest.get(n, 0.0),
                n.lower(),
            ),
        )
    return sorted(counts, key=lambda n: (-counts[n], n.lower()))


def filter_samples(
    paths: Sequence[str],
    query: str = "",
    kind: str = "",
    pack: str = "",
    favorites: set[str] | None = None,
    favorite_only: bool = False,
    root: str = "",
    durations: dict | None = None,
    oneshot_max_sec: float = DEFAULT_ONESHOT_MAX_SEC,
    max_sec: float | None = None,
) -> list[str]:
    """AND 条件でサンプルを絞り込む純粋関数。

    作者の別のアプリと同じ考え方で、指定のない条件は
    無視し、指定された条件はすべて満たすものだけを返す。

    Args:
        paths: 対象パス列
        query: ファイル名の部分一致（大文字小文字無視）。
            向こうと同じく空白を含む1本の文字列として扱う（語の AND ではない）
        kind: KIND_ONESHOT / KIND_LOOP の完全一致（空なら条件なし）
        pack: パック名の完全一致（空なら条件なし）
        favorites: お気に入りパスの集合（favorite_only で使う）
        favorite_only: 真ならお気に入りだけ
        root: パック名を取り出すときの基準フォルダ
        durations: {path: 長さ秒 or None}。渡すと種別判定に長さを使う
        oneshot_max_sec: ワンショットと見なす長さの上限（秒）
        max_sec: 長さの上限で直接絞る（None なら条件なし。
            長さが分からないものは残す）
    """
    result = list(paths)
    if query:
        q = query.lower()
        result = [p for p in result if q in os.path.basename(p).lower()]
    if kind:
        durs = durations or {}
        result = [
            p for p in result
            if resolve_kind(p, durs.get(p), oneshot_max_sec) == kind
        ]
    if max_sec is not None:
        durs = durations or {}
        result = [
            p for p in result
            if durs.get(p) is None or durs[p] <= max_sec
        ]
    if pack:
        result = [p for p in result if extract_pack(p, root) == pack]
    if favorite_only:
        favs = favorites or set()
        result = [p for p in result if p in favs]
    return result


def search_result_summary(count: int) -> str:
    """絞り込み結果の件数を伝える読み上げ文言（作者の別のアプリと同じ文言）。"""
    if count == 0:
        return tr("該当するサンプルはありませんでした", "No samples found")
    return tr(f"{count}件見つかりました", f"{plural(count, 'sample')} found")


def format_list_label(
    path: str,
    key: str,
    favorites: set[str] | None = None,
    recent: Sequence[str] | None = None,
    name_transform=None,
    stat_cache: dict | None = None,
    durations: dict | None = None,
) -> str:
    """
    リスト表示文字列を VoiceOver が読みやすい形式で生成する純粋関数。

    記号（★ など）は VoiceOver が「スター」と読んで紛らわしいため使わず、
    「[お気に入り]」「[最近使用]」などのテキストラベルに統一する。
    ソートキーに応じて拡張子・サイズ・日時・フォルダ名を先頭に付与する。

    name_transform（path -> 表示名）を渡すと、タグはそのままに
    ファイル名部分だけを置き換える（読み上げ補正用。拡張子タグは
    変換前の実ファイル名から取る）。
    """
    favorites = favorites or set()
    recent = set(recent or [])
    fav = tr("[お気に入り] ", "[Favorite] ") if path in favorites else ""
    real_name = os.path.basename(path)
    name = name_transform(path) if name_transform else real_name

    if key == "ext":
        ext = os.path.splitext(real_name)[1].upper().lstrip(".")
        return f"{fav}[{ext}] {name}"

    if key in ("size_asc", "size_desc"):
        st = _cached_stat(path, stat_cache)
        if st is not None:
            size = st.st_size
            if size >= 1_048_576:
                size_str = f"{size / 1_048_576:.1f}MB"
            elif size >= 1024:
                size_str = f"{size / 1024:.0f}KB"
            else:
                size_str = f"{size}B"
        else:
            # 記号（?）だと読み上げで「疑問符」になる。長さ不明と同じ言い方にする
            size_str = tr("サイズ不明", "Size unknown")
        return f"{fav}[{size_str}] {name}"

    if key in ("date_new", "date_old"):
        st = _cached_stat(path, stat_cache)
        if st is not None:
            date_str = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%y/%m/%d")
        else:
            date_str = tr("日時不明", "Date unknown")
        return f"{fav}[{date_str}] {name}"

    if key == "folder":
        folder = os.path.basename(os.path.dirname(path))
        return f"{fav}[{folder[:12]}] {name}"

    if key in ("dur_short", "dur_long"):
        dur = (durations or {}).get(path)
        return f"{fav}[{format_duration(dur)}] {name}"

    if key == "recent":
        tag = tr("[最近使用] ", "[Recently used] ") if path in recent else ""
        return f"{fav}{tag}{name}"

    return f"{fav}{name}"


# ---------------------------------------------------------------------------
# Battery 4 向け音声ファイル検査・正規化
# ---------------------------------------------------------------------------
# Battery 4 の WAV/AIFF パーサーは、テンポマーカー等の追加チャンクや
# 特殊な内部形式を含むファイルの読み込みに失敗することがある
# （afplay や Pro Tools では正常に再生できるのに、セルに載らない）。
# 実例: Splice 配布の一部サンプルはテンポマーカー入り。
#
# 対策: ロード前に afinfo（macOS標準）で検査し、問題があれば
# afconvert（macOS標準）で無劣化のプレーンな PCM WAV に変換してから
# ドラッグする。変換結果は再利用できるようキャッシュする。
# 保存先は Application Support（Battery 側のキットが変換後パスを参照し
# 続けるため、OS が自動削除する Caches は使わない）。

CONVERTED_DIR: str = os.path.expanduser(
    "~/Library/Application Support/Battery4Loader/Converted"
)

# 正規化の対象となる拡張子（REX/RX2 はスライス情報が本体なので対象外）
NORMALIZE_EXT: set[str] = {".wav", ".aif", ".aiff"}


def afinfo_report(path: str) -> str | None:
    """afinfo の出力テキストを返す（失敗時 None）。"""
    try:
        r = subprocess.run(
            ["afinfo", path], capture_output=True, text=True, timeout=15
        )
        if r.returncode == 0 and r.stdout:
            return r.stdout
    except Exception:
        pass
    return None


def parse_audio_info(text: str) -> dict:
    """afinfo の出力から判定に必要な情報を取り出す純粋関数。

    Returns:
        {"ok": 解析成功, "is_lpcm": リニアPCMか, "is_float": 浮動小数点か,
         "bits": ビット深度 or None, "markers": マーカー数,
         "data_offset": データ開始オフセット or None}
    """
    info = {
        "ok": False,
        "is_lpcm": False,
        "is_float": False,
        "bits": None,
        "markers": 0,
        "data_offset": None,
    }
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("Data format:"):
            info["ok"] = True
            # afinfo は 24 ビットを「lpcm (0x0000000C) 24-bit …」、16 ビットと
            # 浮動小数点を「Int16, interleaved」「Float32」と書く（macOS 15.7 の実測）。
            # 後者を知らないと、ふつうの 16 ビットも「非 PCM」として変換していた
            short = re.search(r"\b(?:Int|Float)(\d+)\b", s)
            info["is_lpcm"] = " lpcm" in s or short is not None
            info["is_float"] = "float" in s.lower()
            m = re.search(r"(\d+)-bit", s) or short
            if m:
                info["bits"] = int(m.group(1))
        elif "audio data file offset" in s:
            m = re.search(r"(\d+)\s*$", s)
            if m:
                info["data_offset"] = int(m.group(1))
        else:
            m = re.match(r"^(\d+)\s+markers?;", s)
            if m:
                info["markers"] = int(m.group(1))
    return info


def normalization_reason(info: dict) -> str:
    """parse_audio_info の結果から、変換が必要な理由を返す純粋関数。
    変換不要なら空文字列。"""
    if not info.get("ok"):
        return ""
    if not info.get("is_lpcm"):
        return tr("非PCM形式のため", "Because it is not PCM")
    if info.get("is_float"):
        return tr("浮動小数点形式のため", "Because it is floating point")
    bits = info.get("bits")
    if bits and bits > 24:
        return tr(f"{bits}ビット形式のため", f"Because it is {bits}-bit")
    if info.get("markers", 0) > 0:
        return tr("埋め込みマーカーがあるため", "Because it has embedded markers")
    return ""


def needs_battery_normalization(path: str) -> tuple[bool, str]:
    """Battery 4 で読めない可能性が高いファイルか判定する。

    Returns:
        (変換すべきか, 理由テキスト)
    """
    if os.path.splitext(path)[1].lower() not in NORMALIZE_EXT:
        return False, ""
    text = afinfo_report(path)
    if not text:
        return False, ""  # 検査不能なら変換もできないので原本のまま
    reason = normalization_reason(parse_audio_info(text))
    return (reason != ""), reason


def converted_cache_name(path: str, mtime: float, size: int) -> str:
    """変換キャッシュの相対パス（純粋関数）。

    「ハッシュのサブフォルダ / 元と同じファイル名.wav」の形にする。
    ファイル名を変えないのは、Battery 4 のセル名表示（VoiceOverの
    読み上げ対象）を元サンプルと同じにするため。
    """
    key = f"{os.path.abspath(path)}|{int(mtime)}|{size}"
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    stem = os.path.splitext(os.path.basename(path))[0]
    return os.path.join(h, stem + ".wav")


def normalize_for_battery(path: str) -> str | None:
    """afconvert で無劣化のプレーン PCM WAV に変換し、変換後パスを返す。

    - 16ビット以下は LEI16、それ以外は LEI24 で出力（整数PCM）
    - --no-filler で余分なチャンクのない正規のヘッダにする
    - 同じ元ファイル（パス・更新日時・サイズ）なら キャッシュを再利用
    - 失敗時は None（呼び出し側は原本で続行する）
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    dst = os.path.join(
        CONVERTED_DIR, converted_cache_name(path, st.st_mtime, st.st_size)
    )
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        return dst
    os.makedirs(os.path.dirname(dst), exist_ok=True)

    fmt = "LEI24"
    text = afinfo_report(path)
    if text:
        info = parse_audio_info(text)
        if (
            info.get("is_lpcm")
            and not info.get("is_float")
            and info.get("bits")
            and info["bits"] <= 16
        ):
            fmt = "LEI16"
    try:
        r = subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", fmt, "--no-filler", path, dst],
            capture_output=True, timeout=120,
        )
        if r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
            return dst
    except Exception:
        pass
    # 失敗時は中途半端なファイルを残さない
    try:
        if os.path.exists(dst):
            os.remove(dst)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Quartz（マウスイベント合成）/ アクセシビリティ権限
# ---------------------------------------------------------------------------
# pyobjc の Quartz(CoreGraphics) で実際のマウスドラッグを合成する。
# 旧実装の AppleScript `drag from ... to ...` は System Events に存在しない
# 命令で常に失敗していた（偽の自動化）。CGEvent は OS の HID レベルに
# 本物のイベントを送るため、アクセシビリティ権限があれば確実に動く。
try:
    import Quartz  # type: ignore

    QUARTZ_OK = True
except Exception:
    QUARTZ_OK = False

try:
    from ApplicationServices import (  # type: ignore
        AXIsProcessTrusted,
        AXIsProcessTrustedWithOptions,
        kAXTrustedCheckOptionPrompt,
    )

    _AX_OK = True
except Exception:
    _AX_OK = False

try:
    from ApplicationServices import (  # type: ignore
        AXUIElementCreateApplication,
        AXUIElementCopyAttributeValue,
        kAXErrorSuccess,
    )

    _AX_QUERY_OK = True
except Exception:
    _AX_QUERY_OK = False


def accessibility_trusted(prompt: bool = False) -> bool:
    """
    このプロセスにアクセシビリティ権限（マウス・キーボード合成に必須）が
    あるか調べる。prompt=True なら OS の許可ダイアログを表示する。
    判定 API が使えない環境では True を返し、実行時の失敗に委ねる。
    """
    if not _AX_OK:
        return True
    try:
        if prompt:
            return bool(
                AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
            )
        return bool(AXIsProcessTrusted())
    except Exception:
        return True


def responsible_process_path() -> str | None:
    """この処理の権限主体（responsible process）の実行ファイルパスを返す。

    macOS のアクセシビリティ権限は、実行中の python ではなく
    「責任を持つプロセス」に紐づく。ターミナルから起動した場合は
    ターミナル自身が主体になるため、python に権限を与えようとしても
    いつまでも許可されない（利用者がはまりやすい点）。

    private API の responsibility_get_pid_responsible_for_pid を使う。
    使えない環境では None を返す（呼び出し側でフォールバックする）。
    """
    try:
        import ctypes
        libc = ctypes.CDLL(None)
        fn = libc.responsibility_get_pid_responsible_for_pid
        fn.restype = ctypes.c_int
        fn.argtypes = [ctypes.c_int]
        rpid = fn(os.getpid())
        if rpid <= 0:
            return None
        r = subprocess.run(
            ["ps", "-o", "comm=", "-p", str(rpid)],
            capture_output=True, text=True, timeout=5,
        )
        path = r.stdout.strip()
        return path or None
    except Exception:
        return None


def app_name_from_path(path: str | None) -> str:
    """実行ファイルのパスから、利用者に見える名前を取り出す純粋関数。

    `/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal`
    → `Terminal`（.app の名前を優先する。システム設定の一覧に出る名前）
    """
    if not path:
        return ""
    parts = path.split("/")
    for part in reversed(parts):
        if part.endswith(".app"):
            return part[:-4]
    return parts[-1] if parts else ""


def app_bundle_path(path: str | None) -> str:
    """実行ファイルのパスから、それを含む .app のパスを返す純粋関数。

    `/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal`
    → `/Applications/Utilities/Terminal.app`

    システム設定のアクセシビリティの追加ボタンで選ぶのは **.app**。
    中の実行ファイルを渡すと、Cmd+Shift+G で貼っても .app の中へ
    入り込み、実行ファイルを選ぶことになる（許可の一覧ではアプリと
    別のものとして扱われる）。.app に入っていなければそのまま返す。
    いちばん内側の .app を採る（app_name_from_path と同じ）。
    """
    if not path:
        return ""
    parts = path.split("/")
    for i in range(len(parts) - 1, -1, -1):
        if parts[i].endswith(".app"):
            return "/".join(parts[: i + 1])
    return path


def running_as_bundle() -> bool:
    """PyInstaller でビルドした .app として動いているか。"""
    return bool(getattr(sys, "frozen", False))


def accessibility_diagnosis() -> dict:
    """権限の状態と「どのアプリに許可すればよいか」を調べる。

    「許可したはずなのに動かない」の原因はたいてい次のどれか。
      - 許可した対象が違う（.app に許可したがソースから起動している等）
      - 許可した後にアプリを再起動していない
      - .app をビルドし直して別のアプリと見なされた（ad-hoc 署名のため）

    Returns:
        {"trusted": bool, "target": 権限を与える対象の名前,
         "target_path": その .app のパス（システム設定で選ぶもの）,
         "exec_path": 実行ファイルのパス, "kind": "app" / "source",
         "advice": 読み上げ用の案内文}
    """
    trusted = accessibility_trusted()
    resp = responsible_process_path()
    if running_as_bundle():
        kind = "app"
        exec_path = sys.executable
        target = app_name_from_path(sys.executable) or version.APP_NAME
    else:
        kind = "source"
        exec_path = resp or sys.executable
        target = app_name_from_path(resp) or app_name_from_path(sys.executable) \
            or tr("ターミナル", "Terminal")
    target_path = app_bundle_path(exec_path)

    if trusted:
        advice = (
            tr(f"アクセシビリティ権限は許可されています。対象は {target} です。"
               "自動ロードが使えます。",
               f"Accessibility permission is granted. The target is {target}. "
               "Automatic loading is available.")
        )
    else:
        advice = (
            tr(f"アクセシビリティ権限がありません。許可する対象は {target} です。"
               "システム設定のプライバシーとセキュリティ、アクセシビリティで "
               f"{target} を探して、オンにしてください。"
               "一覧に無ければ、追加ボタンから選んでください。"
               "許可したあとは、このアプリをいったん終了して開き直してください。",
               f"There is no Accessibility permission. The app to allow is {target}. "
               f"In System Settings, Privacy & Security, Accessibility, find {target} "
               "and turn it on. If it is not in the list, choose it with the Add "
               "button. After allowing it, quit this app and open it again.")
        )
        if kind == "source":
            advice += (
                tr("ソースから起動しているため、権限は python ではなく "
                   f"{target} に付きます。",
                   " Because this app was started from source, the permission goes "
                   f"to {target}, not to python.")
            )
        else:
            advice += (
                tr("新しい版に入れ替えると、別のアプリと見なされて"
                   "許可が外れることがあります。一覧にあるのにオンでも動かないときは、"
                   "いったんオフにしてからオンに戻すか、削除ボタンで消してから"
                   "追加し直してください。"
                   "同じ名前が 2 つ並んでいるときは、両方をオンにしてください。",
                   " When you replace it with a new version, it may count as a "
                   "different app and lose the permission. If it is in the list and "
                   "turned on but does not work, turn it off and on again, or remove "
                   "it with the Remove button and add it again. If the same name "
                   "appears twice in the list, turn both on.")
            )
    return {
        "trusted": trusted,
        "target": target,
        "target_path": target_path,
        "exec_path": exec_path,
        "kind": kind,
        "advice": advice,
    }


LOG_FILE: str = os.path.expanduser("~/Library/Logs/Battery4Loader.log")


def log_startup_state() -> None:
    """起動時の権限状態をログに1行残す。

    .app を Finder から起動すると標準出力が見えないため、
    「権限が許可されているか」「どのアプリとして動いているか」を
    あとから確認できるようにしておく（不具合相談時の手がかり）。
    失敗してもアプリの動作を止めない。
    """
    try:
        info = accessibility_diagnosis()
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        line = (
            f"{stamp} started trusted={'yes' if info['trusted'] else 'no'} "
            f"kind={info['kind']} target={info['target']} "
            f"path={info['exec_path']}\n"
        )
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def open_accessibility_settings() -> None:
    """システム設定のアクセシビリティ権限ページを開く。"""
    try:
        subprocess.Popen(
            ["open",
             "x-apple.systempreferences:com.apple.preference.security"
             "?Privacy_Accessibility"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Battery のプラグインウィンドウ検出（Accessibility API）
# ---------------------------------------------------------------------------
# Pro Tools のプラグインウィンドウは、中身が自前描画でも外枠は AX に見える。
#   AXWindow title='Plug-in: Battery 4'
#     AXPopUpButton 'Track Selector'  value='Inst1'    ← どのインスタンスか
#     AXPopUpButton 'Plugin Selector' value='Battery 4'
#     AXGroup 'FXTDMEditView'  pos/size                ← 描画領域の矩形
# これでインスタンスを識別でき、セル位置を描画領域からの比率で解決できる。
# セルそのものは AX に存在しないため、ドロップは合成マウスのまま。
_PLUGIN_WINDOW_PREFIX = "Plug-in:"
_EDIT_VIEW_TITLE = "FXTDMEditView"


def _ax_attr(element, name: str):
    """AX 属性を取得する（取れなければ None）。"""
    if not _AX_QUERY_OK or element is None:
        return None
    try:
        err, val = AXUIElementCopyAttributeValue(element, name, None)
        return val if err == kAXErrorSuccess else None
    except Exception:
        return None


def _ax_geometry(element) -> tuple[float, float, float, float] | None:
    """AX 要素の (x, y, width, height) を返す（画面左上原点）。"""
    pos = _ax_attr(element, "AXPosition")
    size = _ax_attr(element, "AXSize")
    if pos is None or size is None:
        return None
    try:
        from ApplicationServices import (  # type: ignore
            AXValueGetValue,
            kAXValueCGPointType,
            kAXValueCGSizeType,
        )

        okp, pt = AXValueGetValue(pos, kAXValueCGPointType, None)
        oks, sz = AXValueGetValue(size, kAXValueCGSizeType, None)
        if not okp or not oks:
            return None
        return (float(pt.x), float(pt.y), float(sz.width), float(sz.height))
    except Exception:
        return None


def host_app_pids(names: Sequence[str] = PLUGIN_HOST_APPS) -> list[int]:
    """プラグインホスト（Pro Tools など）のプロセス ID を返す。

    Quartz のウィンドウ一覧から引くので AppKit に依存しない。
    """
    if not QUARTZ_OK:
        return []
    try:
        infos = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID
        ) or []
    except Exception:
        return []
    pids: list[int] = []
    for info in infos:
        owner = info.get("kCGWindowOwnerName")
        if owner in names:
            pid = info.get("kCGWindowOwnerPID")
            if pid is not None and int(pid) not in pids:
                pids.append(int(pid))
    return pids


def battery_windows() -> list[dict]:
    """開いている Battery のプラグインウィンドウを列挙する。

    Returns:
        [{"track": "Inst1", "plugin": "Battery 4",
          "window": (x, y, w, h), "view": (x, y, w, h)}, ...]
        view は描画領域（FXTDMEditView）。取れない場合はウィンドウ矩形。
    """
    if not _AX_QUERY_OK:
        return []
    out: list[dict] = []
    for pid in host_app_pids():
        app = None
        try:
            app = AXUIElementCreateApplication(pid)
        except Exception:
            continue
        for w in _ax_attr(app, "AXWindows") or []:
            title = _ax_attr(w, "AXTitle") or ""
            if not title.startswith(_PLUGIN_WINDOW_PREFIX):
                continue
            track = plugin = None
            view = None
            for child in _ax_attr(w, "AXChildren") or []:
                ctitle = _ax_attr(child, "AXTitle") or ""
                if ctitle == "Track Selector":
                    track = _ax_attr(child, "AXValue")
                elif ctitle.startswith("Plugin Selector"):
                    plugin = _ax_attr(child, "AXValue")
                elif ctitle == _EDIT_VIEW_TITLE:
                    view = _ax_geometry(child)
            if not plugin or not str(plugin).startswith(TARGET_PLUGIN_PREFIX):
                continue
            geom = _ax_geometry(w)
            out.append({
                "track": str(track) if track else "",
                "plugin": str(plugin),
                "window": geom,
                "view": view or geom,
                # AX 要素そのもの。狙ったインスタンスを前面に出すのに使う
                # （Pro Tools は複数のプラグイン画面を同じ位置に重ねて開くため、
                #   座標だけでは最前面の1つにしか届かない）
                "element": w,
            })
    return out


# キーコード（Quartz の仮想キー）
_KEY_RETURN = 36
_KEY_ESC = 53
_KEY_DOWN = 125
_KEY_UP = 126


def _cg_key(code: int) -> None:
    """キーを1回押す（合成キーボードイベント）。"""
    for down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(None, code, down)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(0.06)


def _frontmost_pid() -> int | None:
    """いちばん手前にある通常ウィンドウの持ち主 PID を返す。

    Quartz のウィンドウ一覧は手前から順に並ぶので、レイヤ 0（通常の
    ウィンドウ）の先頭が最前面のアプリになる。AppKit を import せずに
    「Pro Tools が前面に出たか」を確かめるために使う。
    """
    if not QUARTZ_OK:
        return None
    try:
        infos = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID
        ) or []
    except Exception:
        return None
    for info in infos:
        try:
            if int(info.get("kCGWindowLayer", 0) or 0) != 0:
                continue
        except (TypeError, ValueError):
            continue
        pid = info.get("kCGWindowOwnerPID")
        if pid is not None:
            return int(pid)
    return None


# 合成イベントの行き先を確かめるときに数える窓の層（kCGWindowLayer）。
# 0 がふつうの窓、3 が Pro Tools のプラグイン画面のような浮いた窓、
# 19〜25 が Dock・メニューバー・ほかのアプリの操作盤など、
# 101 がポップアップメニュー（このアプリのドラッグの始点もここ）。
# 1000 以上はスクリーンセーバーや VoiceOver のカーソルの枠で、
# クリックを受け取らないので数えない。マイナスは壁紙とデスクトップ。
_EVENT_LAYER_MAX = 999


def window_at(x: float, y: float, infos=None) -> dict | None:
    """画面上の点 (x, y) で、いちばん手前にある窓を返す（左上原点の座標）。

    **合成クリック・合成ドラッグを送る前に、行き先の窓を確かめるために使う。**
    Pro Tools のプラグイン画面は浮いた窓（層 3）で、このアプリの窓より
    常に上に来る。始点がその下に隠れていると、マウスを押した合成イベントは
    Battery の画面に落ち、つまみやセルを動かしてしまう（小さい画面では
    既定の窓の配置のままで起きる。2026-10-24 のレビューで判明）。

    Quartz のウィンドウ一覧は手前から並ぶので、点を含む最初の窓が答え。
    AppKit を import しない層なので、ここで判定する。

    Args:
        infos: テスト用。CGWindowListCopyWindowInfo の代わりの一覧。

    Returns:
        {"pid", "owner", "name", "layer", "bounds": (x, y, w, h)}。
        何も無い・調べられないときは None。
    """
    if infos is None:
        if not QUARTZ_OK:
            return None
        try:
            infos = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID
            ) or []
        except Exception:
            return None
    for info in infos:
        try:
            layer = int(info.get("kCGWindowLayer", 0) or 0)
            alpha = float(info.get("kCGWindowAlpha", 1.0))
            b = info.get("kCGWindowBounds") or {}
            bx = float(b.get("X", 0))
            by = float(b.get("Y", 0))
            bw = float(b.get("Width", 0))
            bh = float(b.get("Height", 0))
        except (TypeError, ValueError):
            continue
        if layer < 0 or layer > _EVENT_LAYER_MAX or alpha <= 0.01:
            continue
        if bw <= 0 or bh <= 0:
            continue
        if bx <= x < bx + bw and by <= y < by + bh:
            return {
                "pid": int(info.get("kCGWindowOwnerPID", -1) or -1),
                "owner": str(info.get("kCGWindowOwnerName") or ""),
                "name": str(info.get("kCGWindowName") or ""),
                "layer": layer,
                "bounds": (bx, by, bw, bh),
            }
    return None


def window_owner_label(win: dict | None) -> str:
    """window_at の結果を、読み上げる持ち主の名前にする（純粋関数）。

    その位置に窓が無ければ空文字（呼び出し側が「窓が見つからない」と伝える。
    「画面の外 が重なっている」のように、重なっている窓の名前として読まない）。
    """
    if not win:
        return ""
    return win.get("owner") or tr("名前の分からない窓", "A window with an unknown name")


def host_window_at(
    x: float, y: float, layer: int | None = None, infos=None, pids=None,
) -> tuple[bool, str]:
    """(x, y) のいちばん手前の窓が Pro Tools のものか確かめる。

    Args:
        layer: 層まで指定するとき（ミックスウィンドウ＝0 を押したいのに
            浮いたプラグイン画面が重なっている、を見分ける）。
        infos, pids: テスト用。

    Returns:
        (Pro Tools の窓か, 違うときに読み上げる持ち主の名前。
        その位置に窓が無いときは空文字)
    """
    win = window_at(x, y, infos=infos)
    host = list(pids) if pids is not None else host_app_pids()
    if not win or win["pid"] not in host:
        return False, window_owner_label(win)
    if layer is not None and win["layer"] != layer:
        return False, tr(f"{win['owner']} の別の画面", f"Another window of {win['owner']}")
    return True, ""


def drag_start_point(
    tx: float, ty: float, screens: Sequence, offset: float = 110,
    margin: float = 30,
) -> tuple[float, float]:
    """自己ドラッグの始点を、セル (tx, ty) の近くで選ぶ純粋関数。

    セルから offset だけ下・上・右・左に離れた点を順に試し、セルと同じ
    画面の内側（端から margin。上端のメニューバーも避ける）に入る最初の
    点を返す。近くに置くのは、ドラッグの道のりを短くし、別の画面へ
    またがらないため。始点の窓は最前面の層なので、Battery の画面の
    上に出ても隠れない。

    Args:
        screens: 画面の矩形 (x, y, w, h)（左上原点）の並び
    """
    def inside(px, py, rect, pad):
        x, y, w, h = rect
        return x + pad <= px <= x + w - pad and y + pad <= py <= y + h - pad

    home = next((r for r in screens if inside(tx, ty, r, 0)), None)
    for dx, dy in ((0, offset), (0, -offset), (offset, 0), (-offset, 0)):
        px, py = tx + dx, ty + dy
        if home is None or inside(px, py, home, margin):
            return px, py
    # 画面が極端に小さいとき。下へ置く（始点の窓は最前面なので隠れない）
    return tx, ty + offset


def drag_endpoints_problem(
    sx: float, sy: float, tx: float, ty: float, own_pid: int,
    wait: float = 0.6, infos=None, pids=None,
) -> tuple[str, str]:
    """自己ドラッグを始めてよいかを、始点と終点の窓で確かめる。

    - 始点: このアプリのドラッグの始点の窓（own_pid）がいちばん手前か。
      違えば、マウスを押した合成イベントがほかのアプリへ落ちる
    - 終点: セルの位置のいちばん手前が Pro Tools の窓か。違えば、
      ほかの窓へファイルを落とすことになる

    始点の窓は、出した直後はまだ一覧に載っていないことがあるので
    wait 秒まで待つ（終点は待たない）。

    Returns:
        ("", "") ならよい。だめなら (理由, 重なっている窓の持ち主)。
        理由は "source"（始点）、"hidden"（終点が隠れている）、
        "no_window"（終点に窓が無い。Battery の画面が画面の外にあるなど）。
    """
    deadline = time.time() + max(0.0, wait)
    while True:
        top = window_at(sx, sy, infos=infos)
        if top and top.get("pid") == own_pid:
            break
        if infos is not None or time.time() >= deadline:
            return "source", window_owner_label(top)
        time.sleep(0.05)
    on_host, who = host_window_at(tx, ty, infos=infos, pids=pids)
    if not on_host:
        return ("hidden", who) if who else ("no_window", "")
    return "", ""


def _activate_host(timeout: float = 2.0) -> bool:
    """Pro Tools を前面に出す（合成イベントを届かせるため）。

    **前面になったのを確かめてから戻る。** 固定の待ち時間だけで進むと、
    osascript の起動が遅れたときに合成クリックがこのアプリ自身の画面へ
    落ちる（自己ドラッグのために前面に出ているので実際に起こりうる）。

    **起動していなければ何もしない。** AppleScript の activate は
    起動していないアプリを立ち上げてしまうため、「開いていない
    Battery は操作しない」という方針を保つには先の確認が要る。

    core.py は UI 層（AppKit）を import しない原則があるので、
    前面かどうかは Quartz のウィンドウ一覧で判定する。

    Returns:
        前面に出せたら True。
    """
    app_name = PLUGIN_HOST_APPS[0] if PLUGIN_HOST_APPS else ""
    pids = host_app_pids()
    if not app_name or not pids:
        return False
    if _frontmost_pid() in pids:
        return True
    try:
        subprocess.run(
            ["osascript", "-e",
             f'tell application "{_escape_applescript(app_name)}" to activate'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=timeout, check=False,
        )
    except Exception:
        pass
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _frontmost_pid() in pids:
            return True
        time.sleep(0.05)
    return False


def _plugin_headers() -> list[dict]:
    """プラグイン画面のヘッダ情報（Track Selector 等）を列挙する。

    **プラグインが挿さっていないトラックに切り替わると、ウィンドウの
    タイトルが消えて `Plug-in:` で始まらなくなる**（Plugin Selector が
    `no insert` になる）。タイトルで絞ると見失うので、Track Selector を
    持つウィンドウを集める（Mix / Edit の本体だけ除外）。

    Returns:
        [{"selector": Track SelectorのAX要素, "geometry": その位置,
          "track": 値, "plugin": Plugin Selector の値}, ...]
    """
    out: list[dict] = []
    if not _AX_QUERY_OK:
        return out
    for pid in host_app_pids():
        try:
            app = AXUIElementCreateApplication(pid)
        except Exception:
            continue
        for w in _ax_attr(app, "AXWindows") or []:
            title = _ax_attr(w, "AXTitle") or ""
            if title.startswith("Mix") or title.startswith("Edit"):
                continue
            selector = None
            plugin = ""
            for c in _ax_attr(w, "AXChildren") or []:
                ctitle = _ax_attr(c, "AXTitle") or ""
                if ctitle == "Track Selector":
                    selector = c
                elif ctitle.startswith("Plugin Selector"):
                    plugin = str(_ax_attr(c, "AXValue") or "")
            if selector is not None:
                out.append({
                    "selector": selector,
                    "geometry": _ax_geometry(selector),
                    "track": str(_ax_attr(selector, "AXValue") or ""),
                    "plugin": plugin,
                })
    return out


def _front_plugin_header() -> dict | None:
    """操作対象にするプラグイン画面のヘッダを1つ選ぶ。

    **Battery を表示している画面を優先する。** EQ など別プラグインの
    画面も Track Selector を持つため、単純に最初の1つを取ると
    無関係な画面のトラックを読んだり切り替えたりしてしまう。
    Battery が見つからないとき（切り替え途中の no insert など）は、
    プラグイン画面が**1枚しかないときだけ**それを使う。2枚以上あると
    どれが Battery だったのか区別できず、EQ など別の画面のトラックを
    動かしてしまうので、選ばずに None を返す。
    """
    headers = _plugin_headers()
    if not headers:
        return None
    for h in headers:
        if h["plugin"].startswith(TARGET_PLUGIN_PREFIX):
            return h
    return headers[0] if len(headers) == 1 else None


def _front_track_selector():
    """いま操作対象のプラグイン画面の Track Selector を返す。

    Returns: (AX要素, 位置) / 見つからなければ (None, None)
    """
    header = _front_plugin_header()
    if header is None:
        return None, None
    return header["selector"], header["geometry"]


def front_plugin_name() -> str:
    """いま操作対象のプラグイン画面のプラグイン名。

    トラックを送っている途中で `no insert`（プラグインが挿さっていない
    トラック）になることがあるので、それを見分けるために使う。
    """
    header = _front_plugin_header()
    return header["plugin"] if header else ""


def current_battery_track() -> str:
    """いま表示されているプラグイン画面のトラック名。

    プラグインが挿さっていないトラックでも、Track Selector の値は読める。
    ロード先はこれで決まる（Pro Tools は画面を重ねて開くが、実際に
    操作できるのは手前の1枚だけ）。
    """
    el, _geo = _front_track_selector()
    if el is None:
        return ""
    return str(_ax_attr(el, "AXValue") or "")


def battery_window_count() -> int:
    """開いている Battery のプラグイン画面の数。"""
    return len(battery_windows())


def _rects_overlap(a, b) -> bool:
    """2つの矩形 (x, y, w, h) が重なっていれば True。"""
    if not a or not b:
        return False
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return (ax < bx + bw and bx < ax + aw
            and ay < by + bh and by < ay + ah)


def overlapping_plugin_names() -> list[str]:
    """Battery の Track Selector に重なっている別プラグイン画面の名前。

    Pro Tools はプラグイン画面を同じ位置に重ねて開く。Track Selector が
    重なっていると、合成クリックは**手前にある画面**へ落ちるため、
    Battery のつもりで EQ のトラックを切り替えてしまう（AX 上で
    Battery の画面を選べていても、実際に届く先は変えられない。
    AXRaise も退避も効かないことが実機で分かっている）。

    Battery の画面が 1 枚でも、別プラグインの画面が重なっていれば
    切り替えは当てにならないので、実行前に検出して中止する。
    """
    headers = _plugin_headers()
    battery = [h for h in headers
               if h["plugin"].startswith(TARGET_PLUGIN_PREFIX)]
    if not battery:
        return []
    names: list[str] = []
    for h in headers:
        if h["plugin"].startswith(TARGET_PLUGIN_PREFIX):
            continue
        if any(_rects_overlap(h["geometry"], b["geometry"]) for b in battery):
            name = h["plugin"] or tr("別のプラグイン", "Another plug-in")
            if name not in names:
                names.append(name)
    return names


def can_target_reliably() -> tuple[bool, str]:
    """ロード先を確実に狙える状態かどうかを判定する。

    **画面が2枚以上開いていると、どれに入るか特定できない。**
    実機で分かったこと（2026-08-31）:
      - 画面は完全に同じ位置に重なる
      - AXRaise でも退避でも、実際のドロップ先は変わらない
      - Track Selector も、どの画面のものを操作しているか定まらない
    この状態でロードすると**別のトラックのセルを上書きして
    利用者のサンプルが消える**（実際に起きた）。

    Returns:
        (確実に狙えるか, 狙えないときの理由)
    """
    n = battery_window_count()
    if n == 0:
        return False, (tr("Battery の画面が開いていません。"
                          "Pro Tools でロード先の Battery を開いてください。",
                          "No Battery window is open. Open the Battery you want to "
                          "load into in Pro Tools."))
    if n > 1:
        return False, (
            tr(f"Battery の画面が {n} 枚開いています。"
               "重なった画面はどれに入るか特定できないため、"
               "誤って別のトラックのセルを上書きする恐れがあります。"
               "使う 1 枚だけを残して、ほかの Battery の画面を閉じてください。"
               "ロード先はこのアプリから切り替えられます。",
               f"{n} Battery windows are open. Overlapping windows cannot be told "
               "apart, so a sample could overwrite a cell on the wrong track by "
               "mistake. Keep only the one you want to use, and close the other "
               "Battery windows. You can switch the load target from this app.")
        )
    others = overlapping_plugin_names()
    if others:
        return False, (
            tr(f"Battery の画面に {'、'.join(others)} の画面が重なっています。"
               "重なった画面はどちらを操作しているか特定できないため、"
               "トラックの切り替えが別のプラグインに効いてしまいます。"
               "重なっている画面を閉じてから、もう一度お試しください。",
               f"The window of {', '.join(others)} is covering the Battery window. "
               "Overlapping windows cannot be told apart, so a track switch could "
               "take effect in the other plug-in. Close the covering window and try "
               "again.")
        )
    return True, ""


def _mix_window():
    """Pro Tools の Mix ウィンドウ（AX 要素）を返す。無ければ None。"""
    if not _AX_QUERY_OK:
        return None
    for pid in host_app_pids():
        try:
            app = AXUIElementCreateApplication(pid)
        except Exception:
            continue
        for w in _ax_attr(app, "AXWindows") or []:
            if (_ax_attr(w, "AXTitle") or "").startswith("Mix"):
                return w
    return None


def _strip_track_name(parent) -> str:
    """Inserts グループの親（トラックストリップ）からトラック名を取り出す。

    タイトルは「Inst1 - Inst Track 」の形。書式が変わったら
    host_track_order と battery_inserts の両方が同時に追随するよう、
    ここに一本化してある。
    """
    title = (_ax_attr(parent, "AXTitle") or "").strip()
    return title.split(" - ")[0].strip()


def host_track_order() -> list[str]:
    """Mix ウィンドウに並んでいるトラック名を順番どおりに返す。

    Track Selector のメニューもこの順で並ぶので、目的のトラックまで
    上下どちらへ送るのが近いかを決めるのに使う（総当たりだと
    実測で 20 秒以上かかることがあった）。
    """
    mix = _mix_window()
    if mix is None:
        return []
    names: list[str] = []
    for _group, parent in _find_insert_groups(mix):
        name = _strip_track_name(parent)
        if name and name not in names:
            names.append(name)
    return names


def _step_direction(order: list, now: str, target: str):
    """メニュー上で target まで近いほうの (矢印キー, 歩数) を返す。

    メニューは実機で上下ともラップする（Inst6 から下 8 歩で Inst1 に
    到達した）が、この前提が崩れた環境でも fallback 側が毎回
    方向を計算し直すので詰まない。
    """
    if not (order and now in order and target in order):
        return None, 0
    size = len(order)
    down = (order.index(target) - order.index(now)) % size
    up = size - down
    if up < down:
        return _KEY_UP, up
    return _KEY_DOWN, down


# Track Selector を操作するときの待ち時間（秒）。実機で詰めた値。
_MENU_OPEN_WAIT = 0.7      # メニューが開ききるまで
_MENU_STEP_WAIT = 0.08     # 矢印キーを続けて送る間隔
_MENU_STEP_CONFIRM = 0.3   # 1歩ずつ送るときの、矢印から確定までの間
_MENU_CONFIRM_MAX = 2.0    # 確定してから表示が変わるのを待つ上限
_MENU_POLL = 0.1           # 表示が変わったか見に行く間隔


def select_battery_track(
    target: str,
    max_steps: int = 32,
    cancel=None,
    order: Sequence[str] | None = None,
    now: str | None = None,
) -> bool:
    """プラグイン画面の Track Selector を目的のトラックへ切り替える。

    **これがロード先を決める唯一の確実な方法。** ウィンドウの位置や
    重なり順を操作しても実際のドロップ先は変わらないことが実機で分かった
    （AXRaise も退避も効かなかった）。利用者が手で Track Selector を
    切り替えたときだけ正しく入った、という報告から確立した方式。

    Track Selector のメニューは AX から中身が見えないため、
    Mix の並び順から歩数を計算して一括で送り、着かなければ
    1つずつ確かめながら送る。TARGET_ALL は個別のトラック名では
    ないため拒否する（全呼び出し元を守る）。

    **操作する Track Selector は最初に1つ決めて、そこから変えない。**
    途中で選び直すと、no insert のときに EQ など別プラグインの画面へ
    乗り移り、そちらのトラックを動かしてしまう。

    **並び順に無いトラック名は総当たりしない。** 保存済みのロード先が
    セッションから消えている場合など、当てもなくメニューを送ると
    Battery の画面が全トラックを巡ってしまう（実測で1分近く）。
    並び順そのものが読めないとき（Mix ウィンドウが閉じている）も、
    目的地があるかどうか確かめられないので実行しない。

    Args:
        cancel: 引数なしで呼べる関数。True を返したら中断する
            （Esc 中断をステップ境界で効かせるため）。
        order: Mix のトラック順。省略すると自分で読む。ロードのように
            続けて何度も呼ぶ場合は、呼び出し元が一度読んで渡すと
            Mix の走査（Pro Tools への同期問い合わせ）を減らせる。
        now: いま表示中のトラック名。省略すると自分で読む。

    Returns:
        目的のトラックになったら True。
    """
    if not (_AX_QUERY_OK and QUARTZ_OK) or not target:
        return False
    if target == TARGET_ALL:
        return False

    header = _front_plugin_header()
    if header is None or not header.get("geometry"):
        return False
    selector = header["selector"]
    geometry = header["geometry"]

    def now_track() -> str:
        """最初に決めた Track Selector の現在値。"""
        return str(_ax_attr(selector, "AXValue") or "")

    current = now_track() if now is None else now
    if current == target:
        return True
    if order is None:
        order = host_track_order()
    if not order or target not in order:
        return False
    if not _activate_host():
        return False

    def cancelled() -> bool:
        try:
            return bool(cancel()) if cancel is not None else False
        except Exception:
            return False

    menu_used = False

    def open_menu() -> bool:
        """Track Selector のメニューを開く。

        **押す前に、その位置のいちばん手前が Pro Tools の窓か確かめる。**
        ほかのアプリの浮いた窓（操作盤や確認の画面など）が
        重なっていると、合成クリックはそちらを押してしまう。
        """
        nonlocal menu_used
        x, y, w, h = geometry
        cx, cy = x + w / 2, y + h / 2
        if not host_window_at(cx, cy)[0]:
            return False
        _cg_click(cx, cy)
        menu_used = True
        time.sleep(_MENU_OPEN_WAIT)
        return True

    def confirm() -> str:
        """リターンで確定し、表示が変わるまで待って現在値を返す。

        固定時間で待つと、Pro Tools が忙しいときに「まだ変わっていない」
        だけで失敗と判断してしまう（そして送り直して行き過ぎる）。
        変わったのを見たらすぐ進むので、うまくいく場合はむしろ速い。
        """
        _cg_key(_KEY_RETURN)
        deadline = time.time() + _MENU_CONFIRM_MAX
        while time.time() < deadline:
            got = now_track()
            if got == target:
                return got
            time.sleep(_MENU_POLL)
        return now_track()

    # Mix の並び順が分かれば、一度メニューを開いたまま必要な回数だけ
    # 矢印を送って確定する（1回ずつ確定すると1歩あたり約2.7秒かかる）
    key, steps = _step_direction(order, current, target)
    if key is not None and 0 < steps <= max_steps and not cancelled():
        if open_menu():
            for _ in range(steps):
                _cg_key(key)
                time.sleep(_MENU_STEP_WAIT)
            if confirm() == target:
                return True

    # 一括で着かなければ、1歩ずつ確かめながら送る（確実だが遅い）。
    # 方向は毎回計算し直す（行き過ぎ・想定外の並びでも近づけるように）。
    # 歩数は一周分あれば足りる（それ以上回っても同じ場所に戻るだけ）
    limit = min(max_steps, len(order)) if order else max_steps
    seen: set[str] = set()
    for _ in range(limit):
        current = now_track()
        if current == target:
            return True
        if cancelled():
            break
        if current in seen:
            # 一周して戻ってきた＝その名前は選べない。
            # 読めない（空文字）ときも2回目で抜ける
            break
        seen.add(current)
        step_key, _n = _step_direction(order, current, target)
        if step_key is None:
            step_key = _KEY_DOWN
        if not open_menu():
            break
        _cg_key(step_key)
        time.sleep(_MENU_STEP_CONFIRM)
        confirm()
    ok = now_track() == target
    if not ok and menu_used:
        # メニューが開いたままだと、直後の合成ドラッグがメニューの上に
        # 落ちる。開いた実績があるときだけ Esc で閉じる
        # （開いていないのに送ると Pro Tools の別の操作を取り消しかねない）
        _cg_key(_KEY_ESC)
        time.sleep(_MENU_STEP_CONFIRM)
    return ok


# Mix ウィンドウからインサート（プラグインの差し込み口）を読むための目印。
# Pro Tools の Mix ウィンドウでは、トラックごとに「Inserts A-E」グループがあり、
# その中に「Insert Assignment A」（挿さっているプラグイン名が値）と
# 「Insert selector A」（開いていると値が "open"）が並ぶ。
_INSERTS_GROUP_TITLE = "Inserts A-E"
_INSERT_ASSIGN_PREFIX = "Insert Assignment"
_INSERT_SELECTOR_PREFIX = "Insert selector"
_INSERT_OPEN_VALUE = "open"


def _find_insert_groups(element, depth: int = 0, out: list | None = None) -> list:
    """Mix ウィンドウの中から「Inserts A-E」グループを集める（親も一緒に返す）。"""
    if out is None:
        out = []
    if depth > 3:
        return out
    for child in (_ax_attr(element, "AXChildren") or [])[:80]:
        if (_ax_attr(child, "AXTitle") or "") == _INSERTS_GROUP_TITLE:
            out.append((child, element))
        else:
            _find_insert_groups(child, depth + 1, out)
    return out


def battery_inserts() -> list[dict]:
    """Pro Tools の Mix ウィンドウから Battery のインサートを列挙する。

    **プラグインの画面を開いていなくても分かる**のがこの関数の要点。
    battery_windows() は開いているウィンドウしか見えないため、
    「3つ挿さっているのに1つしか見えない」状態を説明できなかった。

    読み取りだけで、Pro Tools を操作はしない。

    Returns:
        [{"track": "Inst1", "slot": "A", "plugin": "Battery 4",
          "point": (x, y)（インサート欄の中心）, "open": 画面が開いているか}, ...]
        Mix ウィンドウが無い、または AX が使えない場合は空リスト。
    """
    out: list[dict] = []
    mix = _mix_window()
    if mix is None:
        return out
    for group, parent in _find_insert_groups(mix):
        track = _strip_track_name(parent)
        children = _ax_attr(group, "AXChildren") or []
        # スロットごとの開閉状態を先に集める
        states = {}
        for el in children:
            title = _ax_attr(el, "AXTitle") or ""
            if title.startswith(_INSERT_SELECTOR_PREFIX):
                slot = title[len(_INSERT_SELECTOR_PREFIX):].strip()
                states[slot] = (_ax_attr(el, "AXValue") or "")
        for el in children:
            title = _ax_attr(el, "AXTitle") or ""
            if not title.startswith(_INSERT_ASSIGN_PREFIX):
                continue
            plugin = str(_ax_attr(el, "AXValue") or "")
            if not plugin.startswith(TARGET_PLUGIN_PREFIX):
                continue
            slot = title[len(_INSERT_ASSIGN_PREFIX):].strip()
            geom = _ax_geometry(el)
            point = None
            if geom:
                point = (int(geom[0] + geom[2] / 2), int(geom[1] + geom[3] / 2))
            out.append({
                "track": track,
                "slot": slot,
                "plugin": plugin,
                "point": point,
                "open": states.get(slot, "") == _INSERT_OPEN_VALUE,
            })
    return out


def battery_inventory() -> dict:
    """Battery が「いくつ挿さっていて、いくつ画面が開いているか」を調べる。

    「3つ挿しているのに1つしか見えない」を説明するために使う。

    Returns:
        {"inserted": 挿さっている数, "open": 画面が開いている数,
         "closed_tracks": 開いていないトラック名の一覧,
         "inserts": battery_inserts() の結果}
    """
    inserts = battery_inserts()
    closed = [i["track"] for i in inserts if not i["open"]]
    return {
        "inserted": len(inserts),
        "open": sum(1 for i in inserts if i["open"]),
        "closed_tracks": closed,
        "inserts": inserts,
    }


def battery_track_names() -> list[str]:
    """Battery が挿さっているトラック名（重複なし・Mix の並び順）。

    **配り先も切り替え候補もトラック単位で数える。** 1つのトラックに
    複数のスロット（インサート A・B …）で挿さっていることがあり、
    インサートをそのまま並べると同じトラックが 2 回出てくる。
    そのまま配ると同じセルへ二重にドロップし、件数も多く読み上げる。

    Track Selector はスロットを選べないので、表示中と違うスロットの
    Battery には切り替えでは届かない（そのトラックは 1 回だけ扱う）。
    """
    names: list[str] = []
    for ins in battery_inserts():
        track = ins.get("track") or ""
        if track and track not in names:
            names.append(track)
    return names


def describe_battery_inventory(inv: dict, detected: int) -> str:
    """検出状況を読み上げ用に説明する純粋関数。

    Args:
        inv: battery_inventory() の結果
        detected: battery_windows() で実際に見えている数
    """
    inserted = inv.get("inserted", 0)
    closed = inv.get("closed_tracks") or []
    if not inserted:
        return ""
    if not closed:
        return tr(f"Battery は {inserted} 個挿さっていて、すべて画面が開いています。",
                  f"{plural(inserted, 'Battery instance')} inserted, and every window "
                  "is open.")
    # 1 つのトラックに 2 か所挿さっていると同じ名前が 2 回並ぶので、1 回にする
    names = COMMA.join(dict.fromkeys(closed))
    return (
        tr(f"Battery は {inserted} 個挿さっていますが、画面が開いているのは "
           f"{detected} 個です。{names} は画面が閉じているため、"
           "このアプリからは見えません。Pro Tools でそのトラックの Battery を"
           "開いてください。",
           f"{plural(inserted, 'Battery instance')} inserted, but only {detected} "
           f"open. The window is closed for {names}, so this app cannot see them. "
           "Open the Battery on those tracks in Pro Tools.")
    )


def _plugin_windows_of(app) -> list:
    """アプリ配下のプラグインウィンドウ（AX 要素）を返す。"""
    out = []
    for w in _ax_attr(app, "AXWindows") or []:
        if (_ax_attr(w, "AXTitle") or "").startswith(_PLUGIN_WINDOW_PREFIX):
            out.append(w)
    return out


def _set_window_position(win, x: float, y: float) -> bool:
    """AX でウィンドウの位置を変える（退避と復帰に使う）。"""
    try:
        from ApplicationServices import (  # type: ignore
            AXUIElementSetAttributeValue, AXValueCreate, kAXValueCGPointType,
        )
        val = AXValueCreate(kAXValueCGPointType, Quartz.CGPointMake(x, y))
        return AXUIElementSetAttributeValue(win, "AXPosition", val) == kAXErrorSuccess
    except Exception:
        return False


def _cg_click(x: float, y: float, shift: bool = False) -> None:
    """指定位置を合成マウスでクリックする。"""
    point = Quartz.CGPointMake(x, y)
    flags = Quartz.kCGEventFlagMaskShift if shift else 0
    move = Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventMouseMoved, point, Quartz.kCGMouseButtonLeft)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, move)
    time.sleep(0.25)
    for kind in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
        ev = Quartz.CGEventCreateMouseEvent(
            None, kind, point, Quartz.kCGMouseButtonLeft)
        if flags:
            Quartz.CGEventSetFlags(ev, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(0.12)


def _rect_covers(rect, x: float, y: float) -> bool:
    """矩形が点を覆っているか。"""
    if not rect:
        return False
    rx, ry, rw, rh = rect
    return rx <= x <= rx + rw and ry <= y <= ry + rh


def open_closed_battery_windows() -> dict:
    """Battery のプラグイン画面が1枚も無いとき、最初の1枚を開く。

    AX は開いているウィンドウしか見えないため、挿さっているだけの
    Battery はロード先に選べない。Mix ウィンドウのインサート欄を
    合成クリックして画面を開く。

    **開くのは1枚だけ。** 画面が2枚以上あるとどれに入るか特定できず
    ロードを中止する運用（can_target_reliably）なので、全部開くと
    かえってロード不能になる。既に1枚開いていれば何もせず、
    ロード先の変更は select_battery_track（トラック切替）に任せる。

    インサート欄が既存のプラグインウィンドウに覆われていると押せないので、
    その間だけウィンドウを画面下へどかし、**終わったら必ず元の位置に戻す**。

    Returns:
        {"opened": 開けた数, "before": 実行前に見えていた数,
         "after": 実行後に見えている数, "tracks": 開こうとしたトラック名,
         "error": 失敗理由（無ければ空）}
    """
    result = {"opened": 0, "before": 0, "after": 0, "tracks": [], "error": ""}
    if not (_AX_QUERY_OK and QUARTZ_OK):
        result["error"] = tr("この環境では画面を開けません",
                             "Windows cannot be opened in this environment")
        return result
    if not accessibility_trusted():
        result["error"] = tr("アクセシビリティ権限がありません", "There is no Accessibility permission")
        return result

    result["before"] = len(battery_windows())
    if result["before"] >= 1:
        # 既に開いている。増やすとロード先を特定できなくなるので開かない
        result["after"] = result["before"]
        return result
    inserts = battery_inserts()
    closed = [i for i in inserts if not i["open"] and i["point"]]
    if not closed:
        result["after"] = result["before"]
        return result
    target = closed[0]
    result["tracks"] = [target["track"]]

    pids = host_app_pids()
    if not pids:
        result["error"] = tr("Pro Tools が見つかりません", "Pro Tools was not found")
        return result
    app = AXUIElementCreateApplication(pids[0])
    if not _activate_host():
        result["error"] = tr("Pro Tools を前面に出せませんでした",
                             "Could not bring Pro Tools to the front")
        return result

    # インサート欄を覆っているプラグインウィンドウを一時的にどかす
    saved = []
    try:
        screen_h = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID()).size.height
    except Exception:
        screen_h = 1080.0
    for win in _plugin_windows_of(app):
        geom = _ax_geometry(win)
        if not geom:
            continue
        if _rect_covers(geom, target["point"][0], target["point"][1]):
            saved.append((win, geom))
            _set_window_position(win, geom[0], screen_h - 60)
    if saved:
        time.sleep(0.8)

    try:
        x, y = target["point"]
        # **押す前に、インサート欄のいちばん手前がミックスウィンドウ（層 0）か
        # 確かめる。** AX で窓を動かしても実際の表示は変わらないことがある
        # （2026-08-31 の実測。spec.md の「試して駄目だった方法」）。
        # 浮いたプラグイン画面が残っていれば、そちらのつまみを押してしまう
        on_mix, who = host_window_at(x, y, layer=0)
        if on_mix:
            _cg_click(x, y)
            time.sleep(1.2)
        elif who:
            result["error"] = (
                tr(f"インサート欄に {who} が重なっていて押せません。"
                   "開いているプラグインの画面を閉じてから、もう一度お試しください",
                   f"{who} is covering the insert slots, so they cannot be clicked. "
                   "Close the open plug-in windows and try again")
            )
        else:
            result["error"] = (
                tr("インサート欄の位置に窓が見つからないため押せません。"
                   "ミックスウィンドウが画面の中に見えているか確かめてください",
                   "No window was found at the insert slots, so they cannot be "
                   "clicked. Check that the Mix window is on screen")
            )
    finally:
        # 何があっても元の位置へ戻す
        for win, geom in saved:
            _set_window_position(win, geom[0], geom[1])
        time.sleep(0.5)

    result["after"] = len(battery_windows())
    result["opened"] = max(0, result["after"] - result["before"])
    return result


def find_battery_window(track: str = "") -> dict | None:
    """ロード先の Battery ウィンドウを1つ選ぶ。

    track を指定するとそのトラックのものを探す。空なら最初の1件。
    TARGET_ALL（すべての Battery）はトラック名ではないので、
    自動選択と同じく最初の1件を返す（設定ウィンドウの位置合わせ等、
    「いま開いている画面」が欲しい呼び出し元を壊さないため）。
    Track Selector が読めないインスタンスは UI 上プラグイン名で
    選ばれていることがあるため、トラック名で見つからなければ
    トラック名が空のウィンドウをプラグイン名でも照合する。
    """
    if track == TARGET_ALL:
        track = ""
    wins = battery_windows()
    if not wins:
        return None
    if track:
        for w in wins:
            if w["track"] == track:
                return w
        for w in wins:
            if not w["track"] and w["plugin"] == track:
                return w
        return None
    return wins[0]


# ロード先の読み上げ方: (値, 表示ラベル)
READOUT_FULL: str = "full"     # 「Inst1 の Battery 4」
READOUT_TRACK: str = "track"   # 「Inst1」だけ
READOUT_SHORT: str = "short"   # 「インスト1」（英字を日本語読みにして最短）
TARGET_READOUT_OPTIONS: list[tuple[str, str]] = [
    (READOUT_FULL, tr("トラック名とプラグイン名", "Track name and plug-in name")),
    (READOUT_TRACK, tr("トラック名のみ", "Track name only")),
    (READOUT_SHORT, tr("短い日本語", "Short form")),
]

# 短い読み上げでの言い換え。VoiceOver が英字を1文字ずつ読んだり
# 英語読みしたりするのを避け、耳で即座に分かる言い方にする。
_SHORT_TRACK_PREFIXES: list[tuple[str, str]] = [
    ("Inst", tr("インスト", "Inst")),
    ("Audio", tr("オーディオ", "Audio")),
    ("Aux", tr("アックス", "Aux")),
    ("Master", tr("マスター", "Master")),
    ("MIDI", tr("ミディ", "MIDI")),
    ("Track", tr("トラック", "Track")),
]


def short_track_name(track: str) -> str:
    """トラック名を短い日本語にする純粋関数。

    例: Inst1 → インスト1 / Audio 3 → オーディオ3 / Master1 → マスター1
    知らない名前はそのまま返す（勝手に読みを壊さない）。
    **言い換えるのは、続きが数字か空白のときだけ。** 語の途中で切ると
    Instrument 1 が「インストrument 1」（英語は Inst rument 1）になる。
    """
    name = (track or "").strip()
    if not name:
        return ""
    for prefix, yomi in _SHORT_TRACK_PREFIXES:
        if not name.lower().startswith(prefix.lower()):
            continue
        rest = name[len(prefix):]
        if rest and not (rest[0].isdigit() or rest[0].isspace()):
            continue
        rest = rest.strip()
        if not rest:
            return yomi
        return tr(f"{yomi}{rest}", f"{yomi} {rest}")
    return name


def unique_titles(titles: Sequence[str]) -> list[str]:
    """選択肢の題を重ならないようにする（2 つめ以降に番号を付ける）。

    NSPopUpButton の addItemWithTitle_ は、同じ題の項目を消して末尾へ移す。
    位置でロード先と対応を取っている選択肢では、1 つずれると狙っていない
    トラックへ配ってしまう（Auto という名前のトラックを選ぶと、
    すべての Battery へ配る設定になった。2026-11-03 のレビューで判明）。
    """
    used: set[str] = set()
    result: list[str] = []
    for title in titles:
        candidate = title
        number = 1
        while candidate in used:
            number += 1
            candidate = f"{title} ({number})"
        used.add(candidate)
        result.append(candidate)
    return result


def window_label(win: dict, mode: str = READOUT_FULL) -> str:
    """読み上げ用に Battery ウィンドウを表す文字列（純粋関数）。

    mode:
      READOUT_FULL  … 「Inst1 の Battery 4」
      READOUT_TRACK … 「Inst1」だけ
      READOUT_SHORT … 「インスト1」。英字の読み方が揺れないよう日本語にする
    トラック名が読めないインスタンスは、どのモードでもプラグイン名を返す。
    """
    track = win.get("track") or ""
    plugin = win.get("plugin") or ""
    if not track:
        return plugin
    if mode == READOUT_SHORT:
        return short_track_name(track)
    if mode == READOUT_TRACK:
        return track
    return tr(f"{track} の {plugin}", f"{plugin} on {track}")


def _point_for_window(settings: dict, cell: str, win: dict):
    """1つの Battery ウィンドウについてセルの画面座標を出す。"""
    if not win or not win.get("view"):
        return None
    layout = settings.get("cell_layout") or DEFAULT_CELL_LAYOUT
    try:
        x, y = grid_calibration.cell_point(layout, cell, win["view"])
    except Exception:
        return None
    mode = settings.get("target_readout", READOUT_FULL)
    return int(x), int(y), window_label(win, mode)


def resolve_cell_point(
    settings: dict, cell: str, track: str = ""
) -> tuple[int, int, str] | None:
    """セルの画面座標を解決する（ロード先1つ）。

    開いている Battery ウィンドウの描画領域と、設定の比率レイアウトから
    実座標を計算する。ウィンドウが見つからなければ None。

    Returns:
        (x, y, 読み上げ用の説明) または None
    """
    return _point_for_window(settings, cell, find_battery_window(track))


def resolve_cell_points(
    settings: dict, cell: str, track: str = ""
) -> list[dict]:
    """セルの画面座標を解決する（辞書のリストで返す）。

    「いま画面に見えている Battery」の描画領域から座標を出す。
    ロードは画面1枚運用（can_target_reliably）が前提で、複数トラックへ
    配るときは select_battery_track で切り替えるたびに呼び直す。

    **開いている全ウィンドウ分をまとめて返すことはしない。**
    重なった画面はどれに入るか特定できず、狙っていないセルを上書きして
    利用者のサンプルを消す（2026-08-31 のデータ消失事故）。
    TARGET_ALL も「いま見えている1枚」として扱う。

    Returns:
        [{"x": int, "y": int, "where": 読み上げ用の説明}]（0件か1件）
    """
    point = resolve_cell_point(settings, cell, track)
    if point is None:
        return []
    return [{"x": point[0], "y": point[1], "where": point[2]}]


def move_cell_sample(
    settings: dict, from_cell: str, to_cell: str, track: str = "",
    cancel=None,
) -> tuple[bool, str]:
    """Battery のセル間でサンプルを移動する（画面上のドラッグ）。

    「A2 に入れたものを A6 に移したい」ときに使う。Battery 自身が
    セル間のドラッグに対応しているので、その操作を合成マウスで行う。

    ロードと同じく、いま表示されている Battery が対象。track を渡すと
    先にそのトラックへ切り替える。

    Args:
        cancel: 引数なしで呼べる関数。True を返したら中断する
            （エスケープ）。切り替えの途中でも、ドラッグを送る直前でも見る。
            切り替えが無くても、読み上げは「エスケープで中断」と言っている。

    Returns:
        (実行できたか, 読み上げ用の説明)
    """
    def cancelled() -> bool:
        return bool(cancel and cancel())

    stopped = tr("移動を中断しました", "Move stopped")
    if from_cell == to_cell:
        return False, tr("移動元と移動先が同じセルです", "The source and destination are the same cell")
    if not accessibility_trusted():
        return False, tr("アクセシビリティ権限がありません", "There is no Accessibility permission")
    # ロードと同じ守り：画面が2枚以上重なっていると、座標を計算しても
    # 実際のドラッグは最前面の別インスタンスに落ち、無関係なセルを
    # 壊しかねない（データ消失事故と同型）。確実でないなら実行しない
    reliable, why = can_target_reliably()
    if not reliable:
        return False, why
    # TARGET_ALL はトラック名ではない。呼び出し元が設定値をそのまま
    # 渡しても暴走しないよう、ここで「表示中の画面」に正規化する
    if track == TARGET_ALL:
        track = ""
    if track and not select_battery_track(track, cancel=cancel):
        if cancelled():
            return False, stopped
        return False, tr(f"{track} に切り替えられませんでした", f"Could not switch to {track}")
    if cancelled():
        return False, stopped

    win = find_battery_window(track)
    if not win or not win.get("view"):
        return False, tr("Battery のウィンドウが見つかりません", "The Battery window was not found")
    layout = settings.get("cell_layout") or DEFAULT_CELL_LAYOUT
    try:
        sx, sy = grid_calibration.cell_point(layout, from_cell, win["view"])
        tx, ty = grid_calibration.cell_point(layout, to_cell, win["view"])
    except Exception as e:
        return False, tr(f"セルの位置を計算できませんでした（{e}）",
                         f"Could not work out the cell position ({e})")

    if not _activate_host():
        return False, tr("Pro Tools を前面に出せませんでした", "Could not bring Pro Tools to the front")
    # 始点も終点も Battery の画面の上か確かめる。ほかの窓が重なっていると、
    # 合成ドラッグはその窓を動かしてしまう
    for px, py in ((sx, sy), (tx, ty)):
        on_host, who = host_window_at(px, py)
        if not on_host and not who:
            return False, (
                tr("セルの位置に Battery の画面が見つからないため、移動しませんでした。"
                   "Battery の画面が画面の中に見えているか確かめてください",
                   "No window was found at the Battery cell, so nothing was moved. "
                   "Check that the Battery window is on screen")
            )
        if not on_host:
            return False, (
                tr(f"Battery のセルの位置に {who} が重なっているため、"
                   "移動しませんでした。重なっている画面を閉じるか動かしてください",
                   f"{who} is covering the Battery cell, so nothing was moved. Close "
                   "or move the covering window")
            )
    # 前面化を待っている間に押されたエスケープも、ここで効かせる
    if cancelled():
        return False, stopped
    ok = cg_drag(sx, sy, tx, ty)
    where = window_label(win, settings.get("target_readout", READOUT_FULL))
    if ok:
        # cg_drag はイベントを送れたことしか保証しない（移動先で
        # 受け入れられたかは分からない）。断定せず確認を促す
        return True, (
            tr(f"{where} の セル {from_cell} から セル {to_cell} への移動を"
               "実行しました。移動できたか確認してください",
               f"Ran the move from cell {from_cell} to cell {to_cell} on {where}. "
               "Please check that it moved")
        )
    return False, tr(f"セル {from_cell} から セル {to_cell} への移動に失敗しました",
                     f"Could not move cell {from_cell} to cell {to_cell}")


def current_mouse_position() -> tuple[int, int] | None:
    """
    現在のマウス座標を返す（画面左上原点。CGEvent / セル座標と同じ座標系）。
    セル座標キャリブレーションの「今の位置を取得」に使う。
    """
    if not QUARTZ_OK:
        return None
    try:
        ev = Quartz.CGEventCreate(None)
        loc = Quartz.CGEventGetLocation(ev)
        return int(loc.x), int(loc.y)
    except Exception:
        return None


def cg_drag(
    sx: float, sy: float, tx: float, ty: float, duration: float = 0.9
) -> bool:
    """
    Quartz CGEvent で (sx, sy) から (tx, ty) へ実際のドラッグ&ドロップを行う。

    mouseDown → 小刻みなドラッグ移動（ドラッグ開始のしきい値超え）→
    なめらかな移動 → mouseUp を HID レベルで送出する。
    途中で例外が起きた場合も必ず mouseUp を送ってボタンを離す。
    """
    if not QUARTZ_OK:
        return False

    def post(kind, x, y):
        ev = Quartz.CGEventCreateMouseEvent(
            None, kind, Quartz.CGPointMake(x, y), Quartz.kCGMouseButtonLeft
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)

    try:
        steps = max(15, int(duration / 0.02))
        # まずポインタを開始点へ移動（下のアプリに位置を認識させる）
        post(Quartz.kCGEventMouseMoved, sx, sy)
        time.sleep(0.15)
        post(Quartz.kCGEventLeftMouseDown, sx, sy)
        time.sleep(0.25)
        # ドラッグ判定のしきい値を超えるため、目標方向へ数 px 動かす
        ux = 1 if tx >= sx else -1
        uy = 1 if ty >= sy else -1
        for d in (2, 5, 9):
            post(Quartz.kCGEventLeftMouseDragged, sx + ux * d, sy + uy * d)
            time.sleep(0.04)
        # smoothstep で目標までなめらかに移動
        for i in range(1, steps + 1):
            t = i / steps
            e = t * t * (3 - 2 * t)
            post(
                Quartz.kCGEventLeftMouseDragged,
                sx + (tx - sx) * e,
                sy + (ty - sy) * e,
            )
            time.sleep(duration / steps)
        time.sleep(0.3)  # ドロップ先にホバー状態を認識させる
        post(Quartz.kCGEventLeftMouseUp, tx, ty)
        time.sleep(0.2)
        return True
    except Exception:
        try:
            post(Quartz.kCGEventLeftMouseUp, tx, ty)
        except Exception:
            pass
        return False


# ---------------------------------------------------------------------------
# Battery 4 へのドラッグ自動化（Finder 経由フォールバック）
# ---------------------------------------------------------------------------
# Finder の表示形式の名前（AppleScript の定数と、ここでの短い名前）
_FINDER_VIEWS = {"list": "list view", "column": "column view",
                 "flow": "flow view", "icon": "icon view"}


def parse_finder_location(text: str):
    """locate_file_in_finder の osascript の出力を読む純粋関数。

    「x,y,もとの表示形式」の形（表示形式は無いこともある）。
    Returns: (x, y, もとの表示形式) / 読めなければ None
    """
    parts = [p.strip() for p in (text or "").strip().split(",")]
    if len(parts) < 2:
        return None
    try:
        x, y = int(float(parts[0])), int(float(parts[1]))
    except ValueError:
        return None
    prev = parts[2] if len(parts) > 2 and parts[2] in _FINDER_VIEWS else ""
    return x, y, prev


def locate_file_in_finder(filepath: str):
    """
    Finder で対象ファイルを表示・選択し、そのアイコンの中心画面座標を返す。

    リスト表示だと UI 要素の名前検索が失敗しやすいため、表示形式を
    アイコン表示に強制してから位置を取得する（todo.md の既知問題対策）。
    **もとの表示形式も返す。** Finder はフォルダごとに表示形式を覚えるので、
    戻さないと利用者のフォルダがアイコン表示のまま残る（VoiceOver では
    リスト表示のほうが扱いやすい）。戻すのは restore_finder_view。

    Returns: (x, y, もとの表示形式) / 失敗したら None
    """
    fp_esc = _escape_applescript(filepath)
    name_esc = _escape_applescript(os.path.basename(filepath))
    script = f'''
set prevView to ""
tell application "Finder"
    activate
    reveal (POSIX file "{fp_esc}" as alias)
    delay 0.6
    try
        set cv to current view of front Finder window
        if cv is list view then
            set prevView to "list"
        else if cv is column view then
            set prevView to "column"
        else if cv is flow view then
            set prevView to "flow"
        else if cv is icon view then
            set prevView to "icon"
        end if
        set current view of front Finder window to icon view
    end try
end tell
delay 0.4
tell application "System Events"
    tell process "Finder"
        set frontmost to true
        delay 0.2
        set theItem to first UI element of front window whose name is "{name_esc}"
        set p to position of theItem
        set s to size of theItem
        set cx to (item 1 of p) + ((item 1 of s) div 2)
        set cy to (item 2 of p) + ((item 2 of s) div 2)
        return (cx as text) & "," & (cy as text) & "," & prevView
    end tell
end tell
'''
    try:
        r = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=20
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None
        return parse_finder_location(r.stdout)
    except Exception:
        return None


def restore_finder_view(prev: str) -> None:
    """locate_file_in_finder がアイコン表示にした Finder の窓を、もとの表示へ戻す。"""
    view = _FINDER_VIEWS.get(prev or "")
    if not view or view == "icon view":
        return
    script = (
        'tell application "Finder"\n'
        "    try\n"
        f"        set current view of front Finder window to {view}\n"
        "    end try\n"
        "end tell"
    )
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True,
                       timeout=10)
    except Exception:
        pass


def drag_via_finder(filepath: str, coords: tuple[int, int]) -> bool:
    """
    Finder のアイコンをドラッグ元として、Quartz でセル座標へドラッグする。
    自アプリ発ドラッグ（app_main の DragSourceView）が始められなかった
    場合の予備。

    **始点と終点の窓を確かめてから送る。** Battery の画面は浮いた窓なので、
    Finder の窓の上にも重なる。アイコンがその下に隠れていると、
    マウスを押した合成イベントは Battery の画面に落ち、つまみやセルを
    動かしてしまう（2026-10-24 のレビューで判明）。どちらかが違えば運ばない。
    """
    found = locate_file_in_finder(filepath)
    if found is None:
        return False
    x, y, prev = found
    try:
        top = window_at(x, y)
        if not top or top.get("owner") != "Finder":
            return False
        if not host_window_at(coords[0], coords[1])[0]:
            return False
        return cg_drag(x, y, coords[0], coords[1])
    finally:
        restore_finder_view(prev)


def kit_folder_name(kit_name: str) -> str:
    """キット名から、エクスポート先のフォルダ名を作る純粋関数。

    `/` はパスの区切りなので、そのままだとフォルダが入れ子になる
    （「Trap/808」が Trap フォルダの中の 808_samples になっていた）。
    全角に置き換える。`:` も Finder では `/` として見えるので同じ扱い。
    先頭のドットは隠しフォルダになるので外す。
    """
    name = (kit_name or "").strip().replace("/", "／").replace(":", "：")
    name = name.lstrip(".").strip()
    return (name or "kit") + "_samples"


def export_kit_to_battery4_folder(kit_name: str, cell_map: dict) -> tuple[str, dict]:
    """セルに割り当てたサンプルを Battery 4 の Kits フォルダにコピーする。

    セルマップ・作成日時・元パスを記録した _kit_definition.json も
    同じフォルダに書き出す（後からセル対応を確認できるように）。

    **同じ中身がもうあればコピーしない。** 以前は 2 回目のエクスポートで
    同じ名前のファイルを見つけるたびに「名前_A1.wav」の複製を作っていた
    （同じサンプルを 2 つのセルに置いたときも同じ）。サイズと更新日時が
    同じもの（copy2 は更新日時を保つ）は、前にコピーしたものとして使う。
    """
    import filecmp

    os.makedirs(BATTERY4_KITS_DIR, exist_ok=True)
    kit_dir = os.path.join(BATTERY4_KITS_DIR, kit_folder_name(kit_name))
    os.makedirs(kit_dir, exist_ok=True)
    copied: dict[str, str] = {}
    for cell, src in cell_map.items():
        if src and os.path.exists(src):
            dst = os.path.join(kit_dir, os.path.basename(src))
            if os.path.abspath(dst) == os.path.abspath(src):
                # 既にエクスポート先フォルダにあるサンプル（再エクスポート時など）。
                # copy2(src, src) は SameFileError になるため、そのまま採用する
                copied[cell] = src
                continue
            if os.path.exists(dst) and not filecmp.cmp(src, dst, shallow=True):
                # 同じ名前で中身の違うファイル（別のパックの kick.wav など）
                base, ext = os.path.splitext(os.path.basename(src))
                dst = os.path.join(kit_dir, f"{base}_{cell}{ext}")
            if not (os.path.exists(dst) and filecmp.cmp(src, dst, shallow=True)):
                shutil.copy2(src, dst)
            copied[cell] = dst
    definition = {
        "kit_name": kit_name,
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "cells": {
            cell: {
                "file": os.path.basename(copied[cell]),
                "source": cell_map[cell],
            }
            for cell in copied
        },
    }
    try:
        Path(os.path.join(kit_dir, "_kit_definition.json")).write_text(
            json.dumps(definition, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass
    return kit_dir, copied
