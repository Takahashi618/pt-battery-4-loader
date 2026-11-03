"""英語の画面で出る文言を集めて JSON で出す（tests/test_i18n.py が別のプロセスで動かす）。

言語は起動したときに決まるので、新しいプロセスで動かす。窓は出さない。

**ロードの結果を報告する関数を呼ぶので、副作用を止めてから動かす。** ホームは自分で
一時フォルダにする（単独で動かしても、本物の設定を書き換えない）。音と読み上げと
クリップボードは BATTERY4_SILENT で止まる（2026-11-03 に、ここを通して本物の
クリップボードを書き換えていた）。Pro Tools はトラック名を読むだけで、操作しない。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
# core を読む前に決める（core は読み込んだ時点のホームと言語を使う）
os.environ["HOME"] = tempfile.mkdtemp(prefix="b4l_en_probe_")
os.environ["BATTERY4_SILENT"] = "1"
os.environ["BATTERY4_LANG"] = "en"


def _view_texts(view) -> list[str]:
    """部品と、その中の部品の文字（題・見本の文字・読み上げ名・選択肢）。"""
    texts: list[str] = []
    for name in ("title", "stringValue", "placeholderString",
                 "accessibilityLabel", "itemTitles"):
        getter = getattr(view, name, None)
        if getter is None:
            continue
        try:
            value = getter()
        except Exception:
            continue
        if value is None:
            continue
        if name == "itemTitles":
            texts += [str(v) for v in value]
        else:
            texts.append(str(value))
    columns = getattr(view, "tableColumns", None)
    if columns is not None:
        texts += [str(c.title()) for c in columns()]
    for child in view.subviews() or []:
        texts += _view_texts(child)
    return texts


def _status_of(delegate) -> list[str]:
    """_set_status に渡された文を集める。"""
    said: list[str] = []
    delegate._set_status = lambda text, *a, **k: said.append(text)
    return said


def collect() -> dict[str, list[str]]:
    import app_main
    import core
    import grid_calibration
    import i18n
    import name_readout
    import version
    from Cocoa import NSApp
    from layout_check import clipped_texts

    out: dict[str, list[str]] = {"lang": [i18n.LANG]}
    d, km, sm = app_main.build_headless()
    sm._refresh_coord_info()
    # 文字が枠に収まらないもの（空であるべき。ほかの組と違い、test_i18n が別に見る）
    out["clipped"] = (clipped_texts(d.window) + clipped_texts(km.window)
                      + clipped_texts(sm.window))
    # ボタンとチェックの「画面の文字 / 読み上げ名」
    out["buttons"] = []
    for order in (d._key_view_order, km._key_view_order, sm._key_view_order):
        for view in order:
            title = str(getattr(view, "title", lambda: "")() or "")
            if title and view.cell().accessibilityRole() in ("AXButton", "AXCheckBox"):
                out["buttons"].append(f"{title} / {view.accessibilityLabel() or title}")
    out["main_window"] = [str(d.window.title())] + _view_texts(d.window.contentView())
    out["kit_window"] = [str(km.window.title())] + _view_texts(km.window.contentView())
    out["settings_window"] = [str(sm.window.title())] + _view_texts(sm.window.contentView())

    menu = NSApp.mainMenu()
    out["menus"] = []
    for i in range(menu.numberOfItems()):
        sub = menu.itemAtIndex_(i).submenu()
        out["menus"].append(str(sub.title()))
        out["menus"] += [str(sub.itemAtIndex_(j).title())
                         for j in range(sub.numberOfItems())]
    out["help"] = [app_main.help_text()]
    out["about"] = [version.about_text("2026-11-03 12:00", "abc1234", flavor)
                    for flavor in (version.FLAVOR_LOCAL, version.FLAVOR_RELEASE,
                                   version.FLAVOR_SOURCE)]
    out["sort_labels"] = [label for _key, label in core.SORT_OPTIONS]
    out["kind_labels"] = [label for _key, label in core.KIND_OPTIONS]
    out["pack_orders"] = [label for _key, label in core.PACK_ORDER_OPTIONS]
    out["readouts"] = [label for _key, label in core.TARGET_READOUT_OPTIONS]
    out["durations"] = [core.format_duration(v) for v in (None, 1.5, 61, 174.4)]
    out["summaries"] = [core.search_result_summary(n) for n in (0, 1, 3)]
    out["labels"] = [core.target_label(core.TARGET_ALL), core.target_label(""),
                     core.target_label(core.TARGET_ALL, "short"),
                     core.short_track_name("Inst1"), core.short_track_name("Audio 3"),
                     core.short_track_name("Instrument 1"),
                     core.window_label({"track": "Inst1", "plugin": "Battery 4"}),
                     core.normalization_reason(
                         {"ok": True, "is_lpcm": True, "is_float": True, "bits": 32})]
    out["inventory"] = [
        core.describe_battery_inventory(
            {"inserted": n, "open": n, "closed_tracks": []}, n) for n in (1, 3)
    ] + [core.describe_battery_inventory(
        {"inserted": 3, "open": 1, "closed_tracks": ["Inst1", "Inst2"]}, 1)]
    out["name_readout"] = [name_readout.readout_name("01_Kick_808.wav", m)
                           for m in name_readout.VALID_MODES]
    out["name_modes"] = [name_readout.mode_label(m) for m in name_readout.VALID_MODES]

    out["grid"] = [grid_calibration.describe_layout(None),
                   grid_calibration.describe_layout(None, (0, 0, 1000, 700)),
                   grid_calibration.describe_cell("B3"),
                   grid_calibration.validate_layout(None)[1],
                   grid_calibration.describe_layout(
                       {"rows": 1, "cols": 1, "x0": 0.3, "y0": 0.2,
                        "col_step": 0.1, "row_step": 0.1})]
    # 1 行 1 列にしたときの読み上げ（設定の窓から）
    sm.rows_popup.selectItemAtIndex_(0)
    sm.cols_popup.selectItemAtIndex_(0)
    sm.gridChanged_(None)
    out["grid"].append(str(sm.status_field.stringValue()))

    said = _status_of(d)
    path = "/s/Kick_808.wav"
    ok = {"where": "Inst1", "ok": True}
    bad = {"where": "Inst2", "ok": False}
    for targets in (
        [ok, dict(ok, where="Inst2")],
        [ok, bad],
        [bad, dict(bad, where="Inst3")],
        [ok, dict(bad, reason="switch")],
        [dict(bad, reason="overlap"), dict(bad, where="Inst3", reason="resolve")],
    ):
        d.loadFinished_({"path": path, "cell": "A1", "targets": targets,
                         "ok": any(t["ok"] for t in targets), "converted": True})
    for result in (
        {"ok": True, "certain": True, "where": "Inst1", "converted": True},
        {"ok": True, "certain": False, "where": ""},
        {"ok": False, "reason": "rejected"},
        {"ok": False},
        {"ok": False, "targets": [dict(bad, reason="hidden", blocker="Finder")]},
    ):
        d.loadFinished_(dict(result, path=path, cell="A1"))
    d.loadCancelled_({"path": path, "targets": [ok]})
    d.loadCancelled_({"path": path, "targets": []})
    d.scanFinished_({"samples": []})
    # 絞り込み中のスキャン（表示が 1 件）
    d.filter_field.setStringValue_("kick")
    d.scanFinished_({"samples": ["/s/kick.wav", "/s/snare.wav"]})
    d.filter_field.setStringValue_("")
    # すべての Battery を選ぶ（挿さっているのが 1 個のとき）
    core.battery_inventory = lambda: {"inserted": 1}
    d._populate_battery_popup()
    d.battery_popup.selectItemAtIndex_(1)
    d.batteryChanged_(d.battery_popup)
    d.moveCellFinished_({"ok": False, "cancelled": True, "msg": "Move stopped"})
    out["load_results"] = list(said)
    return out


if __name__ == "__main__":
    print(json.dumps(collect(), ensure_ascii=False))
