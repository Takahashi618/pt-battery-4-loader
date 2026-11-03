"""
Battery 4 Sample Loader — Cocoa メインウィンドウ
================================================================

VoiceOver 最適化のネイティブ Cocoa UI。メインウィンドウは
「探す → 選ぶ → ロード」に集中する。

レイアウト（Tab順）:
  フィルター欄 / スキャン / 並び替え / サンプルリスト /
  ロード先セル / ロードボタン / ステータス

主なキー操作（リスト内）:
  ↑↓ 移動  Space プレビュー再生/停止（トグル）  Esc 停止  Return ロード

コマンドキー（メニュー登録・どこでも有効）:
  Cmd+D お気に入り  Cmd+T 並び替え順送り  Cmd+Y 読み上げ補正
  Cmd+F フィルターへ  Cmd+I リストへ  Cmd+O スキャン
  Cmd+Shift+K ヘルプ  Cmd+K キット管理  Cmd+, 設定  Cmd+B ロード先Battery切替
  Cmd+1〜9 いまの行の列選択  Cmd+; 行を順送り
  Cmd+N/S/E/P キット新規/保存/エクスポート/一括プレビュー  Cmd+Q 終了

ロード先の解決:
  セル位置は絶対座標ではなく、Battery の描画領域からの比率で持つ。
  ロードのたびに core.resolve_cell_point() が、開いている Battery の
  ウィンドウ（Accessibility API で取得）へ比率を当てはめて実座標を出す。
  複数インスタンスがある場合は target_track（トラック名）で選ぶ。

ロード自動化（2段構え）:
  1. 自己ドラッグ — 自ウィンドウの DragSourceView から NSDraggingSession を
     開始し、Quartz 合成マウスでセル座標へ運ぶ。ドロップの成否を
     正確に判定できる（推奨・既定）。
  2. Finder 経由 — Finder でファイルを表示してアイコン位置から
     Quartz ドラッグする（自己ドラッグ不成立時の予備）。
  どちらもアクセシビリティ権限が必要（設定ウィンドウから許可を確認）。

起動:
  /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 app_main.py
"""

from __future__ import annotations

import os
import time
import threading

import objc
from Cocoa import (
    NSObject,
    NSApplication,
    NSApp,
    NSWindow,
    NSPanel,
    NSView,
    NSButton,
    NSTextField,
    NSPopUpButton,
    NSScrollView,
    NSTableView,
    NSTableColumn,
    NSMakeRect,
    NSScreen,
    NSURL,
    NSWorkspace,
    NSPasteboardItem,
    NSDraggingItem,
    NSBackingStoreBuffered,
    NSApplicationActivationPolicyRegular,
)
from Foundation import NSIndexSet, NSMakeRange
from PyObjCTools import AppHelper

NSButtonTypeSwitch = 3
_CMD_SHIFT_MASK = (1 << 20) | (1 << 17)   # Cmd+Shift のキー修飾

import core
import name_readout
import grid_calibration
import version
from i18n import COMMA, plural, tr
# サブウィンドウと共有する部品。**サブウィンドウから app_main を import しない**
# ための置き場（理由は ui_common の説明）。ここでも名前を出しておく
from ui_common import click_focused_button

try:
    from Cocoa import NSPasteboardTypeFileURL
except Exception:
    NSPasteboardTypeFileURL = "public.file-url"

# VoiceOver へのネイティブ通知に使う定数
try:
    from Cocoa import (
        NSAccessibilityPostNotificationWithUserInfo,
        NSAccessibilityAnnouncementRequestedNotification,
        NSAccessibilityAnnouncementKey,
        NSAccessibilityPriorityKey,
        NSAccessibilityPriorityHigh,
        NSAccessibilityPriorityMedium,
    )
    _NATIVE_ANNOUNCE_OK = True
except Exception:
    _NATIVE_ANNOUNCE_OK = False


def announce(message: str, high: bool = False):
    """
    VoiceOver に確実にアナウンスする。

    NSAccessibilityPostNotificationWithUserInfo で AnnouncementRequested 通知を
    送る。これは VoiceOver の正規 API で、say と違って二重読みにならず
    VoiceOver の声に統合される。high=True は高優先度（読み上げ中でも割り込む。
    お気に入り・ロード結果など必ず伝えたい通知に使う）。
    メインスレッドから呼ぶこと（ワーカーからは
    AppDelegate.announceMessage_ を performSelectorOnMainThread で呼ぶ）。

    送り先は窓、窓が無ければアプリそのもの。このアプリが後ろにいると
    keyWindow も mainWindow も無くなる（2026-10-24 に実測）。以前はそこで
    osascript の予備へ回っていたが、その方法は macOS 15 で断られて
    何も読まれない。
    **後ろにいるアプリの読み上げは VoiceOver が読まない**ので、
    結果を伝えるときは AppDelegate._set_status(front=True) で前へ戻ってから読む。
    """
    if os.environ.get("BATTERY4_SILENT"):
        return
    if _NATIVE_ANNOUNCE_OK:
        try:
            app = NSApplication.sharedApplication()
            target = app.keyWindow() or app.mainWindow() or app
            prio = (
                NSAccessibilityPriorityHigh
                if high
                else NSAccessibilityPriorityMedium
            )
            info = {
                NSAccessibilityAnnouncementKey: message,
                NSAccessibilityPriorityKey: prio,
            }
            NSAccessibilityPostNotificationWithUserInfo(
                target, NSAccessibilityAnnouncementRequestedNotification, info
            )
            return
        except Exception:
            pass
    core.vo_announce(message)

# ウィンドウスタイル
NSWindowStyleMaskBorderless = 0
NSWindowStyleMaskTitled = 1 << 0
NSWindowStyleMaskClosable = 1 << 1
NSWindowStyleMaskMiniaturizable = 1 << 2
NSWindowStyleMaskResizable = 1 << 3
NSWindowStyleMaskNonactivatingPanel = 1 << 7

# ドラッグの始点の窓の層。ポップアップメニューと同じ 101 にして、
# Pro Tools の浮いたプラグイン画面（3）・Dock（20）・メニューバー（24）より上に出す
DRAG_PANEL_LEVEL = 101
DRAG_PANEL_SIZE = 32
# 始点をセルからどれだけ離して出すか（ポイント）。セルの上に重ならない距離
DRAG_START_OFFSET = 110


class SampleTableDataSource(NSObject):
    """NSTableView のデータソース。表示用ラベル列を返すだけ。"""

    def initWithRows_(self, rows):
        self = objc.super(SampleTableDataSource, self).init()
        if self is None:
            return None
        self._rows = list(rows)
        return self

    def setRows_(self, rows):
        self._rows = list(rows)

    def numberOfRowsInTableView_(self, tableView):
        return len(self._rows)

    def tableView_objectValueForTableColumn_row_(self, tableView, column, row):
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return ""


class SampleTableView(NSTableView):
    """
    サンプルリスト用の NSTableView サブクラス。

    NSTableView は矢印キーでの行移動は標準対応するが、Space / Return / F / S / R
    などの文字キーはデフォルトでビープ音になる。これらをアプリ側のハンドラに
    渡すため keyDown_ をオーバーライドする。

    handler には AppDelegate を設定し、handleListKey_(char) を呼ぶ。
    """

    def setKeyHandler_(self, handler):
        self._handler = handler

    def becomeFirstResponder(self):
        """フォーカスが来たとき、選択が外れていれば戻す。

        Tab で戻ってきたのに何も選ばれていないと、スペースを押しても
        「選択されていません」になって操作できない。直前に見ていた
        サンプルへ戻す（無ければ先頭）。
        """
        ok = objc.super(SampleTableView, self).becomeFirstResponder()
        if ok:
            handler = getattr(self, "_handler", None)
            if handler is not None:
                try:
                    handler.ensure_list_selection()
                except Exception:
                    pass
        return ok

    def keyDown_(self, event):
        handler = getattr(self, "_handler", None)
        chars = event.charactersIgnoringModifiers()
        # 修飾キー（Cmd など）が押されている場合は標準処理に回す
        modifiers = event.modifierFlags()
        NSCommandKeyMask = 1 << 20
        if handler is not None and not (modifiers & NSCommandKeyMask):
            if chars == "\x03":
                chars = "\r"    # テンキーの Enter もリターンと同じ（ボタンと揃える）
            if chars in (" ", "\r", "\x1b"):
                consumed = handler.handleListKey_(chars)
                if consumed:
                    return
        objc.super(SampleTableView, self).keyDown_(event)


class HelpWindow(NSWindow):
    """ヘルプ用ウィンドウ。Esc / Cmd+W で閉じられるようにする。"""

    def cancelOperation_(self, sender):
        # Esc キーで呼ばれる
        self.orderOut_(None)

    def performClose_(self, sender):
        self.orderOut_(None)

    def keyDown_(self, event):
        chars = event.charactersIgnoringModifiers()
        mods = event.modifierFlags()
        NSCommandKeyMask = 1 << 20
        if chars == "\x1b":  # Esc
            self.orderOut_(None)
            return
        if (mods & NSCommandKeyMask) and chars == "w":
            self.orderOut_(None)
            return
        if click_focused_button(self, event):
            return
        objc.super(HelpWindow, self).keyDown_(event)


class MainWindow(NSWindow):
    """
    メインウィンドウ。フォーカスがリスト以外（フィルター欄など）にあっても
    Esc でプレビュー停止・カウントダウン中断できるようにする。
    リターンでフォーカス中のボタンを実行できる。
    """

    def cancelOperation_(self, sender):
        delegate = NSApp.delegate()
        if delegate is not None:
            delegate.stop_preview()
            delegate.cancel_automation()

    def keyDown_(self, event):
        if click_focused_button(self, event):
            return
        objc.super(MainWindow, self).keyDown_(event)


class DragSourceView(NSView):
    """
    自アプリをドラッグ元にするための小さなビュー（自己ドラッグ方式）。

    ロード実行時、core.cg_drag がこのビュー上に合成マウスイベントを送ると、
    mouseDragged_ が NSDraggingSession を開始してファイル URL をドロップ先へ
    渡す。Finder に依存せず、ドロップが受け入れられたかどうかを
    draggingSession:endedAtPoint:operation: で正確に判定できる。

    このビューは DragSourcePanel（最前面の層の小さな窓）に載せる。
    """

    def initWithFrame_(self, frame):
        self = objc.super(DragSourceView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.filepath = None
        self.session_began = False
        self.session_ended = False
        self.drop_accepted = False
        self._armed = False
        return self

    def acceptsFirstMouse_(self, event):
        return True

    @objc.python_method
    def prepare_for_path(self, path):
        """ドラッグ対象を設定し、状態をリセットする。"""
        self.filepath = path
        self.session_began = False
        self.session_ended = False
        self.drop_accepted = False
        self._armed = False

    def mouseDown_(self, event):
        if self.filepath:
            self._armed = True

    def mouseDragged_(self, event):
        if not self._armed or not self.filepath:
            return
        self._armed = False  # セッション開始は1回だけ
        try:
            url = NSURL.fileURLWithPath_(self.filepath)
            pb_item = NSPasteboardItem.alloc().init()
            pb_item.setString_forType_(url.absoluteString(), NSPasteboardTypeFileURL)
            drag_item = NSDraggingItem.alloc().initWithPasteboardWriter_(pb_item)
            icon = NSWorkspace.sharedWorkspace().iconForFile_(self.filepath)
            loc = self.convertPoint_fromView_(event.locationInWindow(), None)
            drag_item.setDraggingFrame_contents_(
                NSMakeRect(loc.x - 16, loc.y - 16, 32, 32), icon
            )
            self.beginDraggingSessionWithItems_event_source_(
                [drag_item], event, self
            )
            self.session_began = True
        except Exception:
            self.session_ended = True
            self.drop_accepted = False

    # ---- NSDraggingSource ----
    def draggingSession_sourceOperationMaskForDraggingContext_(self, session, ctx):
        return 1  # NSDragOperationCopy（アプリ内外どちらへもコピー可）

    def draggingSession_willBeginAtPoint_(self, session, point):
        self.session_began = True

    def draggingSession_endedAtPoint_operation_(self, session, point, operation):
        # operation が None(0) 以外 = ドロップ先が受け入れた（本物の成功判定）
        self.drop_accepted = operation != 0
        self.session_ended = True
        self.filepath = None


class DragSourcePanel(NSPanel):
    """ドラッグの始点を載せる小さな窓。ロードの間だけ、セルの近くに出す。

    **最前面の層（ポップアップメニューと同じ 101）に置く。** 以前は
    メイン画面の右下に始点のビューがあったが、Pro Tools のプラグイン画面は
    浮いた窓（層 3）なので、このアプリの窓より常に上に来る。始点が
    Battery の画面の下に隠れると、マウスを押した合成イベントは
    Battery の画面に落ち、つまみやセルを動かしてしまう。画面の高さが
    1050 ポイントより低い Mac では、既定の窓の配置のままで隠れる
    （2026-10-24 のレビューで判明。この Mac では 17 ピクセル差で避けていた）。

    押しても前面にならない窓（非アクティブ化パネル）にし、キーも
    受け取らない。VoiceOver の読み上げの対象にもしない。
    """

    def canBecomeKeyWindow(self):
        return False

    def canBecomeMainWindow(self):
        return False


class AppDelegate(NSObject):
    """メインアプリ。ウィンドウと UI を構築し、操作を処理する。"""

    # 配り先も切り替え候補も、Mix ウィンドウのインサート欄から作る。
    # 見つからないときの案内は1か所に置く（食い違うと原因を探せない）
    NO_BATTERY_TRACKS = (
        tr("Battery が挿さっているトラックを見つけられませんでした。"
           "Pro Tools のミックスウィンドウが開いているか確認してください。",
           "Could not find a track with Battery inserted. Check that the Pro Tools "
           "Mix window is open.")
    )

    def applicationDidFinishLaunching_(self, notification):
        # 状態（load_* が旧形式を現行形式へ移し替える。移行結果は即保存する）
        settings = core.load_settings()
        kits = core.load_kits()
        try:
            core.save_settings(settings)
            core.save_kits(kits)
        except Exception:
            pass
        self._init_state(settings, kits)

        # 起動確認（BATTERY4_SMOKE=1）は、起動の道筋（設定の読み書き・
        # 窓とメニューの組み立て・記録）を本番どおりに通し、数秒で終わる。
        # ビルドの最中に利用者の画面を騒がせないよう、窓は出さず、
        # 許可を求める macOS の知らせも出さない
        smoke = bool(os.environ.get("BATTERY4_SMOKE"))
        self._build_window()
        if not smoke:
            self._show_main_window()
        self._refresh_list()
        _build_menu(self)
        # 権限の状態をログに残す（.app は標準出力が見えないため）
        core.log_startup_state()
        if not smoke:
            self._check_permission_at_startup()

        if smoke:
            def _auto_quit():
                time.sleep(2.5)
                AppHelper.callAfter(NSApp.terminate_, None)
            threading.Thread(target=_auto_quit, daemon=True).start()

    @objc.python_method
    def _init_state(self, settings, kits):
        """設定とキットから、画面を作る前の状態を整える。

        起動（applicationDidFinishLaunching_）と起動確認（run_selftest）の
        両方から使う。起動確認は利用者の設定ファイルを読まずに既定値で呼ぶ。
        """
        self.settings = settings
        self.kits = kits
        self.favorites = set(self.settings.get("favorites", []))
        self.recent = list(self.settings.get("recent", []))
        self.readout_mode = self.settings.get("readout_mode", "plain")
        self.sort_key = self.settings.get("sort_key", "name_asc")
        if self.sort_key not in [k for k, _ in core.SORT_OPTIONS]:
            self.sort_key = "name_asc"
        self.selected_cell = core.CELL_NAMES[0]
        self._all_samples = []
        self._display_paths = []
        self._stat_cache = {}           # サイズ/日時ソート用（スキャンごとに作り直す）
        self._random_order = None       # ランダムソートの安定順（選び直すまで固定）
        # 絞り込み条件（作者の別のアプリと同じ AND 条件）
        self._filter_kind = ""          # "" / oneshot / loop
        self._filter_pack = ""          # "" ならパック指定なし
        self._filter_fav_only = False
        self._pack_names = []           # パックポップアップの並び（表示と選択の共用）
        self._durations = {}            # {path: 長さ秒 or None}（種別判定に使う）
        self._pack_order = self.settings.get("pack_order", core.PACK_ORDER_COUNT)
        if self._pack_order not in [k for k, _ in core.PACK_ORDER_OPTIONS]:
            self._pack_order = core.PACK_ORDER_COUNT
        self._preview_proc = None
        self._preview_path = None
        # Esc で「Pro Tools を操作している処理」を中断する合図。
        # **プレビュー停止とは分けてある。** 一緒にすると、配っている
        # 最中にスペースで試聴しただけで残りのトラックが黙って省かれる
        self._automation_cancel = False
        # Pro Tools を合成イベントで操作する処理（ロード・トラック切替・
        # セル移動・画面を開く・位置の取得）の相互排他。
        # 併走するとイベントが交錯する。空文字なら空いている
        self._automation_label = ""
        self._duration_stamps = {}      # {path: (更新日時, サイズ)}
        self._help_window = None
        self._kit_manager = None
        self._settings_window = None
        self._last_selected_path = None
        self._drag_prep = None          # 自己ドラッグの開始点 (x, y)（CG座標）
        self._moved_windows = []        # ドラッグの間だけどかした窓と元の位置
        self._scanning = False          # スキャン中（重ねて始めない）
        self._front_seq = 0             # 前面へ戻ってから読む知らせの番号

    @objc.python_method
    def _check_permission_at_startup(self):
        """起動時に権限が無ければ知らせる。

        権限が無いとロードが使えないが、実際にロードを試みるまで
        気づけないと時間を無駄にする。起動時に一度だけ伝える。

        あわせて OS の許可プロンプトを出す。これでシステム設定の
        アクセシビリティ一覧に項目が登録されるので、利用者は
        「追加」をせずにチェックを入れるだけで済む。
        """
        if core.accessibility_trusted():
            return
        info = core.accessibility_diagnosis()
        # プロンプトを出してシステム設定の一覧に登録させる
        try:
            core.accessibility_trusted(prompt=True)
        except Exception:
            pass
        self._set_status(
            tr(f"アクセシビリティ権限がありません。許可する対象は {info['target']} です。"
               "許可しないとサンプルのロードができません。"
               "設定ウィンドウ、コマンド+カンマの「アクセシビリティ権限を確認」で"
               "手順を読み上げます。許可したあとはアプリを開き直してください。",
               "There is no Accessibility permission. The app to allow is "
               f"{info['target']}. Without it, samples cannot be loaded. \"Check "
               "Accessibility Permission\" in the Settings window (Command+Comma) "
               "reads out the steps. After allowing it, open the app again."),
            speak=True,
        )
        core.beep(core.SND_ERROR)

    def applicationSupportsSecureRestorableState_(self, app):
        return True

    def applicationWillTerminate_(self, notification):
        """終了時のクリーンアップ。プレビュー停止と設定保存を確実に行う。"""
        self._automation_cancel = True
        try:
            if self._preview_proc and self._preview_proc.poll() is None:
                self._preview_proc.terminate()
        except Exception:
            pass
        try:
            if self._kit_manager is not None:
                self._kit_manager.stop_kit_preview()
        except Exception:
            pass
        try:
            self._save_settings()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # ウィンドウ構築
    # ------------------------------------------------------------------
    def _build_window(self):
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable
        )
        rect = NSMakeRect(200, 200, 660, 560)
        self.window = MainWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False
        )
        self.window.setTitle_(version.APP_NAME)
        content = self.window.contentView()

        # フィルター欄
        self.filter_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(20, 510, 320, 28)
        )
        self.filter_field.setPlaceholderString_(tr("ファイル名で絞り込み", "Filter by file name"))
        self.filter_field.setAccessibilityLabel_(tr("フィルター。ファイル名で絞り込みます",
                                                    "Filter. Narrows the list by "
                                                    "file name"))
        self.filter_field.setTarget_(self)
        self.filter_field.setAction_("filterChanged:")
        self.filter_field.cell().setSendsActionOnEndEditing_(True)
        # 1文字入力するたびに即絞り込む（controlTextDidChange_ が呼ばれる）
        self.filter_field.setDelegate_(self)
        content.addSubview_(self.filter_field)

        # スキャンボタン
        self.scan_button = NSButton.alloc().initWithFrame_(NSMakeRect(350, 510, 90, 28))
        self.scan_button.setTitle_(tr("スキャン", "Scan"))
        self.scan_button.setAccessibilityLabel_(tr("スキャン。フォルダから音声ファイルを探します",
                                                   "Scan. Looks for audio files in "
                                                   "the folders"))
        self.scan_button.setTarget_(self)
        self.scan_button.setAction_("scanClicked:")
        content.addSubview_(self.scan_button)

        # 並び替えポップアップ
        self.sort_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(450, 510, 190, 28), False
        )
        for _key, label in core.SORT_OPTIONS:
            self.sort_popup.addItemWithTitle_(label)
        self.sort_popup.setAccessibilityLabel_(tr("並び替え", "Sort order"))
        self.sort_popup.setTarget_(self)
        self.sort_popup.setAction_("sortChanged:")
        keys = [k for k, _ in core.SORT_OPTIONS]
        self.sort_popup.selectItemAtIndex_(keys.index(self.sort_key))
        content.addSubview_(self.sort_popup)

        # 絞り込み条件（作者の別のアプリと同じ AND 条件方式）
        self.kind_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(20, 470, 150, 28), False
        )
        for _key, label in core.KIND_OPTIONS:
            self.kind_popup.addItemWithTitle_(label)
        self.kind_popup.setAccessibilityLabel_(
            tr("種別で絞り込み。ワンショットかループかを、長さとファイル名で判断します",
               "Filter by type. Tells one-shots and loops apart by length and file "
               "name")
        )
        self.kind_popup.setTarget_(self)
        self.kind_popup.setAction_("kindChanged:")
        content.addSubview_(self.kind_popup)

        self.pack_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(180, 470, 290, 28), False
        )
        self.pack_popup.setAccessibilityLabel_(
            tr("パックで絞り込み。頭文字を打つと目的のパックへ飛べます",
               "Filter by pack. Type the first letters to jump to a pack")
        )
        self.pack_popup.setTarget_(self)
        self.pack_popup.setAction_("packChanged:")
        content.addSubview_(self.pack_popup)
        self._populate_pack_popup()

        self.fav_check = NSButton.alloc().initWithFrame_(NSMakeRect(480, 470, 160, 28))
        self.fav_check.setButtonType_(NSButtonTypeSwitch)
        self.fav_check.setTitle_(tr("お気に入りだけ", "Favorites only"))
        self.fav_check.setAccessibilityLabel_(tr("お気に入りだけを表示", "Favorites only. Hides the other samples"))
        self.fav_check.setTarget_(self)
        self.fav_check.setAction_("favOnlyChanged:")
        content.addSubview_(self.fav_check)

        # サンプルリスト
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(20, 110, 620, 350))
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(1)
        self.table = SampleTableView.alloc().initWithFrame_(NSMakeRect(0, 0, 620, 350))
        col = NSTableColumn.alloc().initWithIdentifier_("sample")
        col.setTitle_(tr("サンプル", "Sample"))
        col.setWidth_(600)
        # 編集可のままだと、フォーカスが来たときにセルの編集ビュー
        # （NSTextView）が responder を奪い、スペースや上下キーが
        # リストに届かなくなる（実機で「操作できない」と報告された不具合）
        col.setEditable_(False)
        self.table.addTableColumn_(col)
        self.table.setAccessibilityLabel_(tr("サンプル一覧", "Sample list"))
        self.table.setKeyHandler_(self)
        self.datasource = SampleTableDataSource.alloc().initWithRows_([])
        self.table.setDataSource_(self.datasource)
        scroll.setDocumentView_(self.table)
        content.addSubview_(scroll)

        # ロード先セル
        cell_label = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 72, 105, 22))
        cell_label.setStringValue_(tr("ロード先セル", "Load target cell"))
        cell_label.setBezeled_(False)
        cell_label.setDrawsBackground_(False)
        cell_label.setEditable_(False)
        cell_label.setSelectable_(False)
        content.addSubview_(cell_label)

        self.cell_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(130, 70, 150, 28), False
        )
        self._populate_cell_popup()
        self.cell_popup.setAccessibilityLabel_(tr("ロード先セル", "Load target cell"))
        self.cell_popup.setTarget_(self)
        self.cell_popup.setAction_("cellChanged:")
        content.addSubview_(self.cell_popup)

        # ロード先 Battery（複数インスタンスの振り分け）
        bat_label = NSTextField.alloc().initWithFrame_(NSMakeRect(290, 72, 55, 22))
        bat_label.setStringValue_("Battery")
        bat_label.setBezeled_(False)
        bat_label.setDrawsBackground_(False)
        bat_label.setEditable_(False)
        bat_label.setSelectable_(False)
        content.addSubview_(bat_label)

        self.battery_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(350, 70, 170, 28), False
        )
        self.battery_popup.setAccessibilityLabel_(
            tr("ロード先の Battery。Pro Tools のトラック名で選びます",
               "Load target Battery. Choose by Pro Tools track name")
        )
        self.battery_popup.setTarget_(self)
        self.battery_popup.setAction_("batteryChanged:")
        content.addSubview_(self.battery_popup)
        self._populate_battery_popup()

        # ロードボタン
        self.load_button = NSButton.alloc().initWithFrame_(NSMakeRect(530, 70, 100, 28))
        self.load_button.setTitle_(tr("ロード", "Load"))
        self.load_button.setAccessibilityLabel_(tr("ロード。選択中のサンプルを選択セルへ読み込みます",
                                                   "Load. Loads the selected sample "
                                                   "into the selected cell"))
        self.load_button.setTarget_(self)
        self.load_button.setAction_("loadClicked:")
        content.addSubview_(self.load_button)

        # ステータス
        self.status = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 20, 620, 40))
        self.status.setStringValue_(tr("準備完了。スキャン（Cmd+O）でサンプルを読み込みます。",
                                       "Ready. Press Command+O to scan for samples."))
        self.status.setBezeled_(False)
        self.status.setDrawsBackground_(False)
        self.status.setEditable_(False)
        self.status.setSelectable_(False)
        content.addSubview_(self.status)

        self._build_drag_panel()
        self._wire_key_view_loop()

    @objc.python_method
    def _show_main_window(self):
        """メインウィンドウを前に出し、フィルター欄にフォーカスを置く。

        組み立て（_build_window）と分けてあるのは、起動確認（--selftest）と
        マニュアルの生成で、窓を出さずに組み立てて読むため。
        """
        self.window.makeKeyAndOrderFront_(None)
        self.window.makeFirstResponder_(self.filter_field)
        NSApp.activateIgnoringOtherApps_(True)

    @objc.python_method
    def _build_drag_panel(self):
        """自己ドラッグの始点になる小さな窓を作る（ロードの間だけ出す）。

        理由は DragSourcePanel の説明。操作の対象ではないので、
        Tab の順序にも VoiceOver の読み上げにも入れない。
        """
        size = DRAG_PANEL_SIZE
        panel = DragSourcePanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, size, size),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered, False,
        )
        panel.setLevel_(DRAG_PANEL_LEVEL)
        panel.setHidesOnDeactivate_(False)
        panel.setReleasedWhenClosed_(False)
        panel.setOpaque_(True)
        self.drag_view = DragSourceView.alloc().initWithFrame_(
            NSMakeRect(0, 0, size, size)
        )
        panel.setContentView_(self.drag_view)
        for target in (panel, self.drag_view):
            try:
                target.setAccessibilityElement_(False)
            except Exception:
                pass
        self.drag_panel = panel

    @objc.python_method
    def _wire_key_view_loop(self):
        """Tab / Shift+Tab の移動順を明示的に決める。

        既定のままだと macOS が自動で順序を決め、**サンプルリストではなく
        スクロールバー（NSScroller）にフォーカスが行ってしまう**。
        そうなるとリストにキーが届かず、スペースでの再生も上下での移動も
        できなくなる（実機で発生した不具合）。

        操作する順に鎖でつなぎ、最後は先頭へ戻して一周させる。
        ドラッグ元のビューは内部用なので順序から外す。
        """
        order = [
            self.filter_field,
            self.scan_button,
            self.sort_popup,
            self.kind_popup,
            self.pack_popup,
            self.fav_check,
            self.table,          # サンプルリスト（ここへ確実に来るようにする）
            self.cell_popup,
            self.battery_popup,
            self.load_button,
        ]
        for current, nxt in zip(order, order[1:] + order[:1]):
            current.setNextKeyView_(nxt)
        # 自動化用のビューはフォーカスを受け取らない
        try:
            self.drag_view.setRefusesFirstResponder_(True)
        except Exception:
            pass
        self.window.setInitialFirstResponder_(self.filter_field)
        self._key_view_order = order

    @objc.python_method
    def cell_names(self):
        """現在のグリッド設定に対応するセル名リスト。"""
        return grid_calibration.layout_cell_names(self.settings.get("cell_layout"))

    def _populate_cell_popup(self):
        self.cell_popup.removeAllItems()
        labels = self.settings.get("cell_labels", {})
        for c in self.cell_names():
            lab = labels.get(c, "")
            self.cell_popup.addItemWithTitle_(f"{c} {lab}".strip())

    @objc.python_method
    def _target_style(self):
        """ロード先の読み上げスタイル（target_label / short_track_name 用）。"""
        mode = self.settings.get("target_readout", core.READOUT_FULL)
        return "short" if mode == core.READOUT_SHORT else "full"

    @objc.python_method
    def _choose_from_popup(self, title, message, items, initial_index=0,
                           ok_title=tr("実行", "Run"), ax_label=tr("選択", "Choose")):
        """ポップアップ付きの選択ダイアログを出す。

        トラック切替（Cmd+Shift+T）とセル移動（Cmd+M）で共通。
        VoiceOver 向けの改善（ラベル・初期選択など）が片方だけに
        当たらないよう1箇所に集約してある。エスケープはキャンセル
        （NSAlert がエスケープを自動で割り当てるのは「Cancel」という名前の
        ボタンなので、日本語の名前では明示しておく）。

        Returns: 選ばれた index（キャンセルは None）
        """
        from Cocoa import (NSAlert, NSPopUpButton, NSMakeRect,
                           NSAlertFirstButtonReturn)
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(message)
        popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(0, 0, 240, 26), False)
        # 戻り値は項目の位置なので、題を重ねない（同じ題は消えて位置がずれる）
        for item in core.unique_titles(items):
            popup.addItemWithTitle_(item)
        popup.setAccessibilityLabel_(ax_label)
        if 0 <= initial_index < len(items):
            popup.selectItemAtIndex_(initial_index)
        alert.setAccessoryView_(popup)
        alert.addButtonWithTitle_(ok_title)
        cancel = alert.addButtonWithTitle_(tr("キャンセル", "Cancel"))
        cancel.setKeyEquivalent_("\x1b")
        alert.window().setInitialFirstResponder_(popup)
        if alert.runModal() != NSAlertFirstButtonReturn:
            return None
        idx = popup.indexOfSelectedItem()
        return idx if 0 <= idx < len(items) else None

    def menuSelectTrack_(self, sender):
        """Cmd+Shift+T。Battery の画面のトラックを切り替える。

        Pro Tools のプラグイン画面にある Track Selector を操作する。
        画面を1枚だけ開いておけば、これでロード先を自由に選べる
        （画面を何枚も開くより確実。重なると入れ先を特定できないため）。
        """
        self._run_automation(tr("トラック切替", "Track switch"), self._prepare_track_switch)

    @objc.python_method
    def _prepare_track_switch(self):
        """（メインスレッド）トラック切替の下ごしらえ。"""
        ok, why = core.can_target_reliably()
        if not ok:
            core.beep(core.SND_ERROR)
            self._set_status(why, speak=True)
            return None
        now = core.current_battery_track()
        # Battery が挿さっているトラックを候補にする
        # （同じトラックの複数スロットは1つにまとめる）
        try:
            candidates = core.battery_track_names()
        except Exception:
            candidates = []
        if not candidates:
            core.beep(core.SND_ERROR)
            self._set_status(self.NO_BATTERY_TRACKS, speak=True)
            return None

        style = self._target_style()
        short = style == "short"
        initial = candidates.index(now) if now in candidates else 0
        idx = self._choose_from_popup(
            tr("Battery の画面を切り替え", "Switch the Battery window"),
            tr(f"いまは {core.target_label(now, style)} です。"
               "切り替え先のトラックを選んでリターンを押してください。",
               f"It is now {core.target_label(now, style)}. Choose the track to "
               "switch to and press Return."),
            [core.short_track_name(t) if short else t for t in candidates],
            initial_index=initial,
            ok_title=tr("切り替え", "Switch"),
            ax_label=tr("切り替え先のトラック", "Track to switch to"),
        )
        if idx is None:
            self._set_status(tr("切り替えをキャンセルしました", "Switch canceled"))
            return None
        target = candidates[idx]
        if target == now:
            self._set_status(tr("すでにそのトラックです", "It is already that track"))
            return None
        self._set_status(
            tr(f"{core.target_label(target, style)} へ"
               "切り替えます。エスケープで中断。そのままお待ちください。",
               f"Switching to {core.target_label(target, style)}. Press Escape to "
               "stop. Please wait.")
        )
        # Mix の並び順はここで一度だけ読む（切り替えの中で読み直さない）
        order = core.host_track_order()
        self._automation_cancel = False

        def work():
            ok2 = core.select_battery_track(
                target, cancel=lambda: self._automation_cancel,
                order=order, now=now,
            )
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "trackSwitchFinished:",
                {"ok": ok2, "target": target,
                 "cancelled": self._automation_cancel,
                 "now": core.current_battery_track()}, False
            )

        return work

    def trackSwitchFinished_(self, res):
        """（メインスレッド）トラック切り替えの結果を伝える。"""
        style = self._target_style()
        now = res.get("now", "")
        # 切り替えは Pro Tools を前に出すので、結果は前面へ戻ってから読む
        if not res.get("ok") and res.get("cancelled"):
            self._set_status(
                tr("切り替えを中断しました。いまは "
                   f"{core.target_label(now, style)} です。",
                   f"Switch stopped. It is now {core.target_label(now, style)}."),
                front=True,
            )
            return
        if res.get("ok"):
            core.beep(core.SND_OK)
            # 表示中のトラックをロード先として保存する。読み直しが空振りしても
            # 「自動」に化けないよう、切り替えを確かめた名前を使う
            now = now or res.get("target", "")
            self.settings["target_track"] = now
            self._save_settings()
            self._populate_battery_popup()
            self._set_status(
                tr(f"{core.target_label(now, style)} に切り替えました。"
                   "このトラックへロードします。",
                   f"Switched to {core.target_label(now, style)}. Samples will load "
                   "into this track."),
                front=True,
            )
        else:
            core.beep(core.SND_ERROR)
            self._set_status(
                tr(f"切り替えられませんでした。いまは "
                   f"{core.target_label(now, style)} です。"
                   "Pro Tools のプラグイン画面で直接切り替えてください。",
                   f"Could not switch. It is now {core.target_label(now, style)}. "
                   "Switch directly in the Pro Tools plug-in window."),
                front=True,
            )

    @objc.python_method
    def _populate_battery_popup(self):
        """開いている Battery を検出してポップアップに並べる。

        先頭は「自動」。以降は Pro Tools のトラック名。
        保存済みのロード先（target_track）は、いま検出できなくても選択を保つ。
        Pro Tools 側をまだ開いていないだけのことがあり、ここで消すと
        キットに記録したロード先が黙って失われる。ロード時に見つからなければ
        load_selected が案内する。
        """
        self._battery_tracks = []
        try:
            wins = core.battery_windows()
        except Exception:
            wins = []
        style = self._target_style()
        titles = [tr("自動", "Auto")]
        # 「すべての Battery」= 挿さっている全トラックへ、1枚の画面を
        # 切り替えながら順に配る
        self._battery_tracks.append(core.TARGET_ALL)
        titles.append(core.target_label(core.TARGET_ALL, style))
        for w in wins:
            name = w.get("track") or w.get("plugin") or tr("不明", "Unknown")
            if name not in self._battery_tracks:
                self._battery_tracks.append(name)
                titles.append(core.target_label(name, style))

        target = self.settings.get("target_track", "")
        if target and target not in self._battery_tracks:
            self._battery_tracks.append(target)
            shown = core.target_label(target, style)
            titles.append(tr(f"{shown}（未検出）", f"{shown} (not detected)"))
        # 項目の位置と _battery_tracks の位置を対応させているので、題を重ねない
        self.battery_popup.removeAllItems()
        for title in core.unique_titles(titles):
            self.battery_popup.addItemWithTitle_(title)
        if target:
            self.battery_popup.selectItemAtIndex_(
                self._battery_tracks.index(target) + 1
            )
        else:
            self.battery_popup.selectItemAtIndex_(0)
        return wins

    def batteryChanged_(self, sender):
        idx = self.battery_popup.indexOfSelectedItem()
        if idx <= 0:
            self.settings["target_track"] = ""
            self._set_status(tr("ロード先の Battery を自動選択にしました",
                                "Load target Battery set to Auto"))
        elif idx - 1 < len(self._battery_tracks):
            track = self._battery_tracks[idx - 1]
            self.settings["target_track"] = track
            style = self._target_style()
            label = core.target_label(track, style)
            if track == core.TARGET_ALL:
                # 明示的に選んだ瞬間なので、挿さっている数まで調べて伝える
                try:
                    inv = core.battery_inventory()
                    inserted = inv.get("inserted", 0)
                except Exception:
                    inserted = 0
                count_note = tr(f"挿さっている {inserted} 個",
                                f"{plural(inserted, 'inserted Battery instance')}"
                                ) if inserted else tr("全部", "all Battery instances")
                self._set_status(
                    tr(f"ロード先を {label} にしました。ロードすると、"
                       "1枚の画面をトラックごとに切り替えながら、"
                       f"{count_note}の Battery の同じセルへ順に配ります。",
                       f"Load target set to {label}. When you load, one window is "
                       f"switched track by track, and the same cell of {count_note} "
                       "gets the sample in turn.")
                )
            elif style == "short":
                # 短い読み上げでは余計な語を付けない（Cmd+B は何度も押すため）
                self._set_status(tr(f"ロード先 {label}", f"Load target {label}"))
            else:
                self._set_status(tr(f"ロード先の Battery を {label} にしました",
                                    f"Load target Battery set to {label}"))
        self._save_settings()

    @objc.python_method
    def _inventory_note(self, detected):
        """挿さっている数と見えている数が食い違うときの補足文。

        Pro Tools に挿さっていても画面を開いていない Battery は
        AX から見えない。「3つ挿したのに1つしか出ない」の理由を伝える。
        画面は1枚だけ開く運用なので、開くよう促すのは1枚も無いときだけ。
        """
        try:
            inv = core.battery_inventory()
        except Exception:
            return ""
        if inv.get("inserted", 0) <= detected:
            return ""
        note = core.describe_battery_inventory(inv, detected)
        if not note:
            return ""
        if detected == 0:
            note += tr("コマンド+シフト+O で最初の1枚を開けます。",
                       " Command+Shift+O opens the first window.")
        else:
            note += (
                tr("画面は1枚だけで足ります。ロード先の変更は"
                   "コマンド+シフト+T で切り替えてください。",
                   " One window is enough. To change the load target, switch with "
                   "Command+Shift+T.")
            )
        return note

    def menuOpenAllBatteries_(self, sender):
        """Cmd+Shift+O。Battery の画面が無いとき、最初の1枚を開く。

        AX は開いているウィンドウしか見えないため、挿さっているだけの
        Battery はロード先に選べない。Mix ウィンドウのインサート欄を
        合成クリックして開く（Pro Tools の画面を操作する）。

        **開くのは1枚だけ。** 2枚以上開くとどれに入るか特定できず
        ロードできなくなる。ロード先の変更はトラック切替（Cmd+Shift+T）。
        """
        self._run_automation(tr("画面を開く", "Opening the window"), self._prepare_open_battery)

    @objc.python_method
    def _prepare_open_battery(self):
        """（メインスレッド）Battery の画面を1枚開く下ごしらえ。"""
        # 「狙える状態か」の判定と文言は can_target_reliably に一本化する
        # （枚数の規則が2か所にあると、片方だけ直して食い違う）
        ok, why = core.can_target_reliably()
        if ok:
            core.beep(core.SND_OK)
            self._set_status(
                tr("Battery の画面はすでに1枚開いています。"
                   "ロード先を変えるにはコマンド+シフト+T で切り替えてください。",
                   "A Battery window is already open. To change the load target, "
                   "switch with Command+Shift+T."),
                speak=True,
            )
            return None
        if core.battery_window_count() > 0:
            # 0枚ではないのに狙えない＝2枚以上か、別の画面と重なっている。
            # 開くのではなく閉じてもらう必要があるので、理由をそのまま伝える
            core.beep(core.SND_ERROR)
            self._set_status(why, speak=True)
            return None
        try:
            inv = core.battery_inventory()
        except Exception:
            inv = {"inserted": 0, "closed_tracks": []}
        if not inv.get("inserted"):
            core.beep(core.SND_ERROR)
            self._set_status(
                tr("Pro Tools のミックス画面から Battery を"
                   "見つけられませんでした。Pro Tools が起動しているか、"
                   "ミックスウィンドウが開いているか確認してください。",
                   "Could not find Battery from the Pro Tools Mix window. Check that "
                   "Pro Tools is running and the Mix window is open."),
                speak=True,
            )
            return None
        self._set_status(
            tr("Battery の画面を1枚開きます。"
               "Pro Tools の画面を操作しますので、そのままお待ちください。",
               "Opening one Battery window. This operates the Pro Tools window, so "
               "please wait."),
            speak=True,
        )

        def work():
            res = core.open_closed_battery_windows()
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "openAllFinished:", res, False
            )

        return work

    def openAllFinished_(self, res):
        """（メインスレッド）Battery を開いた結果を伝える。"""
        self._populate_battery_popup()
        # 画面を開くときは Pro Tools を前に出すので、前面へ戻ってから読む
        if res.get("error"):
            core.beep(core.SND_ERROR)
            self._set_status(tr(f"開けませんでした。{res['error']}",
                                f"Could not open it. {res['error']}"), front=True)
            return
        opened = res.get("opened", 0)
        if opened > 0:
            tracks = res.get("tracks") or []
            style = self._target_style()
            label = (core.target_label(tracks[0], style) if tracks
                     else "Battery")
            core.beep(core.SND_OK)
            self._set_status(
                tr(f"{label} の Battery の画面を開きました。"
                   "このままロードできます。ほかのトラックへは"
                   "コマンド+シフト+T で切り替えてください。",
                   f"Opened the Battery window of {label}. You can load now. To reach "
                   "other tracks, switch with Command+Shift+T."),
                front=True,
            )
        else:
            core.beep(core.SND_ERROR)
            self._set_status(
                tr("画面を開けませんでした。"
                   "Pro Tools のミックスウィンドウが見えているか確認するか、"
                   "Pro Tools 側でインサートをクリックして開いてください。",
                   "Could not open the window. Check that the Pro Tools Mix window "
                   "is visible, or click the insert in Pro Tools to open it."),
                front=True,
            )

    def menuMoveCell_(self, sender):
        """Cmd+M。Battery のセル間でサンプルを移動する（A2 を A6 へ など）。

        いま選んでいるセルを移動元とし、移動先を選んで実行する。
        ロード先がずれて入ってしまったときの直しに使う。
        """
        self._run_automation(tr("セル移動", "Cell move"), self._prepare_move_cell)

    @objc.python_method
    def _prepare_move_cell(self):
        """（メインスレッド）セル移動の下ごしらえ。"""
        names = self.cell_names()
        src = self.selected_cell
        labels = self.settings.get("cell_labels", {}) or {}
        idx = self._choose_from_popup(
            tr(f"セル {src} のサンプルを移動", f"Move the sample in cell {src}"),
            tr("移動先のセルを選んでリターンを押してください。"
               "Battery の画面上でドラッグして移し替えます。",
               "Choose the destination cell and press Return. The sample is moved by "
               "dragging in the Battery window."),
            [f"{c} {labels.get(c, '')}".strip() for c in names],
            # 移動元の次のセルを初期選択にする（連続で直すことが多い）
            initial_index=((names.index(src) + 1) % len(names)
                           if src in names else 0),
            ok_title=tr("移動", "Move"),
            ax_label=tr("移動先のセル", "Destination cell"),
        )
        if idx is None:
            self._set_status(tr("移動をキャンセルしました", "Move canceled"))
            return None
        dst = names[idx]
        track = self.settings.get("target_track", "")
        if track == core.TARGET_ALL:
            track = ""   # 移動は表示中の1台に対して行う
        self._set_status(
            tr(f"セル {src} を セル {dst} へ移動します。"
               "エスケープで中断。そのままお待ちください。",
               f"Moving cell {src} to cell {dst}. Press Escape to stop. Please wait.")
        )
        self._automation_cancel = False

        def work():
            ok, msg = core.move_cell_sample(
                self.settings, src, dst, track,
                cancel=lambda: self._automation_cancel)
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "moveCellFinished:",
                {"ok": ok, "msg": msg, "dst": dst,
                 "cancelled": self._automation_cancel}, False
            )

        return work

    def moveCellFinished_(self, res):
        """（メインスレッド）セル移動の結果を伝える。

        移動は Pro Tools を前に出すので、前面へ戻ってから読む。
        中断は失敗ではない（失敗の音も、確かめる案内も出さない）。
        """
        if not res.get("ok") and res.get("cancelled"):
            self._set_status(res.get("msg", tr("移動を中断しました", "Move stopped")),
                             front=True)
            return
        if res.get("ok"):
            core.beep(core.SND_OK)
            # 移動先を選択状態にしておく（続けて操作しやすい）
            names = self.cell_names()
            dst = res.get("dst", "")
            if dst in names:
                self.selected_cell = dst
                self.cell_popup.selectItemAtIndex_(names.index(dst))
            self._set_status(res.get("msg", tr("移動しました", "Moved")), front=True)
        else:
            core.beep(core.SND_ERROR)
            self._set_status(
                res.get("msg", tr("移動に失敗しました", "Could not move"))
                + tr("。Battery の画面が表示されているか確認してください。",
                     ". Check that the Battery window is showing."),
                front=True,
            )

    def menuCycleTargetReadout_(self, sender):
        """Cmd+Shift+B。ロード先の読み上げ方を切り替える（3通り）。

        「Inst1 の Battery 4」「Inst1」「インスト1」。何度も聞くので
        短くしたい、という利用者の要望で追加した。
        """
        keys = [k for k, _ in core.TARGET_READOUT_OPTIONS]
        labels = dict(core.TARGET_READOUT_OPTIONS)
        cur = self.settings.get("target_readout", core.READOUT_FULL)
        idx = keys.index(cur) if cur in keys else 0
        nxt = keys[(idx + 1) % len(keys)]
        self.settings["target_readout"] = nxt
        self._save_settings()
        self._populate_battery_popup()
        core.beep(core.SND_TICK)
        # 実際にどう読まれるかを、いまのロード先で示す
        example = ""
        wins = core.battery_windows()
        if wins:
            example = tr(f"たとえば {core.window_label(wins[0], nxt)}。",
                         f"For example, {core.window_label(wins[0], nxt)}.")
        self._set_status(tr(f"{labels[nxt]}。{example}",
                            f"{labels[nxt]}. {example}"), speak=True)

    def menuCycleBattery_(self, sender):
        """ロード先 Battery を検出し直して順送りする（Cmd+B）。"""
        wins = self._populate_battery_popup()
        if not wins:
            core.beep(core.SND_ERROR)
            note = self._inventory_note(0)
            self._set_status(
                tr("Battery のプラグインウィンドウが見つかりません。"
                   "Pro Tools で Battery 4 の画面を開いてください。",
                   "No Battery plug-in window found. Open the Battery 4 window in "
                   "Pro Tools. ") + note,
                speak=True,
            )
            return
        total = len(self._battery_tracks) + 1  # 「自動」を含む
        nxt = (self.battery_popup.indexOfSelectedItem() + 1) % total
        self.battery_popup.selectItemAtIndex_(nxt)
        self.batteryChanged_(self.battery_popup)
        core.beep(core.SND_TICK)


    # ------------------------------------------------------------------
    # 共通ユーティリティ
    # ------------------------------------------------------------------
    def _set_status(self, text, announce_msg=True, speak=False, front=False):
        """ステータス欄を更新する。
        speak=True で VoiceOver 高優先度アナウンス（読み上げ中でも割り込み）。
        お気に入り・並び替え・ロード結果など必ず伝えたい通知は speak=True を使う。
        通常はネイティブ通知（announce）を使い、osascript は announce 内部の
        フォールバックに任せる。

        front=True は「このアプリを前面へ戻してから」高優先度で読む。
        Pro Tools や Finder を前に出す操作（トラック切替・セル移動・
        画面を開く・ロード）の結果に使う。**VoiceOver は前面にいない
        アプリの読み上げを読まない**ので、戻さないと結果が黙って消える
        （2026-10-24 のレビューで判明。トラック切替の結果がこれで届いていなかった）。"""
        self.status.setStringValue_(text)
        if front:
            self._announce_in_front(text)
        elif speak:
            announce(text, high=True)
        elif announce_msg:
            announce(text)

    @objc.python_method
    def _announce_in_front(self, text):
        """このアプリを前面へ戻し、前面になってから高優先度で読む。

        前面に戻ると VoiceOver はまず窓とフォーカスのある所を読むので、
        前面になったのを確かめてから少し待って割り込む。待っている間に
        新しい知らせが来たら、古いほうは読まない（順番が入れ替わらないように）。
        """
        self._front_seq = getattr(self, "_front_seq", 0) + 1
        seq = self._front_seq
        if NSApp.isActive():
            announce(text, high=True)
            return
        NSApp.activateIgnoringOtherApps_(True)
        deadline = time.time() + 2.0

        def speak_if_latest():
            if seq == self._front_seq:
                announce(text, high=True)

        def wait_front():
            if seq != self._front_seq:
                return
            if NSApp.isActive() or time.time() >= deadline:
                AppHelper.callLater(0.3, speak_if_latest)
                return
            AppHelper.callLater(0.05, wait_front)

        AppHelper.callLater(0.05, wait_front)

    def announceFrontMessage_(self, message):
        """（ワーカーから）前面へ戻ってから読む。_set_status(front=True) と同じ。"""
        self._set_status(message, front=True)

    def controlTextDidChange_(self, notification):
        """フィルター欄の1文字ごとの変更で即時に絞り込む。"""
        try:
            if notification.object() is self.filter_field:
                self._refresh_list()
        except Exception:
            pass

    def _selected_path(self):
        row = self.table.selectedRow()
        if 0 <= row < len(self._display_paths):
            path = self._display_paths[row]
            self._last_selected_path = path
            return path
        # フォーカスが別ウィンドウに移ると selectedRow が -1 になるため、
        # 直近に選択していたパスを記憶しておき、それを返す
        last = getattr(self, "_last_selected_path", None)
        if last and last in self._display_paths:
            return last
        return None

    def _save_settings(self):
        self.settings["favorites"] = sorted(self.favorites)
        self.settings["recent"] = self.recent[:core.RECENT_MAX]
        self.settings["readout_mode"] = self.readout_mode
        self.settings["sort_key"] = self.sort_key
        core.save_settings(self.settings)

    # ------------------------------------------------------------------
    # スキャン
    # ------------------------------------------------------------------
    def scanClicked_(self, sender):
        # 重ねて始めない。並行して走ると、後から始めたほうが先に終わったとき
        # 古い結果（変える前のフォルダ）で上書きされる
        if self._scanning:
            self._set_status(tr("スキャン中です。終わるまでお待ちください。",
                                "Scanning. Please wait until it finishes."))
            return
        self._scanning = True
        self._set_status(tr("スキャン中…", "Scanning..."))
        known_durations = dict(self._durations)
        known_stamps = dict(self._duration_stamps)

        def worker():
            folders = self.settings.get("search_folders", [])
            if folders:
                samples = core.find_samples_walk(folders)
            else:
                samples = core.find_samples_spotlight()
            # サイズと日時もここでまとめて読む。メインスレッドで stat すると
            # 並び替えや1文字ごとの絞り込みのたびに固まる（外付け SSD だと
            # 数千件で体感できる）。結果はそのまま stat キャッシュになる
            stats = {}
            for path in samples:
                try:
                    stats[path] = os.stat(path)
                except OSError:
                    stats[path] = False
            stamps = {}
            for path in samples:
                st = stats.get(path)
                stamps[path] = (st.st_mtime, st.st_size) if st else None
            # 既に調べた長さは再利用する（再スキャンのたびに全ファイルを
            # 開き直さない）。ただし**中身が変わったものは読み直す**。
            # 同じ名前で差し替えたときに古い長さのままだと、種別の判定も
            # 長さ順も間違えたままになる
            reuse = {}
            for path in samples:
                dur = known_durations.get(path)
                if (dur is not None and stamps[path] is not None
                        and known_stamps.get(path) == stamps[path]):
                    reuse[path] = dur
            # 長さはヘッダだけ読むので速い（実測 3119 件で 0.6 秒）。
            # メインスレッドでやると入力が止まるので必ずワーカー側で行う
            all_durs = core.read_durations(samples, cache=reuse)
            # 消えたファイルの古いエントリは持ち越さない
            durations = {p: all_durs.get(p) for p in samples}
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "scanFinished:",
                {"samples": samples, "durations": durations,
                 "stats": stats, "stamps": stamps},
                False,
            )

        def guarded():
            try:
                worker()
            except Exception as e:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "scanFailed:", f"{type(e).__name__}", False)

        threading.Thread(target=guarded, daemon=True).start()

    def scanFailed_(self, reason):
        """（メインスレッド）スキャンが例外で止まったとき。旗を下ろして伝える。"""
        self._scanning = False
        core.beep(core.SND_ERROR)
        self._set_status(
            tr(f"スキャンできませんでした（{reason}）。"
               "設定ウィンドウで検索フォルダを確かめてください。",
               f"Could not scan ({reason}). Check the search folders in the Settings "
               "window."),
            speak=True,
        )

    def scanFinished_(self, result):
        self._scanning = False
        # 旧形式（パスの配列だけ）でも動くようにしておく
        if isinstance(result, dict):
            samples = result.get("samples", [])
            self._durations = dict(result.get("durations") or {})
            self._duration_stamps = dict(result.get("stamps") or {})
            # ワーカーで読んだ stat をそのまま使う（メインスレッドでは取らない）
            self._stat_cache = dict(result.get("stats") or {})
        else:
            samples = result
            self._durations = {}
            self._duration_stamps = {}
            self._stat_cache = {}
        self._all_samples = list(samples)
        self._random_order = None   # ランダム順もシャッフルし直す
        self._refresh_list()  # 中でパック候補も作り直す
        n = len(self._all_samples)
        core.beep(core.SND_READY)
        packs = len(self._pack_names)
        extra = tr(f"{packs} パックあります。", f"{plural(packs, 'pack')}. ") if packs else ""
        shown = len(self._display_paths)
        narrowed = tr(f"絞り込み中のため表示は {shown} 件。",
                      f"Filters are on, showing {plural(shown, 'sample')}.") if shown != n else ""
        self._set_status(
            tr(f"スキャン完了。{n} 件のサンプルが見つかりました。{extra}{narrowed}",
               f"Scan complete. {plural(n, 'sample')} found. {extra}{narrowed}")
        )

    # ------------------------------------------------------------------
    # フィルター / ソート / リスト更新
    # ------------------------------------------------------------------
    def filterChanged_(self, sender):
        self._refresh_list()

    def sortChanged_(self, sender):
        idx = self.sort_popup.indexOfSelectedItem()
        if 0 <= idx < len(core.SORT_OPTIONS):
            self.sort_key = core.SORT_OPTIONS[idx][0]
            self._random_order = None  # ランダムを選び直したら新しい並びにする
            self._save_settings()
            self._refresh_list()
            self._set_status(tr(f"並び替え：{core.SORT_OPTIONS[idx][1]}",
                                f"Sort order: {core.SORT_OPTIONS[idx][1]}"))

    # ------------------------------------------------------------------
    # 絞り込み条件（作者の別のアプリと同じ AND 条件）
    # ------------------------------------------------------------------
    @objc.python_method
    def _scan_root(self):
        """パック名を取り出すときの基準フォルダ。

        検索フォルダが1つならそれを使う。複数・未登録（Spotlight 検索）の
        ときは基準を決められないので空にし、パス全体から packs を探す。
        """
        folders = self.settings.get("search_folders", []) or []
        return folders[0] if len(folders) == 1 else ""

    @objc.python_method
    def _oneshot_max_sec(self):
        """ワンショットと見なす長さの上限（秒）。設定で変えられる。"""
        try:
            return float(self.settings.get(
                "oneshot_max_sec", core.DEFAULT_ONESHOT_MAX_SEC))
        except (TypeError, ValueError):
            return core.DEFAULT_ONESHOT_MAX_SEC

    @objc.python_method
    def _populate_pack_popup(self, candidates=None):
        """パックの選択肢を作り直す。

        candidates には「パック以外の条件で絞った結果」を渡す。
        こうすると 553 個あるパックが「いまの条件に該当するパックだけ」に
        減って選びやすくなる（例: フィルターに kick と打つと 22 個になる）。
        作者の別のアプリが読み取り先に連動してパック候補を作り直すのと
        同じ考え方。候補が変わらないときは作り直さない（フォーカス中の
        ポップアップを無用に触らないため）。

        並び順は Cmd+Shift+G で 4 つを順送りできる（中身の多い順、名前順、
        更新の新しい順、追加した順）。作者の別のアプリと同じ順・同じ言葉。
        件数を項目名に添えるので、どの順でも規模が分かる。
        """
        if candidates is None:
            candidates = self._all_samples
        root = self._scan_root()
        # counts を先に取り list_packs に渡す（1文字ごとに呼ばれる経路
        # なので、extract_pack の全件走査を二重にしない）
        counts = core.pack_counts(candidates, root)
        # 日時順のときだけ stat が要る。**ソートと同じキャッシュを使う**
        # （1 文字ごとの絞り込みで全件に stat を発行しないため）。
        names = core.list_packs(candidates, root, self._pack_order,
                                counts=counts, stat_cache=self._stat_cache)
        # 選択中のパックが候補から外れても、条件は黙って消さない。
        # タイプ途中（例: kic）で一時的に該当0件になっただけかもしれず、
        # 勝手に解除すると利用者は条件が生きていると思ったまま
        # 誤った母集団を操作してしまう（1文字ごとの絞り込みは読み上げない）
        if self._filter_pack and self._filter_pack not in names:
            names = names + [self._filter_pack]
        titles = [tr(f"{n} {counts.get(n, 0)}件",
                     f"{n} {plural(counts.get(n, 0), 'item')}") for n in names]
        if names == self._pack_names and titles == getattr(self, "_pack_titles", None):
            return
        self._pack_names = names
        self._pack_titles = titles
        self.pack_popup.removeAllItems()
        self.pack_popup.addItemWithTitle_(tr("パック すべて", "Pack: all"))
        for title in titles:
            self.pack_popup.addItemWithTitle_(title)
        if self._filter_pack in names:
            self.pack_popup.selectItemAtIndex_(names.index(self._filter_pack) + 1)
        else:
            self.pack_popup.selectItemAtIndex_(0)

    @objc.python_method
    def _announce_filter_result(self, prefix=""):
        """絞り込み結果の件数を読み上げる（条件を明示的に変えたときだけ）。

        1文字ごとの絞り込みでは読み上げない。毎文字アナウンスすると
        VoiceOver の読み上げが詰まって入力できなくなるため。
        """
        n = len(self._display_paths)
        core.beep(core.SND_OK if n else core.SND_ERROR)
        summary = core.search_result_summary(n)
        # まだ多いときは、次の一手としてパックで絞れることを伝える
        if n > 30 and not self._filter_pack and len(self._pack_names) > 1:
            summary += tr(f"。{len(self._pack_names)} パックにあります",
                          f". They are in {len(self._pack_names)} packs")
        self._set_status(f"{prefix}{summary}" if prefix else summary, speak=True)

    def kindChanged_(self, sender):
        idx = self.kind_popup.indexOfSelectedItem()
        if 0 <= idx < len(core.KIND_OPTIONS):
            self._filter_kind = core.KIND_OPTIONS[idx][0]
            label = core.KIND_OPTIONS[idx][1]
            self._refresh_list()
            self._announce_filter_result(tr(f"{label}。", f"{label}. "))

    def packChanged_(self, sender):
        idx = self.pack_popup.indexOfSelectedItem()
        if idx <= 0:
            self._filter_pack = ""
            label = tr("パック すべて", "Pack: all")
        elif idx - 1 < len(self._pack_names):
            self._filter_pack = self._pack_names[idx - 1]
            label = self._filter_pack
        else:
            return
        self._refresh_list()
        self._announce_filter_result(tr(f"{label}。", f"{label}. "))

    def favOnlyChanged_(self, sender):
        self._filter_fav_only = bool(self.fav_check.state())
        self._refresh_list()
        label = tr("お気に入りだけ",
                   "Favorites only") if self._filter_fav_only else tr("お気に入りの絞り込みを解除",
                                                           "Favorites filter off")
        self._announce_filter_result(tr(f"{label}。", f"{label}. "))

    def menuCycleKind_(self, sender):
        """Cmd+J で種別を順送りする（すべて→ワンショット→ループ）。"""
        keys = [k for k, _ in core.KIND_OPTIONS]
        idx = keys.index(self._filter_kind) if self._filter_kind in keys else 0
        idx = (idx + 1) % len(keys)
        self.kind_popup.selectItemAtIndex_(idx)
        self.kindChanged_(self.kind_popup)

    def menuFocusPack_(self, sender):
        """Cmd+G でパック選択へ移動する（頭文字で目的のパックへ飛べる）。"""
        self._front_main_window()
        self.window.makeFirstResponder_(self.pack_popup)
        total = len(self._pack_names)
        if not total:
            core.beep(core.SND_ERROR)
            self._set_status(
                tr("パックが見つかりません。"
                   "Splice のように packs フォルダを持つライブラリで使えます。",
                   "No packs found. This works with libraries that have a packs "
                   "folder, such as Splice."),
                speak=True,
            )
            return
        self._set_status(
            tr(f"パックの選択。いまの条件に該当するパックは {total} 個です。"
               "上下矢印で選ぶか、頭文字を入力してください。",
               f"Choose a pack. {plural(total, 'pack')} match the current filters. "
               "Use the up and down arrows or type the first letters."),
            speak=True,
        )

    def menuToggleFavOnly_(self, sender):
        """Cmd+U でお気に入りだけの表示を切り替える。"""
        self.fav_check.setState_(0 if self._filter_fav_only else 1)
        self.favOnlyChanged_(self.fav_check)

    def menuCyclePackOrder_(self, sender):
        """Cmd+Shift+G でパックの並び順を順送りする。

        中身の多い順 → 名前順 → 更新の新しい順 → 追加した順。
        **作者の別のアプリと同じ順・同じ言葉**（持ち替えるので、
        同じキーで違う順に回ると覚え直しになる）。

        | 並び | 何のため |
        | --- | --- |
        | 中身の多い順 | 目当てのパック名が分からないとき、大きなものから当たる |
        | 名前順 | 名前を覚えているとき、頭文字で飛ぶ |
        | 更新の新しい順 | 最近さわったパックへ戻る |
        | 追加した順 | Splice に入れた順に辿る（古いものから） |

        **更新と追加は意味が違う。** 更新はそのパックで最も新しい素材、
        追加は最も古い素材を見る。Splice は素材を 1 つずつ落とすので、
        買い足し続けているパックでは離れる。
        """
        keys = [k for k, _ in core.PACK_ORDER_OPTIONS]
        labels = dict(core.PACK_ORDER_OPTIONS)
        idx = keys.index(self._pack_order) if self._pack_order in keys else 0
        self._pack_order = keys[(idx + 1) % len(keys)]
        self.settings["pack_order"] = self._pack_order
        self._save_settings()
        self._pack_titles = None      # 並びが変わるので作り直させる
        self._refresh_list()
        core.beep(core.SND_TICK)
        head = self._pack_titles[0] if self._pack_titles else ""
        first = tr(f"先頭は {head}。", f"First is {head}.") if head else ""
        self._set_status(
            tr(f"パックの並び順を {labels[self._pack_order]} にしました。{first}",
               f"Pack order set to {labels[self._pack_order]}. {first}"),
            speak=True,
        )

    def menuClearFilters_(self, sender):
        """Cmd+R で絞り込みをすべて解除する。

        条件を重ねると 3000 件から数件まで落ちて戻れなくなるため、
        1操作で最初の状態へ戻せるようにする（作者の別のアプリには
        無い操作。件数が多いこちらでは必須と判断した）。
        """
        self.filter_field.setStringValue_("")
        self._filter_kind = ""
        self._filter_pack = ""
        self._filter_fav_only = False
        self.kind_popup.selectItemAtIndex_(0)
        self.pack_popup.selectItemAtIndex_(0)
        self.fav_check.setState_(0)
        self._refresh_list()
        self._announce_filter_result(tr("絞り込みを解除しました。", "Filters cleared. "))

    def cellChanged_(self, sender):
        names = self.cell_names()
        idx = self.cell_popup.indexOfSelectedItem()
        if 0 <= idx < len(names):
            self.selected_cell = names[idx]
            core.beep(core.SND_TICK)

    def _refresh_list(self, fallback_row=None):
        # 更新前の選択パスを覚えておき、更新後も同じサンプルを選択し続ける
        prev_path = self._selected_path()
        query = self.filter_field.stringValue()
        root = self._scan_root()
        # パック以外の条件で先に絞り、その結果からパックの候補を作る。
        # そのうえでパック条件を重ねる（条件は全体として AND のまま）
        base = core.filter_samples(
            self._all_samples, query,
            kind=self._filter_kind,
            favorites=self.favorites, favorite_only=self._filter_fav_only,
            root=root, durations=self._durations,
            oneshot_max_sec=self._oneshot_max_sec(),
        )
        self._populate_pack_popup(base)
        matched = core.filter_samples(base, pack=self._filter_pack, root=root)
        # ランダムは一度シャッフルした順を保持する（フィルター1文字ごとに
        # 並びが変わると選択維持が意味を失い、リストを追えなくなるため）
        if self.sort_key == "random":
            if self._random_order is None:
                shuffled = core.sort_samples(self._all_samples, "random")
                self._random_order = {p: i for i, p in enumerate(shuffled)}
        else:
            self._random_order = None
        ordered = core.sort_samples(
            matched, self.sort_key, favorites=self.favorites, recent=self.recent,
            stat_cache=self._stat_cache, random_order=self._random_order,
            durations=self._durations,
        )
        self._display_paths = ordered
        # 読み上げ補正はファイル名部分だけに適用し、[最近使用] やサイズなどの
        # ソート文脈タグは補正モードでも保つ
        transform = None
        if self.readout_mode != "off":
            mode = self.readout_mode
            transform = lambda p: name_readout.readout_name(p, mode)
        rows = [
            core.format_list_label(
                p, self.sort_key, favorites=self.favorites, recent=self.recent,
                name_transform=transform, stat_cache=self._stat_cache,
                durations=self._durations,
            )
            for p in ordered
        ]
        self.datasource.setRows_(rows)
        self.table.reloadData()
        self._restore_selection(prev_path, fallback_row)

    def _restore_selection(self, prev_path, fallback_row=None):
        """更新後のリストで以前の選択を復元する。

        選んでいたサンプルが絞り込みで消えた場合、fallback_row を渡すと
        その行位置（範囲内に収める）を選ぶ。お気に入りだけ表示中に
        お気に入りを外したときなど、消えた行の次を続けて操作できる。
        渡さなければ先頭を選ぶ（絞り込み直後は先頭から見るのが自然）。
        """
        if not self._display_paths:
            return
        if prev_path and prev_path in self._display_paths:
            idx = self._display_paths.index(prev_path)
        elif fallback_row is not None:
            idx = max(0, min(fallback_row, len(self._display_paths) - 1))
        else:
            idx = 0
        self.table.selectRowIndexes_byExtendingSelection_(
            NSIndexSet.indexSetWithIndex_(idx), False
        )
        self.table.scrollRowToVisible_(idx)


    # ------------------------------------------------------------------
    # リスト内キー操作（SampleTableView から転送される）
    # ------------------------------------------------------------------
    def handleListKey_(self, char):
        """戻り値 True でイベント消費。Space/Return/Esc のみ扱う。
        F/S/R/H は VoiceOver と競合するため Cmd 付きメニューショートカットに移行した。"""
        if char == " ":
            self.preview_selected()
            return True
        if char == "\r":
            self.load_selected()
            return True
        if char == "\x1b":  # Esc
            self.stop_preview()
            self.cancel_automation()
            return True
        return False

    # ------------------------------------------------------------------
    # プレビュー
    # ------------------------------------------------------------------
    def preview_selected(self):
        """Space キーの動作。再生していなければ再生を開始し、
        同じサンプルを再生中ならトグルで停止する。"""
        path = self._selected_path()
        if not path:
            self._set_status(tr("サンプルが選択されていません", "No sample is selected"))
            return
        # 同じサンプルが再生中 → 停止（トグル）
        if (
            self._preview_proc is not None
            and self._preview_proc.poll() is None
            and self._preview_path == path
        ):
            self.stop_preview()
            self._set_status(
                tr(f"停止しました：{os.path.basename(path)}",
                   f"Stopped: {os.path.basename(path)}"), announce_msg=False
            )
            return
        self.stop_preview()
        try:
            import subprocess
            self._preview_proc = subprocess.Popen(
                ["afplay", path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self._preview_path = path
            self._set_status(tr(f"再生中：{os.path.basename(path)}",
                                f"Playing: {os.path.basename(path)}"), announce_msg=False)
        except Exception:
            self._set_status(tr("再生に失敗しました", "Could not play it"))

    @objc.python_method
    def cancel_automation(self):
        """Esc。Pro Tools を操作している処理（ロードなど）を中断する。

        プレビュー停止と分けてあるのは、配っている最中にスペースで
        試聴しただけで残りのトラックが省かれてしまうため。
        """
        self._automation_cancel = True

    def stop_preview(self):
        if self._preview_proc and self._preview_proc.poll() is None:
            try:
                self._preview_proc.terminate()
            except Exception:
                pass
        self._preview_proc = None
        self._preview_path = None

    # ------------------------------------------------------------------
    # お気に入り / ソート / 読み上げ補正
    # ------------------------------------------------------------------
    def toggle_favorite(self):
        path = self._selected_path()
        if not path:
            # 黙って終わると、登録できたのか分からない
            self._set_status(tr("サンプルが選択されていません", "No sample is selected"), speak=True)
            return
        name = os.path.basename(path)
        # お気に入りだけ表示中に外すと、その行はリストから消える。
        # 選択が先頭へ飛ばないよう、同じ行位置を選び直せるようにする
        row = self.table.selectedRow()
        if path in self.favorites:
            self.favorites.discard(path)
            self._set_status(tr(f"お気に入りから外しました。{name}",
                                f"Removed from favorites. {name}"), speak=True)
        else:
            self.favorites.add(path)
            self._set_status(tr(f"お気に入りに追加しました。{name}",
                                f"Added to favorites. {name}"), speak=True)
        self._save_settings()
        self._refresh_list(fallback_row=row if row >= 0 else None)

    def cycle_sort(self):
        keys = [k for k, _ in core.SORT_OPTIONS]
        idx = keys.index(self.sort_key) if self.sort_key in keys else 0
        idx = (idx + 1) % len(keys)
        self.sort_key = keys[idx]
        self._random_order = None
        self.sort_popup.selectItemAtIndex_(idx)
        self._save_settings()
        self._refresh_list()
        self._set_status(
            tr(f"並び順を {core.SORT_OPTIONS[idx][1]} に変更しました。"
               f"{len(self._display_paths)} 件。",
               f"Sort order changed to {core.SORT_OPTIONS[idx][1]}. "
               f"{plural(len(self._display_paths), 'item')}."),
            speak=True,
        )

    def cycle_readout(self):
        self.readout_mode = name_readout.cycle_mode(self.readout_mode)
        self._save_settings()
        self._refresh_list()
        self._set_status(name_readout.mode_label(self.readout_mode), speak=True)


    # ------------------------------------------------------------------
    # ロード（カウントダウン → ドラッグ）
    # ------------------------------------------------------------------
    def loadClicked_(self, sender):
        self.load_selected()

    @objc.python_method
    def _begin_automation(self, label):
        """Pro Tools を合成イベントで操作する処理の相互排他。

        ロード・トラック切替・セル移動・画面を開く操作は、どれも
        合成マウス/キーを送る。併走すると HID レベルでイベントが交錯し、
        ドラッグ中のファイルが無関係な場所へ落ちる。開始できたら True。
        """
        if self._automation_label:
            self._set_status(
                tr(f"{self._automation_label}の処理が進行中です。"
                   "終わるまでお待ちください。",
                   f"{self._automation_label} is in progress. Please wait until it "
                   "finishes.")
            )
            return False
        self._automation_label = label
        return True

    @objc.python_method
    def _end_automation(self):
        self._automation_label = ""

    @objc.python_method
    def _run_automation(self, label, prepare):
        """合成イベント操作を、排他つきで実行する共通部。

        prepare() はメインスレッドで下ごしらえをして、ワーカースレッドで
        動かす関数を返す。始められないときは None を返す。

        **排他の取り忘れ・解除し忘れをここ1か所に閉じ込める。**
        解除が1か所でも漏れると、以後どの操作も
        「〜の処理が進行中です」で受け付けられなくなる。
        """
        if not self._begin_automation(label):
            return
        work = None
        try:
            work = prepare()
        except Exception as e:
            # AX 走査は Pro Tools が落ちると失敗する。黙って終わると
            # 「リターンを押したのに何も起きない」になり原因を探せない
            core.beep(core.SND_ERROR)
            self._set_status(
                tr(f"{label}を開始できませんでした（{type(e).__name__}）。"
                   "Pro Tools が起動しているか確認してください。",
                   f"{label} could not start ({type(e).__name__}). Check that Pro "
                   "Tools is running."),
                speak=True,
            )
        finally:
            if work is None:
                self._end_automation()
        if work is None:
            return

        def worker():
            try:
                work()
            except Exception as e:
                # 途中で Pro Tools が前に出ていることがあるので、前面へ戻って読む
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "announceFrontMessage:",
                    tr(f"{label}の途中で問題が起きました（{type(e).__name__}）。"
                       "もう一度お試しください。",
                       f"A problem occurred during {label} ({type(e).__name__}). "
                       "Please try again."), False,
                )
                core.beep(core.SND_ERROR)
            finally:
                self._end_automation()

        threading.Thread(target=worker, daemon=True).start()

    @objc.python_method
    def _note_recent(self, path):
        """「最近使用」の先頭に載せて保存する（1つでも入ったとき）。"""
        if path in self.recent:
            self.recent.remove(path)
        self.recent.insert(0, path)
        self.recent = self.recent[:core.RECENT_MAX]
        self._save_settings()

    @objc.python_method
    def _load_targets(self):
        """ロード先を決める。

        Returns:
            (トラック名のリスト, エラーメッセージ, いま表示中のトラック名)。
            リストの "" は「表示中の画面のまま」を意味する。
            エラー時はリストが空。
        """
        track = self.settings.get("target_track", "")
        now = core.current_battery_track()
        if track == core.TARGET_ALL:
            # 挿さっている全トラックへ、1枚の画面を切り替えながら順に配る。
            # 表示中のトラックを先頭にして、無駄な切り替えを減らす
            tracks = core.battery_track_names()
            if not tracks:
                return [], self.NO_BATTERY_TRACKS, now
            if now in tracks:
                tracks.remove(now)
                tracks.insert(0, now)
            return tracks, "", now
        if not track or track == now:
            return [""], "", now
        # **保存済みのロード先が、いまのセッションにあるか確かめる。**
        # 確かめずに切り替えを頼むと、実在しない名前を探して Battery の
        # 画面が全トラックを巡ってしまう（実測で1分近く、その間ずっと
        # 別のトラックが表示される）。キットや前のセッションの名前が
        # 残っているのはふつうのことなので、静かに案内して止める
        tracks = core.battery_track_names()
        if not tracks:
            return [], self.NO_BATTERY_TRACKS, now
        if track in tracks:
            return [track], "", now
        # Track Selector が読めないインスタンスは、ポップアップに
        # プラグイン名で並ぶ。表示中がそれなら切り替えは要らない
        if not now and core.front_plugin_name() == track:
            return [""], "", now
        label = core.target_label(track, self._target_style())
        return [], (
            tr(f"ロード先の {label} が、いまの Pro Tools に見つかりません。"
               "コマンド+シフト+T でトラックを選び直すか、"
               "コマンド+B でロード先を自動に戻してください。",
               f"The load target {label} was not found in the current Pro Tools "
               "session. Choose a track again with Command+Shift+T, or set the load "
               "target back to Auto with Command+B.")
        ), now

    def load_selected(self):
        # 相互排他：カウントダウン〜ドラッグ中の Return 連打や、
        # トラック切替・セル移動との併走は、中断フラグやドラッグ判定を
        # 壊し合い、誤セルへのドロップにつながる
        self._run_automation(tr("ロード", "Load"), self._prepare_load)

    @objc.python_method
    def _prepare_load(self):
        """（メインスレッド）ロードの下ごしらえ。

        Returns: ワーカースレッドで動かす関数。始められないなら None。
        """
        path = self._selected_path()
        if not path:
            self._set_status(tr("ロードするサンプルが選択されていません", "No sample is selected to load"))
            return None
        if not os.path.exists(path):
            core.beep(core.SND_ERROR)
            self._set_status(
                tr(f"ファイルが見つかりません：{os.path.basename(path)}。"
                   "再スキャンしてください。",
                   f"File not found: {os.path.basename(path)}. Scan again."),
                speak=True,
            )
            return None
        cell = self.selected_cell

        # マウス合成に必須のアクセシビリティ権限がなければ、試さずに案内する
        # （権限が無いとプラグインウィンドウの位置も読めない）
        if not core.accessibility_trusted():
            self._copy_path_to_clipboard(path)
            core.beep(core.SND_ERROR)
            # 「許可したはずなのに動かない」を避けるため、実際に許可すべき
            # アプリの名前を伝える（ソース起動ならターミナル等）
            info = core.accessibility_diagnosis()
            self._set_status(
                tr(f"アクセシビリティ権限がないため自動ロードできません。"
                   f"許可する対象は {info['target']} です。"
                   "設定ウィンドウ（コマンド+カンマ）の"
                   "「アクセシビリティ権限を確認」で詳しい手順を読み上げます。"
                   "ファイルパスはクリップボードにコピーしました。",
                   "Cannot load automatically without Accessibility permission. The "
                   f"app to allow is {info['target']}. \"Check Accessibility "
                   "Permission\" in the Settings window (Command+Comma) reads out the "
                   "steps. The file path has been copied to the clipboard."),
                speak=True,
            )
            return None

        # 画面1枚運用の確認。0枚なら開いてもらう、2枚以上や別プラグインと
        # 重なっているならどれに入るか特定できないので運ばない（上書き防止）
        reliable, why = core.can_target_reliably()
        if not reliable:
            self._copy_path_to_clipboard(path)
            core.beep(core.SND_ERROR)
            self._set_status(
                tr("ロードを中止しました。", "Load stopped. ") + why
                + tr("パスはクリップボードにコピーしました。", " The path has been copied to the clipboard."),
                speak=True,
            )
            return None

        targets, err, now = self._load_targets()
        if not targets:
            self._copy_path_to_clipboard(path)
            core.beep(core.SND_ERROR)
            self._set_status(
                err + tr("パスはクリップボードにコピーしました。",
                         " The path has been copied to the clipboard."), speak=True
            )
            return None

        sec = int(self.settings.get("countdown_sec", 3))
        self._automation_cancel = False
        style = self._target_style()
        if len(targets) > 1:
            names = COMMA.join(core.target_label(t, style) for t in targets)
            dest = (tr(f"{len(targets)} 個の Battery（{names}）の セル {cell}。"
                       "トラックを切り替えながら順に配ります",
                       f"cell {cell} of {len(targets)} Batteries ({names}). Tracks "
                       "are switched and they are filled in turn"))
        else:
            dest = tr(f"{core.target_label(targets[0] or now, style)} の セル {cell}",
                      f"{core.target_label(targets[0] or now, style)}, cell {cell}")
        self._set_status(
            tr(f"{os.path.basename(path)} を {dest} へロードします。"
               f"{sec}秒後に開始。エスケープで中断。そのままお待ちください。",
               f"Loading {os.path.basename(path)} into {dest}. Starts in "
               f"{plural(sec, 'second')}. Press Escape to stop. Please wait.")
        )
        return self._load_worker(path, cell, targets, sec, style, now)

    @objc.python_method
    def _load_worker(self, path, cell, targets, sec, style, now):
        """ロードのワーカー関数を作る（排他は _run_automation が持つ）。"""

        def cancelled():
            return self._automation_cancel

        def announce(message):
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "announceMessage:", message, False
            )

        def worker():
            current = now
            # --- 事前検査: Battery 4 が読めない形式なら無劣化変換してから運ぶ ---
            # （例: テンポマーカー入り WAV は Battery のパーサーが受け付けない）
            drag_path = path
            converted = False
            need, reason = core.needs_battery_normalization(path)
            if need:
                announce(tr(f"{reason}、Battery 4 対応形式に変換しています。",
                            f"{reason}, converting to a format Battery 4 can read."))
                conv = core.normalize_for_battery(path)
                if conv:
                    drag_path = conv
                    converted = True
                else:
                    announce(tr("変換に失敗したため、元のファイルでロードを試します。",
                                "The conversion failed, so the original file will be "
                                "tried."))

            per_target = []

            def stop_here():
                """中断して、そこまでの結果を伝える。"""
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "loadCancelled:",
                    {"targets": per_target, "path": path, "cell": cell}, False
                )

            for n in range(sec, 0, -1):
                if cancelled():
                    stop_here()
                    return
                announce(str(n))
                core.beep(core.SND_TICK)
                time.sleep(1.0)
            if cancelled():
                stop_here()
                return

            # --- ロード先ごとに「切り替え → 座標解決 → ドロップ」---
            # Pro Tools のプラグイン画面は1枚だけ開いて運用する。
            # ロード先は Track Selector の切り替えで決める（ウィンドウの
            # 重なり順や位置の操作では実際のドロップ先が変わらないことが
            # 実機で判明している）。複数トラックへ配るときも、この1枚を
            # 切り替えながら1つずつ運ぶ。
            #
            # Mix の並び順は最初に一度だけ読む。切り替えのたびに読み直すと
            # Pro Tools への同期問い合わせが増え、その間 UI が止まる。
            # 表示中のトラックはカウントダウンのあいだに手で変えられて
            # いることがあるので、ここで読み直す
            order = []
            if any(targets):
                order = core.host_track_order()
                current = core.current_battery_track()
            for track in targets:
                if cancelled():
                    stop_here()
                    return
                label = core.target_label(track or current, style)
                # 表示中と違うトラックなら切り替える（Esc はステップ境界で効く）
                if track and track != current:
                    announce(tr(f"{label} に切り替えています。", f"Switching to {label}."))
                    if not core.select_battery_track(
                            track, cancel=cancelled, order=order,
                            now=current):
                        if cancelled():
                            stop_here()
                            return
                        per_target.append({
                            "where": label, "ok": False, "certain": True,
                            "engine": "skip", "reason": "switch",
                        })
                        continue
                    current = track
                if cancelled():
                    stop_here()
                    return
                # **ドロップの直前に、画面が増えていないか確かめ直す。**
                # カウントダウンや切り替えの間に2枚目が開くことがあり、
                # 重なった画面へ落とすと狙っていないセルを上書きする
                reliable, _why = core.can_target_reliably()
                if not reliable:
                    per_target.append({
                        "where": label, "ok": False, "certain": True,
                        "engine": "skip", "reason": "overlap",
                    })
                    continue
                # 切り替え後の表示（1枚）から座標を解決する
                resolved = core.resolve_cell_points(self.settings, cell, "")
                if not resolved:
                    per_target.append({
                        "where": label, "ok": False, "certain": True,
                        "engine": "skip", "reason": "resolve",
                    })
                    continue
                tx, ty = resolved[0]["x"], resolved[0]["y"]
                where = resolved[0]["where"]

                # --- 方式1: 自己ドラッグ（成否を正確に判定できる） ---
                ok = False
                certain = False
                engine = "self"
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "prepareSelfDrag:",
                    {"path": drag_path, "tx": tx, "ty": ty}, True
                )
                prep = self._drag_prep
                problem, blocker = "source", ""
                if prep is not None:
                    sx, sy = prep
                    # **送る前に、始点がこのアプリのドラッグの窓、終点が
                    # Pro Tools の窓か確かめる。** 違えば、合成マウスは
                    # その窓（Battery のつまみやほかのアプリ）を動かしてしまう
                    problem, blocker = core.drag_endpoints_problem(
                        sx, sy, tx, ty, os.getpid())
                    if not problem:
                        core.cg_drag(sx, sy, tx, ty)
                        # ドラッグセッションの終了（＝ドロップ判定）を最大6秒待つ
                        deadline = time.time() + 6.0
                        while (time.time() < deadline
                               and not self.drag_view.session_ended):
                            time.sleep(0.1)
                        ok = bool(
                            self.drag_view.session_ended
                            and self.drag_view.drop_accepted
                        )
                        certain = True
                began = bool(self.drag_view.session_began)
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "finishSelfDrag:", None, True
                )
                if problem in ("hidden", "no_window"):
                    # セルの位置にほかの窓が重なっているか、窓が無い。
                    # Finder 経由でも同じ所へ落ちるので運ばない
                    per_target.append({
                        "where": where, "ok": False, "certain": True,
                        "engine": "skip", "reason": problem,
                        "blocker": blocker,
                    })
                    continue

                # --- 方式2: Finder 経由（自己ドラッグを始められなかったときだけ）---
                # 始められたのに受け取られなかった（ドロップ先が断った）ときは
                # 試さない。同じファイルを同じ所へ運んでも断られるうえ、
                # Finder が前に出て VoiceOver の位置が移り、二重に落とす恐れもある
                if not ok and not began and not cancelled():
                    engine = "finder"
                    certain = False
                    ok = core.drag_via_finder(drag_path, (tx, ty))

                per_target.append(
                    {"where": where, "ok": ok, "certain": certain,
                     "engine": engine}
                )
                if len(targets) > 1:
                    # 次のドロップに移る前に、受け取り側が落ち着くのを待つ
                    time.sleep(0.4)

            if cancelled():
                stop_here()
                return

            ok_all = bool(per_target) and all(r["ok"] for r in per_target)
            first = per_target[0] if per_target else {}
            result = {
                "ok": ok_all,
                "certain": all(r["certain"] for r in per_target)
                           if per_target else False,
                "engine": first.get("engine", "self"),
                "path": path, "cell": cell, "converted": converted,
                "where": first.get("where", ""),
                "targets": per_target,
            }
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "loadFinished:", result, False
            )

        return worker

    def prepareSelfDrag_(self, info):
        """（メインスレッド）自己ドラッグの準備。

        1. このアプリを前面へ出す（結果を読み上げるため。VoiceOver は
           前面にいないアプリの読み上げを読まない）
        2. このアプリの窓がセルの位置を覆っていれば、その間だけどかす。
           Battery の画面は浮いた窓なのでふつうは覆わない。覆っていると
           確かめたときだけ動かす
        3. ドラッグの始点の小さな窓（DragSourcePanel）をセルの近くに出す
        始点の CG 座標（左上原点）を self._drag_prep に入れる。失敗したら None。
        """
        self._drag_prep = None
        self._moved_windows = []
        try:
            path = info["path"]
            tx = float(info["tx"])
            ty = float(info["ty"])

            NSApp.activateIgnoringOtherApps_(True)
            primary_h = NSScreen.screens()[0].frame().size.height
            top = core.window_at(tx, ty)
            if top and top.get("pid") == os.getpid():
                self._move_own_windows_off(tx, ty, primary_h)

            sx, sy = core.drag_start_point(
                tx, ty, self._screen_rects(primary_h), DRAG_START_OFFSET)
            half = DRAG_PANEL_SIZE / 2.0
            self.drag_view.prepare_for_path(path)
            self.drag_panel.setFrameOrigin_((sx - half, primary_h - sy - half))
            self.drag_panel.orderFrontRegardless()
            self._drag_prep = (sx, sy)
        except Exception:
            self._drag_prep = None

    @objc.python_method
    def _screen_rects(self, primary_h):
        """画面の矩形を CG（左上原点）の (x, y, w, h) で返す。"""
        rects = []
        for screen in NSScreen.screens():
            f = screen.frame()
            rects.append((f.origin.x, primary_h - (f.origin.y + f.size.height),
                          f.size.width, f.size.height))
        return rects

    @objc.python_method
    def _move_own_windows_off(self, tx, ty, primary_h):
        """このアプリの窓がセル (tx, ty) を覆っていれば、横へどかす。

        どかした窓と元の位置は self._moved_windows に積み、finishSelfDrag_ が
        元へ戻す。メイン画面だけでなく、キット管理・設定・ヘルプの窓も見る。
        """
        width = NSScreen.screens()[0].frame().size.width
        ty_bl = primary_h - ty     # Cocoa（左下原点）の座標へ
        for win in NSApp.windows():
            if win is self.drag_panel or not win.isVisible():
                continue
            f = win.frame()
            covers = (f.origin.x - 10 <= tx <= f.origin.x + f.size.width + 10
                      and f.origin.y - 10 <= ty_bl
                      <= f.origin.y + f.size.height + 10)
            if not covers:
                continue
            # セルと重ならない側（左右の広いほう）へ寄せる
            if tx > width / 2:
                new_x = max(0, tx - f.size.width - 80)
            else:
                new_x = min(width - f.size.width, tx + 80)
            self._moved_windows.append((win, (f.origin.x, f.origin.y)))
            win.setFrameOrigin_((new_x, f.origin.y))

    def finishSelfDrag_(self, _):
        """（メインスレッド）始点の窓をしまい、どかした窓を元の位置へ戻す。"""
        try:
            self.drag_panel.orderOut_(None)
        except Exception:
            pass
        for win, origin in getattr(self, "_moved_windows", None) or []:
            try:
                win.setFrameOrigin_(origin)
            except Exception:
                pass
        self._moved_windows = []

    @objc.python_method
    def _copy_path_to_clipboard(self, path):
        try:
            core.copy_to_clipboard(path)
        except Exception:
            pass

    def loadCancelled_(self, info):
        """（メインスレッド）エスケープで中断したときの報告。

        **中断は失敗ではない。** 設定が悪いかのような案内をすると、
        自分で止めただけなのに原因を探すことになる。
        そこまでに入ったぶんは伝える。
        """
        done = []
        path = ""
        if isinstance(info, dict):
            done = [r["where"] for r in (info.get("targets") or [])
                    if r.get("ok")]
            path = info.get("path", "")
        # 切り替えの途中なら Pro Tools が前に出ている。前面へ戻ってから読む
        if done:
            core.beep(core.SND_OK)
            if path:
                self._note_recent(path)
            self._set_status(
                tr("ロードを中断しました。中断までに "
                   f"{'、'.join(done)} に入りました。",
                   f"Load stopped. Before that, it went into {', '.join(done)}."),
                front=True,
            )
            return
        self._set_status(tr("ロードを中断しました", "Load stopped"), front=True)

    @objc.python_method
    def _advance_cell_if_needed(self, cell):
        """設定が有効なら次のセルへ移動する（ロード成功後）。"""
        if not self.settings.get("auto_advance_cell", True):
            return
        names = self.cell_names()
        if cell in names:
            nxt = (names.index(cell) + 1) % len(names)
            self.selected_cell = names[nxt]
            self.cell_popup.selectItemAtIndex_(nxt)

    # 運ばずに見送った理由（失敗とは分けて伝える）
    SKIP_REASONS = ("switch", "overlap", "resolve", "hidden", "no_window")

    @objc.python_method
    def _skip_note(self, per_target):
        """運ばずに見送ったものの説明を作る（理由ごとに次の一手が違う）。"""
        notes = []
        blockers = [r.get("blocker") for r in per_target
                    if r.get("reason") == "hidden" and r.get("blocker")]
        hidden_by = COMMA.join(dict.fromkeys(blockers)) or tr("ほかの画面", "the other windows")
        for reason, why, advice in (
            ("switch", tr("トラックを切り替えられなかったため", "because the track could not be switched"),
             tr("コマンド+シフト+T でトラックを切り替えてから、"
                "もう一度お試しください。",
                "Switch tracks with Command+Shift+T and try again. ")),
            ("overlap", tr("Battery の画面が増えていたため",
                           "because more Battery windows had opened"),
             tr("使う 1 枚だけを残して、ほかの画面を閉じてください。",
                "Keep only the one you want to use and close the others. ")),
            ("resolve", tr("Battery の画面から位置を出せなかったため",
                           "because the position could not be worked out from the "
                           "Battery window"),
             tr("Battery の画面が開いたままか確認してください。",
                "Check that the Battery window is still open. ")),
            ("hidden", tr(f"セルの位置に {hidden_by} が重なっていたため",
                          f"because {hidden_by} was covering the cell"),
             tr("重なっている画面を閉じるか動かしてから、もう一度お試しください。",
                "Close or move the covering window and try again. ")),
            ("no_window", tr("セルの位置に Battery の画面が見つからなかったため",
                             "because no window was found at the cell"),
             tr("Battery の画面が画面の中に見えているか確かめてから、もう一度お試しください。",
                "Check that the Battery window is on screen and try again. ")),
        ):
            where = [r["where"] for r in per_target
                     if r.get("reason") == reason]
            if where:
                notes.append(
                    tr(f"{'、'.join(where)} は{why}、"
                       "誤ったセルを上書きしないよう運びませんでした。",
                       f"Nothing was carried to {', '.join(where)} {why}, so that the "
                       "wrong cell would not be overwritten. ") + advice
                )
        return "".join(notes)

    def loadFinished_(self, result):
        """（メインスレッド）ロードの結果を伝える。

        Finder 経由で運んだときやトラックを切り替えたときは、ほかのアプリが
        前に出ている。結果は前面へ戻ってから読む（front=True）。
        """
        path = result["path"]
        cell = result["cell"]
        name = os.path.basename(path)
        per_target = result.get("targets") or []

        # トラックを切り替えられず／画面が増えていて／座標を出せず／
        # セルがほかの窓に隠れていて、運ばずに見送ったものは「失敗」と区別する
        _SKIP_REASONS = self.SKIP_REASONS
        skipped = [r["where"] for r in per_target
                   if r.get("reason") in _SKIP_REASONS]
        # 複数の Battery へ配った場合は、どこに入ってどこが失敗したかを伝える
        if len(per_target) > 1:
            done = [r["where"] for r in per_target if r["ok"]]
            failed = [r["where"] for r in per_target
                      if not r["ok"] and r.get("reason") not in _SKIP_REASONS]
            conv_note = tr("（対応形式に変換済み）",
                           " (converted to a supported format)") if result.get("converted") else ""
            skip_note = self._skip_note(per_target)
            if done:
                # 1つでも入れば「最近使用」に載せる（続けて呼び出せるように）
                self._note_recent(path)
            if done and not failed and not skipped:
                core.beep(core.SND_OK)
                msg = (
                    tr(f"ロード完了：{name} を {len(done)} 個の Battery の"
                       f"セル {cell} に読み込みました{conv_note}。"
                       f"{'、'.join(done)}。",
                       f"Load complete: {name} into cell {cell} of "
                       f"{plural(len(done), 'Battery instance')}{conv_note}. "
                       f"{', '.join(done)}.")
                )
                self._advance_cell_if_needed(cell)
            elif done:
                core.beep(core.SND_ERROR)
                fail_note = (tr(f"{'、'.join(failed)} は失敗しました。"
                                "ウィンドウが隠れていないか確認してください。",
                                f"{', '.join(failed)} failed. Check that the window "
                                "is not hidden. ")
                             if failed else "")
                msg = (
                    tr(f"一部だけロードしました：{name} は "
                       f"{'、'.join(done)} に入りました。",
                       f"Partly loaded: {name} went into {', '.join(done)}. ") + fail_note + skip_note
                )
            else:
                core.beep(core.SND_ERROR)
                self._copy_path_to_clipboard(path)
                msg = (
                    tr(f"ロード失敗：{name} をどの Battery にも入れられませんでした。",
                       f"Load failed: {name} could not go into any Battery. ")
                    + (skip_note or
                       tr("ウィンドウが他のウィンドウに隠れていないか、"
                          "セル位置合わせが正しいか確認してください。",
                          "Check that the window is not hidden by another window, "
                          "and that the cell calibration is right. "))
                    + tr("パスはクリップボードにコピーしました。",
                         "The path has been copied to the clipboard.")
                )
            self._set_status(msg, front=True)
            return

        # 単一のロード先でも、切り替え・座標解決ができず運べなかった場合は
        # 明確に伝える（どこに入るか分からない状態では運ばない方針）
        if skipped:
            core.beep(core.SND_ERROR)
            self._copy_path_to_clipboard(path)
            self._set_status(
                tr(f"ロードを中止しました：{name}。",
                   f"Load stopped: {name}. ") + self._skip_note(per_target)
                + tr("パスはクリップボードにコピーしました。", "The path has been copied to the clipboard."),
                front=True,
            )
            return

        if result["ok"]:
            core.beep(core.SND_OK)
            self._note_recent(path)
            conv_note = tr("（対応形式に変換済み）",
                           " (converted to a supported format)") if result.get("converted") else ""
            where = result.get("where", "")
            at = tr(f"{where} の ", f"{where}, ") if where else ""
            if result.get("certain"):
                msg = tr(f"ロード完了：{name} を {at}セル {cell} に読み込みました{conv_note}。",
                         f"Load complete: {name} into {at}cell {cell}{conv_note}.")
            else:
                msg = (
                    tr(f"Finder経由で {name} を {at}セル {cell} へドラッグしました{conv_note}。"
                       "ロードされたか確認してください。",
                       f"Dragged {name} to {at}cell {cell} through Finder{conv_note}. "
                       "Check that it loaded.")
                )
            self._set_status(msg, front=True)
            self._advance_cell_if_needed(cell)
        else:
            core.beep(core.SND_ERROR)
            self._copy_path_to_clipboard(path)
            if result.get("certain"):
                # 自己ドラッグは実行されたが、ドロップ先が受け取らなかった
                # （ほかの窓が重なっているときは運ぶ前に見送るので、ここへは来ない）
                msg = (
                    tr(f"ロード失敗：セル {cell} の位置でドロップが受け入れられませんでした。"
                       "設定ウィンドウ（コマンド+カンマ）のセル位置合わせが"
                       "正しいか確認してください。"
                       "パスはクリップボードにコピーしました。",
                       f"Load failed: the drop was not accepted at cell {cell}. Check "
                       "the cell calibration in the Settings window (Command+Comma). "
                       "The path has been copied to the clipboard.")
                )
            else:
                msg = (
                    tr(f"ロード失敗：{name}。"
                       "パスをクリップボードにコピーしました。手動で貼り付けてください。",
                       f"Load failed: {name}. The path has been copied to the "
                       "clipboard. Please paste it by hand.")
                )
            self._set_status(msg, front=True)


    # ------------------------------------------------------------------
    # ヘルプ
    # ------------------------------------------------------------------
    def show_help(self):
        from Cocoa import NSTextView, NSScrollView as _NSScrollView

        text = help_text()

        # 既存のヘルプウィンドウがあれば前面に出すだけ
        if getattr(self, "_help_window", None) is not None:
            self._help_window.makeKeyAndOrderFront_(None)
            self._help_window.makeFirstResponder_(self._help_textview)
            announce(tr("操作一覧を表示しました。上下矢印で読み進められます。",
                        "Command list shown. Read it with the up and down arrows."))
            return

        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskResizable
        )
        rect = NSMakeRect(260, 180, 520, 460)
        win = HelpWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False
        )
        win.setTitle_(tr("操作一覧（ヘルプ）", "Command List (Help)"))
        win.setReleasedWhenClosed_(False)

        scroll = _NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 520, 460))
        scroll.setHasVerticalScroller_(True)
        tv = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 520, 460))
        tv.setString_(text)
        tv.setEditable_(False)
        tv.setSelectable_(True)
        tv.setAccessibilityLabel_(tr("操作一覧の本文", "Command list text"))
        scroll.setDocumentView_(tv)
        win.setContentView_(scroll)

        self._help_window = win
        self._help_textview = tv
        # カーソルとスクロールを先頭に合わせる（既定では末尾に来てしまう）
        tv.setSelectedRange_(NSMakeRange(0, 0))
        tv.scrollRangeToVisible_(NSMakeRange(0, 0))
        win.makeKeyAndOrderFront_(None)
        win.makeFirstResponder_(tv)
        announce(tr("操作一覧を表示しました。上下矢印で読み進められます。エスケープまたはコマンドWで閉じます。",
                    "Command list shown. Read it with the up and down arrows. Press "
                    "Escape or Command+W to close."))

    # ------------------------------------------------------------------
    # メニュー経由のグローバルアクション
    # ------------------------------------------------------------------
    @objc.python_method
    def _front_main_window(self):
        """メイン画面を前に出す（キット管理や設定から移るとき）。

        前に出さずにフォーカスだけ移すと、利用者はキット管理などの窓に
        残ったまま、後ろのメイン画面の中でフォーカスが動く。
        """
        if not self.window.isKeyWindow():
            self.window.makeKeyAndOrderFront_(None)

    def focusFilter_(self, sender):
        self._front_main_window()
        self.window.makeFirstResponder_(self.filter_field)
        self._set_status(tr("フィルター入力", "Filter input"), announce_msg=False)

    @objc.python_method
    def ensure_list_selection(self):
        """リストの選択が外れていたら戻す（直前のサンプル、無ければ先頭）。

        Tab でリストへ入ったときに何も選ばれていないと、スペースも
        上下キーも意味を持たず「操作できない」ように見える。
        """
        if not self._display_paths:
            return
        if self.table.selectedRow() >= 0:
            return
        idx = 0
        last = getattr(self, "_last_selected_path", None)
        if last and last in self._display_paths:
            idx = self._display_paths.index(last)
        self.table.selectRowIndexes_byExtendingSelection_(
            NSIndexSet.indexSetWithIndex_(idx), False
        )
        self.table.scrollRowToVisible_(idx)

    def focusList_(self, sender):
        self._front_main_window()
        self.window.makeFirstResponder_(self.table)
        self.ensure_list_selection()

    def doScan_(self, sender):
        self.scanClicked_(sender)

    def selectCellByIndex_(self, sender):
        """Cmd+1〜9 で「いまの行」の 1〜9 列目へ移動する。

        セルは最大 48 個あり直接指定しきれないため、列だけを選ぶ。
        行の移動は Cmd+セミコロン（menuCycleRow_）。
        """
        col = sender.tag() - 1
        row, _ = grid_calibration.cell_to_rowcol(self.selected_cell)
        self._select_cell_rowcol(row, col)

    def menuCycleRow_(self, sender):
        """Cmd+セミコロンで行（A→B→C→D→A）を順送りする。"""
        lay = grid_calibration.normalize_layout(self.settings.get("cell_layout"))
        row, col = grid_calibration.cell_to_rowcol(self.selected_cell)
        self._select_cell_rowcol((row + 1) % lay["rows"], col)

    @objc.python_method
    def _select_cell_rowcol(self, row, col):
        names = self.cell_names()
        try:
            cell = grid_calibration.rowcol_to_cell(row, col)
        except Exception:
            return
        if cell not in names:
            core.beep(core.SND_ERROR)
            self._set_status(tr(f"セル {cell} は現在のグリッドにありません",
                                f"Cell {cell} is not in the current grid"))
            return
        self.selected_cell = cell
        self.cell_popup.selectItemAtIndex_(names.index(cell))
        core.beep(core.SND_TICK)
        label = (self.settings.get("cell_labels", {}) or {}).get(cell, "")
        suffix = f" {label}" if label else ""
        self._set_status(
            tr(f"セル {cell}{suffix} を選択。{grid_calibration.describe_cell(cell)}",
               f"Cell {cell}{suffix} selected. {grid_calibration.describe_cell(cell)}")
        )

    def applicationShouldTerminateAfterLastWindowClosed_(self, sender):
        return True

    # AppDelegate からも announce を呼べるようにする（サブウィンドウが利用）
    def announceMessage_(self, message):
        announce(message)

    def announceHighMessage_(self, message):
        """高優先度（割り込み読み）のアナウンス。エラーや位置合わせ結果など、
        読み上げ中でも必ず伝えたい通知にサブウィンドウが使う。"""
        announce(message, high=True)

    # ---- キット管理ウィンドウ ----
    def _ensure_kit_manager(self):
        if getattr(self, "_kit_manager", None) is None:
            import app_kit
            self._kit_manager = app_kit.KitManager.alloc().initWithController_(self)
        return self._kit_manager

    @objc.python_method
    def _open_kit_manager(self):
        """キット管理ウィンドウを開いて KitManager を返す。
        失敗したらエラー音と読み上げで案内して None（無音で失敗させない）。"""
        try:
            km = self._ensure_kit_manager()
            km.show()
            return km
        except Exception:
            core.beep(core.SND_ERROR)
            self._set_status(tr("キット管理ウィンドウを開けませんでした",
                                "Could not open the Kit Manager window"), speak=True)
            return None

    def openKit_(self, sender):
        self._open_kit_manager()

    # ---- キット操作メニュー（Cmd+N / Cmd+S / Cmd+E / Cmd+P）----
    # キット管理ウィンドウが閉じていれば開いてから実行する
    def kitNew_(self, sender):
        km = self._open_kit_manager()
        if km is not None:
            km.newKitClicked_(sender)

    def kitSave_(self, sender):
        km = self._open_kit_manager()
        if km is not None:
            km.saveKitClicked_(sender)

    def kitExport_(self, sender):
        km = self._open_kit_manager()
        if km is not None:
            km.exportClicked_(sender)

    def kitPreviewAll_(self, sender):
        km = self._open_kit_manager()
        if km is not None:
            km.previewAllClicked_(sender)

    # ---- 設定ウィンドウ ----
    def openSettings_(self, sender):
        try:
            if getattr(self, "_settings_window", None) is None:
                import app_settings
                self._settings_window = (
                    app_settings.SettingsManager.alloc().initWithController_(self)
                )
            self._settings_window.show()
        except Exception:
            core.beep(core.SND_ERROR)
            self._set_status(tr("設定ウィンドウを開けませんでした",
                                "Could not open the Settings window"), speak=True)

    # ---- メニュー経由の F/S/R/H 相当アクション ----
    def menuToggleFavorite_(self, sender):
        self.toggle_favorite()

    def menuCycleSort_(self, sender):
        self.cycle_sort()

    def menuCycleReadout_(self, sender):
        self.cycle_readout()

    def menuShowHelp_(self, sender):
        self.show_help()

    def showAbout_(self, sender):
        """「PT Battery 4 Loader について」。版・ビルド日時・ライセンスと、
        AI で作ったことを伝える（配布物の決まり）。"""
        from Cocoa import NSAlert, NSBundle
        flavor, date, commit = version.FLAVOR_SOURCE, "", ""
        if core.running_as_bundle():
            info = NSBundle.mainBundle().infoDictionary() or {}
            flavor = str(info.get(version.KEY_FLAVOR) or "")
            date = str(info.get(version.KEY_BUILD_DATE) or "")
            commit = str(info.get(version.KEY_COMMIT) or "")
        alert = NSAlert.alloc().init()
        alert.setMessageText_(tr(f"{version.APP_NAME} について", f"About {version.APP_NAME}"))
        alert.setInformativeText_(version.about_text(date, commit, flavor))
        alert.addButtonWithTitle_(tr("閉じる", "Close"))
        alert.runModal()


def help_text() -> str:
    """操作一覧（ヘルプ、コマンド+シフト+K）の本文。

    メニューのキーはすべてここに書く（テストで確かめる。キーを足したのに
    ヘルプに無い、を防ぐ）。マニュアルのキーの表はメニューから作る。
    """
    return tr(_help_text_ja(), _help_text_en())


def _help_text_en() -> str:
    return (
        f"{version.APP_NAME} Command List\n"
        "\n"
        "[In the sample list]\n"
        "Up and Down arrows: move between samples\n"
        "Space: preview. Press again to stop\n"
        "Escape: stop the preview / stop a load\n"
        "Return: load into the selected cell\n"
        "\n"
        "[Pressing buttons]\n"
        "Press Tab to reach a button, then Return or Space to press it.\n"
        "The buttons in the Settings and Kit Manager windows work the same way.\n"
        "\n"
        "[Command keys (work anywhere)]\n"
        "Command+D: add or remove a favorite\n"
        "Command+T: cycle the sort order\n"
        "Command+Y: cycle the name reading (off / symbols cleaned up / digits as words)\n"
        "Command+Shift+K: show this help\n"
        "Command+F: go to the filter field\n"
        "Command+I: go to the sample list\n"
        "Command+O: scan\n"
        "Command+B: cycle the load target Battery (for several instances)\n"
        "Command+Comma: Settings window (folders, cell positions, labels)\n"
        "Command+H: hide the app (macOS standard)\n"
        "Command+Q: quit\n"
        "\n"
        "[Editing text fields]\n"
        "In the filter and kit name fields, the macOS standard keys work:\n"
        "Command+A select all, Command+C copy, Command+V paste,\n"
        "Command+X cut, Command+Z undo, Command+Shift+Z redo.\n"
        "\n"
        "[Filtering (when you have many samples)]\n"
        "Filters can be combined. Only samples that meet all of them remain.\n"
        "Filter field: type part of a file name (narrows with each character)\n"
        "Command+J: cycle the type (all, one-shot, loop)\n"
        "  Decided by the sample's length. Samples longer than the set seconds\n"
        "  (2 seconds at first) count as loops. Shorter ones count as loops if the\n"
        "  file name has loop, groove, beat, or the like, and otherwise as one-shots.\n"
        "Command+G: choose a pack (Splice pack names. Type the first letters to jump)\n"
        "Command+Shift+G: cycle the pack order\n"
        "  Most samples first, by name, recently updated first, order added\n"
        "Command+U: turn favorites only on or off\n"
        "Command+R: clear all filters\n"
        "When you change a filter, the number of samples is read out.\n"
        "A low sound plays if nothing is found.\n"
        "\n"
        "[Sample length]\n"
        "The length of every sample is read when you scan.\n"
        "Choose \"Length, shortest first\" or \"Length, longest first\" with Command+T\n"
        "to hear the length at the start of each row.\n"
        "You can change the length that counts as a one-shot in the Settings\n"
        "window (Command+Comma).\n"
        "\n"
        "[Choosing a cell]\n"
        "Cells have the same names as in Battery. Rows are A to D, columns 1 to 12.\n"
        "A2 is to the right of A1, and B1 is directly below A1.\n"
        "Command+1 to 9: go to column 1 to 9 of the current row\n"
        "Command+Semicolon: cycle the row (A to B, C, D)\n"
        "Columns from 10 on can be chosen from the load target cell pop-up.\n"
        "\n"
        "[Kit Manager]\n"
        "Command+K: open the Kit Manager window\n"
        "Command+N: new kit\n"
        "Command+S: save the kit\n"
        "Command+E: export to Battery 4\n"
        "Command+P: preview the kit\n"
        "\n"
        "[About loading]\n"
        "Return or the Load button drags the selected sample to the selected cell\n"
        "automatically after the countdown you set.\n"
        "Press Escape to stop it (during the countdown and during a track switch too).\n"
        "A small square window appears near the cell only while dragging.\n"
        "If another app's window covers the cell, nothing is carried, and the name\n"
        "of the covering window is read out.\n"
        "The load position is worked out each time from the open Battery window.\n"
        "You do not need to capture positions again when the window moves.\n"
        "Formats Battery 4 cannot read (such as WAV with markers) are converted\n"
        "automatically to a supported format before loading.\n"
        "Allow Accessibility permission in the Settings window the first time.\n"
        "Calibrate in the Settings window only if the cell positions are off.\n"
        "If a load fails, the file path is copied to the clipboard.\n"
        "\n"
        "[Important: keep only one Battery window open]\n"
        "Pro Tools opens plug-in windows on top of each other in the same place.\n"
        "With overlapping windows, there is no way to tell which one gets the sample.\n"
        "Carrying it anyway could overwrite a cell on another track, so the\n"
        "load stops when two or more windows are open.\n"
        "Keep only the one you want to use and close the other Battery windows.\n"
        "If none is open, Command+Shift+O opens one.\n"
        "\n"
        "[Using several Batteries]\n"
        "To change the load target, switch tracks with Command+Shift+T.\n"
        "With one window, you can load to every track.\n"
        "\n"
        "If you choose \"All Batteries\" as the load target, one action fills the\n"
        "same cell of every inserted Battery.\n"
        "One window is switched track by track as the sample is carried.\n"
        "With 3 inserted, for example, the same sample goes into 3 A1 cells.\n"
        "When finished, where it went is read out.\n"
        "If only some fail, the names of those tracks are read out.\n"
        "Saving a kit also remembers \"All Batteries\".\n"
        "\n"
        "Command+Shift+B cycles how the load target is read out:\n"
        "\"Battery 4 on Inst1\", \"Inst1\" only, or the short form \"Inst 1\".\n"
        "\n"
        "[Moving a sample between cells]\n"
        "Command+M moves the sample in the current cell to another cell.\n"
        "Use this to correct a load that went to the wrong cell,\n"
        "for example to move what went into A2 to A6.\n"
        "After moving, please check by ear that it moved.\n"
        "\n"
        "[About this app]\n"
        f"\"About {version.APP_NAME}\" in the app menu shows the version.\n"
        f"{version.AI_NOTE}\n"
    )


def _help_text_ja() -> str:
    return (
        f"{version.APP_NAME} 操作一覧\n"
        "\n"
        "【サンプルリスト内】\n"
        "上下矢印キー: サンプルを移動\n"
        "スペース: プレビュー再生。もう一度押すと停止\n"
        "エスケープ: 再生停止 / ロード中断\n"
        "リターン: 選択セルへロード\n"
        "\n"
        "【ボタンの押し方】\n"
        "タブでボタンへ移動し、リターンかスペースで実行します。\n"
        "設定ウィンドウやキット管理のボタンも同じです。\n"
        "\n"
        "【コマンドキー操作（どこでも有効）】\n"
        "コマンド+D: お気に入り 登録 / 解除\n"
        "コマンド+T: 並び替えを順送り\n"
        "コマンド+Y: 読み上げ補正を切替（オフ / 記号整形 / 数字カナ）\n"
        "コマンド+シフト+K: このヘルプを表示\n"
        "コマンド+F: フィルター欄へ移動\n"
        "コマンド+I: サンプルリストへ移動\n"
        "コマンド+O: スキャン実行\n"
        "コマンド+B: ロード先の Battery を切替（複数インスタンス用）\n"
        "コマンド+カンマ: 設定ウィンドウ（フォルダ登録・セル位置・ラベル）\n"
        "コマンド+H: アプリを隠す（macOS の標準）\n"
        "コマンド+Q: 終了\n"
        "\n"
        "【入力欄の編集】\n"
        "フィルター欄やキット名の欄では、macOS の標準どおり\n"
        "コマンド+A ですべてを選択、コマンド+C でコピー、コマンド+V でペースト、\n"
        "コマンド+X でカット、コマンド+Z で取り消し、コマンド+シフト+Z でやり直しができます。\n"
        "\n"
        "【絞り込み（サンプルが多いとき）】\n"
        "条件は重ねられます。すべてを満たすサンプルだけが残ります。\n"
        "フィルター欄: ファイル名の一部を入力（1文字ごとに絞り込み）\n"
        "コマンド+J: 種別を順送り（すべて、ワンショット、ループ）\n"
        "  サンプルの長さで判断します。設定した秒数（初期値2秒）より\n"
        "  長いものはループとみなします。短いものは、ファイル名に\n"
        "  loop・groove・beat などがあればループ、なければワンショットです。\n"
        "コマンド+G: パックを選ぶ（Splice のパック名。頭文字で飛べます）\n"
        "コマンド+シフト+G: パックの並び順を順送り\n"
        "  中身の多い順、名前順、更新の新しい順、追加した順の 4 つです\n"
        "コマンド+U: お気に入りだけの表示を切替\n"
        "コマンド+R: 絞り込みをすべて解除して元に戻す\n"
        "条件を変えると件数を読み上げます。\n"
        "見つからないときは低い音が鳴ります。\n"
        "\n"
        "【サンプルの長さ】\n"
        "スキャンのときに全サンプルの長さを調べます。\n"
        "並び替え（コマンド+T）で「長さ 短い順」「長さ 長い順」を選ぶと、\n"
        "行の先頭に長さが読み上げられます。\n"
        "ワンショットとみなす長さの境目は、設定ウィンドウ\n"
        "（コマンド+カンマ）で変えられます。\n"
        "\n"
        "【セルの選び方】\n"
        "セルは Battery と同じ呼び方です。行が A から D、列が 1 から 12。\n"
        "A1 の右隣は A2、A1 の真下は B1 です。\n"
        "コマンド+1から9: いまの行の 1 列目から 9 列目へ移動\n"
        "コマンド+セミコロン: 行を順送り（A から B、C、D）\n"
        "10 列目より右は、ロード先セルのポップアップから選べます。\n"
        "\n"
        "【キット管理】\n"
        "コマンド+K: キット管理ウィンドウを開く\n"
        "コマンド+N: 新規キット\n"
        "コマンド+S: キットを保存\n"
        "コマンド+E: Battery 4 へエクスポート\n"
        "コマンド+P: キットを一括プレビュー\n"
        "\n"
        "【ロードについて】\n"
        "リターンまたはロードボタンで、設定した秒数のカウントダウン後に\n"
        "選択中のサンプルを選択セルへ自動ドラッグします。\n"
        "エスケープで中断できます（カウントダウン中もトラックの切り替え中も）。\n"
        "ドラッグの間だけ、セルの近くに小さな四角い窓が出ます。\n"
        "セルの位置にほかのアプリの画面が重なっているときは、運ばずに中止し、\n"
        "重なっている画面の名前を伝えます。\n"
        "ロード先の位置は、開いている Battery のウィンドウから毎回\n"
        "計算します。ウィンドウを動かしても座標を取り直す必要はありません。\n"
        "Battery 4 が読めない形式（マーカー入りWAVなど）は\n"
        "自動的に対応形式へ変換してからロードします。\n"
        "初回は設定ウィンドウでアクセシビリティ権限を許可してください。\n"
        "セル位置が合わないときだけ、設定ウィンドウで位置合わせをします。\n"
        "失敗した場合はファイルパスをクリップボードにコピーします。\n"
        "\n"
        "【重要：Battery の画面は1枚だけ開いてください】\n"
        "Pro Tools は複数のプラグイン画面を同じ位置に重ねて開きます。\n"
        "重なった画面は、どれに入るか特定できません。\n"
        "そのまま運ぶと別のトラックのセルを上書きしてしまうため、\n"
        "画面が2枚以上開いているときはロードを中止します。\n"
        "使う 1 枚だけを残して、ほかの Battery の画面を閉じてください。\n"
        "1枚も開いていないときは、コマンド+シフト+O で開けます。\n"
        "\n"
        "【複数の Battery を使うとき】\n"
        "ロード先の変更は、コマンド+シフト+T でトラックを切り替えます。\n"
        "画面は1枚のまま、全部のトラックへロードできます。\n"
        "\n"
        "ロード先で「すべての Battery」を選ぶと、1回の操作で\n"
        "挿さっているすべての Battery の同じセルへ配れます。\n"
        "1枚の画面をトラックごとに切り替えながら、順に運びます。\n"
        "たとえば 3 つ挿さっていれば、3 つのセル A1 に同じサンプルが\n"
        "入ります。配り終えると、どこに入ったかを読み上げます。\n"
        "一部だけ失敗した場合は、失敗したトラックの名前を伝えます。\n"
        "キットに保存すると「すべての Battery」も一緒に覚えます。\n"
        "\n"
        "コマンド+シフト+B で、ロード先の読み上げ方を切り替えられます。\n"
        "「Inst1 の Battery 4」、「Inst1」だけ、「インスト1」の3通りです。\n"
        "\n"
        "【セルの入れ直し】\n"
        "コマンド+M で、いま選んでいるセルのサンプルを別のセルへ\n"
        "移動できます。ロード先がずれてしまったときの直しに使います。\n"
        "たとえば A2 に入ったものを A6 へ移せます。\n"
        "移動のあとは、実際に移動できたか音で確認してください。\n"
        "\n"
        "【このアプリについて】\n"
        f"アプリのメニューの「{version.APP_NAME} について」で、版を確かめられます。\n"
        f"{version.AI_NOTE}\n"
    )


# ---------------------------------------------------------------------------
# メニュー構築（Cmd系ショートカット）
# ---------------------------------------------------------------------------
# 編集メニュー: (項目名, 動作, キー, シフトも要るか)。項目名 None は区切り線。
# 名前は macOS の日本語の標準どおり
EDIT_MENU_ITEMS = (
    (tr("取り消す", "Undo"), "undo:", "z", False),
    (tr("やり直す", "Redo"), "redo:", "z", True),
    (None, None, None, False),
    (tr("カット", "Cut"), "cut:", "x", False),
    (tr("コピー", "Copy"), "copy:", "c", False),
    (tr("ペースト", "Paste"), "paste:", "v", False),
    (tr("すべてを選択", "Select All"), "selectAll:", "a", False),
)


def _build_menu(delegate):
    from Cocoa import NSMenu, NSMenuItem

    mainmenu = NSMenu.alloc().init()

    # アプリメニュー（バージョン情報・隠す・終了）
    app_item = NSMenuItem.alloc().init()
    mainmenu.addItem_(app_item)
    app_menu = NSMenu.alloc().init()
    app_item.setSubmenu_(app_menu)
    about_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        tr(f"{version.APP_NAME} について", f"About {version.APP_NAME}"), "showAbout:", ""
    )
    about_item.setTarget_(delegate)
    app_menu.addItem_(about_item)
    app_menu.addItem_(NSMenuItem.separatorItem())
    # **隠すは標準どおり Cmd+H。** かつてヘルプに Cmd+H を使っていたため
    # Cmd+Shift+H へ動かしていたが、ヘルプを Cmd+Shift+K へ移したので
    # 標準に戻した（2026-09-04）。標準の位置にあるほうが迷わない。
    hide_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        tr("隠す", "Hide"), "hide:", "h"
    )
    app_menu.addItem_(hide_item)
    quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        tr("終了", "Quit"), "terminate:", "q"
    )
    app_menu.addItem_(quit_item)

    # 編集メニュー（入力欄の取り消し・カット・コピー・ペースト・すべてを選択）。
    # **これが無いと入力欄でコマンド+V などが効かない。** AppKit は
    # これらのキーをメニューの項目として受け取るため（nib を使わずに
    # 組み立てるアプリでは自分で作る必要がある。2026-10-24 のレビューで、
    # フィルター欄でコマンド+A が効かないことを確かめた）。
    # 送り先は決めない（フォーカスのある入力欄が受け取る）
    edit_item = NSMenuItem.alloc().init()
    mainmenu.addItem_(edit_item)
    edit_menu = NSMenu.alloc().initWithTitle_(tr("編集", "Edit"))
    edit_item.setSubmenu_(edit_menu)
    for title, action, key, shift in EDIT_MENU_ITEMS:
        if title is None:
            edit_menu.addItem_(NSMenuItem.separatorItem())
            continue
        it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            title, action, key)
        if shift:
            it.setKeyEquivalentModifierMask_(_CMD_SHIFT_MASK)
        edit_menu.addItem_(it)

    # 操作メニュー
    act_item = NSMenuItem.alloc().init()
    mainmenu.addItem_(act_item)
    act_menu = NSMenu.alloc().initWithTitle_(tr("操作", "Actions"))
    act_item.setSubmenu_(act_menu)

    def add(menu, title, action, key, target=delegate, tag=None,
            cmd_shift=False):
        it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
        it.setTarget_(target)
        if tag is not None:
            it.setTag_(tag)
        if cmd_shift:
            it.setKeyEquivalentModifierMask_(_CMD_SHIFT_MASK)
        menu.addItem_(it)
        return it

    add(act_menu, tr("スキャン", "Scan"), "doScan:", "o")
    add(act_menu, tr("フィルターへ", "Go to Filter"), "focusFilter:", "f")
    add(act_menu, tr("リストへ", "Go to List"), "focusList:", "i")
    add(act_menu, tr("お気に入り 登録／解除", "Add or Remove Favorite"), "menuToggleFavorite:", "d")
    add(act_menu, tr("並び替えを順送り", "Cycle Sort Order"), "menuCycleSort:", "t")
    add(act_menu, tr("読み上げ補正を切替", "Cycle Name Reading"), "menuCycleReadout:", "y")
    # **ヘルプは Cmd+Shift+K。** 作者の別のアプリと揃えてある
    # （2026-09-04 に利用者の判断で、こちらを 2 つに合わせた）。
    add(act_menu, tr("操作一覧（ヘルプ）",
                     "Command List (Help)"), "menuShowHelp:", "k", cmd_shift=True)
    add(act_menu, tr("ロード先 Battery を切替",
                     "Cycle Load Target Battery"), "menuCycleBattery:", "b")
    add(act_menu, tr("ロード先の読み上げ方を切替",
                     "Cycle Load Target Reading"), "menuCycleTargetReadout:", "b",
        cmd_shift=True)
    add(act_menu, tr("Battery の画面のトラックを切替",
                     "Switch Battery Window Track"), "menuSelectTrack:", "t",
        cmd_shift=True)
    add(act_menu, tr("Battery の画面を開く", "Open Battery Window"), "menuOpenAllBatteries:", "o",
        cmd_shift=True)
    add(act_menu, tr("設定", "Settings"), "openSettings:", ",")

    # 絞り込みメニュー（作者の別のアプリと同じ AND 条件）
    flt_item = NSMenuItem.alloc().init()
    mainmenu.addItem_(flt_item)
    flt_menu = NSMenu.alloc().initWithTitle_(tr("絞り込み", "Filter"))
    flt_item.setSubmenu_(flt_menu)
    add(flt_menu, tr("種別を順送り（すべて、ワンショット、ループ）",
                     "Cycle Type (All, One-Shot, Loop)"), "menuCycleKind:", "j")
    add(flt_menu, tr("パックを選ぶ", "Choose Pack"), "menuFocusPack:", "g")
    add(flt_menu, tr("パックの並び順を順送り（中身の多い順、名前順、更新の新しい順、追加した順）",
                     "Cycle Pack Order (Most Samples, By Name, Recently Updated, "
                     "Order Added)"),
        "menuCyclePackOrder:", "g", cmd_shift=True)
    add(flt_menu, tr("お気に入りだけ 表示切替", "Favorites Only On or Off"), "menuToggleFavOnly:", "u")
    add(flt_menu, tr("絞り込みをすべて解除", "Clear All Filters"), "menuClearFilters:", "r")

    # キットメニュー
    kit_item = NSMenuItem.alloc().init()
    mainmenu.addItem_(kit_item)
    kit_menu = NSMenu.alloc().initWithTitle_(tr("キット", "Kit"))
    kit_item.setSubmenu_(kit_menu)
    add(kit_menu, tr("キット管理を開く", "Open Kit Manager"), "openKit:", "k")
    add(kit_menu, tr("新規キット", "New Kit"), "kitNew:", "n")
    add(kit_menu, tr("キットを保存", "Save Kit"), "kitSave:", "s")
    add(kit_menu, tr("Battery 4 へエクスポート", "Export to Battery 4"), "kitExport:", "e")
    add(kit_menu, tr("キットを一括プレビュー", "Preview Kit"), "kitPreviewAll:", "p")

    # セル選択：Cmd+1〜9 は「いまの行の 1〜9 列目」、Cmd+; で行を送る
    cell_item = NSMenuItem.alloc().init()
    mainmenu.addItem_(cell_item)
    cell_menu = NSMenu.alloc().initWithTitle_(tr("セル", "Cell"))
    cell_item.setSubmenu_(cell_menu)
    for i in range(1, 10):
        add(cell_menu, tr(f"いまの行の {i} 列目",
                          f"Column {i} of This Row"), "selectCellByIndex:", str(i), tag=i)
    add(cell_menu, tr("行を順送り（A から B、C、D）",
                      "Cycle Row (A to B, C, D)"), "menuCycleRow:", ";")
    add(cell_menu, tr("このセルのサンプルを別のセルへ移動",
                      "Move This Cell's Sample to Another Cell"), "menuMoveCell:", "m")

    NSApp.setMainMenu_(mainmenu)


def build_headless():
    """窓を出さずにアプリを組み立てる（起動確認とマニュアルの生成で使う）。

    利用者の設定ファイルは読まず、既定値で組み立てる。音と読み上げも止める。
    Returns: (AppDelegate, KitManager, SettingsManager)
    """
    import app_settings

    os.environ["BATTERY4_SILENT"] = "1"
    app = NSApplication.sharedApplication()
    # Dock にアイコンを出さない（ビルドの最中に利用者の画面を騒がせない）
    app.setActivationPolicy_(2)   # NSApplicationActivationPolicyProhibited
    d = AppDelegate.alloc().init()
    d._init_state(core.default_settings(), {})
    d._build_window()
    _build_menu(d)
    km = d._ensure_kit_manager()
    km._build_window()
    sm = app_settings.SettingsManager.alloc().initWithController_(d)
    sm._build_window()
    sm._sync_widgets_from_settings()
    d._settings_window = sm
    return d, km, sm


def run_selftest() -> int:
    """窓を出さずにアプリを組み立て、結果を 1 行の JSON で出す。

    ビルドの起動確認に使う（.app の中の実行ファイルに `--selftest` を付ける）。
    固めたときに入れ忘れた部品（遅れて import するモジュールや pyobjc の
    枠組み）は、ふだんの起動では窓を開いたときにやっと見つかるが、
    ここなら目で見ずに見つけられる。設定ファイルには書かない。
    """
    import json
    import sys

    report = {"ok": False, "version": version.VERSION,
              "frozen": core.running_as_bundle(),
              "python": sys.version.split()[0]}
    try:
        d, km, sm = build_headless()
        # 読み上げ補正（数字カナ）と一覧の作り直しを、全角数字の名前でも通す
        d.readout_mode = "kana"
        d._all_samples = ["/s/packs/Pack A/kick１.wav", "/s/loop_120.aif"]
        d._durations = {"/s/loop_120.aif": 4.0}
        d._refresh_list()
        menu = NSApp.mainMenu()
        titles = [menu.itemAtIndex_(i).submenu().title()
                  for i in range(menu.numberOfItems())]
        report.update({
            "rows": len(d._display_paths),
            "menus": titles,
            "cells": len(d.cell_names()),
            "kit_buttons": len(km._buttons),
            "settings_tab_stops": len(sm._key_view_order),
            "quartz": bool(core.QUARTZ_OK),
            "accessibility_api": bool(core._AX_OK and core._AX_QUERY_OK),
            # サブウィンドウが app_main をもう一度読み込んでいないこと
            # （読み込むと設定とキット管理でリターンが効かなくなる）
            "app_main_reimported": "app_main" in sys.modules
            and __name__ == "__main__",
        })
        report["ok"] = (report["rows"] == 2 and tr("編集", "Edit") in titles
                        and report["quartz"] and report["accessibility_api"]
                        and not report["app_main_reimported"])
    except Exception as e:
        report["error"] = f"{type(e).__name__}: {e}"
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report["ok"] else 1


def main():
    import sys
    if "--selftest" in sys.argv[1:]:
        raise SystemExit(run_selftest())
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
