"""マニュアルのうち「コードから作る部分」を作り直す。

    python3 tools/doc_parts.py
        docs/manual.html と manual_en.html を今のコードに合わせ、txt も作り直す
    python3 tools/doc_parts.py --check
        合わせる必要があるかだけ調べる（あれば 1 で終わる。書き換えない）

キーの一覧・メニュー・タブで止まる欄の並び・設定の項目と初めの値・並び替えの
種類などは、手で書き写すと必ずずれる。
**本物の窓とメニューを作って読み**（窓は出さない）、文書の印の間に差し込む。
印は HTML のコメントなので、読み上げにも txt にも出ない:

    <!-- gen:menus -->
    …ここが作り直される…
    <!-- /gen:menus -->

版の番号は <span class="version">…</span> と <title> を version.py に合わせる。

画面の言語は起動したときに決まるので、言語ごとに別のプロセスで動かす
（`--lang ja` / `--lang en`。付けなければ両方を順に）。Mac の言語によらず、
日本語のマニュアルには日本語の表、英語のマニュアルには英語の表が入る。

★音と読み上げは止め、ホームフォルダを一時フォルダに向ける（利用者の設定に触らない）。
★文書にある印を生成側が知らなければ止める（綴り違いの印は、黙って古い中身のまま残る）。
★並び替えなどに説明の無い項目があれば止める（足したのに文書が追いつかないのを防ぐ）。
"""

from __future__ import annotations

import argparse
import html
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
# 言語 → (正本の HTML, 作る txt)
MANUALS = {"ja": (DOCS / "manual.html", DOCS / "manual.txt"),
           "en": (DOCS / "manual_en.html", DOCS / "manual_en.txt")}


def _lang_argument() -> str | None:
    """`--lang`（`--lang=en` も）を先に読む。画面の言語は import の前に決める。"""
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--lang", choices=sorted(MANUALS))
    return pre.parse_known_args()[0].lang


LANG = _lang_argument() or "ja"
os.environ["BATTERY4_LANG"] = LANG
os.environ["BATTERY4_SILENT"] = "1"
os.environ["HOME"] = tempfile.mkdtemp(prefix="b4l_doc_home_")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import version  # noqa: E402
from i18n import tr  # noqa: E402

MARK_RE = re.compile(
    r"(<!-- gen:(?P<name>[a-z_]+) -->)(?P<body>.*?)(<!-- /gen:(?P=name) -->)",
    re.S)
VERSION_SPAN_RE = re.compile(r'(<span class="version">)[^<]*(</span>)')
TITLE_RE = re.compile(rf"(<title>{re.escape(version.APP_NAME)} (?:マニュアル（|Manual \())[^）)<]*([）)])")

# キーの名前（読み上げで分かる言葉にする。記号だけだと VoiceOver が読まない）
KEY_NAMES = {",": tr("カンマ", "Comma"), ";": tr("セミコロン", "Semicolon"),
             " ": tr("スペース", "Space")}
# 修飾キーを言う順。アプリのヘルプやマニュアルの本文と同じ「コマンド+シフト+K」
MOD_NAMES = ((1 << 20, tr("コマンド", "Command")), (1 << 17, tr("シフト", "Shift")),
             (1 << 19, tr("オプション", "Option")), (1 << 18, tr("コントロール", "Control")))
NO_KEY = tr("なし（メニューから選ぶ）", "None (choose from the menu)")
NONE = tr("なし", "None")
NO_VALUE = tr("値なし", "No value")

# 部品の種類
KIND_CHECK = tr("チェック", "Checkbox")
KIND_POPUP = tr("選択肢", "Pop-up")
KIND_BUTTON = tr("ボタン", "Button")
KIND_LIST = tr("一覧", "List")
KIND_FIELD = tr("入力欄", "Text field")

# 一覧やボタンで使うキー（メニューの項目ではないもの）。場所・キー・動作
KEY_HELP = tr((
    ("サンプル一覧", "上下の矢印", "サンプルを移動する"),
    ("サンプル一覧", "スペース",
     "試聴する。同じサンプルでもう一度押すと止まる"),
    ("サンプル一覧", "リターン（テンキーの Enter も）",
     "選んでいるサンプルを、選んでいるセルへロードする"),
    ("どこでも（メイン画面）", "エスケープ",
     "試聴を止める。ロード・トラック切替・セル移動を中断する"),
    ("ボタン", "リターン、またはスペース", "ボタンを押す（どの窓でも）"),
    ("キット管理の保存済みキット", "リターン", "そのキットを編集に読み込む"),
    ("キット管理", "エスケープ",
     "一括プレビュー中なら止める。止めるものが無ければ窓を閉じる"),
    ("設定・キット管理・ヘルプ", "エスケープ、またはコマンド+W", "窓を閉じる"),
    ("確かめる画面・選ぶ画面", "リターン / エスケープ",
     "最初のボタン（実行・削除など）/ キャンセル"),
), (
    ("Sample list", "Up and Down arrows", "Move between samples"),
    ("Sample list", "Space",
     "Preview. Press again on the same sample to stop"),
    ("Sample list", "Return (also Enter on the numeric keypad)",
     "Load the selected sample into the selected cell"),
    ("Anywhere (main window)", "Escape",
     "Stop the preview. Stop a load, track switch, or cell move"),
    ("Buttons", "Return or Space", "Press the button (in every window)"),
    ("Saved kits in Kit Manager", "Return", "Load that kit for editing"),
    ("Kit Manager", "Escape",
     "Stop the kit preview if it is playing. Otherwise close the window"),
    ("Settings, Kit Manager, Help", "Escape or Command+W", "Close the window"),
    ("Confirmation and choice dialogs", "Return / Escape",
     "The first button (Run, Delete, and so on) / Cancel"),
))

# 並び替えの説明（core.SORT_OPTIONS の key ごと。足したらここにも書く）
SORT_NOTES = tr({
    "name_asc": "ファイル名の昇順（初めの値）",
    "name_desc": "ファイル名の降順",
    "ext": "形式（拡張子）ごとにまとめ、その中は名前順。行の先頭に形式を読む",
    "size_asc": "ファイルの小さい順。行の先頭に大きさを読む",
    "size_desc": "ファイルの大きい順。行の先頭に大きさを読む",
    "date_new": "更新日時の新しい順。行の先頭に日付を読む",
    "date_old": "更新日時の古い順。行の先頭に日付を読む",
    "folder": "フォルダごとにまとめる。行の先頭にフォルダ名を読む",
    "dur_short": "長さの短い順。行の先頭に長さを読む。長さが分からないものは最後",
    "dur_long": "長さの長い順。行の先頭に長さを読む。長さが分からないものは最後",
    "fav_first": "お気に入りを先頭にまとめる",
    "recent": "最近ロードしたものを先頭に。行の先頭に [最近使用] と読む",
    "random": "ばらばらに並べる。選び直すまで並びは変わらない",
}, {
    "name_asc": "File names in ascending order (the first setting)",
    "name_desc": "File names in descending order",
    "ext": "Grouped by format (extension), by name within each. The format is read at the start of each row",
    "size_asc": "Smallest files first. The size is read at the start of each row",
    "size_desc": "Largest files first. The size is read at the start of each row",
    "date_new": "Most recently modified first. The date is read at the start of each row",
    "date_old": "Least recently modified first. The date is read at the start of each row",
    "folder": "Grouped by folder. The folder name is read at the start of each row",
    "dur_short": "Shortest first. The length is read at the start of each row. Unknown lengths come last",
    "dur_long": "Longest first. The length is read at the start of each row. Unknown lengths come last",
    "fav_first": "Favorites grouped at the top",
    "recent": "Recently loaded samples at the top, with [Recently used] read at the start of the row",
    "random": "Shuffled. The order stays the same until you choose it again",
})
PACK_ORDER_NOTES = tr({
    "count": "初めの値。目当てのパックの名前が分からないとき、大きなものから当たる",
    "name": "名前を覚えているとき、頭文字で飛ぶ",
    "mtime": "そのパックで最も新しい素材の日時の順。最近さわったパックへ戻る",
    "added": "そのパックで最も古い素材の日時の順。Splice に入れた順に辿る",
}, {
    "count": "The first setting. Start from the biggest packs when you do not know the name",
    "name": "Jump by first letters when you remember the name",
    "mtime": "By the date of the newest sample in each pack. Get back to packs you used recently",
    "added": "By the date of the oldest sample in each pack. Go through packs in the order you added them to Splice",
})


def esc(text) -> str:
    return html.escape(str(text), quote=False)


def count_word(n: int) -> str:
    """「3 つ」「11 項目」のように数える（10 以上を「11 つ」としない）。"""
    return f"{n} つ" if n < 10 else f"{n} 項目"


def table(lead: str, head: tuple, rows: list) -> str:
    """表の前に「何の表か・何行・どの列か」を段落で言う（caption は使わない）。"""
    cols = tr("".join(f"「{h}」" for h in head), ", ".join(head))
    heads = "".join(f'<th scope="col">{esc(h)}</th>' for h in head)
    about = tr(f"表は {len(rows)} 行で、列は{cols}の {count_word(len(head))}です。",
               f" The table has {len(rows)} rows and {len(head)} columns: {cols}.")
    lines = [f"<p>{esc(lead)}{esc(about)}</p>",
             "<table>", f"<thead><tr>{heads}</tr></thead>", "<tbody>"]
    lines += ["<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>"
              for row in rows]
    lines += ["</tbody>", "</table>"]
    return "\n".join(lines)


def key_display(item) -> str:
    """メニュー項目のキーを「コマンド+シフト+T」の形にする。"""
    key = str(item.keyEquivalent() or "")
    if not key:
        return NO_KEY
    mask = int(item.keyEquivalentModifierMask())
    mods = [name for bit, name in MOD_NAMES if mask & bit]
    return "+".join(mods + [KEY_NAMES.get(key, key.upper())])


def spoken_name(view) -> str:
    """VoiceOver が読む名前の最初の文（「。」まで）。"""
    label = ""
    try:
        label = str(view.accessibilityLabel() or "")
    except Exception:
        pass
    if not label:
        for attr in ("title", "placeholderString", "stringValue"):
            try:
                label = str(getattr(view, attr)() or "")
            except Exception:
                continue
            if label:
                break
    return label.split(tr("。", ". "), 1)[0].strip()


ROLE_KINDS = {"AXCheckBox": KIND_CHECK, "AXPopUpButton": KIND_POPUP,
              "AXButton": KIND_BUTTON, "AXTable": KIND_LIST,
              "AXTextField": KIND_FIELD}


def kind_of(view) -> str:
    """部品の種類。VoiceOver が読む役割（AXButton など）から決める。

    ボタンや入力欄はセル（NSCell）が役割を持っていて、部品そのものは
    AXUnknown を返す（2026-10-25 に確かめた）。そのときはセルに聞く。
    """
    role = ""
    for target in (view, getattr(view, "cell", lambda: None)()):
        if target is None:
            continue
        try:
            role = str(target.accessibilityRole() or "")
        except Exception:
            role = ""
        if role in ROLE_KINDS:
            break
    if role not in ROLE_KINDS:
        raise SystemExit(f"[エラー] 種類の分からない部品があります: "
                         f"{type(view).__name__}（{role}）")
    return ROLE_KINDS[role]


def screen_text(view) -> str:
    """画面に出ている文字（ボタンの文字・入力欄の見本の文字）。無ければ「なし」。"""
    kind = kind_of(view)
    text = ""
    if kind in (KIND_BUTTON, KIND_CHECK):
        text = str(view.title() or "")
    elif kind == KIND_FIELD:
        text = str(view.placeholderString() or "")
    return text or NONE


def value_of(view) -> str:
    """設定の画面の初めの値。値を持たない部品は「値なし」と書く（空欄にしない）。"""
    kind = kind_of(view)
    if kind == KIND_CHECK:
        return tr("入れる", "On") if view.state() else tr("外す", "Off")
    if kind == KIND_POPUP:
        return str(view.titleOfSelectedItem() or NO_VALUE)
    if kind == KIND_FIELD:
        return str(view.stringValue() or "") or tr("空欄", "Empty")
    return NO_VALUE


def read_ui() -> dict:
    """本物の窓とメニューを作って読む（窓は出さない）。"""
    import app_main
    import core
    import name_readout
    from Cocoa import NSApp

    d, km, sm = app_main.build_headless()
    menu = NSApp.mainMenu()
    menu_rows = []
    for i in range(menu.numberOfItems()):
        sub = menu.itemAtIndex_(i).submenu()
        title = sub.title() or tr(f"アプリ（{version.APP_NAME}）",
                                  f"App ({version.APP_NAME})")
        for j in range(sub.numberOfItems()):
            item = sub.itemAtIndex_(j)
            if item.isSeparatorItem():
                continue
            menu_rows.append((title, str(item.title()), key_display(item)))

    def tabs(order):
        return [(str(n), spoken_name(v), kind_of(v))
                for n, v in enumerate(order, 1)]

    sm._sync_widgets_from_settings()
    settings_rows = [(spoken_name(v), screen_text(v), kind_of(v), value_of(v))
                     for v in sm._key_view_order]

    missing = [k for k, _ in core.SORT_OPTIONS if k not in SORT_NOTES]
    missing += [k for k, _ in core.PACK_ORDER_OPTIONS if k not in PACK_ORDER_NOTES]
    if missing:
        raise SystemExit(f"[エラー] 説明の無い項目があります: {missing}")
    sort_rows = [(label, SORT_NOTES[key]) for key, label in core.SORT_OPTIONS]
    pack_rows = [(label, PACK_ORDER_NOTES[key])
                 for key, label in core.PACK_ORDER_OPTIONS]
    example = {"track": "Inst1", "plugin": "Battery 4"}
    readout_target_rows = [(label, core.window_label(example, key))
                           for key, label in core.TARGET_READOUT_OPTIONS]
    sample = "01_Kick_808.wav"
    readout_rows = [(name_readout.mode_label(m).split(tr("：", ": "), 1)[-1],
                     name_readout.readout_name(sample, m))
                    for m in name_readout.VALID_MODES]
    return {
        "keys": list(KEY_HELP),
        "menus": menu_rows,
        "main_tabs": tabs(d._key_view_order),
        "kit_tabs": tabs(km._key_view_order),
        "settings": settings_rows,
        "sorts": sort_rows,
        "pack_orders": pack_rows,
        "target_readouts": readout_target_rows,
        "readouts": readout_rows,
    }


def build_parts(ui: dict) -> dict[str, str]:
    tab_head = tr(("順", "読み上げられる名前", "種類"), ("Order", "Spoken name", "Kind"))
    return {
        "keys": table(
            tr("メニューの項目ではない、一覧やボタンで使うキーです。",
               "These keys are used in lists and on buttons, and are not menu items."),
            tr(("場所", "キー", "動作"), ("Where", "Key", "Action")), ui["keys"]),
        "menus": table(
            tr("メニューバーの項目です。VO+M でメニューバーへ移り、"
               "左右の矢印でメニューを選びます。"
               "キーのある項目は、どの窓にいてもそのキーで使えます。",
               "These are the items in the menu bar. Press VO+M to go to the menu bar, "
               "and use the left and right arrows to choose a menu. "
               "Items with a key work with that key from any window."),
            tr(("メニュー", "項目", "キー"), ("Menu", "Item", "Key")), ui["menus"]),
        "main_tabs": table(
            tr("メイン画面でタブを押したときに止まる場所を、止まる順に並べました。"
               "名前は VoiceOver が読む名前の最初の文です。"
               "シフト+タブで逆の順に戻り、最後の次は最初へ戻ります。",
               "These are the places Tab stops at in the main window, in order. "
               "The name is the first sentence of what VoiceOver reads. "
               "Shift+Tab goes back, and after the last one you return to the first."),
            tab_head, ui["main_tabs"]),
        "kit_tabs": table(
            tr("キット管理でタブを押したときに止まる場所を、止まる順に並べました。",
               "These are the places Tab stops at in Kit Manager, in order."),
            tab_head, ui["kit_tabs"]),
        "settings": table(
            tr("設定の窓でタブを押したときに止まる場所を、止まる順に並べ、"
               "アプリを初めて開いたときの値を書きました。"
               "ボタンには、読み上げる名前とは別に、画面に短い文字が出ています。",
               "These are the places Tab stops at in the Settings window, in order, with "
               "their values when the app is first opened. "
               "Buttons show short text on screen, apart from the spoken name."),
            tr(("読み上げられる名前", "画面の文字", "種類", "初めの値"),
               ("Spoken name", "Text on screen", "Kind", "First value")),
            ui["settings"]),
        "sorts": table(
            tr("コマンド+T で、この順に切り替わります。",
               "Command+T cycles through these in this order."),
            tr(("並び", "並び方"), ("Order", "How it sorts")), ui["sorts"]),
        "pack_orders": table(
            tr("コマンド+シフト+G で、この順に切り替わります。",
               "Command+Shift+G cycles through these in this order."),
            tr(("並び", "使いどころ"), ("Order", "When to use it")), ui["pack_orders"]),
        "target_readouts": table(
            tr("コマンド+シフト+B で、この順に切り替わります。"
               "例は、トラック Inst1 の Battery 4 のときの読み方です。",
               "Command+Shift+B cycles through these in this order. "
               "The examples show the reading for Battery 4 on track Inst1."),
            tr(("読み方", "例"), ("Reading", "Example")), ui["target_readouts"]),
        "readouts": table(
            tr("コマンド+Y で、この順に切り替わります。"
               "例は、ファイル名 01_Kick_808.wav の読み方です。",
               "Command+Y cycles through these in this order. "
               "The examples show the reading of the file name 01_Kick_808.wav."),
            tr(("モード", "読み方の例"), ("Mode", "Example")), ui["readouts"]),
    }


def apply(text: str, parts: dict[str, str]) -> str:
    names = {m.group("name") for m in MARK_RE.finditer(text)}
    unknown = names - set(parts)
    if unknown:
        raise SystemExit(
            f"[エラー] 生成側が知らない印があります: {sorted(unknown)}")
    missing = set(parts) - names
    if missing:
        raise SystemExit(f"[エラー] 文書に印がありません: {sorted(missing)}")

    def fill(m) -> str:
        return f"{m.group(1)}\n{parts[m.group('name')]}\n{m.group(4)}"

    text = MARK_RE.sub(fill, text)
    text = VERSION_SPAN_RE.sub(
        lambda m: f"{m.group(1)}{version.VERSION}{m.group(2)}", text)
    return TITLE_RE.sub(
        lambda m: f"{m.group(1)}{version.VERSION}{m.group(2)}", text)


def txt_of(html_text: str) -> str:
    from html_to_txt import DocumentToText

    parser = DocumentToText()
    parser.feed(html_text)
    return parser.render()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="マニュアルのうちコードから作る部分を作り直す")
    ap.add_argument("--check", action="store_true",
                    help="書き換えずに、古いかどうかだけ調べる")
    ap.add_argument("--lang", choices=sorted(MANUALS),
                    help="1 つの言語だけ（付けなければ両方）")
    args = ap.parse_args(argv)
    if args.lang is None:
        # 言語は起動したときに決まるので、言語ごとに別のプロセスで動かす
        extra = ["--check"] if args.check else []
        codes = [subprocess.run([sys.executable, "-X", "utf8", __file__,
                                 "--lang", lang] + extra, cwd=ROOT).returncode
                 for lang in MANUALS]
        return max(codes)
    manual, manual_txt = MANUALS[LANG]
    source = manual.read_text(encoding="utf-8")
    new = apply(source, build_parts(read_ui()))
    new_txt = txt_of(new)
    old_txt = (manual_txt.read_text(encoding="utf-8-sig")
               if manual_txt.is_file() else "")
    stale = (new != source
             or new_txt.replace("\r\n", "\n") != old_txt.replace("\r\n", "\n"))
    if args.check:
        print(f"マニュアル（{LANG}）は古いです。tools/doc_parts.py で作り直してください"
              if stale else f"マニュアル（{LANG}）は今のコードと合っています")
        return 1 if stale else 0
    manual.write_text(new, encoding="utf-8")
    manual_txt.write_text(new_txt, encoding="utf-8-sig", newline="\r\n")
    print(f"作り直しました: {manual.name}（{len(new)} 字）、"
          f"{manual_txt.name}（{len(new_txt)} 字）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
