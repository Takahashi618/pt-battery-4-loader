"""配布物（zip）を作る。確かめる → 組み立てる → 並べる → zip → 中身の検証、の順。

    python3 tools/build_release.py
        release/PTBattery4Loader_<版>.zip を作る
    python3 tools/build_release.py --dirty
        試し（書きかけの変更があっても止めない。コミットに + が付く）
    python3 tools/build_release.py --app-only
        .app の組み立てと確認だけ（zip は作らない）

配布版は **ad-hoc 署名**で、作者の手元で使う版とはバンドル ID も分けてある（tools/release.spec）。

組み立てには python.org の python3.14 を使う（PyInstaller と pyobjc が入っている。
Homebrew の python だと、固めたときに Homebrew の OpenSSL などが入り、
ライセンスを別に集めることになる）。

zip は release/ に置く。

工程（どこかで落ちたら zip を作らない）:

  1. 書きかけの変更  同梱するソースと、配布物の印（コミット）をずらさない
  2. テスト          tests/run_all.py（ホームは一時フォルダ。音と読み上げなし）
  3. マニュアル      tools/doc_parts.py --check と check_html.py
  4. .app の組み立て PyInstaller（tools/release.spec）。build/release/ に作る
  5. .app の確認     中の実行ファイルで --selftest と、起動の処理を通す起動
                     （BATTERY4_SMOKE=1。窓は出さない）。通ったらビルド日時と
                     コミットを刻んで ad-hoc で署名し直し、署名ともう一度の
                     --selftest を確かめる
  6. 同梱部品のライセンス  PyInstaller の目録（toc）と各部品の RECORD から作る
  7. 並べる          はじめにお読みください・マニュアル・ライセンス・.app
  8. zip             ditto（.app の中のシンボリックリンクと署名を壊さない）
  9. zip の検証      展開して署名と --selftest と起動、並び、txt の形、個人の情報

**署名は ad-hoc。** 手元の証明書で署名すると、証明書の名前が配布物に入る。
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import marshal
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import version  # noqa: E402
from i18n import ENV_NAME, SUPPORTED  # noqa: E402

VERSION = version.VERSION
APP_NAME = version.APP_NAME
EXE_NAME = "Battery4Loader"
BUILD = ROOT / "build" / "release"
OUT = ROOT / "release"
SPEC = ROOT / "tools" / "release.spec"
APP = BUILD / "dist" / f"{APP_NAME}.app"
WORK = BUILD / "work"
STAGE = BUILD / "stage"
# コメントと説明文を除いた写しの置き場所（tools/release.spec が作る）
CLEAN = BUILD / "clean"
# 空白の無い英字にする（GitHub は添付の名前の空白を「.」に置き換える）
FOLDER = f"PTBattery4Loader_{VERSION}"
ZIP = OUT / f"{FOLDER}.zip"
DOCS = ROOT / "docs"

# 組み立てに使う python（上の説明）
BASE_PYTHON = Path(
    "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14")

# 配る文書（配布物の中の名前 → 元のファイル）。テストと公開の道具もこれを読む
DOC_FILES = {
    "はじめにお読みください.txt": DOCS / "readme.txt",
    "マニュアル.html": DOCS / "manual.html",
    "マニュアル.txt": DOCS / "manual.txt",
    "ライセンス.txt": ROOT / "LICENSE.txt",
    "README (English).txt": DOCS / "readme_en.txt",
    "Manual (English).html": DOCS / "manual_en.html",
    "Manual (English).txt": DOCS / "manual_en.txt",
}
THIRD_PARTY = "同梱部品のライセンス.txt"
# 配布物の直下に並べるもの（はじめにお読みくださいの説明と合わせる。ずれたら止める）
TOP_FILES = (*DOC_FILES, THIRD_PARTY)
TOP_APPS = (f"{APP_NAME}.app",)

# 自分たちのモジュール（コメントと説明文を除いた写しから組み立て、コンパイルしたものを調べる）
OWN_MODULES = ("app_main", "app_kit", "app_settings", "core", "name_readout",
               "grid_calibration", "ui_common", "version", "i18n")

# 検査の検索語（個人の情報・関係の無い名前）は**ソースに書かない**（検索語そのものが
# 個人の情報になる）。作者の手元の release_private.json にだけ置く。無ければ、
# その検査はしない（公開しているソースから組み立てるとき）
PRIVATE_FILE = ROOT / "release_private.json"
_WORD_LISTS = ("personal", "personal_text", "words")


def read_private(path: Path) -> dict:
    """検索語を読む。ファイルが無ければ空（公開しているソースから組み立てるとき）。

    **壊れているときは止める。** 読めないまま空にすると、検査が全部黙って素通りして
    「確かめた」と表示される。語は小文字にそろえる（どの検査も大文字小文字を問わない）。
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise SystemExit(f"[エラー] {path.name} を読めません（{exc}）") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"[エラー] {path.name} の形が違います")
    for key in _WORD_LISTS:
        words = data.get(key)
        if not (words and isinstance(words, list)
                and all(w and isinstance(w, str) for w in words)):
            raise SystemExit(f"[エラー] {path.name} の {key} が語の並びではありません")
        data[key] = tuple(w.lower() for w in words)
    return data


_PRIVATE = read_private(PRIVATE_FILE)
# 配布物に入っていてはいけないもの（作者の名前など）。**全部のファイルで探す**
PERSONAL = _PRIVATE.get("personal", ())
# 自分たちで書いた文字のファイルでだけ探すもの（メールアドレスの印と手元のパス）。
# python 本体や同梱した部品の文書には、関係のない人のアドレスの例が入っている
PERSONAL_TEXT = _PRIVATE.get("personal_text", ())
# 自分たちで書いた文字のファイルと、コンパイルしたコードに入っていてはいけない言葉
# （ほかのプロジェクトの名前・手元のメモ）
PRIVATE_WORDS = _PRIVATE.get("words", ())
TEXT_SUFFIXES = (".py", ".txt", ".html", ".md", ".plist", ".strings", ".sh", ".json")

RESULTS: list[tuple[str, bool, str]] = []


def find_words(text: str, words) -> list[str]:
    """文字列の中にある検索語（大文字小文字を問わない。語は小文字にしてある）。"""
    lowered = text.lower()
    return [word for word in words if word in lowered]


def clean_source(text: str) -> str:
    """同梱する .py から、**コメントと説明文（docstring）を除く**（動きは変えない）。

    コンパイルしたコードにも説明文は文字列として残る。開発の経過を配布物に出さない。
    ast で読んで書き直すとコメントは消える。説明文を消すと中身が空になるところは
    pass にする。
    """
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body.pop(0)
                if not body and not isinstance(node, ast.Module):
                    body.append(ast.Pass())
    return ast.unparse(tree) + "\n"


def step(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    tail = f" — {detail}" if detail else ""
    print(f"{'OK' if ok else 'NG'}  {name}{tail}", flush=True)
    return ok


def run(args: list, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([str(a) for a in args], capture_output=True,
                          text=True, **kw)


def last_line(done: subprocess.CompletedProcess) -> str:
    for text in (done.stderr, done.stdout):
        lines = (text or "").strip().splitlines()
        if lines:
            return lines[-1]
    return "出力なし"


def quiet_env() -> dict:
    """音も読み上げも出さず、ホームを一時フォルダへ向ける（利用者の設定に触らない）。"""
    return dict(os.environ, HOME=tempfile.mkdtemp(prefix="b4l_release_home_"),
                BATTERY4_SILENT="1", PYTHONDONTWRITEBYTECODE="1")


# --------------------------------------------------------------------------
# 1〜3. 確かめる
# --------------------------------------------------------------------------

def git_lines(*args: str) -> str:
    return run(["git", *args], cwd=ROOT).stdout.strip()


def git_commit() -> str:
    commit = git_lines("rev-parse", "--short", "HEAD") or "?"
    dirty = git_lines("status", "--porcelain")
    return commit + ("+" if dirty else "")


def check_clean(allow_dirty: bool) -> bool:
    name = "1. 書きかけの変更"
    dirty = [line for line in git_lines("status", "--porcelain").splitlines()
             if line.strip()]
    if dirty and not allow_dirty:
        return step(name, False,
                    f"{len(dirty)} 件（コミットしてから作る。"
                    f"試すなら --dirty）: {dirty[0]}")
    detail = "なし" if not dirty else f"{len(dirty)} 件あるが --dirty で続ける"
    return step(name, True, detail)


def run_tests() -> bool:
    done = run([BASE_PYTHON, ROOT / "tests" / "run_all.py"], cwd=ROOT,
               env=quiet_env(), timeout=900)
    summary = next((line.strip() for line in done.stdout.splitlines()
                    if "合計" in line), last_line(done))
    return step("2. テスト", done.returncode == 0, summary)


def check_docs() -> bool:
    name = "3. マニュアル"
    parts = run([BASE_PYTHON, ROOT / "tools" / "doc_parts.py", "--check"],
                cwd=ROOT, env=quiet_env(), timeout=300)
    if parts.returncode != 0:
        return step(name, False, "コードから作る部分が古い。"
                    "tools/doc_parts.py で作り直してコミットする")
    html = run([BASE_PYTHON, "-X", "utf8", ROOT / "tools" / "check_html.py",
                DOCS / "manual.html", DOCS / "manual_en.html"], cwd=ROOT)
    if html.returncode != 0:
        return step(name, False, "check_html: " + last_line(html))
    return step(name, True, "コードと合っている。構造の検査も通過")


# --------------------------------------------------------------------------
# 4〜5. 組み立てて確かめる
# --------------------------------------------------------------------------

def build_app() -> bool:
    name = "4. .app の組み立て"
    if not BASE_PYTHON.is_file():
        return step(name, False, f"python.org の python3.14 がありません: {BASE_PYTHON}")
    if BUILD.exists():
        shutil.rmtree(BUILD)
    done = run([BASE_PYTHON, "-m", "PyInstaller", "--noconfirm", "--clean",
                "--log-level", "WARN", "--distpath", BUILD / "dist",
                "--workpath", WORK, SPEC], cwd=ROOT,
               env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), timeout=900)
    if done.returncode != 0 or not APP.is_dir():
        return step(name, False, last_line(done))
    size = sum(p.stat().st_size for p in APP.rglob("*")
               if p.is_file() and not p.is_symlink())
    return step(name, True, f"{size / 1e6:.0f} MB")


def selftest(app: Path, lang: str = "ja") -> tuple[bool, str]:
    exe = app / "Contents" / "MacOS" / EXE_NAME
    try:
        done = run([exe, "--selftest"], timeout=180,
                   env=dict(quiet_env(), **{ENV_NAME: lang}))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    try:
        report = json.loads((done.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return False, last_line(done)
    if (done.returncode != 0 or not report.get("ok")
            or not report.get("frozen")
            or report.get("version") != VERSION):
        return False, json.dumps(report, ensure_ascii=False)
    return True, (f"版 {report['version']}、メニュー {len(report['menus'])} 個、"
                  f"python {report['python']}")


def smoke_launch(app: Path, lang: str = "ja") -> tuple[bool, str]:
    """本番の起動の道筋を通して、数秒で終わるか（BATTERY4_SMOKE=1）。

    窓は出さず、許可を求める知らせも出さない。
    ホームは一時フォルダ。そこに設定と記録ができていれば通った証拠。
    """
    exe = app / "Contents" / "MacOS" / EXE_NAME
    home = Path(tempfile.mkdtemp(prefix="b4l_smoke_home_"))
    env = dict(os.environ, HOME=str(home), BATTERY4_SILENT="1",
               BATTERY4_SMOKE="1", PYTHONDONTWRITEBYTECODE="1", **{ENV_NAME: lang})
    try:
        done = run([exe], env=env, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if done.returncode != 0:
        return False, f"終了コード {done.returncode}: {last_line(done)}"
    if not (home / ".battery4_loader_settings.json").is_file():
        return False, "設定を書いていない（起動の処理を通っていない）"
    if not (home / "Library" / "Logs" / "Battery4Loader.log").is_file():
        return False, "起動の記録が無い"
    return True, "起動の処理を通って終了"


def signature_problems(app: Path) -> list[str]:
    """署名が壊れていないか、ad-hoc か（証明書の名前が入っていないか）。"""
    problems = []
    verify = run(["codesign", "--verify", "--deep", "--strict", app])
    if verify.returncode != 0:
        problems.append("署名の検証: " + last_line(verify))
    info = run(["codesign", "-dvv", app]).stderr
    if "Signature=adhoc" not in info:
        problems.append("ad-hoc の署名ではない")
    if "Authority=" in info:
        problems.append("証明書の名前が入っている")
    return problems


def stamp(app: Path) -> str:
    """Info.plist にビルド日時とコミットを書き、ad-hoc で署名し直す。"""
    plist_path = app / "Contents" / "Info.plist"
    with open(plist_path, "rb") as f:
        info = plistlib.load(f)
    info[version.KEY_BUILD_DATE] = time.strftime("%Y-%m-%d %H:%M")
    info[version.KEY_COMMIT] = git_commit()
    with open(plist_path, "wb") as f:
        plistlib.dump(info, f)
    signed = run(["codesign", "--force", "--deep", "--sign", "-", app])
    if signed.returncode != 0:
        return "署名し直せません: " + last_line(signed)
    return ""


def check_app() -> bool:
    name = "5. .app の確認"
    for lang in SUPPORTED:
        ok, detail = selftest(APP, lang)
        if not ok:
            return step(name, False, f"--selftest（{lang}）: {detail}")
        ok, smoke = smoke_launch(APP, lang)
        if not ok:
            return step(name, False, f"起動（{lang}）: {smoke}")
    if not all((APP / "Contents" / "Resources" / f"{lang}.lproj" / "InfoPlist.strings").is_file()
               for lang in SUPPORTED):
        return step(name, False, "許可の説明の訳（lproj）が無い")
    problems = signature_problems(APP)
    if problems:
        return step(name, False, "、".join(problems))
    hits, checked = frozen_hits(APP)
    if hits:
        return step(name, False, f"コンパイルしたコードに関係の無い名前や個人の情報: {'、'.join(hits[:5])}")
    # 確認を通したものにだけ、ビルドの日時とコミットを刻む
    failed = stamp(APP)
    if failed:
        return step(name, False, failed)
    ok, detail = selftest(APP)
    problems = signature_problems(APP)
    if not ok or problems:
        return step(name, False, f"刻印のあと: {detail}、{'、'.join(problems)}")
    scanned = (f"コンパイルしたコード {checked} 個に関係の無い名前は無い" if PERSONAL
               else "検索語が無いので、関係の無い名前は調べていない")
    return step(name, True, f"{detail}。{smoke}。日本語と英語で起動。署名は ad-hoc。"
                f"{scanned}。刻印 {git_commit()}")


# --------------------------------------------------------------------------
# 6. 同梱部品のライセンス（実際に入ったファイルから）
# --------------------------------------------------------------------------

# 組み立てに使う python の中で動かす（そちらの部品の RECORD を読むため）。
# 結果は JSON で受け取る
_LICENSE_PROBE = r'''
import ast, importlib.metadata as metadata, json, re, sys
from pathlib import Path

work, own = Path(sys.argv[1]), [Path(p).resolve() for p in sys.argv[2:]]
DOC_SUFFIXES = ("", ".txt", ".md", ".rst")
LICENSE_NAME = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", re.I)

found = set()
def walk(node):
    if isinstance(node, (list, tuple)):
        if len(node) == 3 and all(isinstance(x, str) for x in node):
            p = Path(node[1])
            if p.is_absolute() and p.is_file():
                found.add(p.resolve())
            return
        for child in node:
            walk(child)
for toc in sorted(work.glob("*.toc")):
    try:
        walk(ast.literal_eval(toc.read_text(encoding="utf-8")))
    except (ValueError, SyntaxError):
        pass

owner = {}
for dist in metadata.distributions():
    for rel in dist.files or []:
        try:
            owner[Path(dist.locate_file(rel)).resolve()] = dist
        except (OSError, ValueError):
            pass

def texts(dist):
    out = []
    for rel in dist.files or []:
        parts = Path(str(rel)).parts
        if not parts or not parts[0].endswith(".dist-info"):
            continue
        name = parts[-1]
        if (LICENSE_NAME.match(name)
                and Path(name).suffix.lower() in DOC_SUFFIXES):
            try:
                t = Path(dist.locate_file(rel)).read_text(
                    encoding="utf-8", errors="replace")
            except OSError:
                continue
            out.append([str(rel), t.replace("\f", "\n")])
    return out

def label(dist):
    meta = dist.metadata
    lab = meta.get("License-Expression") or ""
    if not lab:
        t = (meta.get("License") or "").strip()
        lab = t if t and len(t) < 120 else ""
    if not lab:
        lab = "、".join(c.split("::")[-1].strip()
                       for c in meta.get_all("Classifier") or []
                       if c.startswith("License ::"))
    return lab or "（部品の文書を参照）"

def project(dist):
    meta = dist.metadata
    urls = [meta.get("Home-page") or ""] + [
        u.split(",", 1)[-1].strip() for u in meta.get_all("Project-URL") or []]
    return next((u.rstrip("/").lower() for u in urls if "github.com" in u),
                (urls[0] or "").rstrip("/").lower())

base = Path(sys.base_prefix).resolve()
site = [Path(p).resolve() for p in sys.path if p.endswith("site-packages")]
import PyInstaller
pyi = Path(PyInstaller.__file__).resolve().parent
used, unknown, python_files = {}, [], 0
for path in sorted(found):
    # 部品の持ち主を先に見る（自分たちのフォルダの判定を先にすると、
    # そこに置いた部品が一覧から黙って落ちる）
    dist = owner.get(path)
    if dist is not None:
        used[dist.metadata["Name"]] = dist
        continue
    if any(path.is_relative_to(r) for r in own):
        continue
    if path.is_relative_to(pyi):
        used["pyinstaller"] = metadata.distribution("pyinstaller")
        continue
    if (path.is_relative_to(base)
            and not any(path.is_relative_to(s) for s in site)):
        python_files += 1
        continue
    unknown.append(str(path))
used.setdefault("pyinstaller", metadata.distribution("pyinstaller"))
version = f"python{sys.version_info.major}.{sys.version_info.minor}"
lic = base / "lib" / version / "LICENSE.txt"
print(json.dumps({
    "unknown": unknown,
    "python_files": python_files,
    "python_version": sys.version.split()[0],
    "python_license": lic.read_text(encoding="utf-8", errors="replace"),
    "parts": [{"name": n, "version": used[n].version,
               "label": label(used[n]), "project": project(used[n]),
               "texts": texts(used[n])}
              for n in sorted(used, key=str.lower)],
}, ensure_ascii=False))
'''

_PYINSTALLER_NOTE = [
    "PyInstaller のブートローダー（.app の中の実行ファイル）と、"
    "起動時に使う部品が入っています。",
    "PyInstaller の条件は、PyInstaller でまとめたプログラムを、"
    "どのような条件でも配ってよいとしています"
    "（下の文書の「Bootloader Exception」）。",
    "",
]


def _license_head(data: dict) -> list[str]:
    parts = data["parts"]
    lines = [
        f"{APP_NAME} {VERSION}: third-party licenses / 同梱している部品のライセンス",
        "",
        "This .app contains the parts listed below (the list was made from the files",
        "actually packaged). The full terms of each part follow, part by part.",
        "",
        "この .app には、次の部品が入っています"
        "（PyInstaller でまとめたとき、実際に入ったファイルから作った一覧です）。",
        f"部品は {len(parts) + 1} 個です。"
        "それぞれの条件の全文を、この下に部品ごとに載せます。",
        "",
        f"・Python {data['python_version']}（Python Software Foundation "
        "License。標準ライブラリと、Python に含まれる部品）",
    ]
    lines += [f"・{p['name']} {p['version']}（{p['label']}）" for p in parts]
    lines += [
        "",
        "このアプリ自身（作者 Takahashi）のライセンスは「ライセンス.txt」に"
        "あります。",
        "",
        "=" * 70, f"Python {data['python_version']}", "=" * 70, "",
        data["python_license"].strip(), "",
    ]
    return lines


def collect_licenses() -> tuple[bool, str, str]:
    """(成否, 文書, 詳細)。

    **どの部品にも当たらないファイルがあれば止める**（黙って一覧から落とさない）。
    """
    done = run([BASE_PYTHON, "-c", _LICENSE_PROBE, WORK / SPEC.stem, ROOT])
    if done.returncode != 0:
        return False, "", "目録を読めない: " + last_line(done)
    data = json.loads(done.stdout.strip().splitlines()[-1])
    if data["unknown"]:
        return False, "", (f"持ち主の分からないファイルが "
                           f"{len(data['unknown'])} 件: {data['unknown'][0]}")
    parts = data["parts"]
    if not parts:
        return False, "", "PyInstaller の目録が読めない"
    lines = _license_head(data)
    missing, borrowed = [], []
    for part in parts:
        texts = part["texts"]
        title = f"{part['name']} {part['version']}（{part['label']}）"
        lines += ["=" * 70, title, "=" * 70, ""]
        if part["name"].lower() == "pyinstaller":
            lines += _PYINSTALLER_NOTE
        if not texts:
            # 部品の配布物に文書が無いとき: 同じプロジェクトの、同じ条件の
            # 部品の文書を載せる（MIT は全文を添える条件のため）
            sibling = next(
                (p for p in parts if p is not part
                 and p["project"] == part["project"]
                 and p["label"] == part["label"] and p["texts"]), None)
            if sibling is None:
                missing.append(part["name"])
                lines += [
                    "この部品の配布物にはライセンスの文書が入っていません。"
                    f"条件は「{part['label']}」です。",
                    f"部品の案内: {part['project']}", ""]
            else:
                borrowed.append(part["name"])
                texts = sibling["texts"]
                lines += [
                    "この部品の配布物にはライセンスの文書が入っていないので、"
                    f"同じプロジェクト（{part['project']}）の同じ条件の部品 "
                    f"{sibling['name']} の文書を載せます。", ""]
        for rel, text in texts:
            lines += [f"--- {rel} ---", "", text.strip(), ""]
    names = "、".join(p["name"] for p in parts)
    detail = (f"部品 {len(parts) + 1} 個（Python と {names}。"
              f"Python の標準ライブラリなど {data['python_files']} ファイル）")
    if borrowed:
        detail += f"。同じプロジェクトの文書を載せたもの: {'、'.join(borrowed)}"
    if missing:
        return False, "", (detail + "。ライセンスの文書が見つからないもの: "
                           + "、".join(missing))
    return True, "\n".join(lines) + "\n", detail


# --------------------------------------------------------------------------
# 7〜9. 並べて zip にし、確かめる
# --------------------------------------------------------------------------

def doc_text(src: Path) -> str:
    """配る文書の中身（{VERSION} を埋める）。"""
    text = src.read_text(encoding="utf-8-sig")
    return text.replace("{VERSION}", VERSION) if src.name.startswith("readme") else text


def write_txt(path: Path, text: str) -> None:
    """配る txt は BOM 付き UTF-8・CRLF（古い日本語のエディタで化けない）。"""
    path.write_text(text.replace("\r\n", "\n"), encoding="utf-8-sig",
                    newline="\r\n")


def stage_files(third_party: str) -> bool:
    name = "7. 並べる"
    if STAGE.exists():
        shutil.rmtree(STAGE)
    folder = STAGE / FOLDER
    folder.mkdir(parents=True)
    copy = run(["ditto", APP, folder / APP.name])
    if copy.returncode != 0:
        return step(name, False, last_line(copy))
    for doc, src in DOC_FILES.items():
        if doc.endswith(".txt"):
            write_txt(folder / doc, doc_text(src))
        else:
            shutil.copy2(src, folder / doc)
    write_txt(folder / THIRD_PARTY, third_party)
    names = sorted(p.name for p in folder.iterdir())
    want = sorted(TOP_FILES + TOP_APPS)
    if names != want:
        return step(name, False, f"並びが説明と違う: {names}")
    return step(name, True,
                f"ファイル {len(TOP_FILES)} つとアプリ {len(TOP_APPS)} つ")


def make_zip() -> bool:
    OUT.mkdir(exist_ok=True)
    if ZIP.exists():
        ZIP.unlink()
    # --norsrc --noextattr: 拡張属性（隔離の印など）を __MACOSX に入れない。
    # 署名は .app の中の CodeResources にある
    done = run(["ditto", "-c", "-k", "--norsrc", "--noextattr",
                "--keepParent", STAGE / FOLDER, ZIP])
    if done.returncode != 0 or not ZIP.is_file():
        return step("8. zip", False, last_line(done))
    return step("8. zip", True,
                f"{ZIP.name}（{ZIP.stat().st_size / 1e6:.1f} MB）")


def _own_text(folder: Path, path: Path) -> bool:
    """自分たちで書いた文字のファイルか（メールアドレスの印と関係の無い名前も探す対象）。"""
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return False
    rel = path.relative_to(folder)
    if len(rel.parts) == 1:
        return rel.name != THIRD_PARTY
    return (rel.name == "Info.plist"
            or (rel.parts[1:3] == ("Contents", "Resources")
                and rel.parent.name.endswith(".lproj")))


def personal_hits(folder: Path) -> list[str]:
    hits = []
    for path in sorted(folder.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes().lower()
        words = PERSONAL + (PERSONAL_TEXT if _own_text(folder, path) else ())
        for word in words:
            if word.encode() in data:
                hits.append(f"{path.relative_to(folder)}（{word}）")
                break
    return hits


def private_hits(folder: Path) -> list[str]:
    """自分たちで書いた文字のファイルに、ほかのプロジェクトの名前・手元のメモが無いか。"""
    hits = []
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder).as_posix()
        named = find_words(rel, PRIVATE_WORDS + PERSONAL)  # 名前も見る
        if named:
            hits.append(f"{rel}（名前に {'・'.join(named)}）")
            continue
        if path.is_symlink() or not path.is_file() or not _own_text(folder, path):
            continue
        found = find_words(path.read_text(encoding="utf-8", errors="replace"),
                           PRIVATE_WORDS)
        if found:
            hits.append(f"{rel}（{'・'.join(found)}）")
    return hits


def _strings_in(value) -> list[str]:
    """コードオブジェクトの定数の中の文字。タプルと frozenset の中も見る。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, bytes):
        return [value.decode("utf-8", "replace")]
    if isinstance(value, (tuple, frozenset)):
        return [s for item in value for s in _strings_in(item)]
    return []


def _strings_of(code) -> list[str]:
    """コードオブジェクトの中の文字（説明文・定数・名前）。入れ子の関数やクラスの中まで。"""
    found: list[str] = []
    stack = [code]
    while stack:
        current = stack.pop()
        found += list(current.co_names) + list(current.co_varnames)
        for const in current.co_consts:
            if hasattr(const, "co_consts"):
                stack.append(const)
            else:
                found += _strings_in(const)
    return found


def frozen_hits(app: Path) -> tuple[list[str], int]:
    """固めた python の部品（実行ファイルの中の PYZ）の中の、自分たちのモジュールの文字を探す。

    **ファイルの中の文字を探すだけでは見つからない。** PYZ は zlib で縮めてあるので、
    説明文に手元のパスが残っていても、上の `personal_hits` は素通りする。
    戻り値は (当たったもの, 調べたモジュールの数)。別の python（PyInstaller の入ったもの）で動かす。
    """
    from PyInstaller.archive.readers import CArchiveReader

    exe = app / "Contents" / "MacOS" / EXE_NAME
    pkg = CArchiveReader(str(exe))
    pyz_name = next(n for n in pkg.toc if n.upper().startswith("PYZ"))
    pyz = pkg.open_embedded_archive(pyz_name)
    words = PERSONAL + PERSONAL_TEXT + PRIVATE_WORDS
    hits: list[str] = []
    checked = 0
    for name in [n for n in pyz.toc if n in OWN_MODULES]:
        try:
            code = pyz.extract(name)
        except Exception as exc:
            hits.append(f"{name}（読めない: {exc}）")
            continue
        if code is None:
            continue
        checked += 1
        hits += [f"{name}（{word}）"
                 for word in find_words("\n".join(_strings_of(code)), words)][:1]
    # 入口の app_main は PYZ ではなく、実行ファイルの中に別に入っている
    for name in pkg.toc:
        if name != "app_main":
            continue
        data = pkg.extract(name)
        try:
            text = "\n".join(_strings_of(marshal.loads(data)))
        except Exception:
            text = (data or b"").decode("utf-8", "replace")
        checked += 1
        hits += [f"app_main（{word}）" for word in find_words(text, words)][:1]
    return hits, checked


def txt_problems(folder: Path) -> list[str]:
    problems = []
    for name in TOP_FILES:
        if not name.endswith(".txt"):
            continue
        data = (folder / name).read_bytes()
        if not data.startswith(b"\xef\xbb\xbf"):
            problems.append(f"{name} に BOM が無い")
        if b"\n" in data.replace(b"\r\n", b""):
            problems.append(f"{name} に CRLF でない改行がある")
        if re.search(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", data):
            problems.append(f"{name} に制御文字がある")
    return problems


def _zip_layout() -> tuple[list[str], list[str]]:
    """(問題, UTF-8 の印が無い日本語の名前)。

    **ditto の zip は、日本語の名前に UTF-8 の印（0x800）を付けない。**
    名前は UTF-8 なので、そのとおりに読む（Mac の「アーカイブユーティリティ」も
    UTF-8 として読むので化けない）。
    """
    with zipfile.ZipFile(ZIP, metadata_encoding="utf-8") as zf:
        bad = zf.testzip()
        if bad is not None:
            return [f"壊れたファイル: {bad}"], []
        names = zf.namelist()
        tops = sorted({n.split("/", 2)[1] for n in names
                       if n.count("/") >= 1 and n.split("/", 2)[1]})
        roots = {n.split("/", 1)[0] for n in names}
        unflagged = [i.filename for i in zf.infolist()
                     if not i.filename.isascii()
                     and not i.flag_bits & 0x800]
    problems = []
    if roots != {FOLDER}:
        problems.append(f"最上位のフォルダが 1 つでない: {sorted(roots)}")
    if tops != sorted(TOP_FILES + TOP_APPS):
        problems.append(f"直下の並びが説明と違う: {tops}")
    junk = ("__MACOSX", ".DS_Store", "__pycache__")
    if any(j in n for n in names for j in junk):
        problems.append("__MACOSX・.DS_Store・__pycache__ のどれかが入っている")
    return problems, unflagged


def verify_zip() -> bool:
    name = "9. zip の検証"
    problems, unflagged = _zip_layout()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        done = run(["ditto", "-x", "-k", ZIP, out])
        if done.returncode != 0:
            return step(name, False, "展開できない: " + last_line(done))
        folder = out / FOLDER
        app = folder / f"{APP_NAME}.app"
        problems += signature_problems(app)
        for lang in SUPPORTED:
            ok, detail = selftest(app, lang)
            if not ok:
                problems.append(f"展開した .app の --selftest（{lang}）: {detail}")
            ok, smoke = smoke_launch(app, lang)
            if not ok:
                problems.append(f"展開した .app の起動（{lang}）: {smoke}")
        # --selftest のあとも署名が壊れていないこと（.app の中に書いていないこと）
        problems += [f"--selftest のあと: {p}"
                     for p in signature_problems(app)]
        problems += txt_problems(folder)
        hits = personal_hits(folder)
        if hits:
            problems.append(f"個人の情報が入っている: {'、'.join(hits[:5])}")
        hits = private_hits(folder)
        if hits:
            problems.append(f"関係の無い名前が入っている: {'、'.join(hits[:5])}")
        hits, checked = frozen_hits(app)
        if hits:
            problems.append("コンパイルしたコードに関係の無い名前や個人の情報がある: "
                            + "、".join(hits[:5]))
        same = run(["diff", "-rq", STAGE / FOLDER, folder])
        if same.returncode != 0:
            problems.append("展開したものが並べたものと違う: " + last_line(same))
    if problems:
        return step(name, False, "、".join(problems))
    note = ""
    if unflagged:
        note = (f"。日本語の名前に UTF-8 の印が無いもの {len(unflagged)} 件"
                "（Mac の展開では化けない）")
    digest = hashlib.sha256(ZIP.read_bytes()).hexdigest()
    words = (f"個人の情報と関係の無い名前（固めた部品の中の {checked} モジュールまで）"
             if PERSONAL else "（検索語が無いので、個人の情報と関係の無い名前は調べていない）")
    return step(name, True, f"展開して署名・--selftest・起動（日本語と英語）・並び・txt・"
                f"{words}を確かめた{note}。SHA-256 {digest[:16]}…")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="配布物（zip）を作る")
    ap.add_argument("--dirty", action="store_true",
                    help="書きかけの変更があっても止めない（試し）")
    ap.add_argument("--app-only", action="store_true",
                    help=".app の組み立てと確認だけ")
    args = ap.parse_args(argv)
    print(f"配布物: {FOLDER}（{ROOT}）", flush=True)
    print()
    if args.app_only:
        ok = build_app() and check_app()
        print()
        head = ".app を作りました" if ok else ".app を作れませんでした"
        print(f"{head}: {APP}")
        return 0 if ok else 1
    ok = check_clean(args.dirty) and run_tests() and check_docs()
    ok = ok and build_app() and check_app()
    third_party = ""
    if ok:
        good, third_party, detail = collect_licenses()
        ok = step("6. 同梱部品のライセンス", good, detail)
    ok = ok and stage_files(third_party) and make_zip() and verify_zip()
    print()
    if not ok:
        failed = [name for name, good, _ in RESULTS if not good]
        print(f"配布物を作れませんでした。通らなかった工程: {'、'.join(failed)}")
        if ZIP.exists() and failed:
            ZIP.unlink()          # 検証に通らなかった zip は残さない
        return 1
    print(f"配布物ができました: {ZIP}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
