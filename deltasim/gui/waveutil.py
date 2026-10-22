"""波形ユーティリティ — エッジ検索・符号付き変換（サーバー側の純粋関数）

テスト可能な形に独立させ、フロントエンドと共通のロジックを持つ。
"""

from __future__ import annotations

from typing import List, Optional, Tuple


def find_edge(
    changes: List[Tuple[int, str]],
    from_ns: int,
    direction: str,
    edge: str,
    width: int = 1,
) -> Optional[int]:
    """指定信号の次/前のエッジ時刻を返す（見つからなければ None）。

    Parameters
    ----------
    changes:
        [(time_ns, value_str), ...] 昇順
    from_ns:
        検索の基準時刻。
        direction="next" は from_ns より大きい最初のエッジを返す。
        direction="prev" は from_ns より小さい最後のエッジを返す。
    direction: "next" | "prev"
    edge: "any" | "rising" | "falling"
        1 ビット信号: rising = L→H、falling = H→L。
        多ビット信号: rising/falling は any と同じ（値の変化）。
    width:
        信号のビット幅。1 ビットの場合のみ rising/falling が有効。
    """

    def _match(prev_v: str, curr_v: str) -> bool:
        if prev_v == curr_v:
            return False
        if edge == "any" or width != 1:
            return True
        p = (prev_v or "0").lower()
        c = (curr_v or "0").lower()
        if edge == "rising":
            return p != "1" and c == "1"
        if edge == "falling":
            return p == "1" and c != "1"
        return True

    if not changes:
        return None

    if direction == "next":
        # from_ns の直前の値を先に決定する
        prev_v = changes[0][1]
        for t, v in changes:
            if t <= from_ns:
                prev_v = v
                continue
            if _match(prev_v, v):
                return t
            prev_v = v
        return None

    # direction == "prev"
    prev_v = changes[0][1]
    best: Optional[int] = None
    for t, v in changes:
        if t >= from_ns:
            break
        if _match(prev_v, v):
            best = t
        prev_v = v
    return best


def signed_value(bin_str: str, width: int) -> Optional[int]:
    """バイナリ文字列を符号付き整数に変換する（2 の補数）。

    x/z を含む場合は None を返す（表示時はそのまま文字列で表示）。
    """
    s = (bin_str or "").lower()
    if not s or "x" in s or "z" in s:
        return None
    try:
        n = int(s, 2)
    except ValueError:
        return None
    if width <= 0:
        return n
    if n >= (1 << (width - 1)):
        n -= (1 << width)
    return n


def fmt_value(bin_str: str, width: int, radix: str) -> str:
    """バイナリ文字列を指定基数の表示文字列に変換する。

    radix: "bin" | "hex" | "unsigned" | "signed"
    x/z を含む場合はそのまま返す。
    """
    s = (bin_str or "").lower()
    # x/z を含む場合はそのまま大文字表示
    if s in ("x", "z"):
        return s.upper()
    if "x" in s or "z" in s:
        return bin_str  # 混在バイナリはそのまま

    if radix == "bin":
        return "b" + s.zfill(width)

    try:
        n = int(s, 2)
    except ValueError:
        return bin_str

    if radix == "hex":
        hex_digits = max(1, (width + 3) // 4)
        return "0x" + format(n, f"0{hex_digits}X")
    if radix == "unsigned":
        return str(n)
    if radix == "signed":
        sv = signed_value(s, width)
        return str(sv) if sv is not None else bin_str
    return bin_str
