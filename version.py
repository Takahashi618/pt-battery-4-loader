"""
PT Battery 4 Loader の版と、バージョン情報の文（UI 非依存の純粋関数）
=================================================================

版の番号は**ここ 1 か所で決める。** Info.plist（PyInstaller の spec）、
アプリの「PT Battery 4 Loader について」、マニュアル、配布物の名前は、
どれもここを読む。書き写すと必ずずれる。

番号は 2026-10-24 まで Info.plist にあった 3.0 の続き（手元の .app の
表示が新しい版で小さくならないようにした）。

**名前を変えても、データの置き場所は変えない。** `~/.battery4_loader_*.json`、
`~/Library/Application Support/Battery4Loader`（Battery 4 のキットが指している）、
ログ `Battery4Loader.log`、実行ファイル `Battery4Loader`、バンドル ID、
環境変数 `BATTERY4_*` は前のまま。変えると設定と許可が引き継がれない。
"""

from __future__ import annotations

from i18n import tr

VERSION = "3.2.1"
APP_NAME = "PT Battery 4 Loader"
COPYRIGHT = "Copyright (c) 2026 Takahashi"

# Info.plist に書き込むビルドの情報の名前（ビルドの道具が書き、アプリが読む）
KEY_BUILD_DATE = "B4LBuildDate"
KEY_COMMIT = "B4LGitCommit"
KEY_FLAVOR = "B4LFlavor"

# Info.plist に書く値は言葉にしない（読み上げる言葉は言語で変わる）
FLAVOR_LOCAL = "local"
FLAVOR_RELEASE = "release"
FLAVOR_SOURCE = "source"
FLAVOR_LABELS = {
    FLAVOR_LOCAL: tr("手元版", "Local build"),
    FLAVOR_RELEASE: tr("配布版", "Release build"),
    FLAVOR_SOURCE: tr("ソースから起動", "Started from source"),
}

AI_NOTE = (
    tr("このアプリは、作者（Takahashi）の指示と判断のもとで、"
       "AI（Anthropic 社の Claude）を使って作りました。"
       "プログラムのコードとマニュアルの大部分は AI が書いたものです。",
       "This app was made by the author (Takahashi) using AI (Claude by Anthropic), "
       "under the author's direction and judgment. Most of the code and the manual "
       "was written by the AI.")
)


def about_text(build_date: str = "", commit: str = "",
               flavor: str = "") -> str:
    """「PT Battery 4 Loader について」で読み上げる文。

    ビルドの日時とコミットは .app の Info.plist にあるときだけ添える
    （ソースから起動しているときは無い）。
    """
    lines = [f"{APP_NAME} {VERSION}"]
    if flavor:
        label = FLAVOR_LABELS.get(flavor, flavor)
        lines.append(tr(f"種類: {label}", f"Type: {label}"))
    if build_date:
        lines.append(tr(f"ビルド日時: {build_date}", f"Build date: {build_date}"))
    if commit:
        lines.append(tr(f"コミット: {commit}", f"Commit: {commit}"))
    lines.append(tr(f"MIT ライセンス。{COPYRIGHT}", f"MIT License. {COPYRIGHT}"))
    lines.append(AI_NOTE)
    return "\n".join(lines)
