# -*- mode: python ; coding: utf-8 -*-
# PT Battery 4 Loader（配布版）— PyInstaller の設定
#
# 直接は使わない。tools/build_release.py が、組み立て・確認・刻印・
# ライセンスの収集・zip まで行う。
#   python3 tools/build_release.py
#
# 出力: build/release/dist/PT Battery 4 Loader.app（windowed / onedir / arm64）
# - 名前の PT は Pro Tools のこと。データの置き場所とバンドル ID は変えていない（version.py）
# - 中の実行ファイルは ASCII 名（Battery4Loader）。.app の名前は読み上げで分かるよう空白入り
# - 署名は ad-hoc（PyInstaller の既定）。**手元の証明書では署名しない**（証明書の名前が配布物に入る）
# - バンドル ID は手元版（com.user.battery4loader）と分ける。同じ Mac に両方あっても、
#   アクセシビリティの許可が混ざらないように
# - upx は使わない（署名が壊れることがある）
# - 開発言語は en（Info.plist の既定の説明も英語）。日本語は resources/ja.lproj から出る。
#   画面の言語の決め方（i18n.py）と同じく、日本語も英語も無い Mac では英語になる
# - 自分たちのコードは、**説明文とコメントを除いた写し**から組み立てる。コンパイルしたコード
#   （.app の中の PYZ）にも説明文は文字列として残り、取り出せば読めるため
# - 使わない部品（tkinter・PyInstaller・setuptools など）は入れない。入れると
#   同梱部品のライセンスが増え、配布物も大きくなる
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(ROOT))

import version  # noqa: E402
from i18n import SUPPORTED  # noqa: E402
from tools.build_release import CLEAN, OWN_MODULES, clean_source  # noqa: E402

SRC = CLEAN / "src"
SRC.mkdir(parents=True, exist_ok=True)
for name in OWN_MODULES:
    path = ROOT / f"{name}.py"
    (SRC / path.name).write_text(clean_source(path.read_text(encoding="utf-8")),
                                 encoding="utf-8")

# 許可の説明の日本語と英語（Contents/Resources/*.lproj）
datas = [(str(ROOT / "resources" / f"{lang}.lproj" / "InfoPlist.strings"), f"{lang}.lproj")
         for lang in SUPPORTED]

a = Analysis(
    [str(SRC / "app_main.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    # app_kit / app_settings はメニュー操作時に遅れて import されるため明示する
    hiddenimports=[name for name in OWN_MODULES if name != "app_main"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PyInstaller", "pytest", "IPython", "setuptools",
              "pkg_resources"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Battery4Loader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Battery4Loader",
)
app = BUNDLE(
    coll,
    name=f"{version.APP_NAME}.app",
    icon=None,
    bundle_identifier="local.takahashi.battery4loader",
    version=version.VERSION,
    info_plist={
        "CFBundleName": version.APP_NAME,
        "CFBundleDisplayName": version.APP_NAME,
        "CFBundleDevelopmentRegion": "en",
        "CFBundleLocalizations": list(SUPPORTED),
        "CFBundleShortVersionString": version.VERSION,
        "CFBundleVersion": version.VERSION,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "14.0",
        "LSArchitecturePriority": ["arm64"],
        "LSRequiresNativeExecution": True,
        # バージョン情報で読み上げる種類。ビルドの日時とコミットは、
        # tools/build_release.py が確認を通したあとで書き込む（刻印）
        version.KEY_FLAVOR: version.FLAVOR_RELEASE,
        # 許可の説明の既定は英語。日本語は resources/ja.lproj/InfoPlist.strings にある
        "NSAccessibilityUsageDescription": (
            "Used to drag samples to the cells of Battery 4 and to read the Battery "
            "window in Pro Tools."),
        "NSAppleEventsUsageDescription": (
            "Controls Pro Tools, Finder, and System Events to bring Pro Tools to the "
            "front and for the backup load method (dragging through Finder)."),
        "NSDesktopFolderUsageDescription": "Used to load samples placed on the Desktop.",
        "NSDocumentsFolderUsageDescription": "Used to load samples in the Documents folder.",
        "NSDownloadsFolderUsageDescription": "Used to load downloaded samples.",
        "NSRemovableVolumesUsageDescription": "Used to load samples on external drives.",
        "NSNetworkVolumesUsageDescription": "Used to load samples in network folders.",
    },
)
