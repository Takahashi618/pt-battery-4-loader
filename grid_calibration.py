"""
grid_calibration.py
===================
Battery 4 のセルグリッドの位置を、プラグイン描画領域からの「比率」で扱う
ドメインモジュール。

tkinter・AppKit・Quartz など環境依存物に依存しない純粋関数のみ。

セル名は Battery 4 の実表記に合わせる（2026-08-07 実機確認）:
  行 = A, B, C, D ...（上→下）
  列 = 1, 2, 3 ...  （左→右）
  A1 の右隣は A2、A1 の真下は B1。
  既定キットは 12列 × 4行 ＝ 48セル。

    A1 A2 A3 ... A12    ← row 0
    B1 B2 B3 ... B12    ← row 1
    C1 C2 C3 ... C12    ← row 2
    D1 D2 D3 ... D12    ← row 3

なぜ比率か:
  絶対座標で持つと、ウィンドウを動かす・Battery のサイズを変える・設定が
  初期化される、のいずれでも壊れる。プラグインの描画領域（AX から取れる
  FXTDMEditView の矩形）を基準にした比率で持てば、

    - ウィンドウが動いても追随する
    - 複数の Battery インスタンスに同じ1組の値を使い回せる
    - 初期値を組み込めば、多くの場合キャリブレーション自体が不要になる

  という利点がある。座標系は画面左上原点（CGEvent と同じ）。
"""
from __future__ import annotations

from typing import Dict, Tuple

from i18n import plural, tr

# 行に使う文字（Battery 4 の行ラベル）
ROW_LETTERS = "ABCDEFGH"

DEFAULT_ROWS = 4
DEFAULT_COLS = 12
MAX_ROWS = len(ROW_LETTERS)
MAX_COLS = 16

Coord = Tuple[int, int]
Ratio = Tuple[float, float]
CoordMap = Dict[str, Coord]
# 描画領域の矩形（左上原点）: (x, y, width, height)
ViewFrame = Tuple[float, float, float, float]

# 実機実測値（Pro Tools 2026.4.1 / Battery 4.3.1 / 描画領域 1176x650）
#   A1 セル中心 = 画面 (386, 213)、描画領域原点 (20, 128)
#   列間隔 70px、行間隔 56px
DEFAULT_LAYOUT: dict = {
    "rows": DEFAULT_ROWS,
    "cols": DEFAULT_COLS,
    "x0": 366 / 1176,    # 0.3112 左上セル A1 の中心（横）
    "y0": 85 / 650,      # 0.1308 左上セル A1 の中心（縦）
    "col_step": 70 / 1176,   # 0.0595
    "row_step": 56 / 650,    # 0.0862
}

# 旧12セル（列=A〜D・行=1〜3 という誤った前提）から新表記への対応。
# 画面上の物理位置を保つように変換する。
LEGACY_CELL_NAMES = [
    "A1", "B1", "C1", "D1",
    "A2", "B2", "C2", "D2",
    "A3", "B3", "C3", "D3",
]


def cell_names(rows: int = DEFAULT_ROWS, cols: int = DEFAULT_COLS) -> list[str]:
    """行×列のセル名リストを返す（A1, A2, ... A12, B1, ...）。"""
    if not (1 <= rows <= MAX_ROWS):
        raise ValueError(tr(f"行数が範囲外です: {rows}", f"Number of rows is out of range: {rows}"))
    if not (1 <= cols <= MAX_COLS):
        raise ValueError(tr(f"列数が範囲外です: {cols}",
                            f"Number of columns is out of range: {cols}"))
    return [
        f"{ROW_LETTERS[r]}{c + 1}"
        for r in range(rows)
        for c in range(cols)
    ]


def cell_to_rowcol(cell: str) -> Tuple[int, int]:
    """セル名を (row, col) の0始まりインデックスに変換する。

    例) "A1" -> (0, 0)、"A12" -> (0, 11)、"D3" -> (3, 2)
    """
    if not cell or cell[0] not in ROW_LETTERS:
        raise ValueError(tr(f"不正なセル名: {cell!r}", f"Invalid cell name: {cell!r}"))
    digits = cell[1:]
    if not digits.isdigit():
        raise ValueError(tr(f"不正なセル名: {cell!r}", f"Invalid cell name: {cell!r}"))
    col = int(digits) - 1
    if col < 0:
        raise ValueError(tr(f"不正なセル名: {cell!r}", f"Invalid cell name: {cell!r}"))
    return ROW_LETTERS.index(cell[0]), col


def rowcol_to_cell(row: int, col: int) -> str:
    """(row, col) をセル名に変換する。例) (1, 2) -> "B3"。"""
    if not (0 <= row < MAX_ROWS) or col < 0:
        raise ValueError(tr(f"不正な行列: ({row}, {col})",
                            f"Invalid row and column: ({row}, {col})"))
    return f"{ROW_LETTERS[row]}{col + 1}"


def migrate_legacy_cell(cell: str) -> str:
    """旧セル名を新表記へ変換する（画面上の位置を保つ）。

    旧は「列=A〜D、行=1〜3」だったため、A1→A1、B1→A2、A2→B1 のように
    行と列が入れ替わる。旧名でないものはそのまま返す。
    """
    if cell not in LEGACY_CELL_NAMES:
        return cell
    idx = LEGACY_CELL_NAMES.index(cell)
    return rowcol_to_cell(idx // 4, idx % 4)


def migrate_legacy_map(mapping: dict) -> dict:
    """旧セル名をキーに持つ辞書を新表記のキーに移し替える。"""
    return {migrate_legacy_cell(k): v for k, v in mapping.items()}


# ---------------------------------------------------------------------------
# 比率レイアウト
# ---------------------------------------------------------------------------
def normalize_layout(layout: dict | None) -> dict:
    """欠けている項目をデフォルトで補ったレイアウトを返す。"""
    base = dict(DEFAULT_LAYOUT)
    if layout:
        for k in base:
            if k in layout and layout[k] is not None:
                base[k] = layout[k]
    base["rows"] = int(base["rows"])
    base["cols"] = int(base["cols"])
    for k in ("x0", "y0", "col_step", "row_step"):
        base[k] = float(base[k])
    return base


def layout_cell_names(layout: dict | None) -> list[str]:
    """レイアウトの行数・列数に対応するセル名リスト。"""
    lay = normalize_layout(layout)
    return cell_names(lay["rows"], lay["cols"])


def cell_ratio(layout: dict | None, cell: str) -> Ratio:
    """セル中心の比率 (rx, ry) を返す。"""
    lay = normalize_layout(layout)
    row, col = cell_to_rowcol(cell)
    return (
        lay["x0"] + col * lay["col_step"],
        lay["y0"] + row * lay["row_step"],
    )


def ratio_to_point(ratio: Ratio, view: ViewFrame) -> Coord:
    """比率を描画領域の矩形に当てはめて画面座標にする。"""
    vx, vy, vw, vh = view
    return (round(vx + ratio[0] * vw), round(vy + ratio[1] * vh))


def point_to_ratio(point: Coord, view: ViewFrame) -> Ratio:
    """画面座標を描画領域からの比率にする。"""
    vx, vy, vw, vh = view
    if vw <= 0 or vh <= 0:
        raise ValueError(tr(f"描画領域の大きさが不正です: {view}",
                            f"Invalid size of the drawing area: {view}"))
    return ((point[0] - vx) / vw, (point[1] - vy) / vh)


def cell_point(layout: dict | None, cell: str, view: ViewFrame) -> Coord:
    """セルの画面座標を返す（比率 → 実座標）。"""
    return ratio_to_point(cell_ratio(layout, cell), view)


def layout_to_coords(layout: dict | None, view: ViewFrame) -> CoordMap:
    """全セルの画面座標を返す。"""
    lay = normalize_layout(layout)
    return {c: cell_point(lay, c, view) for c in layout_cell_names(lay)}


def layout_from_two_cells(
    first_cell: str,
    first_ratio: Ratio,
    last_cell: str,
    last_ratio: Ratio,
    rows: int = DEFAULT_ROWS,
    cols: int = DEFAULT_COLS,
) -> dict:
    """2つのセルの比率から、レイアウト（原点と間隔）を逆算する。

    Args:
        first_cell: 基準にする1つ目のセル名（通常 "A1"）
        first_ratio: そのセル中心の比率
        last_cell: 2つ目のセル名（通常 右下のセル）
        last_ratio: そのセル中心の比率
        rows, cols: グリッドの行数・列数

    2点が同じ行（または同じ列）にある場合、その方向の間隔は求められないため
    デフォルト値を使う。
    """
    r1, c1 = cell_to_rowcol(first_cell)
    r2, c2 = cell_to_rowcol(last_cell)
    col_step = (
        (last_ratio[0] - first_ratio[0]) / (c2 - c1)
        if c2 != c1 else DEFAULT_LAYOUT["col_step"]
    )
    row_step = (
        (last_ratio[1] - first_ratio[1]) / (r2 - r1)
        if r2 != r1 else DEFAULT_LAYOUT["row_step"]
    )
    return {
        "rows": int(rows),
        "cols": int(cols),
        "x0": first_ratio[0] - c1 * col_step,
        "y0": first_ratio[1] - r1 * row_step,
        "col_step": col_step,
        "row_step": row_step,
    }


def validate_layout(layout: dict | None) -> Tuple[bool, str]:
    """レイアウトが妥当か検査する。

    - 行数・列数が範囲内か
    - 間隔が正か（左→右・上→下に並ぶか）
    - 全セルが描画領域（比率 0〜1）に収まるか
    """
    try:
        lay = normalize_layout(layout)
    except Exception as e:
        return False, tr(f"レイアウトを解釈できません（{e}）", f"Cannot read the layout ({e})")

    if not (1 <= lay["rows"] <= MAX_ROWS):
        return False, tr(f"行数が範囲外です（{lay['rows']}）",
                         f"Number of rows is out of range ({lay['rows']})")
    if not (1 <= lay["cols"] <= MAX_COLS):
        return False, tr(f"列数が範囲外です（{lay['cols']}）",
                         f"Number of columns is out of range ({lay['cols']})")
    if lay["cols"] > 1 and lay["col_step"] <= 0:
        return False, tr("列の向きが逆です（右へ進んでいません）",
                         "Columns run the wrong way (they do not go to the right)")
    if lay["rows"] > 1 and lay["row_step"] <= 0:
        return False, tr("行の向きが逆です（下へ進んでいません）",
                         "Rows run the wrong way (they do not go down)")

    names = layout_cell_names(lay)
    for cell in (names[0], names[-1]):
        rx, ry = cell_ratio(lay, cell)
        if not (0.0 <= rx <= 1.0) or not (0.0 <= ry <= 1.0):
            return False, tr(f"セル {cell} が Battery の表示範囲の外にあります",
                             f"Cell {cell} is outside the Battery display area")
    return True, tr("レイアウトは妥当です", "The layout is valid")


def describe_layout(layout: dict | None, view: ViewFrame | None = None) -> str:
    """スクリーンリーダー向けの確認用テキスト。

    view を渡すと実際の画面座標も併せて説明する。
    """
    lay = normalize_layout(layout)
    names = layout_cell_names(lay)
    first, last = names[0], names[-1]
    lines = [
        tr(f"グリッドは {lay['rows']} 行 かける {lay['cols']} 列、"
           f"合わせて {len(names)} セル",
           f"The grid is {plural(lay['rows'], 'row')} by "
           f"{plural(lay['cols'], 'column')}, {plural(len(names), 'cell')} in all"),
        tr(f"左上は {first}、右下は {last}", f"Top left is {first}, bottom right is {last}"),
    ]
    if view:
        fx, fy = cell_point(lay, first, view)
        lx, ly = cell_point(lay, last, view)
        lines.append(tr(f"{first} は X {fx}、Y {fy}", f"{first} is at X {fx}, Y {fy}"))
        lines.append(tr(f"{last} は X {lx}、Y {ly}", f"{last} is at X {lx}, Y {ly}"))
        col_px = round(lay["col_step"] * view[2])
        row_px = round(lay["row_step"] * view[3])
        lines.append(tr(f"列の間隔はおよそ {col_px} ピクセル",
                        f"Columns are about {col_px} pixels apart"))
        lines.append(tr(f"行の間隔はおよそ {row_px} ピクセル", f"Rows are about {row_px} pixels apart"))
    else:
        lines.append(tr(f"列の間隔は幅の {lay['col_step'] * 100:.1f} パーセント",
                        f"Columns are {lay['col_step'] * 100:.1f} percent of the "
                        "width apart"))
        lines.append(tr(f"行の間隔は高さの {lay['row_step'] * 100:.1f} パーセント",
                        f"Rows are {lay['row_step'] * 100:.1f} percent of the height "
                        "apart"))
    return tr("。", ". ").join(lines)


def describe_cell(cell: str) -> str:
    """セル名を読み上げやすい説明にする。例) "B3" -> "B行 3列"。"""
    row, col = cell_to_rowcol(cell)
    return tr(f"{ROW_LETTERS[row]}行 {col + 1}列",
              f"row {ROW_LETTERS[row]}, column {col + 1}")
