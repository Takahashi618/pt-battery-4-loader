"""
core.py（UI非依存コアロジック）のユニットテスト。

- ソート・フィルター・リスト表示ラベルの純粋関数
- 設定・キットの保存/読み込み（一時ファイルで roundtrip）
- VoiceOver 表記ルール（禁止記号を UI 文字列に使わない）のソース検査
- 「存在しない AppleScript drag 命令」再発防止のソース検査
"""
import os
import sys
import json
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# テストは日本語の文を期待する（英語は test_i18n が別のプロセスで確かめる）。
# 言語は import のときに決まるので、ここで決めないと英語の Mac で単独で動かすと落ちる
os.environ["BATTERY4_LANG"] = "ja"

import core


def test_default_settings_keys():
    s = core.default_settings()
    for key in ("schema_version", "search_folders", "cell_layout", "cell_labels",
                "target_track", "favorites", "recent", "countdown_sec",
                "auto_advance_cell", "readout_mode", "sort_key"):
        assert key in s, f"デフォルト設定に {key} がない"
    assert set(s["cell_labels"].keys()) == set(core.CELL_NAMES)
    # セル位置は絶対座標ではなく比率で持つ（窓が動いても壊れないように）
    assert "cell_coords" not in s
    assert s["cell_layout"]["rows"] == 4 and s["cell_layout"]["cols"] == 12
    print("PASS: デフォルト設定が必要なキーを全て持つ")


def test_cell_names_are_battery_notation():
    """セル名が Battery 4 の実表記（行＝A〜D、列＝1〜12）であること。"""
    assert len(core.CELL_NAMES) == 48
    assert core.CELL_NAMES[0] == "A1"
    # A1 の右隣は A2（旧実装の B1 ではない）
    assert core.CELL_NAMES[1] == "A2"
    assert core.CELL_NAMES[12] == "B1"
    assert core.CELL_NAMES[-1] == "D12"
    print("PASS: セル名が Battery の表記に一致する")


def test_sort_options_unique_and_labeled():
    keys = [k for k, _ in core.SORT_OPTIONS]
    assert len(keys) == len(set(keys)), "ソートキーが重複"
    assert "recent" in keys and "fav_first" in keys
    for _k, label in core.SORT_OPTIONS:
        assert label.strip(), "ラベルが空"
    print("PASS: ソート11種のキーとラベルが妥当")


def test_sort_name_and_folder(tmpdir=None):
    paths = ["/b/Snare.wav", "/a/kick.wav", "/a/Hat.wav"]
    assert core.sort_samples(paths, "name_asc") == [
        "/a/Hat.wav", "/a/kick.wav", "/b/Snare.wav"]
    assert core.sort_samples(paths, "name_desc") == [
        "/b/Snare.wav", "/a/kick.wav", "/a/Hat.wav"]
    assert core.sort_samples(paths, "folder") == [
        "/a/Hat.wav", "/a/kick.wav", "/b/Snare.wav"]
    print("PASS: 名前・フォルダ別ソート")


def test_sort_fav_and_recent():
    paths = ["/x/a.wav", "/x/b.wav", "/x/c.wav", "/x/d.wav"]
    fav = {"/x/c.wav"}
    assert core.sort_samples(paths, "fav_first", favorites=fav)[0] == "/x/c.wav"
    recent = ["/x/c.wav", "/x/a.wav"]  # c が最新
    out = core.sort_samples(paths, "recent", recent=recent)
    assert out == ["/x/c.wav", "/x/a.wav", "/x/b.wav", "/x/d.wav"], out
    print("PASS: お気に入り優先・最近使用ソート")


def test_sort_random_keeps_members():
    paths = [f"/x/{i}.wav" for i in range(20)]
    out = core.sort_samples(paths, "random")
    assert sorted(out) == sorted(paths)
    print("PASS: ランダムソートは要素を失わない")


def test_filter_case_insensitive():
    paths = ["/x/Kick_808.wav", "/x/snare.wav"]
    assert core.filter_samples(paths, "kick") == ["/x/Kick_808.wav"]
    assert core.filter_samples(paths, "") == paths
    print("PASS: フィルターは大文字小文字を無視")


# ---- 絞り込み（作者の別のアプリと同じ規則）----

SPLICE_ROOT = "/Volumes/Audio/Splice"
SPLICE_ONESHOT = (
    "/Volumes/Audio/Splice/sounds/packs/King of Trap/Splice_-_King/"
    "One_Shots/Drums/SOUTHSIDE_percussion_hrazy.wav"
)
SPLICE_LOOP = (
    "/Volumes/Audio/Splice/sounds/packs/King of Trap/Splice_-_King/"
    "Loops/Drum_Loops/SOUTHSIDE_135_drums_top_skyami_loop.wav"
)
SPLICE_OTHER_PACK = (
    "/Volumes/Audio/Splice/sounds/packs/VINTAGE VIBES/Origin_Sound/"
    "loops/OS_VV_130_perc_groove.wav"
)


def test_guess_kind_hint_words():
    """種別推定は作者の別のアプリと同じヒント語で動く。

    ヒント語は ("loop", "ループ", "_lp", "groove", "beat")。
    ファイル名だけを見る（フォルダ名は見ない）ことも合わせている。
    """
    assert core.guess_kind(SPLICE_LOOP) == core.KIND_LOOP
    assert core.guess_kind("/x/OS_VV_130_perc_groove.wav") == core.KIND_LOOP
    assert core.guess_kind("/x/hard_beat_01.wav") == core.KIND_LOOP
    assert core.guess_kind("/x/bass_lp_c.wav") == core.KIND_LOOP
    assert core.guess_kind("/x/ループ素材.wav") == core.KIND_LOOP
    assert core.guess_kind(SPLICE_ONESHOT) == core.KIND_ONESHOT
    assert core.guess_kind("/x/KICK.wav") == core.KIND_ONESHOT
    # フォルダ名に Loops があってもファイル名に無ければワンショット扱い
    assert core.guess_kind("/x/Loops/kick.wav") == core.KIND_ONESHOT
    print("PASS: 種別推定が作者の別のアプリと同じ規則")


def test_extract_pack_from_splice_layout():
    """packs/<パック名>/… からパック名を取り出す（作者の別のアプリと同じ）。"""
    assert core.extract_pack(SPLICE_ONESHOT, SPLICE_ROOT) == "King of Trap"
    assert core.extract_pack(SPLICE_OTHER_PACK, SPLICE_ROOT) == "VINTAGE VIBES"
    # root を渡さなくても packs 階層を見つけられる
    assert core.extract_pack(SPLICE_ONESHOT) == "King of Trap"
    # packs 直下のファイルはパック無し（Splice に実在する形）
    assert core.extract_pack("/Volumes/Audio/Splice/sounds/packs/SAUCE.WAV") == ""
    # packs 階層が無ければ空
    assert core.extract_pack("/Volumes/Audio/Music/kick.wav") == ""
    print("PASS: Splice のパック名を取り出せる")


def test_list_packs_sorted_unique():
    paths = [SPLICE_ONESHOT, SPLICE_LOOP, SPLICE_OTHER_PACK, "/x/none.wav"]
    assert core.list_packs(paths, SPLICE_ROOT) == ["King of Trap", "VINTAGE VIBES"]
    print("PASS: パック一覧は重複なく名前順")


def test_filter_samples_and_conditions():
    """条件は AND（作者の別のアプリと同じ考え方）。"""
    paths = [SPLICE_ONESHOT, SPLICE_LOOP, SPLICE_OTHER_PACK]
    fav = {SPLICE_LOOP}

    # 種別だけ
    assert core.filter_samples(paths, kind=core.KIND_LOOP) == [
        SPLICE_LOOP, SPLICE_OTHER_PACK]
    assert core.filter_samples(paths, kind=core.KIND_ONESHOT) == [SPLICE_ONESHOT]
    # パックだけ
    assert core.filter_samples(paths, pack="VINTAGE VIBES", root=SPLICE_ROOT) == [
        SPLICE_OTHER_PACK]
    # お気に入りだけ
    assert core.filter_samples(paths, favorites=fav, favorite_only=True) == [SPLICE_LOOP]
    # 組み合わせ（AND）: ループ かつ King of Trap パック
    assert core.filter_samples(
        paths, kind=core.KIND_LOOP, pack="King of Trap", root=SPLICE_ROOT
    ) == [SPLICE_LOOP]
    # テキストも重ねられる
    assert core.filter_samples(
        paths, "perc", kind=core.KIND_LOOP, root=SPLICE_ROOT
    ) == [SPLICE_OTHER_PACK]
    # どれも満たさなければ空
    assert core.filter_samples(
        paths, "kick", kind=core.KIND_LOOP, root=SPLICE_ROOT) == []
    # 条件なしは全件（既存の呼び出しと互換）
    assert core.filter_samples(paths) == paths
    assert core.filter_samples(paths, "") == paths
    print("PASS: 絞り込みが AND 条件で効く")


def test_filter_query_is_single_substring():
    """テキストは空白ごと1本の部分文字列として扱う（作者の別のアプリと同じ）。

    向こうは LIKE '%text%' の1本勝負で、空白区切りの AND 検索ではない。
    こちらだけ語の AND にすると同じ言葉で違う結果になるため揃える。
    """
    paths = ["/x/kick 808 hard.wav", "/x/kick_hard.wav", "/x/808_kick.wav"]
    assert core.filter_samples(paths, "kick 808") == ["/x/kick 808 hard.wav"]
    print("PASS: テキスト検索は1本の部分文字列（語の AND ではない）")


def test_kind_from_duration_and_resolve():
    """長さから種別を判断し、ファイル名より長さを優先すること。

    利用者の実データでは、ファイル名で「ワンショット」と判定された
    2241 件のうち 1270 件が 3 秒以上あった（ファイル名だけでは当てにならない）。
    そのため長さが分かるときは長さを優先する。
    """
    # 長さだけの判断
    assert core.kind_from_duration(0.5) == core.KIND_ONESHOT
    assert core.kind_from_duration(2.0) == core.KIND_ONESHOT   # 境目は含む
    assert core.kind_from_duration(2.1) == core.KIND_LOOP
    assert core.kind_from_duration(None) is None               # 判断できない
    assert core.kind_from_duration(3.0, oneshot_max_sec=4.0) == core.KIND_ONESHOT

    # 長い音は、ファイル名がワンショット風でもループ（利用者が困っていた形）
    assert core.resolve_kind("/x/KSHMR_Punchy_Kick_09.wav", 8.0) == core.KIND_LOOP
    # 短い音は、ファイル名にヒント語があればループ（短いドラムループを拾う）
    assert core.resolve_kind("/x/drum_loop_top.wav", 1.5) == core.KIND_LOOP
    # 短くてヒント語も無ければワンショット
    assert core.resolve_kind("/x/KSHMR_Punchy_Kick_09.wav", 0.5) == core.KIND_ONESHOT
    # 長さが分からなければファイル名から推定（従来どおり）
    assert core.resolve_kind("/x/KSHMR_Punchy_Kick_09.wav", None) == core.KIND_ONESHOT
    assert core.resolve_kind("/x/drum_loop_top.wav", None) == core.KIND_LOOP
    # 境目を変えると結果が変わる
    assert core.resolve_kind("/x/crash.wav", 3.0) == core.KIND_LOOP
    assert core.resolve_kind("/x/crash.wav", 3.0, oneshot_max_sec=4.0) == \
        core.KIND_ONESHOT
    print("PASS: 長さによる種別判定（長さをファイル名より優先）")


def test_filter_by_kind_uses_durations():
    """絞り込みの種別判定に長さが使われること。"""
    long_kick = "/x/Kick_Long.wav"      # 名前はワンショット風だが長い
    short_kick = "/x/Kick_Short.wav"
    durations = {long_kick: 9.0, short_kick: 0.4}
    paths = [long_kick, short_kick]
    # 長さを渡すと、長い方はワンショットから外れる
    assert core.filter_samples(
        paths, kind=core.KIND_ONESHOT, durations=durations) == [short_kick]
    assert core.filter_samples(
        paths, kind=core.KIND_LOOP, durations=durations) == [long_kick]
    # 長さを渡さなければ従来どおりファイル名で判定（両方ワンショット）
    assert core.filter_samples(paths, kind=core.KIND_ONESHOT) == paths
    # 長さで直接絞ることもできる（長さ不明は残す）
    durations2 = dict(durations, **{"/x/unknown.wav": None})
    assert core.filter_samples(
        paths + ["/x/unknown.wav"], max_sec=1.0, durations=durations2
    ) == [short_kick, "/x/unknown.wav"]
    print("PASS: 種別の絞り込みが長さを見る")


def test_parse_duration_and_format():
    """afinfo の出力から長さを取り出し、読み上げやすく整形する。"""
    assert core.parse_duration(AFINFO_MARKER_SAMPLE) == 5.807506
    assert core.parse_duration("no duration here") is None
    # 読み上げ用の整形（記号を使わない）
    assert core.format_duration(1.5) == "1.5秒"
    assert core.format_duration(0.46) == "0.5秒"
    assert core.format_duration(None) == "長さ不明"
    assert core.format_duration(95.0) == "1分35秒"
    print("PASS: 長さの解析と読み上げ整形")


def test_read_duration_real_file():
    """実ファイル（stdlib で生成した WAV）から長さを読める。"""
    import wave
    import struct
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "tone.wav")
        with wave.open(src, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(b"".join(struct.pack("<h", 1000) for _ in range(22050)))
        got = core.read_duration(src)
        assert got is not None and abs(got - 0.5) < 0.01, got
        # まとめ取得とキャッシュ再利用
        cache = {}
        core.read_durations([src], cache)
        assert abs(cache[src] - 0.5) < 0.01
        cache[src] = 99.0            # キャッシュがあれば読み直さない
        core.read_durations([src], cache)
        assert cache[src] == 99.0
        # 読めないファイルは None（例外にしない）
        bad = os.path.join(d, "broken.wav")
        with open(bad, "wb") as f:
            f.write(b"not a wav")
        assert core.read_duration(bad) is None
    print("PASS: 実ファイルから長さを読む（キャッシュ・失敗も安全）")


def test_read_durations_retries_failures():
    """読めなかったファイルは覚えないこと（次のスキャンで読み直す）。

    ダウンロード途中のサンプルは一度失敗するが、落ち切れば読める。
    失敗まで覚えると、再スキャンしてもずっと「長さ不明」のままで、
    種別の判定もファイル名頼みに戻ってしまう。
    """
    import wave
    import struct
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "late.wav")
        with open(path, "wb") as f:      # まだ音になっていない
            f.write(b"not a wav yet")
        cache = {}
        core.read_durations([path], cache)
        assert cache[path] is None, cache

        # 落ち切ったあと、同じキャッシュで読み直せる
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(b"".join(struct.pack("<h", 0) for _ in range(4410)))
        core.read_durations([path], cache)
        assert cache[path] is not None and abs(cache[path] - 0.1) < 0.01, cache
    print("PASS: 読めなかった長さは覚えない（再スキャンで読み直す）")


def test_sort_by_duration():
    """長さ順のソートと、行頭への長さ表示。"""
    a, b, c, unknown = "/x/a.wav", "/x/b.wav", "/x/c.wav", "/x/u.wav"
    durs = {a: 5.0, b: 0.5, c: 2.0, unknown: None}
    paths = [a, b, c, unknown]
    # 長さ不明は短い順・長い順のどちらでも末尾
    assert core.sort_samples(paths, "dur_short", durations=durs) == [b, c, a, unknown]
    assert core.sort_samples(paths, "dur_long", durations=durs) == [a, c, b, unknown]
    # 行頭に長さが付く
    label = core.format_list_label(b, "dur_short", durations=durs)
    assert label == "[0.5秒] b.wav", label
    assert core.format_list_label(unknown, "dur_long", durations=durs) == \
        "[長さ不明] u.wav"
    print("PASS: 長さ順のソートと行頭の長さ表示")


def test_pack_order_by_count():
    """パックの並び順を中身の多さで切り替えられること。"""
    paths = [
        "/r/packs/Small/a.wav",
        "/r/packs/Big/b.wav",
        "/r/packs/Big/c.wav",
        "/r/packs/Big/d.wav",
        "/r/packs/Mid/e.wav",
        "/r/packs/Mid/f.wav",
    ]
    assert core.pack_counts(paths, "/r") == {"Small": 1, "Big": 3, "Mid": 2}
    # 中身の多い順（既定）
    assert core.list_packs(paths, "/r") == ["Big", "Mid", "Small"]
    assert core.list_packs(paths, "/r", core.PACK_ORDER_COUNT) == ["Big", "Mid", "Small"]
    # 名前順
    assert core.list_packs(paths, "/r", core.PACK_ORDER_NAME) == ["Big", "Mid", "Small"]
    # 同数なら名前順になることを確かめる（Mid と Small を同数にする）
    paths2 = paths + ["/r/packs/Small/g.wav"]
    assert core.list_packs(paths2, "/r", core.PACK_ORDER_COUNT) == \
        ["Big", "Mid", "Small"]
    # 件数が逆転すれば順序も変わる
    paths3 = paths + ["/r/packs/Small/g.wav", "/r/packs/Small/h.wav",
                      "/r/packs/Small/i.wav", "/r/packs/Small/j.wav"]
    assert core.list_packs(paths3, "/r", core.PACK_ORDER_COUNT)[0] == "Small"
    print("PASS: パックを中身の多い順に並べられる")


def test_pack_order_by_time():
    """パックを日時でも並べられること（更新の新しい順 / 追加した順）。

    **更新と追加は意味が違う。** 更新はそのパックで最も新しい素材、
    追加は最も古い素材。Splice は素材を 1 つずつ落とすので、
    買い足し続けているパックでは離れる。
    """
    import os
    import tempfile

    root = tempfile.mkdtemp()

    def mk(pack, name, when):
        d = os.path.join(root, "packs", pack)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, name)
        with open(path, "wb") as f:
            f.write(b"x")
        os.utime(path, (when, when))
        return path

    # Old: 古くに入れて、そのまま。New: 最近入れた。
    # Growing: 古くから買い足し続けている（追加は古い、更新は新しい）
    paths = [
        mk("Old", "a.wav", 1000),
        mk("Growing", "b.wav", 1100),
        mk("Growing", "c.wav", 9000),
        mk("New", "d.wav", 8000),
    ]
    cache = {}
    newest, oldest = core.pack_times(paths, root, cache)
    assert newest["Growing"] == 9000, newest
    assert oldest["Growing"] == 1100, oldest

    # 更新の新しい順: Growing(9000) > New(8000) > Old(1000)
    assert core.list_packs(paths, root, core.PACK_ORDER_MTIME,
                           stat_cache=cache) == ["Growing", "New", "Old"]
    # 追加した順（古いものから）: Old(1000) < Growing(1100) < New(8000)
    assert core.list_packs(paths, root, core.PACK_ORDER_ADDED,
                           stat_cache=cache) == ["Old", "Growing", "New"]

    # **4 つとも順送りで一巡すること。** 作者の別のアプリと同じ順・同じ言葉。
    assert [k for k, _ in core.PACK_ORDER_OPTIONS] == [
        core.PACK_ORDER_COUNT, core.PACK_ORDER_NAME,
        core.PACK_ORDER_MTIME, core.PACK_ORDER_ADDED,
    ]
    assert [v for _, v in core.PACK_ORDER_OPTIONS] == [
        "中身の多い順", "名前順", "更新の新しい順", "追加した順。古いものから",
    ]

    # 日時が取れないものは末尾へ（先頭に来ると邪魔になる）
    ghost = paths + ["/nowhere/packs/Ghost/x.wav"]
    assert core.list_packs(ghost, root, core.PACK_ORDER_ADDED,
                           stat_cache=cache)[-1] == "Ghost"
    print("PASS: パックを日時でも並べられる（更新 / 追加）")


def test_accessibility_diagnosis():
    """権限診断が「どのアプリに許可すべきか」を示すこと。

    「許可したはずなのに動かない」は、たいてい許可した対象が違うため。
    ソースから起動していると権限は python ではなく責任プロセス
    （ターミナルなど）に付く、という点を案内できるかを確かめる。
    """
    info = core.accessibility_diagnosis()
    for key in ("trusted", "target", "target_path", "kind", "advice"):
        assert key in info, f"診断結果に {key} が無い"
    assert isinstance(info["trusted"], bool)
    assert info["kind"] in ("app", "source")
    assert info["target"], "権限を与える対象の名前が空"
    # 案内文には対象の名前が必ず含まれる（読み上げで分かるように）
    assert info["target"] in info["advice"], info["advice"]
    # 未許可のときは再起動の案内も含める（許可後に反映されない相談が多いため）
    if not info["trusted"]:
        assert "開き直" in info["advice"] or "終了" in info["advice"], info["advice"]
    print(f"PASS: 権限診断（対象 {info['target']} / {info['kind']} / "
          f"許可 {info['trusted']}）")


def test_app_name_from_path():
    """実行ファイルのパスから、システム設定に出る名前を取り出せること。"""
    assert core.app_name_from_path(
        "/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal") == "Terminal"
    # 中の実行ファイルの名前ではなく .app の名前（システム設定の一覧に出る名前）
    assert core.app_name_from_path(
        "/Applications/PT Battery 4 Loader.app/Contents/MacOS/Battery4Loader"
    ) == "PT Battery 4 Loader"
    # .app が無ければ実行ファイル名
    assert core.app_name_from_path("/usr/bin/python3") == "python3"
    assert core.app_name_from_path("") == ""
    assert core.app_name_from_path(None) == ""
    print("PASS: パスから権限対象の名前を取り出す")


def test_pack_candidates_narrow_with_other_conditions():
    """パック候補は「パック以外の条件で絞った結果」から作れること。

    実データでは 553 パックあり、そのままでは選べない。テキストや種別で
    絞ってから候補を作ると実用的な数になる（実測: kick で 22 パック）。
    app_main._refresh_list はこの2段階で候補を作っている。
    """
    paths = [SPLICE_ONESHOT, SPLICE_LOOP, SPLICE_OTHER_PACK]
    # 条件なしなら両方のパックが候補
    assert core.list_packs(paths, SPLICE_ROOT) == ["King of Trap", "VINTAGE VIBES"]
    # ワンショットだけに絞ると、候補は King of Trap だけになる
    base = core.filter_samples(paths, kind=core.KIND_ONESHOT, root=SPLICE_ROOT)
    assert core.list_packs(base, SPLICE_ROOT) == ["King of Trap"]
    # 候補から選んだパックを重ねても結果は空にならない（AND が壊れていない）
    assert core.filter_samples(base, pack="King of Trap", root=SPLICE_ROOT) == [
        SPLICE_ONESHOT]
    print("PASS: パック候補が他の条件で絞り込まれる")


def test_search_result_summary_wording():
    """件数の読み上げ文言を作者の別のアプリと揃える。"""
    assert core.search_result_summary(0) == "該当するサンプルはありませんでした"
    assert core.search_result_summary(1) == "1件見つかりました"
    assert core.search_result_summary(3119) == "3119件見つかりました"
    print("PASS: 件数の読み上げ文言が共通")


def test_format_list_label_tags():
    fav = {"/x/a.wav"}
    label = core.format_list_label("/x/a.wav", "name_asc", favorites=fav)
    assert label == "[お気に入り] a.wav", label
    label2 = core.format_list_label(
        "/x/b.wav", "recent", favorites=set(), recent=["/x/b.wav"])
    assert label2 == "[最近使用] b.wav", label2
    assert "★" not in label + label2
    print("PASS: リスト表示ラベルはテキストタグ（記号なし）")


def test_format_list_label_name_transform():
    """読み上げ補正（name_transform）を渡してもソート文脈タグが保たれる。

    2026-08-31: 補正オン（既定）だと GUI 側がタグを捨てて自前合成しており、
    [最近使用] やサイズ・日付タグが読み上げから消えていた再発防止。
    """
    spoken = lambda p: "マルイチ Kick"
    label = core.format_list_label(
        "/x/01_Kick.wav", "recent", favorites={"/x/01_Kick.wav"},
        recent=["/x/01_Kick.wav"], name_transform=spoken,
    )
    assert label == "[お気に入り] [最近使用] マルイチ Kick", label
    # 拡張子タグは変換前の実ファイル名から取る（補正で拡張子が消えても保つ）
    label2 = core.format_list_label(
        "/x/01_Kick.wav", "ext", name_transform=spoken)
    assert label2 == "[WAV] マルイチ Kick", label2
    print("PASS: 読み上げ補正とソート文脈タグが両立する")


def test_sort_stat_cache_and_random_order():
    """stat キャッシュとランダム安定順が結果を変えず・安定させる。"""
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for i, size in enumerate((10, 300, 5)):
            p = os.path.join(d, f"s{i}.wav")
            with open(p, "wb") as f:
                f.write(b"x" * size)
            paths.append(p)
        cache = {}
        by_size = core.sort_samples(paths, "size_asc", stat_cache=cache)
        assert [os.path.getsize(p) for p in by_size] == [5, 10, 300]
        assert len(cache) == 3, "stat 結果がキャッシュされていない"
        # キャッシュありでも結果はキャッシュなしと同じ
        assert by_size == core.sort_samples(paths, "size_asc")
        # 消えたファイルは 0 扱いで例外にしない（失敗もキャッシュされる）
        missing = os.path.join(d, "missing.wav")
        out = core.sort_samples(paths + [missing], "size_asc", stat_cache=cache)
        assert out[0] == missing
        assert cache[missing] is False
    # ランダム順は random_order を渡すとその順で安定する
    paths = [f"/x/{i}.wav" for i in range(10)]
    order = {p: i for i, p in enumerate(reversed(paths))}
    out1 = core.sort_samples(paths, "random", random_order=order)
    out2 = core.sort_samples(paths[:5], "random", random_order=order)
    assert out1 == list(reversed(paths))
    assert out2 == out1[5:], "絞り込んでも相対順が保たれていない"
    print("PASS: stat キャッシュとランダム安定順")


def test_export_kit_reexport_from_kit_folder():
    """エクスポート先フォルダ内のサンプルを再エクスポートしても失敗しない。

    2026-08-31: dst == src のとき shutil.copy2 が SameFileError を出して
    エクスポート全体が失敗していた再発防止。
    """
    with tempfile.TemporaryDirectory() as d:
        orig = core.BATTERY4_KITS_DIR
        core.BATTERY4_KITS_DIR = os.path.join(d, "Kits")
        try:
            src_dir = os.path.join(d, "src")
            os.makedirs(src_dir)
            sample = os.path.join(src_dir, "kick.wav")
            with open(sample, "wb") as f:
                f.write(b"RIFFdummy")
            kit_dir, copied = core.export_kit_to_battery4_folder(
                "再出力", {"A1": sample}
            )
            # エクスポート済みのファイルをそのままセルに割り当てて再実行
            exported = copied["A1"]
            kit_dir2, copied2 = core.export_kit_to_battery4_folder(
                "再出力", {"A1": exported, "A2": sample}
            )
            assert kit_dir2 == kit_dir
            assert copied2["A1"] == exported, "既存ファイルがそのまま使われていない"
            assert os.path.exists(copied2["A2"])
            def_path = os.path.join(kit_dir, "_kit_definition.json")
            assert os.path.exists(def_path)
            print("PASS: キット再エクスポート（同一フォルダ内ファイル）が成功する")
        finally:
            core.BATTERY4_KITS_DIR = orig


def test_settings_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        orig_settings = core.SETTINGS_FILE
        orig_kits = core.LOADER_KITS_FILE
        core.SETTINGS_FILE = os.path.join(d, "s.json")
        core.LOADER_KITS_FILE = os.path.join(d, "k.json")
        try:
            s = core.default_settings()
            s["favorites"] = ["/x/kick 'quote'.wav"]
            s["cell_labels"]["A1"] = "キック"
            core.save_settings(s)
            loaded = core.load_settings()
            assert loaded == s, "設定の roundtrip が一致しない"

            kits = {"デモ": {"cells": {"A1": "/x/kick.wav"}, "track": "Inst1"}}
            core.save_kits(kits)
            assert core.load_kits() == kits
            print("PASS: 設定・キットの保存/読み込み roundtrip")
        finally:
            core.SETTINGS_FILE = orig_settings
            core.LOADER_KITS_FILE = orig_kits


def test_broken_settings_fallback():
    with tempfile.TemporaryDirectory() as d:
        orig = core.SETTINGS_FILE
        core.SETTINGS_FILE = os.path.join(d, "s.json")
        try:
            with open(core.SETTINGS_FILE, "w") as f:
                f.write("{ broken json !!!")
            s = core.load_settings()
            assert s == core.default_settings()
            print("PASS: 壊れた設定 JSON はデフォルトにフォールバック")
        finally:
            core.SETTINGS_FILE = orig


def _runtime_string_literals(path):
    """ソース中の文字列リテラルを集める（docstring は説明用なので除外）。

    コメント・docstring での言及は許容し、実行時に使われる文字列だけを
    検査対象にする。f-string は定数部分を検査する。
    """
    import ast
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                out.append(node.value)
    return out


def test_no_forbidden_symbols_in_ui_sources():
    """このプロジェクトのルール：GUI テキストに ★ ♪ ✅ ❌ などの記号を使わない。

    → も VoiceOver が「右矢印」と読み上げて通知やメニューの雑音になるため
    禁止する（2026-08-31: アナウンス文とメニュータイトルで実際に混入した）。
    ** は Markdown の強調がヘルプ文へ紛れ込んだもの（そのまま
    「アスタリスク」と読まれる。2026-08-31 に実際に混入した）。
    """
    forbidden = ["★", "♪", "✅", "❌", "→", "**"]
    for fname in ("core.py", "app_main.py", "app_kit.py", "app_settings.py"):
        for s in _runtime_string_literals(os.path.join(ROOT, fname)):
            for ch in forbidden:
                assert ch not in s, \
                    f"{fname} の文字列に禁止記号 {ch} が含まれている: {s!r}"
    print("PASS: UI 文字列に禁止記号（スター・矢印等）が無い")


def test_no_fake_applescript_drag():
    """System Events に存在しない `drag from` 命令が復活していないこと。
    （旧実装はこれで常に失敗し、ロード不能の原因だった）"""
    for s in _runtime_string_literals(os.path.join(ROOT, "core.py")):
        assert "drag from" not in s, \
            "存在しない AppleScript drag 命令が復活している"
    assert hasattr(core, "cg_drag"), "Quartz ドラッグ関数 cg_drag が無い"
    assert hasattr(core, "drag_via_finder")
    assert hasattr(core, "accessibility_trusted")
    assert hasattr(core, "current_mouse_position")
    print("PASS: ドラッグ自動化は Quartz ベース（偽 AppleScript drag なし）")


def test_export_kit(tmp=None):
    with tempfile.TemporaryDirectory() as d:
        orig = core.BATTERY4_KITS_DIR
        core.BATTERY4_KITS_DIR = os.path.join(d, "Kits")
        try:
            src_dir = os.path.join(d, "src")
            os.makedirs(src_dir)
            sample = os.path.join(src_dir, "kick.wav")
            with open(sample, "wb") as f:
                f.write(b"RIFFdummy")
            kit_dir, copied = core.export_kit_to_battery4_folder(
                "テストキット", {"A1": sample, "B1": ""}
            )
            assert os.path.isdir(kit_dir)
            assert "A1" in copied and os.path.exists(copied["A1"])
            assert "B1" not in copied
            def_path = os.path.join(kit_dir, "_kit_definition.json")
            assert os.path.exists(def_path), "_kit_definition.json が無い"
            definition = json.load(open(def_path, encoding="utf-8"))
            assert definition["kit_name"] == "テストキット"
            assert definition["cells"]["A1"]["file"] == "kick.wav"
            print("PASS: キットエクスポートがサンプルと定義JSONを書き出す")
        finally:
            core.BATTERY4_KITS_DIR = orig


# ---- Battery 4 向け音声ファイル検査・正規化 ----

AFINFO_MARKER_SAMPLE = """File:           /x/STCR2_PHONK_Kit_One_Shot_TurboCharger_Gunshot.wav
File type ID:   WAVE
Num Tracks:     1
----
Data format:     2 ch,  44100 Hz, lpcm (0x0000000C) 24-bit little-endian signed integer
                no channel layout.
estimated duration: 5.807506 sec
audio bytes: 1536666
audio data file offset: 80
source bit depth: I24
1 markers; SMPTE time type 0 (none)
  marker 1, "Tempo: 110.0": frame 0
----
"""

AFINFO_CLEAN_16BIT = """File: /x/clean.wav
File type ID:   WAVE
----
Data format:     2 ch,  44100 Hz, lpcm (0x0000000C) 16-bit little-endian signed integer
audio data file offset: 44
source bit depth: I16
----
"""

AFINFO_FLOAT32 = """File: /x/float.wav
File type ID:   WAVE
----
Data format:     2 ch,  48000 Hz, lpcm (0x00000009) 32-bit little-endian float
audio data file offset: 80
source bit depth: F32
----
"""


def test_parse_audio_info_short_format_names():
    """afinfo の短い書き方（Int16・Float32）も PCM と分かること。

    実機の Battery へのロードで、ふつうの 16 ビットの WAV まで「非 PCM」として
    変換していたと分かった（2026-11-03）。
    """
    for line, bits, is_float, wants_convert in (
        ("1 ch,  44100 Hz, Int16", 16, False, False),
        ("2 ch,  44100 Hz, Int16, interleaved", 16, False, False),
        ("2 ch,  44100 Hz, Int24, interleaved", 24, False, False),
        ("1 ch,  44100 Hz, Float32", 32, True, True),
        ("2 ch,  44100 Hz, Float32, interleaved", 32, True, True),
    ):
        info = core.parse_audio_info(f"Data format:     {line}\n")
        assert info["ok"] and info["is_lpcm"], line
        assert info["bits"] == bits and info["is_float"] == is_float, (line, info)
        reason = core.normalization_reason(info)
        assert bool(reason) == wants_convert, (line, reason)
        assert "非PCM" not in reason, (line, reason)
    print("PASS: afinfo の短い書き方も PCM と判定する")


def test_parse_audio_info_marker_file():
    """実際に読み込めなかった Splice ファイルの afinfo 出力を判定できる。"""
    info = core.parse_audio_info(AFINFO_MARKER_SAMPLE)
    assert info["ok"] and info["is_lpcm"] and not info["is_float"]
    assert info["bits"] == 24
    assert info["markers"] == 1
    assert info["data_offset"] == 80
    reason = core.normalization_reason(info)
    assert "マーカー" in reason, reason
    print("PASS: マーカー入り WAV を変換対象と判定")


def test_parse_audio_info_clean_and_float():
    """通常の 16bit PCM は変換不要、32bit float は変換対象。"""
    clean = core.parse_audio_info(AFINFO_CLEAN_16BIT)
    assert core.normalization_reason(clean) == ""
    fl = core.parse_audio_info(AFINFO_FLOAT32)
    assert "浮動小数点" in core.normalization_reason(fl)
    print("PASS: 16bit PCM は変換不要 / float は変換対象")


def test_converted_cache_name():
    """キャッシュ名は決定的で、元ファイル名を保ち、内容が変わると別名になる。"""
    a = core.converted_cache_name("/x/Kick.wav", 1000.0, 500)
    b = core.converted_cache_name("/x/Kick.wav", 1000.0, 500)
    c = core.converted_cache_name("/x/Kick.wav", 2000.0, 500)
    assert a == b, "同一入力で名前が変わった"
    assert a != c, "更新日時が変わっても同じ名前"
    assert os.path.basename(a) == "Kick.wav", a
    aiff = core.converted_cache_name("/x/Snare.aiff", 1.0, 2)
    assert os.path.basename(aiff) == "Snare.wav", aiff
    print("PASS: 変換キャッシュ名（決定的・ファイル名維持・wav拡張子）")


def test_normalize_real_file():
    """stdlib で生成した WAV を実際に afconvert で正規化できる（macOSのみ）。"""
    import shutil as _shutil
    import wave
    import struct
    if _shutil.which("afconvert") is None or _shutil.which("afinfo") is None:
        print("SKIP: afconvert が無い環境のためスキップ")
        return
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "tone.wav")
        with wave.open(src, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(44100)
            frames = b"".join(
                struct.pack("<h", 3000 if i % 100 < 50 else -3000)
                for i in range(4410)
            )
            w.writeframes(frames)
        orig_dir = core.CONVERTED_DIR
        core.CONVERTED_DIR = os.path.join(d, "conv")
        try:
            out = core.normalize_for_battery(src)
            assert out and os.path.exists(out), "変換出力が無い"
            assert out.endswith(".wav")
            info = core.parse_audio_info(core.afinfo_report(out) or "")
            assert info["ok"] and info["is_lpcm"] and info["markers"] == 0
            # 2回目はキャッシュを再利用（同じパスが返る）
            out2 = core.normalize_for_battery(src)
            assert out2 == out
            print("PASS: afconvert 正規化とキャッシュ再利用")
        finally:
            core.CONVERTED_DIR = orig_dir


def test_no_absolute_cell_coords():
    """セル位置を絶対座標で持つ実装に戻っていないことを確かめる。

    絶対座標は「設定が飛ぶ・窓が動く・Battery のサイズが変わる」のどれでも
    壊れ、実際に一度壊れた。比率方式を守る。
    """
    for fname in ("core.py", "app_main.py", "app_settings.py", "app_kit.py"):
        src = open(os.path.join(ROOT, fname), encoding="utf-8").read()
        assert "DEFAULT_CELL_COORDS" not in src, f"{fname} に絶対座標の既定値"
    # UI 層は cell_coords を一切扱わない（core は旧設定を捨てるためだけに触る）
    for fname in ("app_main.py", "app_settings.py", "app_kit.py"):
        src = open(os.path.join(ROOT, fname), encoding="utf-8").read()
        assert "cell_coords" not in src, f"{fname} に cell_coords が残っている"
    core_src = open(os.path.join(ROOT, "core.py"), encoding="utf-8").read()
    uses = [ln.strip() for ln in core_src.splitlines()
            if "cell_coords" in ln and not ln.strip().startswith("#")
            and "絶対座標" not in ln]
    # 旧設定を捨てる処理だけが触れてよい（読み取りに使ってはいけない）
    assert uses and all(
        ln in ('settings.pop("cell_coords", None)', 'if "cell_coords" in settings:')
        for ln in uses
    ), uses
    # 既定設定にも入っていない
    assert "cell_coords" not in core.default_settings()
    print("PASS: セル位置は比率方式のみ（絶対座標の再発なし）")


def test_battery_windows_is_safe():
    """battery_windows は Pro Tools がなくても例外を出さずリストを返す。"""
    wins = core.battery_windows()
    assert isinstance(wins, list)
    for w in wins:
        assert set(w) >= {"track", "plugin", "window", "view"}
        assert w["plugin"].startswith(core.TARGET_PLUGIN_PREFIX)
    print(f"PASS: battery_windows が安全に動く（検出 {len(wins)} 件）")


# ---------------------------------------------------------------------------
# 2026-10-24 のレビューで直したものの再発防止
# ---------------------------------------------------------------------------
def test_scan_skips_hidden_and_appledouble():
    """先頭がドットのファイル（._kick.wav など）は拾わないこと。

    exFAT の外付けドライブでは macOS が ._ で始まるリソースフォークを
    横に作る。拾うと同じ名前が二重に並び、ロードもできない。
    作者の別のアプリと同じ規則。
    """
    assert core.is_sample_name("kick.wav")
    assert core.is_sample_name("Loop.AIFF")
    assert not core.is_sample_name("._kick.wav")
    assert not core.is_sample_name(".hidden.wav")
    assert not core.is_sample_name("notes.txt")
    assert not core.is_sample_name("")
    with tempfile.TemporaryDirectory() as d:
        for name in ("kick.wav", "._kick.wav", ".snare.wav", "hat.aif"):
            open(os.path.join(d, name), "wb").close()
        found = [os.path.basename(p) for p in core.find_samples_walk([d])]
        assert found == ["hat.aif", "kick.wav"], found
    print("PASS: 隠しファイルと ._ のリソースフォークを拾わない")


def test_format_duration_carries_over():
    """丸めで 60 秒になるときは分へ繰り上げること（「1分60秒」にしない）。"""
    assert core.format_duration(119.6) == "2分0秒"
    assert core.format_duration(59.96) == "1分0秒"
    assert core.format_duration(59.94) == "59.9秒"
    assert core.format_duration(174.0) == "2分54秒"
    assert core.format_duration(60.0) == "1分0秒"
    print("PASS: 長さの読み上げは丸めてから分と秒に分ける")


def test_format_list_label_unknown_has_no_symbols():
    """サイズや日時が取れないとき、記号（?）ではなく言葉で伝えること。"""
    missing = "/no/such/dir/kick.wav"
    size = core.format_list_label(missing, "size_asc", stat_cache={})
    date = core.format_list_label(missing, "date_new", stat_cache={})
    assert size == "[サイズ不明] kick.wav", size
    assert date == "[日時不明] kick.wav", date
    assert "?" not in size + date
    print("PASS: サイズ・日時が分からないときは言葉で伝える")


def test_app_bundle_path():
    """権限を与える対象は .app のパスで渡すこと（中の実行ファイルではない）。"""
    assert core.app_bundle_path(
        "/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal"
    ) == "/Applications/Utilities/Terminal.app"
    assert core.app_bundle_path(
        "/Applications/PT Battery 4 Loader.app/Contents/MacOS/Battery4Loader"
    ) == "/Applications/PT Battery 4 Loader.app"
    # いちばん内側の .app（app_name_from_path と同じ）
    assert core.app_bundle_path(
        "/A/Outer.app/Contents/B/Inner.app/Contents/MacOS/x"
    ) == "/A/Outer.app/Contents/B/Inner.app"
    assert core.app_bundle_path("/usr/bin/python3") == "/usr/bin/python3"
    assert core.app_bundle_path("") == ""
    assert core.app_bundle_path(None) == ""

    orig_resp = core.responsible_process_path
    orig_bundle = core.running_as_bundle
    core.responsible_process_path = lambda: (
        "/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal")
    core.running_as_bundle = lambda: False
    try:
        info = core.accessibility_diagnosis()
        assert info["target_path"] == "/Applications/Utilities/Terminal.app", info
        assert info["exec_path"].endswith("/MacOS/Terminal"), info
        assert info["target"] == "Terminal"
    finally:
        core.responsible_process_path = orig_resp
        core.running_as_bundle = orig_bundle
    print("PASS: 権限を与えるパスは .app を指す")


def test_export_kit_does_not_duplicate_on_reexport():
    """同じキットを続けてエクスポートしても、複製（名前_A1.wav）が増えないこと。

    同じサンプルを 2 つのセルに置いたときも 1 つだけ置く。名前は同じでも
    中身の違うファイルは、セル名を付けて別に置く。
    """
    with tempfile.TemporaryDirectory() as d:
        orig = core.BATTERY4_KITS_DIR
        core.BATTERY4_KITS_DIR = os.path.join(d, "Kits")
        try:
            a = os.path.join(d, "packA", "kick.wav")
            b = os.path.join(d, "packB", "kick.wav")
            for path, body in ((a, b"RIFF-a"), (b, b"RIFF-bbbb")):
                os.makedirs(os.path.dirname(path))
                with open(path, "wb") as f:
                    f.write(body)
            cells = {"A1": a, "A2": a, "A3": b}
            kit_dir, first = core.export_kit_to_battery4_folder("重複", cells)
            _, second = core.export_kit_to_battery4_folder("重複", cells)
            files = sorted(n for n in os.listdir(kit_dir) if n.endswith(".wav"))
            assert files == ["kick.wav", "kick_A3.wav"], files
            assert first == second, (first, second)
            assert first["A1"] == first["A2"]
        finally:
            core.BATTERY4_KITS_DIR = orig
    print("PASS: 再エクスポートで複製が増えない")


def test_kit_folder_name_has_no_separator():
    """キット名の / でフォルダが入れ子にならないこと。"""
    assert core.kit_folder_name("Trap/808") == "Trap／808_samples"
    assert core.kit_folder_name("a:b") == "a：b_samples"
    assert core.kit_folder_name(".hidden") == "hidden_samples"
    assert core.kit_folder_name("  ") == "kit_samples"
    with tempfile.TemporaryDirectory() as d:
        orig = core.BATTERY4_KITS_DIR
        core.BATTERY4_KITS_DIR = os.path.join(d, "Kits")
        try:
            src = os.path.join(d, "kick.wav")
            with open(src, "wb") as f:
                f.write(b"RIFF")
            kit_dir, _ = core.export_kit_to_battery4_folder("Trap/808", {"A1": src})
            assert os.path.dirname(kit_dir) == core.BATTERY4_KITS_DIR, kit_dir
        finally:
            core.BATTERY4_KITS_DIR = orig
    print("PASS: キット名の / はフォルダを入れ子にしない")


def _win(owner, pid, x, y, w, h, layer=0, alpha=1.0, name=""):
    """CGWindowListCopyWindowInfo の1件の形（テスト用）。"""
    return {"kCGWindowOwnerName": owner, "kCGWindowOwnerPID": pid,
            "kCGWindowLayer": layer, "kCGWindowAlpha": alpha,
            "kCGWindowName": name,
            "kCGWindowBounds": {"X": x, "Y": y, "Width": w, "Height": h}}


# 窓の並び（手前から）の見本。このアプリが前面にいる形。
# Battery の画面は層 3 の浮いた窓で、前面のこのアプリの窓より上に来る
_REAL_LAYOUT = [
    _win("VoiceOver", 50, 0, 0, 1920, 1080, layer=1500),     # カーソルの枠
    _win("Window Server", 1, 0, 0, 1920, 24, layer=24, name="Menubar"),
    _win("Floating Panel", 60, 1878, 25, 42, 142, layer=19),
    _win("Pro Tools", 10, 20, 25, 1176, 753, layer=3),        # Battery の画面
    _win("PT Battery 4 Loader", 77, 200, 292, 660, 588, layer=0),
    _win("Pro Tools", 10, 0, 25, 1920, 1055, layer=0, name="Mix: s"),
    _win("Dock", 2, 0, 0, 1920, 1080, layer=-2147483624),
]


def test_window_at_follows_window_server_order():
    """点のいちばん手前の窓を、窓の層と並びから正しく選ぶこと。"""
    top = core.window_at(386, 213, infos=_REAL_LAYOUT)
    assert top["owner"] == "Pro Tools" and top["layer"] == 3, top
    # 浮いた Battery の画面は、このアプリの窓より上（始点が隠れる理由）
    top = core.window_at(500, 600, infos=_REAL_LAYOUT)
    assert top["layer"] == 3, top
    # Battery の画面の外でこのアプリの窓の上なら、このアプリ
    top = core.window_at(500, 800, infos=_REAL_LAYOUT)
    assert top["owner"] == "PT Battery 4 Loader", top
    # ほかのアプリの操作盤（層 19）は数える
    assert core.window_at(1890, 100, infos=_REAL_LAYOUT)["owner"] == \
        "Floating Panel"
    # 透明な窓・壁紙は数えない
    clear = [_win("Ghost", 9, 0, 0, 3000, 3000, alpha=0.0)] + _REAL_LAYOUT
    assert core.window_at(1500, 1000, infos=clear)["owner"] == "Pro Tools"
    assert core.window_at(5000, 5000, infos=_REAL_LAYOUT) is None
    print("PASS: 点のいちばん手前の窓を選ぶ")


def test_host_window_at_and_drag_endpoints():
    """合成ドラッグの始点と終点の窓を確かめること。"""
    ok, who = core.host_window_at(386, 213, infos=_REAL_LAYOUT, pids=[10])
    assert ok and who == ""
    ok, who = core.host_window_at(1890, 100, infos=_REAL_LAYOUT, pids=[10])
    assert not ok and who == "Floating Panel"
    # 層まで指定（ミックスウィンドウを押したいのに浮いた画面が重なっている）
    ok, who = core.host_window_at(386, 213, layer=0, infos=_REAL_LAYOUT,
                                  pids=[10])
    assert not ok and "Pro Tools" in who

    # 始点がこのアプリの窓（層 101 のドラッグの窓）なら運べる
    panel = [_win("PT Battery 4 Loader", 77, 370, 307, 32, 32, layer=101)]
    assert core.drag_endpoints_problem(
        386, 323, 386, 213, own_pid=77, infos=panel + _REAL_LAYOUT,
        pids=[10]) == ("", "")
    # 始点が Battery の画面の下に隠れている（以前のメイン画面の右下の始点）
    reason, who = core.drag_endpoints_problem(
        500, 600, 386, 213, own_pid=77, infos=_REAL_LAYOUT, pids=[10])
    assert reason == "source" and who == "Pro Tools", (reason, who)
    # セルの位置にほかのアプリの窓が重なっている
    covered = [_win("確認の画面", 88, 300, 150, 300, 200, layer=8)]
    reason, who = core.drag_endpoints_problem(
        386, 323, 386, 213, own_pid=77,
        infos=panel + covered + _REAL_LAYOUT, pids=[10])
    assert reason == "hidden" and who == "確認の画面", (reason, who)
    # セルの位置に窓が無い（画面の外など）。重なっている窓の名前としては読まない
    assert core.window_owner_label(None) == ""
    ok, who = core.host_window_at(5000, 5000, infos=_REAL_LAYOUT, pids=[10])
    assert not ok and who == "", who
    assert core.drag_endpoints_problem(
        386, 323, 5000, 5000, own_pid=77, infos=panel + _REAL_LAYOUT,
        pids=[10]) == ("no_window", "")
    print("PASS: 合成ドラッグの始点と終点を確かめる")


def test_permission_advice_covers_two_entries_with_the_same_name():
    """アクセシビリティの一覧に同じ名前が 2 つ並ぶときの案内があること。

    手元版と配布版、古い場所に残った項目は同じ名前で並び、読み上げでは
    見分けられない。両方オンにしても困らないので、そう案内する。
    """
    original = (core.accessibility_trusted, core.running_as_bundle,
                core.responsible_process_path)
    core.accessibility_trusted = lambda prompt=False: False
    core.running_as_bundle = lambda: True
    core.responsible_process_path = lambda: ""
    try:
        advice = core.accessibility_diagnosis()["advice"]
    finally:
        (core.accessibility_trusted, core.running_as_bundle,
         core.responsible_process_path) = original
    assert "同じ名前が 2 つ" in advice and "両方をオン" in advice, advice
    print("PASS: 同じ名前が 2 つ並ぶときの案内がある")


def test_unique_titles():
    """選択肢の題を重ねないこと（同じ題は項目が消えて、位置の対応がずれる）。"""
    assert core.unique_titles(["自動", "すべて", "Inst1"]) == ["自動", "すべて", "Inst1"]
    assert core.unique_titles(["Auto", "All", "Auto"]) == ["Auto", "All", "Auto (2)"]
    assert core.unique_titles(["A", "A", "A"]) == ["A", "A (2)", "A (3)"]
    # 番号を付けた題が、ほかの題と重ならないこと
    titles = core.unique_titles(["A", "A", "A (2)"])
    assert len(set(titles)) == 3, titles
    print("PASS: 選択肢の題を重ねない")


def test_drag_start_point_stays_on_screen():
    """ドラッグの始点はセルの近く、同じ画面の内側に置くこと。"""
    screens = [(0, 0, 1920, 1080)]
    assert core.drag_start_point(386, 213, screens) == (386, 323)
    # 下に余裕が無ければ上
    assert core.drag_start_point(386, 1000, screens) == (386, 890)
    # 上下とも無理なら右
    assert core.drag_start_point(386, 60, [(0, 0, 1920, 150)]) == (496, 60)
    # 2 つ目の画面のセル（左上原点で上にある画面）
    two = [(0, 0, 1440, 900), (0, -1080, 1920, 1080)]
    assert core.drag_start_point(500, -900, two) == (500, -790)
    print("PASS: ドラッグの始点はセルの近くの画面の中")


def test_parse_finder_location():
    """Finder の位置ともとの表示形式を読む（表示形式を戻すため）。"""
    assert core.parse_finder_location("120,340,list\n") == (120, 340, "list")
    assert core.parse_finder_location("120.0,340.6,") == (120, 340, "")
    assert core.parse_finder_location("120,340") == (120, 340, "")
    assert core.parse_finder_location("120,340,unknown") == (120, 340, "")
    assert core.parse_finder_location("") is None
    assert core.parse_finder_location("x,y,list") is None
    print("PASS: Finder の位置ともとの表示形式を読む")


def test_silent_mode_mutes_sounds():
    """BATTERY4_SILENT のときは音も読み上げも出さないこと（ビルドの起動確認用）。"""
    import subprocess
    calls = []
    orig_popen = subprocess.Popen
    orig_env = os.environ.get("BATTERY4_SILENT")
    subprocess.Popen = lambda *a, **k: calls.append(a)
    try:
        os.environ["BATTERY4_SILENT"] = "1"
        core.beep(core.SND_OK)
        core.vo_announce("テスト")
        assert calls == [], calls
    finally:
        subprocess.Popen = orig_popen
        if orig_env is None:
            os.environ.pop("BATTERY4_SILENT", None)
        else:
            os.environ["BATTERY4_SILENT"] = orig_env
    print("PASS: 静かにする設定で音も読み上げも出ない")


def run_all():
    tests = [
        test_scan_skips_hidden_and_appledouble,
        test_format_duration_carries_over,
        test_format_list_label_unknown_has_no_symbols,
        test_app_bundle_path,
        test_export_kit_does_not_duplicate_on_reexport,
        test_kit_folder_name_has_no_separator,
        test_window_at_follows_window_server_order,
        test_host_window_at_and_drag_endpoints,
        test_permission_advice_covers_two_entries_with_the_same_name,
        test_unique_titles,
        test_drag_start_point_stays_on_screen,
        test_parse_finder_location,
        test_silent_mode_mutes_sounds,
        test_default_settings_keys,
        test_cell_names_are_battery_notation,
        test_no_absolute_cell_coords,
        test_battery_windows_is_safe,
        test_sort_options_unique_and_labeled,
        test_sort_name_and_folder,
        test_sort_fav_and_recent,
        test_sort_random_keeps_members,
        test_sort_stat_cache_and_random_order,
        test_filter_case_insensitive,
        test_guess_kind_hint_words,
        test_extract_pack_from_splice_layout,
        test_list_packs_sorted_unique,
        test_filter_samples_and_conditions,
        test_filter_query_is_single_substring,
        test_kind_from_duration_and_resolve,
        test_filter_by_kind_uses_durations,
        test_parse_duration_and_format,
        test_read_duration_real_file,
        test_read_durations_retries_failures,
        test_sort_by_duration,
        test_pack_order_by_count,
        test_pack_order_by_time,
        test_accessibility_diagnosis,
        test_app_name_from_path,
        test_pack_candidates_narrow_with_other_conditions,
        test_search_result_summary_wording,
        test_format_list_label_tags,
        test_format_list_label_name_transform,
        test_settings_roundtrip,
        test_broken_settings_fallback,
        test_no_forbidden_symbols_in_ui_sources,
        test_no_fake_applescript_drag,
        test_export_kit,
        test_export_kit_reexport_from_kit_folder,
        test_parse_audio_info_short_format_names,
        test_parse_audio_info_marker_file,
        test_parse_audio_info_clean_and_float,
        test_converted_cache_name,
        test_normalize_real_file,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"FAIL: {t.__name__}: {e}")
        except Exception as e:
            print(f"ERROR: {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(tests)} テストが PASS")
    return passed, len(tests)


if __name__ == "__main__":
    run_all()
