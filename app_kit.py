"""
Battery 4 Sample Loader — キット管理ウィンドウ（フェーズ3）
=============================================================

メインウィンドウ（app_main.AppDelegate）から Cmd+K で開く独立ウィンドウ。
セルへのサンプル割り当てを「キット」として保存・編集・エクスポートする。

キットのデータ構造:
  kits[kit_name] = {
      "cells": { "A1": "/path/to/kick.wav", "A2": "...", ... },
      "track": "Inst1",   # ロード先の Battery（空なら自動）
  }
  セル名は Battery 4 の実表記（行＝A〜D、列＝1〜12）。A1 の右隣は A2。
  未割り当てセルはキーを持たない。旧形式（セルマップ直置き）は
  core.load_kits() が読み込み時に移し替える。

VoiceOver 対応:
  - 全ウィジェットに日本語アクセシビリティラベル
  - 状態通知は controller.announce()（メイン側の高優先度アナウンス）を利用
  - Esc / Cmd+W で閉じる

レイアウト（Tab順）:
  キット名入力 / 保存済みキットリスト / セルマップ表(12行) / 操作ボタン群
"""

from __future__ import annotations

import os
import subprocess
import threading
import time

import objc
from Cocoa import (
    NSObject,
    NSWindow,
    NSButton,
    NSTextField,
    NSScrollView,
    NSTableView,
    NSTableColumn,
    NSMakeRect,
    NSBackingStoreBuffered,
)

import core
from i18n import plural, tr
# app_main からは import しない（ui_common の説明。リターンが効かなくなる）
from ui_common import click_focused_button

NSWindowStyleMaskTitled = 1 << 0
NSWindowStyleMaskClosable = 1 << 1
NSWindowStyleMaskResizable = 1 << 3
NSCommandKeyMask = 1 << 20

# 窓の高さ。上から キット名 → 2 つの一覧 → ボタン 10 個（30 ずつ）の順に
# 積んで、いちばん下のボタンも窓の中（下端から 20 以上）に収まる高さ
KIT_WINDOW_HEIGHT = 640


class KitWindow(NSWindow):
    """キット管理ウィンドウ。

    Esc は一括プレビュー再生中ならまず停止、そうでなければ閉じる。
    Cmd+W で閉じる。
    """

    def _stop_preview_if_running(self):
        manager = getattr(self, "manager", None)
        if manager is not None and manager.is_previewing():
            manager.stop_kit_preview()
            return True
        return False

    def _close_or_stop(self):
        # Esc はプレビュー停止が先。止めるものが無ければ閉じる
        if self._stop_preview_if_running():
            return
        self.orderOut_(None)

    def _stop_and_close(self):
        # Cmd+W / クローズボタンは必ず閉じる。閉じたウィンドウの外から
        # 一括プレビューを止める手段は無いため、閉じる前に必ず停止する
        self._stop_preview_if_running()
        self.orderOut_(None)

    def cancelOperation_(self, sender):
        self._close_or_stop()

    def performClose_(self, sender):
        self._stop_and_close()

    def keyDown_(self, event):
        chars = event.charactersIgnoringModifiers()
        mods = event.modifierFlags()
        if chars == "\x1b":
            self._close_or_stop()
            return
        if (mods & NSCommandKeyMask) and chars == "w":
            self._stop_and_close()
            return
        # リターンでフォーカス中のボタンを実行する
        if click_focused_button(self, event):
            return
        objc.super(KitWindow, self).keyDown_(event)


class KitTableView(NSTableView):
    """保存済みキットリスト。Return で選択キットを編集に読み込む。"""

    def setReturnHandler_(self, handler):
        self._return_handler = handler

    def keyDown_(self, event):
        chars = event.charactersIgnoringModifiers()
        mods = event.modifierFlags()
        handler = getattr(self, "_return_handler", None)
        if handler is not None and not (mods & NSCommandKeyMask) and chars == "\r":
            handler.loadKitClicked_(self)
            return
        objc.super(KitTableView, self).keyDown_(event)


class KitListDataSource(NSObject):
    """保存済みキット名の一覧を表示する単一列テーブル。"""

    def initWithNames_(self, names):
        self = objc.super(KitListDataSource, self).init()
        if self is None:
            return None
        self._names = list(names)
        return self

    def setNames_(self, names):
        self._names = list(names)

    def numberOfRowsInTableView_(self, tableView):
        return len(self._names)

    def tableView_objectValueForTableColumn_row_(self, tableView, column, row):
        if 0 <= row < len(self._names):
            return self._names[row]
        return ""


class CellMapDataSource(NSObject):
    """
    全セルの割り当て状況を表示する単一列テーブル。

    各行は「A1: kick.wav」または「A1: 未割り当て」の形式。
    VoiceOver はこの 1 行のテキストをそのまま読むため、セル名と割り当て内容が
    同時に伝わる。controller には KitManager を渡す（cell_names と
    current_kit_cells の両方を持つのは KitManager）。
    """

    def initWithController_(self, controller):
        self = objc.super(CellMapDataSource, self).init()
        if self is None:
            return None
        self._controller = controller
        return self

    def numberOfRowsInTableView_(self, tableView):
        return len(self._controller.cell_names())

    def tableView_objectValueForTableColumn_row_(self, tableView, column, row):
        names = self._controller.cell_names()
        if not (0 <= row < len(names)):
            return ""
        cell = names[row]
        path = self._controller.current_kit_cells.get(cell)
        if path:
            return f"{cell}: {os.path.basename(path)}"
        return tr(f"{cell}: 未割り当て", f"{cell}: unassigned")


class KitManager(NSObject):
    """
    キット管理ウィンドウのコントローラ。

    メインの AppDelegate を controller として受け取り、状態（kits, settings,
    現在選択中サンプル）と announce() を共有する。
    """

    def initWithController_(self, controller):
        self = objc.super(KitManager, self).init()
        if self is None:
            return None
        self.controller = controller          # app_main.AppDelegate
        self.current_kit_cells = {}            # 編集中キット {cell: path}
        self.current_kit_name = ""
        self.window = None
        self._kit_names = []                   # キットリストの表示順（選択解決と共用）
        self._preview_cancel = False           # 一括プレビューの中断フラグ
        self._preview_running = False
        self._preview_proc = None
        return self

    @objc.python_method
    def cell_names(self):
        """セル名一覧。メイン側のグリッド設定に追随する。

        CellMapDataSource が行数・行内容の生成に使う（ここに無いと
        キット管理ウィンドウ自体が開けない。テストで配線を検査している）。
        """
        return self.controller.cell_names()

    @objc.python_method
    def is_previewing(self):
        return self._preview_running

    def stop_kit_preview(self):
        """一括プレビューを停止する（Esc・終了時に呼ばれる）。"""
        self._preview_cancel = True
        proc = self._preview_proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # ウィンドウ表示
    # ------------------------------------------------------------------
    def show(self):
        first = self.window is None
        if first:
            self._build_window()
        else:
            self._refresh_kit_list()
            self._refresh_cell_map()
        self.window.makeKeyAndOrderFront_(None)
        if first:
            self.window.center()
            self.window.orderFrontRegardless()
            from Cocoa import NSApplication
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            self.window.makeFirstResponder_(self.name_field)
            self.controller.announceMessage_(
                tr("キット管理を開きました。タブで項目を移動できます。"
                   "エスケープまたはコマンドWで閉じます。",
                   "Kit Manager opened. Press Tab to move between items. Press "
                   "Escape or Command+W to close.")
            )
            return
        self.controller.announceMessage_(tr("キット管理を開きました", "Kit Manager opened"))

    def _build_window(self):
        """窓を組み立てる（出すのは show）。

        組み立てと表示を分けてあるのは、起動確認（--selftest）とマニュアルの
        生成で、窓を出さずに組み立てて読むため。

        **ボタンは窓の中に収める。** 以前は高さ 540 の窓にボタン 10 個を
        30 ずつ下へ並べていて、最後の 2 つ（エクスポート・一括プレビュー）が
        窓の外（y が負）に置かれ、画面に見えなかった（2026-10-24 のレビュー）。
        部品が窓からはみ出していないか・重なっていないかはテストで確かめる。
        """
        style = (
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable
        )
        rect = NSMakeRect(240, 160, 560, KIT_WINDOW_HEIGHT)
        win = KitWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False
        )
        win.setTitle_(tr("キット管理", "Kit Manager"))
        win.setReleasedWhenClosed_(False)
        win.manager = self  # Esc でのプレビュー停止判定に使う
        content = win.contentView()
        top = KIT_WINDOW_HEIGHT

        # キット名入力
        self._label(content, tr("キット名", "Kit name"), NSMakeRect(20, top - 40, 80, 22))
        self.name_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(105, top - 40, 300, 24))
        self.name_field.setAccessibilityLabel_(tr("キット名", "Kit name"))
        content.addSubview_(self.name_field)

        # 保存済みキットリスト
        self._label(content, tr("保存済みキット", "Saved kits"), NSMakeRect(20, top - 72, 200, 22))
        kit_scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(20, top - 190, 250, 112))
        kit_scroll.setHasVerticalScroller_(True)
        kit_scroll.setBorderType_(1)
        self.kit_table = KitTableView.alloc().initWithFrame_(NSMakeRect(0, 0, 250, 112))
        kcol = NSTableColumn.alloc().initWithIdentifier_("kit")
        kcol.setTitle_(tr("キット", "Kit"))
        kcol.setWidth_(230)
        # 編集可のままだとフォーカス時にセルの編集ビューが responder を
        # 奪い、リターンや上下キーが一覧に届かなくなる
        kcol.setEditable_(False)
        self.kit_table.addTableColumn_(kcol)
        self.kit_table.setAccessibilityLabel_(tr("保存済みキット一覧。リターンで編集に読み込み",
                                                 "Saved kits. Press Return to load "
                                                 "one for editing"))
        self.kit_ds = KitListDataSource.alloc().initWithNames_([])
        self.kit_table.setDataSource_(self.kit_ds)
        self.kit_table.setTarget_(self)
        self.kit_table.setDoubleAction_("loadKitClicked:")
        self.kit_table.setReturnHandler_(self)
        kit_scroll.setDocumentView_(self.kit_table)
        content.addSubview_(kit_scroll)

        # セルマップ表
        self._label(content, tr("セルの割り当て",
                                "Cell assignments"), NSMakeRect(290, top - 72, 200, 22))
        map_scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(290, top - 290, 250, 212))
        map_scroll.setHasVerticalScroller_(True)
        map_scroll.setBorderType_(1)
        self.map_table = NSTableView.alloc().initWithFrame_(NSMakeRect(0, 0, 250, 212))
        mcol = NSTableColumn.alloc().initWithIdentifier_("cell")
        mcol.setTitle_(tr("セル", "Cell"))
        mcol.setWidth_(230)
        mcol.setEditable_(False)
        self.map_table.addTableColumn_(mcol)
        self.map_table.setAccessibilityLabel_(tr("セルの割り当て一覧", "Cell assignments"))
        self.map_ds = CellMapDataSource.alloc().initWithController_(self)
        self.map_table.setDataSource_(self.map_ds)
        map_scroll.setDocumentView_(self.map_table)
        content.addSubview_(map_scroll)

        # 操作ボタン群
        self._buttons = self._build_buttons(content)

        # Tab の移動順を明示する。既定のままだとスクロールバーに
        # フォーカスが行き、リストにキーが届かなくなる（メイン画面で発生）
        order = [self.name_field, self.kit_table, self.map_table] + self._buttons
        for cur, nxt in zip(order, order[1:] + order[:1]):
            cur.setNextKeyView_(nxt)
        win.setInitialFirstResponder_(self.name_field)
        self._key_view_order = order

        self.window = win
        self._refresh_kit_list()
        self._refresh_cell_map()

    @objc.python_method
    def _label(self, parent, text, frame):
        lbl = NSTextField.alloc().initWithFrame_(frame)
        lbl.setStringValue_(text)
        lbl.setBezeled_(False)
        lbl.setDrawsBackground_(False)
        lbl.setEditable_(False)
        lbl.setSelectable_(False)
        parent.addSubview_(lbl)
        return lbl


    @objc.python_method
    def _build_buttons(self, content):
        """操作ボタンを縦に並べる。各ボタンにアクセシビリティラベルを付与。

        Returns: 作ったボタンのリスト（Tab の移動順をつなぐのに使う）
        """
        defs = [
            (tr("選択サンプルを現在のセルへ割り当て",
                "Assign Selected Sample to Current Cell"), "assignClicked:"),
            (tr("新規キット", "New Kit"), "newKitClicked:"),
            (tr("保存", "Save"), "saveKitClicked:"),
            (tr("別名で保存", "Save As"), "saveAsClicked:"),
            (tr("選択キットを編集に読み込み", "Load Selected Kit for Editing"), "loadKitClicked:"),
            (tr("選択キットを複製", "Duplicate Selected Kit"), "duplicateClicked:"),
            (tr("選択キットを削除", "Delete Selected Kit"), "deleteClicked:"),
            (tr("現在のセルの割り当てを解除", "Clear Current Cell"), "clearCellClicked:"),
            (tr("Battery 4 へエクスポート", "Export to Battery 4"), "exportClicked:"),
            (tr("キットを一括プレビュー", "Preview Kit"), "previewAllClicked:"),
        ]
        made = []
        # セルマップ表（下端は KIT_WINDOW_HEIGHT - 290）の 40 下から、
        # 30 ずつ下へ 10 個並べる（いちばん下は y=40 で、窓の中に収まる）
        y = KIT_WINDOW_HEIGHT - 290 - 40
        for title, action in defs:
            btn = NSButton.alloc().initWithFrame_(NSMakeRect(20, y, 320, 26))
            btn.setTitle_(title)
            btn.setAccessibilityLabel_(title)
            btn.setTarget_(self)
            btn.setAction_(action)
            btn.setBezelStyle_(1)
            content.addSubview_(btn)
            made.append(btn)
            y -= 30
        return made

    # ------------------------------------------------------------------
    # リスト更新
    # ------------------------------------------------------------------
    def _refresh_kit_list(self):
        # 表示に使った並びを保持し、選択行の解決（_selected_kit_name）と
        # 必ず同じリストを使う（別々に導出すると並びを変えた瞬間にずれる）
        self._kit_names = sorted(self.controller.kits.keys())
        self.kit_ds.setNames_(self._kit_names)
        self.kit_table.reloadData()

    def _refresh_cell_map(self):
        self.map_table.reloadData()

    def _selected_kit_name(self):
        row = self.kit_table.selectedRow()
        if 0 <= row < len(self._kit_names):
            return self._kit_names[row]
        return None

    def _current_map_cell(self):
        """セルマップ表で選択中のセル名。未選択ならメイン側の選択セル。"""
        names = self.cell_names()
        row = self.map_table.selectedRow()
        if 0 <= row < len(names):
            return names[row]
        return self.controller.selected_cell

    # ------------------------------------------------------------------
    # アクション
    # ------------------------------------------------------------------
    def assignClicked_(self, sender):
        path = self.controller._selected_path()
        if not path:
            self.controller.announceMessage_(tr("メイン画面でサンプルを選択してください",
                                                "Select a sample in the main window"))
            return
        cell = self._current_map_cell()
        self.current_kit_cells[cell] = path
        self._refresh_cell_map()
        self.controller.announceMessage_(
            tr(f"セル {cell} に {os.path.basename(path)} を割り当てました",
               f"Assigned {os.path.basename(path)} to cell {cell}")
        )

    def clearCellClicked_(self, sender):
        cell = self._current_map_cell()
        if cell in self.current_kit_cells:
            del self.current_kit_cells[cell]
            self._refresh_cell_map()
            self.controller.announceMessage_(tr(f"セル {cell} の割り当てを解除しました",
                                                f"Cleared cell {cell}"))
        else:
            self.controller.announceMessage_(tr(f"セル {cell} は未割り当てです",
                                                f"Cell {cell} is unassigned"))

    def newKitClicked_(self, sender):
        self.current_kit_cells = {}
        self.current_kit_name = ""
        self.name_field.setStringValue_("")
        self._refresh_cell_map()
        self.controller.announceMessage_(tr("新規キットを作成しました。全セル未割り当てです。",
                                            "New kit created. All cells are "
                                            "unassigned."))

    @objc.python_method
    def _confirm(self, title, message, ok_title):
        """確かめる画面を出す。ok_title を押したら True。

        リターンは ok_title（macOS の標準どおり最初のボタン）、
        エスケープはキャンセル。NSAlert がエスケープを自動で割り当てるのは
        「Cancel」という名前のボタンなので、日本語の名前では明示しておく。
        """
        from Cocoa import NSAlert, NSAlertFirstButtonReturn
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(message)
        alert.addButtonWithTitle_(ok_title)
        cancel = alert.addButtonWithTitle_(tr("キャンセル", "Cancel"))
        cancel.setKeyEquivalent_("\x1b")
        return alert.runModal() == NSAlertFirstButtonReturn

    def saveKitClicked_(self, sender):
        name = self.name_field.stringValue().strip()
        if not name:
            self.controller.announceMessage_(tr("キット名を入力してください", "Enter a kit name"))
            self.window.makeFirstResponder_(self.name_field)
            return
        if not self.current_kit_cells:
            self.controller.announceMessage_(tr("セルにサンプルが割り当てられていません",
                                                "No samples are assigned to cells"))
            return
        # 編集中のキットとは別の、すでにある名前なら上書きしてよいか確かめる。
        # 「別名で保存」のつもりで既存の名前を打つと、黙ってそのキットが
        # 置き換わっていた（元に戻す手段が無い）
        if name in self.controller.kits and name != self.current_kit_name:
            if not self._confirm(
                tr(f"キット {name} はすでにあります", f"Kit {name} already exists"),
                tr("上書きすると、いま編集している内容に置き換わります。"
                   "元のキットは戻せません。上書きしますか。",
                   "Overwriting replaces it with what you are editing now. The "
                   "original kit cannot be restored. Overwrite it?"),
                tr("上書き", "Overwrite"),
            ):
                self.controller.announceMessage_(
                    tr("保存をやめました。キット名を変えてから保存してください",
                       "Save canceled. Change the kit name, then save"))
                self.window.makeFirstResponder_(self.name_field)
                return
        # いまのロード先 Battery（トラック名）もキットに覚えさせる。
        # 複数の Battery を使い分けるとき、キットごとに行き先が決まる。
        track = self.controller.settings.get("target_track", "")
        self.controller.kits[name] = {
            "cells": dict(self.current_kit_cells),
            "track": track,
        }
        core.save_kits(self.controller.kits)
        self.current_kit_name = name
        self._refresh_kit_list()
        core.beep(core.SND_OK)
        where = tr(f"ロード先は {core.target_label(track)}。",
                   f"Load target is {core.target_label(track)}.")
        self.controller.announceMessage_(
            tr(f"キット {name} を保存しました。{len(self.current_kit_cells)} セル。{where}",
               f"Saved kit {name}. {plural(len(self.current_kit_cells), 'cell')}. "
               f"{where}")
        )

    def saveAsClicked_(self, sender):
        # 別名保存は名前欄の値で保存（ユーザーが先に名前を変える運用）
        self.saveKitClicked_(sender)


    def loadKitClicked_(self, sender):
        name = self._selected_kit_name()
        if not name:
            self.controller.announceMessage_(tr("読み込むキットを選択してください", "Select a kit to load"))
            return
        kit = self.controller.kits.get(name, {})
        self.current_kit_cells = core.kit_cells(kit)
        self.current_kit_name = name
        self.name_field.setStringValue_(name)
        self._refresh_cell_map()
        core.beep(core.SND_TICK)
        # キットに記録されたロード先 Battery をメイン画面へ反映する
        track = core.kit_track(kit)
        where = ""
        if track:
            self.controller.settings["target_track"] = track
            self.controller._populate_battery_popup()
            core.save_settings(self.controller.settings)
            where = tr(f"ロード先を {core.target_label(track)} にしました。",
                       f"Load target set to {core.target_label(track)}.")
        self.controller.announceMessage_(
            tr(f"キット {name} を編集に読み込みました。"
               f"{len(self.current_kit_cells)} セル。{where}",
               f"Loaded kit {name} for editing. "
               f"{plural(len(self.current_kit_cells), 'cell')}. {where}")
        )

    def duplicateClicked_(self, sender):
        name = self._selected_kit_name()
        if not name:
            self.controller.announceMessage_(tr("複製するキットを選択してください",
                                                "Select a kit to duplicate"))
            return
        new_name = f"{name}_copy"
        i = 2
        while new_name in self.controller.kits:
            new_name = f"{name}_copy{i}"
            i += 1
        src = self.controller.kits[name]
        self.controller.kits[new_name] = {
            "cells": core.kit_cells(src),
            "track": core.kit_track(src),
        }
        core.save_kits(self.controller.kits)
        self._refresh_kit_list()
        self.controller.announceMessage_(tr(f"キット {name} を {new_name} として複製しました",
                                            f"Duplicated kit {name} as {new_name}"))

    def deleteClicked_(self, sender):
        name = self._selected_kit_name()
        if not name:
            self.controller.announceMessage_(tr("削除するキットを選択してください",
                                                "Select a kit to delete"))
            return
        if name in self.controller.kits:
            # 削除は元に戻せない。ボタンを Tab で渡っているときの押し間違いで
            # キットが消えないよう、確かめてから消す
            if not self._confirm(
                tr(f"キット {name} を削除しますか", f"Delete kit {name}?"),
                tr("削除したキットは元に戻せません。", "A deleted kit cannot be restored."),
                tr("削除", "Delete"),
            ):
                self.controller.announceMessage_(tr("削除をやめました", "Delete canceled"))
                return
            del self.controller.kits[name]
            core.save_kits(self.controller.kits)
            self._refresh_kit_list()
            core.beep(core.SND_OK)
            self.controller.announceMessage_(tr(f"キット {name} を削除しました",
                                                f"Deleted kit {name}"))

    def exportClicked_(self, sender):
        name = self.name_field.stringValue().strip() or self.current_kit_name
        if not name:
            self.controller.announceMessage_(tr("エクスポートするにはキット名が必要です",
                                                "A kit name is needed to export"))
            return
        if not self.current_kit_cells:
            self.controller.announceMessage_(tr("セルにサンプルが割り当てられていません",
                                                "No samples are assigned to cells"))
            return
        try:
            kit_dir, copied = core.export_kit_to_battery4_folder(
                name, self.current_kit_cells
            )
            core.beep(core.SND_OK)
            self.controller.announceMessage_(
                tr(f"キット {name} をエクスポートしました。"
                   f"{len(copied)} 個のサンプルを {os.path.basename(kit_dir)} にコピーしました。",
                   f"Exported kit {name}. Copied {plural(len(copied), 'sample')} to "
                   f"{os.path.basename(kit_dir)}.")
            )
        except Exception as e:
            core.beep(core.SND_ERROR)
            self.controller.announceHighMessage_(tr(f"エクスポートに失敗しました。{e}",
                                                    f"Export failed. {e}"))

    def previewAllClicked_(self, sender):
        """割り当て済みセルを A1→A2→… の順に 0.8 秒ずつ再生する。

        Esc（キット管理ウィンドウ内）でいつでも停止できる。
        """
        if self._preview_running:
            self.controller.announceMessage_(tr("すでに一括プレビュー中です。エスケープで停止できます",
                                                "The kit preview is already playing. "
                                                "Press Escape to stop it"))
            return
        paths = [self.current_kit_cells[c] for c in self.cell_names()
                 if c in self.current_kit_cells]
        if not paths:
            self.controller.announceMessage_(tr("プレビューするサンプルがありません",
                                                "There are no samples to preview"))
            return
        self._preview_cancel = False
        self._preview_running = True
        self.controller.announceMessage_(
            tr(f"{len(paths)} 個のサンプルを順番に再生します。エスケープで停止します",
               f"Playing {plural(len(paths), 'sample')} in order. Press Escape to stop")
        )

        def worker():
            try:
                for p in paths:
                    if self._preview_cancel:
                        break
                    if not os.path.exists(p):
                        continue
                    try:
                        self._preview_proc = subprocess.Popen(
                            ["afplay", "-t", "0.8", p],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        )
                        self._preview_proc.wait(timeout=5)
                    except Exception:
                        pass
                    time.sleep(0.15)
            finally:
                self._preview_proc = None
                self._preview_running = False

        threading.Thread(target=worker, daemon=True).start()
