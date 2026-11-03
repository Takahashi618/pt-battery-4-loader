"""
Battery 4 Sample Loader — 設定ウィンドウ（フェーズ3）
=====================================================

メインウィンドウ（app_main.AppDelegate）から Cmd+, で開く独立ウィンドウ。

セクション（Tab順）:
  A. 検索フォルダ … 登録フォルダの一覧・追加・削除
  B. ロード動作   … カウントダウン秒数 / 次セル自動移動
  C. セルラベル   … 各セルの役割名（A1=Kick 等）・スタンダード一括設定
  D. セル座標     … グリッドの行数・列数と2点キャリブレーション
                     （左上・右下セルのマウス位置を取得して全セルを
                     grid_calibration で補間）・権限確認

VoiceOver 対応:
  - 全ウィジェットに日本語アクセシビリティラベル
  - 状態通知は controller.announceHighMessage_（高優先度ネイティブ通知）
  - Esc / Cmd+W で閉じる
  - 設定変更は即 core.save_settings で永続化

座標取得の使い方（視覚に頼らない手順。通常は初期値のままで足りる）:
  1. Battery 4 を表示し、VoiceOver カーソルを左上のセル A1 に合わせる
  2. VO+Cmd+F5 でマウスポインタを VoiceOver カーソル位置へ移動
  3. このウィンドウの「左上セルの位置を取得」を押す（5秒のカウントダウン後に
     その時点のマウス位置を記録する。カウント中に Cmd+Tab で戻ってよい）
  4. 右下のセル（既定グリッドなら D12）も同様に取得し、
     「2点から位置合わせを保存」を押す
"""

from __future__ import annotations

import os
import threading
import time

import objc
from Cocoa import (
    NSObject,
    NSWindow,
    NSButton,
    NSTextField,
    NSPopUpButton,
    NSScrollView,
    NSTableView,
    NSTableColumn,
    NSOpenPanel,
    NSMakeRect,
    NSBackingStoreBuffered,
)

import core
import grid_calibration
from i18n import COMMA, plural, tr
# app_main からは import しない（ui_common の説明。リターンが効かなくなる）
from ui_common import click_focused_button

NSWindowStyleMaskTitled = 1 << 0
NSWindowStyleMaskClosable = 1 << 1
NSWindowStyleMaskResizable = 1 << 3
NSCommandKeyMask = 1 << 20
NSButtonTypeSwitch = 3

# 窓の高さ。見出しと部品を上から積み、どれも重ならない高さ
# （配置の検査は tests/test_cocoa_smoke.py の test_windows_layout）
SETTINGS_WINDOW_HEIGHT = 760

# スタンダードキットのラベル一括設定
# セル名は Battery 4 の実表記（行＝A〜D、列＝1〜12）。左上から横に並べる。
STANDARD_LABELS = {
    "A1": "Kick", "A2": "Snare", "A3": "Closed Hat", "A4": "Open Hat",
    "B1": "Clap", "B2": "Rim", "B3": "Tom Low", "B4": "Tom Mid",
    "C1": "Tom High", "C2": "Ride", "C3": "Crash", "C4": "Perc",
}

# ワンショットとみなす長さの選択肢（秒）。絞り込みの種別判定に使う。
# 初期値 2.0 秒は実測にもとづく（ドラムの単発音はおおむねここに収まる）。
ONESHOT_SEC_CHOICES = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0]


class SettingsWindow(NSWindow):
    """Esc / Cmd+W で閉じられる設定ウィンドウ。"""

    def cancelOperation_(self, sender):
        self.orderOut_(None)

    def performClose_(self, sender):
        self.orderOut_(None)

    def keyDown_(self, event):
        chars = event.charactersIgnoringModifiers()
        mods = event.modifierFlags()
        if chars == "\x1b":
            self.orderOut_(None)
            return
        if (mods & NSCommandKeyMask) and chars == "w":
            self.orderOut_(None)
            return
        # リターンでフォーカス中のボタンを実行する
        if click_focused_button(self, event):
            return
        objc.super(SettingsWindow, self).keyDown_(event)


class FolderListDataSource(NSObject):
    """登録フォルダ一覧を表示する単一列テーブル。"""

    def initWithPaths_(self, paths):
        self = objc.super(FolderListDataSource, self).init()
        if self is None:
            return None
        self._paths = list(paths)
        return self

    def setPaths_(self, paths):
        self._paths = list(paths)

    def numberOfRowsInTableView_(self, tableView):
        return len(self._paths)

    def tableView_objectValueForTableColumn_row_(self, tableView, column, row):
        if 0 <= row < len(self._paths):
            return self._paths[row]
        return ""


class SettingsManager(NSObject):
    """設定ウィンドウのコントローラ。

    メインの AppDelegate を controller として受け取り、settings 辞書を共有する。
    設定変更は即 core.save_settings で保存する。
    """

    def initWithController_(self, controller):
        self = objc.super(SettingsManager, self).init()
        if self is None:
            return None
        self.controller = controller     # app_main.AppDelegate
        self.window = None
        self._buttons = []               # Tab の移動順に使う（_button が積む）
        # 取得済み基準点 {"first": (rx, ry), "last": (rx, ry)}（描画領域からの比率）
        self._captured = {}
        return self

    # ------------------------------------------------------------------
    # 表示
    # ------------------------------------------------------------------
    def show(self):
        if self.window is None:
            self._build_window()
        self._refresh_folder_list()
        self._refresh_coord_info()
        self._sync_widgets_from_settings()
        self.window.makeKeyAndOrderFront_(None)
        from Cocoa import NSApplication
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.window.makeFirstResponder_(self.folder_table)
        self.controller.announceMessage_(
            tr("設定を開きました。タブで項目を移動できます。"
               "エスケープまたはコマンドWで閉じます。",
               "Settings opened. Press Tab to move between items. Press Escape or "
               "Command+W to close.")
        )

    # ------------------------------------------------------------------
    # ウィンドウ構築
    # ------------------------------------------------------------------
    def _build_window(self):
        """窓を組み立てる（出すのは show）。

        **部品を重ねない。** 以前は「権限を与えるアプリのパスをコピー」の
        ボタンが説明文の上に、「セルラベル」の見出しがワンショットの行の
        上に重なっていた（2026-10-24 のレビュー）。部品が窓からはみ出して
        いないか・重なっていないかはテストで確かめる。

        **Tab は見た目の順に回る。** 以前はポップアップを全部回ってから
        ボタンへ行く順で、ラベルを入力して Tab を押すと「このセルに設定」
        ではなくグリッドの行数へ飛んでいた。
        """
        style = (
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable
        )
        rect = NSMakeRect(220, 80, 580, SETTINGS_WINDOW_HEIGHT)
        win = SettingsWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False
        )
        win.setTitle_(tr("設定", "Settings"))
        win.setReleasedWhenClosed_(False)
        content = win.contentView()

        # --- A. 検索フォルダ ---
        self._label(content, tr("検索フォルダ（スキャン対象）",
                                "Search folders (scan targets)"), NSMakeRect(20, 724, 300, 22))
        folder_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(20, 620, 390, 100))
        folder_scroll.setHasVerticalScroller_(True)
        folder_scroll.setBorderType_(1)
        self.folder_table = NSTableView.alloc().initWithFrame_(NSMakeRect(0, 0, 390, 100))
        fcol = NSTableColumn.alloc().initWithIdentifier_("folder")
        fcol.setTitle_(tr("フォルダ", "Folder"))
        fcol.setWidth_(370)
        # 編集可のままだとフォーカス時にセルの編集ビューが responder を奪う
        fcol.setEditable_(False)
        self.folder_table.addTableColumn_(fcol)
        self.folder_table.setAccessibilityLabel_(
            tr("検索フォルダ一覧。未登録の場合は Mac 全体を Spotlight で検索します",
               "Search folders. If none are registered, the whole Mac is searched "
               "with Spotlight")
        )
        self.folder_ds = FolderListDataSource.alloc().initWithPaths_([])
        self.folder_table.setDataSource_(self.folder_ds)
        folder_scroll.setDocumentView_(self.folder_table)
        content.addSubview_(folder_scroll)

        add_folder = self._button(content, tr("フォルダを追加", "Add Folder"), "addFolderClicked:",
                                  NSMakeRect(420, 686, 140, 28),
                                  tr("フォルダを追加。フォルダ選択ダイアログを開きます",
                                     "Add Folder. Opens the folder selection dialog"))
        remove_folder = self._button(content, tr("選択を削除",
                                                 "Remove Selected"), "removeFolderClicked:",
                                     NSMakeRect(420, 652, 140, 28),
                                     tr("選択を削除。選択中のフォルダを検索対象から外します",
                                        "Remove Selected. Removes the selected "
                                        "folder from the search"))

        # --- B. ロード動作 ---
        self._label(content, tr("カウントダウン秒数",
                                "Countdown seconds"), NSMakeRect(20, 580, 150, 22))
        self.countdown_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(180, 576, 100, 28), False
        )
        for n in range(0, 10):
            self.countdown_popup.addItemWithTitle_(tr(f"{n} 秒", f"{plural(n, 'second')}"))
        self.countdown_popup.setAccessibilityLabel_(tr("ロード前のカウントダウン秒数",
                                                       "Countdown seconds before "
                                                       "loading"))
        self.countdown_popup.setTarget_(self)
        self.countdown_popup.setAction_("countdownChanged:")
        content.addSubview_(self.countdown_popup)

        self.advance_check = NSButton.alloc().initWithFrame_(NSMakeRect(300, 576, 260, 28))
        self.advance_check.setButtonType_(NSButtonTypeSwitch)
        self.advance_check.setTitle_(tr("ロード後に次のセルへ自動移動",
                                        "Move to the next cell after loading"))
        self.advance_check.setAccessibilityLabel_(tr("ロード後に次のセルへ自動移動",
                                                     "Move to the next cell after "
                                                     "loading"))
        self.advance_check.setTarget_(self)
        self.advance_check.setAction_("advanceChanged:")
        content.addSubview_(self.advance_check)

        # ワンショットとみなす長さの境目（絞り込みの種別判定に使う）
        self._label(content, tr("ワンショットとみなす長さ",
                                "Length to treat as a one-shot"), NSMakeRect(20, 544, 190, 22))
        self.oneshot_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(215, 542, 150, 26), False
        )
        for sec in ONESHOT_SEC_CHOICES:
            self.oneshot_popup.addItemWithTitle_(tr(f"{sec} 秒以下",
                                                    f"{plural(sec, 'second')} or less"))
        self.oneshot_popup.setAccessibilityLabel_(
            tr("ワンショットとみなす長さの上限。これより長いサンプルはループとして扱います",
               "Longest length to treat as a one-shot. Longer samples are treated as "
               "loops")
        )
        self.oneshot_popup.setTarget_(self)
        self.oneshot_popup.setAction_("oneshotSecChanged:")
        content.addSubview_(self.oneshot_popup)

        # --- C. セルラベル ---
        self._label(content, tr("セルラベル（セルの役割名）",
                                "Cell labels (the role of each cell)"), NSMakeRect(20, 504, 300, 22))
        self.label_cell_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(20, 470, 130, 28), False
        )
        for c in self._cells():
            self.label_cell_popup.addItemWithTitle_(c)
        self.label_cell_popup.setAccessibilityLabel_(tr("ラベルを設定するセル", "Cell to label"))
        self.label_cell_popup.setTarget_(self)
        self.label_cell_popup.setAction_("labelCellChanged:")
        content.addSubview_(self.label_cell_popup)

        self.label_field = NSTextField.alloc().initWithFrame_(NSMakeRect(160, 470, 190, 28))
        self.label_field.setPlaceholderString_(tr("例: Kick", "Example: Kick"))
        self.label_field.setAccessibilityLabel_(tr("セルのラベル名", "Cell label"))
        content.addSubview_(self.label_field)

        apply_label = self._button(content, tr("このセルに設定",
                                               "Set for This Cell"), "applyLabelClicked:",
                                   NSMakeRect(360, 470, 140, 28),
                                   tr("このセルに設定。入力したラベルを選択中のセルに設定します",
                                      "Set for This Cell. Sets the label you entered "
                                      "on the selected cell"))
        standard = self._button(content, tr("スタンダードキットを一括設定", "Set Standard Kit Labels"),
                                "standardLabelsClicked:",
                                NSMakeRect(20, 436, 220, 28),
                                tr("スタンダードキットを一括設定。"
                                   "キック・スネアなど標準的なラベルを左上のセルから一括設定します",
                                   "Set Standard Kit Labels. Sets standard labels "
                                   "such as Kick and Snare, starting from the top "
                                   "left cell"))
        clear = self._button(content, tr("ラベルを全て消去",
                                         "Clear All Labels"), "clearLabelsClicked:",
                             NSMakeRect(250, 436, 140, 28),
                             tr("ラベルを全て消去。全セルのラベルを消去します",
                                "Clear All Labels. Clears the labels of all cells"))

        # --- D. セル位置（比率）とグリッドの大きさ ---
        self._label(content, tr("セル位置（Battery 4 の画面位置）",
                                "Cell positions (where they are in the Battery 4 "
                                "window)"), NSMakeRect(20, 396, 540, 22))

        self._label(content, tr("グリッド", "Grid"), NSMakeRect(20, 366, 60, 22))
        self.rows_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(85, 364, 100, 26), False
        )
        for r in range(1, grid_calibration.MAX_ROWS + 1):
            self.rows_popup.addItemWithTitle_(tr(f"{r} 行", f"{plural(r, 'row')}"))
        self.rows_popup.setAccessibilityLabel_(tr("グリッドの行数。Battery の既定は4行",
                                                  "Number of grid rows. The Battery "
                                                  "default is 4 rows"))
        self.rows_popup.setTarget_(self)
        self.rows_popup.setAction_("gridChanged:")
        content.addSubview_(self.rows_popup)

        self.cols_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(195, 364, 130, 26), False
        )
        for c in range(1, grid_calibration.MAX_COLS + 1):
            self.cols_popup.addItemWithTitle_(tr(f"{c} 列", f"{plural(c, 'column')}"))
        self.cols_popup.setAccessibilityLabel_(tr("グリッドの列数。Battery の既定は12列",
                                                  "Number of grid columns. The "
                                                  "Battery default is 12 columns"))
        self.cols_popup.setTarget_(self)
        self.cols_popup.setAction_("gridChanged:")
        content.addSubview_(self.cols_popup)

        self.coord_info = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 288, 540, 70))
        self.coord_info.setBezeled_(False)
        self.coord_info.setDrawsBackground_(False)
        self.coord_info.setEditable_(False)
        self.coord_info.setSelectable_(True)
        self.coord_info.setAccessibilityLabel_(tr("現在のセル位置の説明",
                                                  "Description of the current cell "
                                                  "positions"))
        content.addSubview_(self.coord_info)

        # 2 列に並べ、行ごとに左から右へ Tab が進む
        position_buttons = [
            self._button(content, tr("Battery を検出",
                                     "Detect Battery"), "detectBatteryClicked:",
                         NSMakeRect(20, 252, 260, 28),
                         tr("Battery を検出。開いている Battery のプラグインの画面を探して読み上げます",
                            "Detect Battery. Looks for open Battery plug-in windows "
                            "and reads them out")),
            self._button(content, tr("アクセシビリティ権限を確認",
                                     "Check Accessibility Permission"), "checkPermissionClicked:",
                         NSMakeRect(300, 252, 260, 28),
                         tr("アクセシビリティ権限を確認。自動ドラッグに必要な許可の状態と、"
                            "許可すべきアプリの名前を読み上げます",
                            "Check Accessibility Permission. Reads out the "
                            "permission state needed for automatic dragging, and the "
                            "name of the app to allow")),
            self._button(content, tr("権限を与えるアプリのパスをコピー", "Copy Path of App to Allow"),
                         "copyPermissionPathClicked:",
                         NSMakeRect(300, 218, 260, 28),
                         tr("権限を与えるアプリのパスをコピー。"
                            "システム設定の一覧に対象が無いとき、追加ボタンで指定するパスです",
                            "Copy Path of App to Allow. This is the path to give to "
                            "the Add button when the app is not in the System "
                            "Settings list")),
            self._button(content, tr("左上セルの位置を取得",
                                     "Capture Top Left Cell"), "captureFirstClicked:",
                         NSMakeRect(20, 184, 260, 28),
                         tr("左上セルの位置を取得。5秒後のマウスの位置を、左上のセルの位置として覚えます",
                            "Capture Top Left Cell. Remembers the mouse position "
                            "after 5 seconds as the position of the top left cell")),
            self._button(content, tr("右下セルの位置を取得",
                                     "Capture Bottom Right Cell"), "captureLastClicked:",
                         NSMakeRect(300, 184, 260, 28),
                         tr("右下セルの位置を取得。5秒後のマウスの位置を、右下のセルの位置として覚えます",
                            "Capture Bottom Right Cell. Remembers the mouse position "
                            "after 5 seconds as the position of the bottom right cell")),
            self._button(content, tr("2点から位置合わせを保存",
                                     "Save Calibration from 2 Points"), "computeCoordsClicked:",
                         NSMakeRect(20, 150, 260, 28),
                         tr("2点から位置合わせを保存。取得した2点から全セルの位置を計算して保存します",
                            "Save Calibration from 2 Points. Works out all cell "
                            "positions from the 2 captured points and saves them")),
            self._button(content, tr("位置を初期値に戻す", "Reset Positions"), "resetLayoutClicked:",
                         NSMakeRect(300, 150, 260, 28),
                         tr("位置を初期値に戻す。セル位置を実測にもとづく初期値に戻します",
                            "Reset Positions. Returns the cell positions to the "
                            "measured defaults")),
        ]

        # 補足説明（読み上げ・視認両用）
        note = (
            tr("セル位置は Battery のウィンドウを基準にした比率で覚えるため、"
               "ウィンドウを動かしても取り直す必要はありません。"
               "通常は初期値のままロードできます。"
               "ずれる場合だけ位置合わせをします。手順は、Battery の左上のセルに "
               "VoiceOver カーソルを合わせ、VO+コマンド+F5 でマウスポインタを移動してから "
               "取得ボタンを押します。5秒のカウントダウン中に Cmd+Tab で "
               "Battery に切り替えても構いません。",
               "Cell positions are kept as ratios of the Battery window, so you do "
               "not need to capture them again when the window moves. Normally you "
               "can load with the defaults. Calibrate only if they are off. To do "
               "so, put the VoiceOver cursor on the top left cell of Battery, move "
               "the mouse pointer there with VO+Command+F5, then press the capture "
               "button. You may switch to Battery with Command+Tab during the "
               "5-second countdown.")
        )
        note_field = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 62, 540, 82))
        note_field.setStringValue_(note)
        note_field.setBezeled_(False)
        note_field.setDrawsBackground_(False)
        note_field.setEditable_(False)
        note_field.setSelectable_(True)
        note_field.setAccessibilityLabel_(tr("座標取得手順の説明", "How to capture positions"))
        content.addSubview_(note_field)

        # ウィンドウ内ステータス
        self.status_field = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 8, 540, 50))
        self.status_field.setStringValue_("")
        self.status_field.setBezeled_(False)
        self.status_field.setDrawsBackground_(False)
        self.status_field.setEditable_(False)
        self.status_field.setSelectable_(False)
        self.status_field.setAccessibilityLabel_(tr("設定の状態", "Settings status"))
        content.addSubview_(self.status_field)

        # Tab の移動順を明示する。既定のままだとスクロールバーに
        # フォーカスが行き、一覧にキーが届かなくなる（メイン画面で発生）。
        # 並びは見た目の順（上から、行の中は左から）
        order = [
            self.folder_table, add_folder, remove_folder,
            self.countdown_popup, self.advance_check, self.oneshot_popup,
            self.label_cell_popup, self.label_field, apply_label,
            standard, clear,
            self.rows_popup, self.cols_popup,
        ] + position_buttons
        for cur, nxt in zip(order, order[1:] + order[:1]):
            cur.setNextKeyView_(nxt)
        win.setInitialFirstResponder_(self.folder_table)
        self._key_view_order = order

        self.window = win
        win.center()

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
    def _button(self, parent, title, action, frame, ax_label):
        btn = NSButton.alloc().initWithFrame_(frame)
        btn.setTitle_(title)
        btn.setAccessibilityLabel_(ax_label)
        btn.setTarget_(self)
        btn.setAction_(action)
        btn.setBezelStyle_(1)
        parent.addSubview_(btn)
        # Tab の移動順をつなぐために作った順で覚えておく
        self._buttons.append(btn)
        return btn

    @objc.python_method
    def _status(self, text, high=True):
        """ウィンドウ内ステータス表示と VoiceOver アナウンスを同時に行う。

        high=True（既定）は高優先度＝読み上げ中でも割り込む。設定ウィンドウの
        通知は権限エラーや位置合わせ結果など操作の成否そのものなので、
        カウントダウン読み上げ等に埋もれないよう割り込みで伝える。
        """
        if self.window is not None:
            self.status_field.setStringValue_(text)
        if high:
            self.controller.announceHighMessage_(text)
        else:
            self.controller.announceMessage_(text)

    # ------------------------------------------------------------------
    # 設定値 ⇔ ウィジェット同期
    # ------------------------------------------------------------------
    @objc.python_method
    def _cells(self):
        """現在のグリッド設定に対応するセル名リスト（メイン側と同じ定義を使う）。"""
        return self.controller.cell_names()

    @objc.python_method
    def _layout(self):
        return grid_calibration.normalize_layout(
            self.controller.settings.get("cell_layout")
        )

    @objc.python_method
    def _sync_widgets_from_settings(self):
        s = self.controller.settings
        sec = int(s.get("countdown_sec", 3))
        self.countdown_popup.selectItemAtIndex_(max(0, min(9, sec)))
        self.advance_check.setState_(1 if s.get("auto_advance_cell", True) else 0)
        # ワンショットの長さの境目（一覧に無い値なら一番近いものを選ぶ）
        try:
            cur = float(s.get("oneshot_max_sec", core.DEFAULT_ONESHOT_MAX_SEC))
        except (TypeError, ValueError):
            cur = core.DEFAULT_ONESHOT_MAX_SEC
        nearest = min(
            range(len(ONESHOT_SEC_CHOICES)),
            key=lambda i: abs(ONESHOT_SEC_CHOICES[i] - cur),
        )
        self.oneshot_popup.selectItemAtIndex_(nearest)
        lay = self._layout()
        self.rows_popup.selectItemAtIndex_(
            max(0, min(grid_calibration.MAX_ROWS - 1, lay["rows"] - 1))
        )
        self.cols_popup.selectItemAtIndex_(
            max(0, min(grid_calibration.MAX_COLS - 1, lay["cols"] - 1))
        )
        self._refresh_label_cells()
        self._sync_label_field()

    @objc.python_method
    def _refresh_label_cells(self):
        """ラベル設定用のセル一覧を現在のグリッドに合わせる。"""
        prev = self.label_cell_popup.titleOfSelectedItem()
        self.label_cell_popup.removeAllItems()
        cells = self._cells()
        for c in cells:
            self.label_cell_popup.addItemWithTitle_(c)
        if prev in cells:
            self.label_cell_popup.selectItemAtIndex_(cells.index(prev))

    @objc.python_method
    def _sync_label_field(self):
        cells = self._cells()
        idx = self.label_cell_popup.indexOfSelectedItem()
        if not (0 <= idx < len(cells)):
            return
        labels = self.controller.settings.get("cell_labels", {})
        self.label_field.setStringValue_(labels.get(cells[idx], ""))

    @objc.python_method
    def _refresh_folder_list(self):
        folders = self.controller.settings.get("search_folders", [])
        self.folder_ds.setPaths_(folders)
        self.folder_table.reloadData()

    @objc.python_method
    def _refresh_coord_info(self):
        """現在のレイアウトと、いま開いている Battery の状況を説明する。"""
        lay = self._layout()
        win = None
        try:
            win = core.find_battery_window(
                self.controller.settings.get("target_track", "")
            )
        except Exception:
            win = None
        view = win.get("view") if win else None
        text = grid_calibration.describe_layout(lay, view)
        if win:
            where = win.get("track") or win.get("plugin")
            text = tr(f"{where} を検出。", f"{where} detected. ") + text
        else:
            text = tr("Battery のウィンドウは見つかっていません。", "No Battery window found. ") + text
        got = []
        if "first" in self._captured:
            got.append(tr("左上セルの位置は取得済み", "Top left cell captured"))
        if "last" in self._captured:
            got.append(tr("右下セルの位置は取得済み", "Bottom right cell captured"))
        if got:
            text += tr("。", ". ") + COMMA.join(got)
        self.coord_info.setStringValue_(text)

    @objc.python_method
    def _save(self):
        core.save_settings(self.controller.settings)

    # ------------------------------------------------------------------
    # A. フォルダ
    # ------------------------------------------------------------------
    def addFolderClicked_(self, sender):
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseDirectories_(True)
        panel.setCanChooseFiles_(False)
        panel.setAllowsMultipleSelection_(True)
        panel.setPrompt_(tr("追加", "Add"))
        panel.setMessage_(tr("スキャン対象に追加するフォルダを選択してください",
                             "Choose a folder to add to the scan targets"))
        if panel.runModal() != 1:  # NSModalResponseOK
            self._status(tr("フォルダの追加をキャンセルしました", "Adding a folder was canceled"))
            return
        folders = self.controller.settings.setdefault("search_folders", [])
        added = 0
        for url in panel.URLs():
            p = url.path()
            if p and p not in folders:
                folders.append(p)
                added += 1
        self._save()
        self._refresh_folder_list()
        self._status(
            tr(f"{added} 個のフォルダを追加しました。合計 {len(folders)} 個。"
               "スキャン（コマンド+O）で反映されます。",
               f"Added {plural(added, 'folder')}. {len(folders)} in all. They take "
               "effect when you scan (Command+O).")
        )

    def removeFolderClicked_(self, sender):
        folders = self.controller.settings.get("search_folders", [])
        row = self.folder_table.selectedRow()
        if not (0 <= row < len(folders)):
            self._status(tr("削除するフォルダをリストで選択してください",
                            "Select a folder to remove in the list"))
            return
        removed = folders.pop(row)
        self._save()
        self._refresh_folder_list()
        self._status(tr(f"削除しました：{os.path.basename(removed) or removed}",
                        f"Removed: {os.path.basename(removed) or removed}"))

    # ------------------------------------------------------------------
    # B. ロード動作
    # ------------------------------------------------------------------
    def countdownChanged_(self, sender):
        sec = int(self.countdown_popup.indexOfSelectedItem())
        self.controller.settings["countdown_sec"] = sec
        self._save()
        self._status(tr(f"カウントダウンを {sec} 秒にしました",
                        f"Countdown set to {plural(sec, 'second')}"))

    def advanceChanged_(self, sender):
        on = bool(self.advance_check.state())
        self.controller.settings["auto_advance_cell"] = on
        self._save()
        self._status(
            tr("ロード後に次のセルへ自動移動します",
               "Will move to the next cell after loading") if on else tr("次セルへの自動移動をオフにしました",
                                              "Moving to the next cell is off")
        )

    def oneshotSecChanged_(self, sender):
        """ワンショットとみなす長さの上限を変える。

        絞り込みの種別判定にすぐ反映する（メイン画面のリストを作り直す）。
        """
        idx = self.oneshot_popup.indexOfSelectedItem()
        if not (0 <= idx < len(ONESHOT_SEC_CHOICES)):
            return
        sec = ONESHOT_SEC_CHOICES[idx]
        self.controller.settings["oneshot_max_sec"] = sec
        self._save()
        try:
            self.controller._refresh_list()
        except Exception:
            pass
        self._status(
            tr(f"{sec} 秒以下をワンショットとみなします。"
               "これより長いサンプルはループとして扱います。",
               f"{plural(sec, 'second')} or less counts as a one-shot. Longer samples "
               "are treated as loops.")
        )

    # ------------------------------------------------------------------
    # C. セルラベル
    # ------------------------------------------------------------------
    def labelCellChanged_(self, sender):
        self._sync_label_field()

    def applyLabelClicked_(self, sender):
        cells = self._cells()
        idx = self.label_cell_popup.indexOfSelectedItem()
        if not (0 <= idx < len(cells)):
            self._status(tr("ラベルを設定するセルを選んでください", "Choose a cell to label"))
            return
        cell = cells[idx]
        text = self.label_field.stringValue().strip()
        labels = self.controller.settings.setdefault("cell_labels", {})
        labels[cell] = text
        self._save()
        self._refresh_main_cell_popup()
        if text:
            self._status(tr(f"セル {cell} のラベルを {text} にしました",
                            f"Label of cell {cell} set to {text}"))
        else:
            self._status(tr(f"セル {cell} のラベルを消去しました", f"Label of cell {cell} cleared"))

    def standardLabelsClicked_(self, sender):
        labels = {c: "" for c in self._cells()}
        labels.update(
            {c: v for c, v in STANDARD_LABELS.items() if c in labels}
        )
        self.controller.settings["cell_labels"] = labels
        self._save()
        self._sync_label_field()
        self._refresh_main_cell_popup()
        self._status(
            tr("スタンダードキットのラベルを設定しました。"
               "A1 キック、A2 スネア、A3 クローズドハットなど。",
               "Standard kit labels set. A1 Kick, A2 Snare, A3 Closed Hat, and so on.")
        )

    def clearLabelsClicked_(self, sender):
        self.controller.settings["cell_labels"] = {c: "" for c in self._cells()}
        self._save()
        self._sync_label_field()
        self._refresh_main_cell_popup()
        self._status(tr("全セルのラベルを消去しました", "All cell labels cleared"))

    @objc.python_method
    def _refresh_main_cell_popup(self):
        """メインウィンドウのセルポップアップを再構築し、選択を維持する。"""
        c = self.controller
        c._populate_cell_popup()
        try:
            names = self._cells()
            if c.selected_cell not in names:
                c.selected_cell = names[0]
            c.cell_popup.selectItemAtIndex_(names.index(c.selected_cell))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # D. セル座標
    # ------------------------------------------------------------------
    def gridChanged_(self, sender):
        """グリッドの行数・列数を変更する。"""
        rows = self.rows_popup.indexOfSelectedItem() + 1
        cols = self.cols_popup.indexOfSelectedItem() + 1
        lay = self._layout()
        lay["rows"], lay["cols"] = rows, cols
        ok, msg = grid_calibration.validate_layout(lay)
        if not ok:
            core.beep(core.SND_ERROR)
            self._status(tr(f"この大きさにはできません。{msg}", f"The grid cannot be this size. {msg}"))
            self._sync_widgets_from_settings()
            return
        self.controller.settings["cell_layout"] = lay
        # セル集合が変わるのでラベルを揃える
        self.controller.settings, _ = core.migrate_settings(self.controller.settings)
        self._save()
        # 取得済みの位置合わせ点は旧グリッドの隅の位置なので捨てる。
        # 残すと「2点から位置合わせを保存」が別のセル名として誤解釈し、
        # 検証を通る不正なレイアウトが保存されてしまう
        had_captured = bool(self._captured)
        self._captured.clear()
        self._refresh_label_cells()
        self._sync_label_field()
        self._refresh_coord_info()
        self._refresh_main_cell_popup()
        core.beep(core.SND_OK)
        msg = tr(f"グリッドを {rows} 行 かける {cols} 列 に変更しました",
                 f"Grid changed to {plural(rows, 'row')} by {plural(cols, 'column')}")
        if had_captured:
            msg += tr("。取得済みの位置はクリアしました。位置合わせをやり直してください",
                      ". The captured positions were cleared. Calibrate again")
        self._status(msg)

    def detectBatteryClicked_(self, sender):
        """開いている Battery のプラグインウィンドウを探して読み上げる。"""
        try:
            wins = core.battery_windows()
        except Exception:
            wins = []
        self._refresh_coord_info()
        if not wins:
            core.beep(core.SND_ERROR)
            self._status(
                tr("Battery のプラグインウィンドウが見つかりません。"
                   "Pro Tools で Battery 4 の画面を開いてから、もう一度お試しください。"
                   "アクセシビリティ権限が未許可の場合も見つかりません。",
                   "No Battery plug-in window found. Open the Battery 4 window in "
                   "Pro Tools and try again. It is also not found without "
                   "Accessibility permission.")
            )
            return
        core.beep(core.SND_OK)
        names = COMMA.join(w.get("track") or w.get("plugin") for w in wins)
        self._status(
            tr(f"Battery を {len(wins)} 個検出しました。{names}。"
               "メイン画面のロード先 Battery（コマンド+B）で選べます。",
               f"Detected {plural(len(wins), 'Battery instance')}. {names}. You can "
               "choose it as the load target in the main window (Command+B).")
        )

    def captureFirstClicked_(self, sender):
        self._capture_point("first")

    def captureLastClicked_(self, sender):
        self._capture_point("last")

    @objc.python_method
    def _corner_cells(self):
        """位置合わせに使う左上・右下のセル名を返す。"""
        cells = self._cells()
        return cells[0], cells[-1]

    @objc.python_method
    def _capture_point(self, which):
        """5秒カウントダウン後のマウス位置を基準点として取得する。

        取得した画面座標は、その時点の Battery 描画領域からの比率に直して
        覚える（絶対座標では窓が動くと使えなくなるため）。

        **ロードやトラック切替と同じ排他を取る。** 併走させると、
        ロードが合成マウスでポインタをセルへ運んでいる最中の位置を
        基準点として覚えてしまい、以後ずっと違うセルへ落ちる
        レイアウトが保存される。
        """
        if not core.QUARTZ_OK:
            self._status(tr("この環境ではマウス座標を取得できません（Quartz なし）",
                            "The mouse position cannot be read in this environment "
                            "(no Quartz)"))
            return
        if not self.controller._begin_automation(tr("位置の取得", "Position capture")):
            return
        first_cell, last_cell = self._corner_cells()
        cell = first_cell if which == "first" else last_cell
        self._status(
            tr(f"5秒後にマウス位置を セル {cell} として取得します。"
               "Battery のそのセルへマウスポインタを移動してください。",
               f"In 5 seconds the mouse position will be captured as cell {cell}. "
               "Move the mouse pointer to that cell in Battery.")
        )

        def worker():
            try:
                for n in range(5, 0, -1):
                    self.controller.performSelectorOnMainThread_withObject_waitUntilDone_(
                        "announceMessage:", str(n), False
                    )
                    core.beep(core.SND_TICK)
                    time.sleep(1.0)
                pos = core.current_mouse_position()
                win = core.find_battery_window(
                    self.controller.settings.get("target_track", "")
                )
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "captureFinished:",
                    {"which": which, "cell": cell, "pos": pos,
                     "view": list(win["view"]) if win and win.get("view") else None},
                    False,
                )
            except Exception:
                self.controller._end_automation()

        threading.Thread(target=worker, daemon=True).start()

    def captureFinished_(self, info):
        self.controller._end_automation()
        which = info["which"]
        cell = info["cell"]
        pos = info["pos"]
        view = info.get("view")
        if pos is None:
            core.beep(core.SND_ERROR)
            self._status(tr("マウス位置を取得できませんでした", "Could not read the mouse position"))
            return
        if not view:
            core.beep(core.SND_ERROR)
            self._status(
                tr("Battery のウィンドウが見つからないため位置を覚えられません。"
                   "Battery 4 の画面を開いた状態でやり直してください。",
                   "The position cannot be remembered because no Battery window was "
                   "found. Open the Battery 4 window and try again.")
            )
            return
        try:
            ratio = grid_calibration.point_to_ratio(
                (int(pos[0]), int(pos[1])), tuple(view)
            )
        except Exception as e:
            core.beep(core.SND_ERROR)
            self._status(tr(f"位置を比率に変換できませんでした。{e}",
                            f"Could not convert the position to a ratio. {e}"))
            return
        if not (0.0 <= ratio[0] <= 1.0 and 0.0 <= ratio[1] <= 1.0):
            core.beep(core.SND_ERROR)
            self._status(
                tr(f"取得した位置が Battery の表示範囲の外です。"
                   f"X {int(pos[0])}、Y {int(pos[1])}。セルの上で取り直してください。",
                   "The captured position is outside the Battery display area. X "
                   f"{int(pos[0])}, Y {int(pos[1])}. Capture it again over the cell.")
            )
            return
        self._captured[which] = ratio
        core.beep(core.SND_OK)
        self._refresh_coord_info()
        both = "first" in self._captured and "last" in self._captured
        if both:
            extra = tr("「2点から位置合わせを保存」を押してください。",
                       "Press \"Save Calibration from 2 Points\".")
        elif which == "first":
            extra = tr("次に右下セルの位置を取得してください。", "Next, capture the bottom right cell.")
        else:
            extra = tr("次に左上セルの位置を取得してください。", "Next, capture the top left cell.")
        self._status(
            tr(f"セル {cell} の位置を取得しました。"
               f"X {int(pos[0])}、Y {int(pos[1])}。{extra}",
               f"Captured the position of cell {cell}. X {int(pos[0])}, Y "
               f"{int(pos[1])}. {extra}")
        )

    def computeCoordsClicked_(self, sender):
        first = self._captured.get("first")
        last = self._captured.get("last")
        if not first or not last:
            if not first and not last:
                missing = tr("左上セルと右下セル", "the top left and bottom right cells")
            else:
                missing = tr("左上セル",
                             "the top left cell") if not first else tr("右下セル", "the bottom right cell")
            core.beep(core.SND_ERROR)
            self._status(tr(f"先に {missing} の位置を取得してください",
                            f"First capture the position of {missing}"))
            return
        lay = self._layout()
        first_cell, last_cell = self._corner_cells()
        try:
            new_layout = grid_calibration.layout_from_two_cells(
                first_cell, first, last_cell, last, lay["rows"], lay["cols"]
            )
        except Exception as e:
            core.beep(core.SND_ERROR)
            self._status(tr(f"位置の計算に失敗しました。{e}", f"Could not work out the positions. {e}"))
            return
        ok, msg = grid_calibration.validate_layout(new_layout)
        if not ok:
            core.beep(core.SND_ERROR)
            self._status(tr(f"位置が不正です。{msg}。取り直してください。",
                            f"The positions are invalid. {msg}. Capture them again."))
            return
        self.controller.settings["cell_layout"] = new_layout
        self._save()
        self._captured.clear()
        self._refresh_coord_info()
        core.beep(core.SND_OK)
        win = core.find_battery_window(
            self.controller.settings.get("target_track", "")
        )
        view = win.get("view") if win else None
        self._status(
            tr("セル位置を計算して保存しました。", "Cell positions worked out and saved. ")
            + grid_calibration.describe_layout(new_layout, view)
        )

    def resetLayoutClicked_(self, sender):
        """セル位置を実測にもとづく初期値に戻す（行数・列数は保つ）。"""
        lay = self._layout()
        new_layout = dict(core.DEFAULT_CELL_LAYOUT)
        new_layout["rows"], new_layout["cols"] = lay["rows"], lay["cols"]
        self.controller.settings["cell_layout"] = new_layout
        self._save()
        self._captured.clear()
        self._refresh_coord_info()
        core.beep(core.SND_OK)
        self._status(
            tr("セル位置を初期値に戻しました。", "Cell positions returned to the defaults. ")
            + grid_calibration.describe_layout(new_layout)
        )

    def checkPermissionClicked_(self, sender):
        """権限の状態を調べ、「どのアプリに許可すればよいか」まで読み上げる。

        「許可したはずなのに動かない」は、たいてい許可した対象が違うため
        （ソースから起動していると権限は python ではなくターミナルに付く）。
        推測させず、実際の権限主体の名前を伝える。
        """
        info = core.accessibility_diagnosis()
        if info["trusted"]:
            core.beep(core.SND_OK)
            self._status(info["advice"])
            return
        core.beep(core.SND_ERROR)
        # OS の許可ダイアログを出してから、システム設定の該当ページを開く
        core.accessibility_trusted(prompt=True)
        core.open_accessibility_settings()
        self._status(info["advice"] + tr("システム設定を開きました。", " System Settings opened."))

    def copyPermissionPathClicked_(self, sender):
        """権限を与える対象のパスをクリップボードにコピーする。

        システム設定の一覧に対象が出てこないとき、追加ボタンから
        「フォルダへ移動（Cmd+Shift+G）」でこのパスを貼り付けて選べる。
        """
        info = core.accessibility_diagnosis()
        path = info.get("target_path") or ""
        if not path:
            core.beep(core.SND_ERROR)
            self._status(tr("権限を与える対象のパスが分かりませんでした",
                            "Could not find the path of the app to allow"))
            return
        try:
            core.copy_to_clipboard(path)
            core.beep(core.SND_OK)
            self._status(
                tr(f"{info['target']} のパスをクリップボードにコピーしました。"
                   "システム設定のアクセシビリティで追加ボタンを押し、"
                   "コマンド+シフト+G でパスを貼り付けて選んでください。",
                   f"Copied the path of {info['target']} to the clipboard. In "
                   "Accessibility in System Settings, press the Add button, paste "
                   "the path with Command+Shift+G, and choose it.")
            )
        except Exception as e:
            core.beep(core.SND_ERROR)
            self._status(tr(f"パスをコピーできませんでした。{e}", f"Could not copy the path. {e}"))
