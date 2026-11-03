"""部品の文字が枠に収まっているかを調べる（tests/test_cocoa_smoke.py と tests/en_probe.py が使う）。

部品の幅は座標で決めてあるので、文言を変えたり英語にしたりすると、文字が切れても
気づかない（英語の Load target cell が Load target に、Remove Selected が Remove に
なっていた。2026-11-03 のレビュー）。読み上げには影響しないが、画面で見る人と、
手伝う人が困る。
"""
from Cocoa import (NSButton, NSFontAttributeName, NSMakeRect, NSPopUpButton,
                   NSTextField)
from Foundation import NSString

# ポップアップの枠のうち、文字に使えない幅（左の余白と右の矢印）。
# cellSize は いちばん長い項目に広い余白を足した値で、実際より大きく出る
POPUP_MARGIN = 30
# この高さを超える入力不可の欄は、複数行の欄として高さを調べる
ONE_LINE_HEIGHT = 30


def _subviews(view):
    for child in view.subviews() or []:
        yield child
        yield from _subviews(child)


def _text_width(text, font):
    return NSString.stringWithString_(text).sizeWithAttributes_(
        {NSFontAttributeName: font}).width


def clipped_texts(window) -> list[str]:
    """枠に収まっていない文字の一覧（「文字: 要る幅 > ある幅」の形）。

    入力欄（利用者が打つ所）は調べない。
    """
    problems = []
    for view in _subviews(window.contentView()):
        frame = view.frame()
        if isinstance(view, NSPopUpButton):
            room = frame.size.width - POPUP_MARGIN
            for title in view.itemTitles():
                need = _text_width(title, view.font())
                if need > room + 0.5:
                    problems.append(f"{title}: {need:.0f} > {room:.0f}")
        elif isinstance(view, NSButton):
            need = view.cell().cellSize().width
            if need > frame.size.width + 0.5:
                problems.append(f"{view.title()}: {need:.0f} > {frame.size.width:.0f}")
        elif isinstance(view, NSTextField) and not view.isEditable():
            text = str(view.stringValue() or "")
            if not text:
                continue
            if frame.size.height > ONE_LINE_HEIGHT:
                bounds = NSMakeRect(0, 0, frame.size.width, 10000)
                need = view.cell().cellSizeForBounds_(bounds).height
                if need > frame.size.height + 0.5:
                    problems.append(f"{text[:40]}: 高さ {need:.0f} > {frame.size.height:.0f}")
            else:
                need = view.cell().cellSize().width
                if need > frame.size.width + 0.5:
                    problems.append(f"{text}: {need:.0f} > {frame.size.width:.0f}")
    return problems
