# PT Battery 4 Loader

Splice や自分のフォルダのドラムなどのサンプルを、探して、試聴して、Pro Tools に挿した Native Instruments の Battery 4 のセルへ読み込む macOS のアプリです。
読み込むときは、マウスでドラッグする操作をアプリが代わりに行い、読み込めたかどうかを読み上げます。
VoiceOver とキーボードだけで使えるように作っています。名前の「PT」は Pro Tools のことです。
画面と読み上げは、Mac の言語の設定に合わせて日本語か英語で出ます。

- 動く環境: Apple シリコン（M1 以降）の Mac、macOS 14 以降、Pro Tools、Native Instruments Battery 4、VoiceOver。
  作者は macOS 15.7.8、Pro Tools 2026.4.1、Battery 4.3.1 で確かめています
- 使うだけなら: [Releases](https://github.com/Takahashi618/pt-battery-4-loader/releases) にある `PTBattery4Loader_3.2.0.zip` を開き、中の「はじめにお読みください.txt」を読んでください
- 使い方: [docs/manual.txt](docs/manual.txt)（HTML 版は [docs/manual.html](docs/manual.html)。ダウンロードしてブラウザで開きます）
- ライセンス: MIT ライセンス（Copyright (c) 2026 Takahashi）。全文は [LICENSE.txt](LICENSE.txt)。
  配布版のアプリに入っているほかの人の部品の条件は [同梱部品のライセンス.txt](同梱部品のライセンス.txt)
- このソフトウェアは、作者（Takahashi）の指示と判断のもとで、AI（Anthropic 社の Claude）を使って作りました。
  プログラムのコードと文章の大部分は AI が書いたものです
- Pro Tools は Avid Technology 社の、Battery 4 は Native Instruments 社の、Splice は Splice 社の製品です。このアプリは、それらの会社とは関係ありません

## ソースから動かす

Python 3.14 と、[requirements.txt](requirements.txt) の部品を入れます。

```bash
python3.14 -m pip install -r requirements.txt
```

```bash
python3.14 -X utf8 app_main.py
```

- 合成マウスで Battery 4 のセルへドラッグし、Pro Tools の画面を読むので、動かすアプリ（端末など）にアクセシビリティの許可が要ります
- 設定とキットはホームフォルダの `.battery4_loader_settings.json` と `.battery4_loader_kits.json` に保存します（配布版と同じです）
- テスト: `python3.14 -X utf8 tests/run_all.py`（Pro Tools には触りません。ホームは一時フォルダにします）
- 配布版（zip）を作る: `python3.14 -X utf8 tools/build_release.py`
  （[python.org](https://www.python.org/) の Python 3.14 で動かします。PyInstaller を使います）
- マニュアルを直したら: `python3.14 -X utf8 tools/doc_parts.py`（キーの一覧などを作り直し、txt も作ります）

### 中身

- `app_main.py`・`app_kit.py`・`app_settings.py`・`ui_common.py`: 画面（PyObjC と AppKit）
- `core.py`: サンプルの検索と絞り込み、設定、Pro Tools の画面の読み取り、合成ドラッグ
- `grid_calibration.py`・`name_readout.py`: セルの位置の計算と、ファイル名の読み上げの補正
- `i18n.py`: 表示の言語（Mac の言語の設定に合わせて日本語か英語。文言は `tr("日本語", "English")` と並べて書いています）
- `docs/`: マニュアルと「はじめにお読みください」の元
- コメントには、開発中に実機で測って分かったことを日付つきで残しています

## English

PT Battery 4 Loader is a macOS app for finding drum samples and the like (from Splice or your own folders), previewing them, and loading them into the cells of Native Instruments Battery 4 inserted in Pro Tools.
The app performs the mouse drag on your behalf and reads out whether the load worked. It is designed to be used with VoiceOver and the keyboard only. "PT" stands for Pro Tools.

The app's window and speech follow your Mac's language settings: English or Japanese.

- Requirements: a Mac with Apple silicon (M1 or later), macOS 14 or later, Pro Tools, Native Instruments Battery 4, VoiceOver.
  Tested on macOS 15.7.8, Pro Tools 2026.4.1, and Battery 4.3.1
- To use it: open `PTBattery4Loader_3.2.0.zip` from [Releases](https://github.com/Takahashi618/pt-battery-4-loader/releases) and read “README (English).txt” inside
- Manual: [docs/manual_en.txt](docs/manual_en.txt) (HTML version: [docs/manual_en.html](docs/manual_en.html))
- License: MIT License (Copyright (c) 2026 Takahashi). Full text in [LICENSE.txt](LICENSE.txt).
  Third-party components in the packaged app: [同梱部品のライセンス.txt](同梱部品のライセンス.txt)
- This software was made by the author (Takahashi) using AI (Claude by Anthropic), under the author's direction and judgment.
  Most of the code and text was written by the AI
- Pro Tools is a product of Avid Technology, Battery 4 is a product of Native Instruments, and Splice is a product of Splice. This app is not affiliated with those companies

### Running from source

Install Python 3.14 and [requirements.txt](requirements.txt), then run `python3.14 -X utf8 app_main.py`. The app you run it from (such as Terminal) needs Accessibility permission,
because it drags to Battery 4 cells with a synthesized mouse and reads the Pro Tools windows. Settings and kits are saved in `.battery4_loader_settings.json` and `.battery4_loader_kits.json` in your home folder
(same as the packaged app). Code comments are mostly in Japanese.
Tests: `python3.14 -X utf8 tests/run_all.py` Packaging: `python3.14 -X utf8 tools/build_release.py`
(use the python.org Python 3.14 with PyInstaller installed)
