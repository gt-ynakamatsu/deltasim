"""VCD ファイルリーダー — 大規模VCD対応（信号1万本・値変化100万件）

API 概要:
  read_vcd_index(path) → 信号定義のみを高速取得（ヘッダのみ読む）
  read_vcd_range(path, signals, t_start, t_end) → 指定信号・時間範囲の値変化を取得
  read_vcd(path, ...) → 小規模用（後方互換）

大規模VCDの設計:
  - read_vcd_index: `$enddefinitions` に到達したら即停止 → ヘッダのみ O(header_size)
  - read_vcd_range: ファイルを先頭から読んで指定信号・時間範囲だけフィルタ
    - t_start が大きい場合でも現状は先頭から読む（将来: byte index による seekable 設計）
    - 読み込みは Python の line iterator で行い、GC 圧を抑える

返却形式:
  index: {"timescale": str, "signals": {name: {"width": int, "vcd_id": str}}, "end_time": int}
  range: {"timescale": str, "signals": {name: {"width": int, "changes": [[t,v],...]}} , "end_time": int}
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple


class VCDReadError(Exception):
    pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_vcd_index(path: str) -> dict:
    """VCD のヘッダだけ読んで信号定義一覧を返す（高速）。

    Returns
    -------
    {
        "timescale": str,
        "signals": {name: {"width": int, "vcd_id": str}},
        "end_time": 0  # index では計算しない
    }
    """
    path = str(path)
    if not Path(path).exists():
        raise VCDReadError(f"VCD ファイルが見つかりません: {path!r}")

    timescale = "1ns"
    id_to_name: Dict[str, str] = {}
    id_to_width: Dict[str, int] = {}

    with open(path, encoding="ascii", errors="replace") as f:
        buf = ""
        for raw_line in f:
            buf += raw_line
            # $enddefinitions が来たら終了
            if "$enddefinitions" in buf:
                _parse_header_buf(buf, timescale_out := [timescale],
                                  id_to_name, id_to_width)
                timescale = timescale_out[0]
                break
        else:
            # $enddefinitions が無い場合もパース
            _parse_header_buf(buf, ts_out := [timescale], id_to_name, id_to_width)
            timescale = ts_out[0]

    signals = {
        name: {"width": id_to_width.get(vcd_id, 1), "vcd_id": vcd_id}
        for vcd_id, name in id_to_name.items()
    }
    return {"timescale": timescale, "signals": signals, "end_time": 0}


def read_vcd_range(
    path: str,
    *,
    signal_names: Optional[List[str]] = None,
    t_start: int = 0,
    t_end: Optional[int] = None,
) -> dict:
    """指定信号・時間範囲の値変化を返す。

    Parameters
    ----------
    path:
        VCD ファイルパス
    signal_names:
        返す信号名リスト。None = 全信号。
    t_start:
        フィルタ開始時刻（ns）
    t_end:
        フィルタ終了時刻（ns）。None = 全範囲。

    Returns
    -------
    {
        "timescale": str,
        "signals": {name: {"width": int, "changes": [[t, v], ...]}},
        "end_time": int
    }
    """
    path = str(path)
    if not Path(path).exists():
        raise VCDReadError(f"VCD ファイルが見つかりません: {path!r}")

    with open(path, encoding="ascii", errors="replace") as f:
        timescale, id_to_name, id_to_width, changes, end_time = _parse_vcd_full(
            f, signal_names=signal_names, t_start=t_start, t_end=t_end
        )

    out_signals: Dict[str, dict] = {}
    for vcd_id, name in id_to_name.items():
        if signal_names is not None and name not in signal_names:
            continue
        out_signals[name] = {
            "width": id_to_width.get(vcd_id, 1),
            "changes": changes.get(vcd_id, []),
        }

    return {"timescale": timescale, "signals": out_signals, "end_time": end_time}


def read_vcd(
    path: str,
    *,
    t_start: int = 0,
    t_end: Optional[int] = None,
    signals: Optional[List[str]] = None,
) -> dict:
    """後方互換 API（小規模用）。read_vcd_range に委譲する。"""
    return read_vcd_range(path, signal_names=signals, t_start=t_start, t_end=t_end)


# ---------------------------------------------------------------------------
# 内部パーサ
# ---------------------------------------------------------------------------

def _parse_header_buf(
    buf: str,
    timescale_out: list,
    id_to_name: Dict[str, str],
    id_to_width: Dict[str, int],
) -> None:
    """バッファ文字列からヘッダ情報を抽出する。"""
    # $timescale ... $end
    m = re.search(r"\$timescale\s+(.+?)\s+\$end", buf, re.DOTALL)
    if m:
        timescale_out[0] = m.group(1).strip()

    # $var wire/reg WIDTH ID NAME ... $end
    for m in re.finditer(
        r"\$var\s+\w+\s+(\d+)\s+(\S+)\s+(\S+)(?:\s+\[[\d:]+\])?\s+\$end", buf
    ):
        width = int(m.group(1))
        vcd_id = m.group(2)
        name = m.group(3)
        if vcd_id not in id_to_name:
            id_to_name[vcd_id] = name
            id_to_width[vcd_id] = width


def _parse_vcd_full(
    lines: Iterable[str],
    *,
    signal_names: Optional[List[str]],
    t_start: int,
    t_end: Optional[int],
) -> Tuple[str, Dict[str, str], Dict[str, int], Dict[str, List], int]:
    """VCD ストリームを完全パースして (timescale, id_to_name, id_to_width, changes, end_time) を返す。

    signal_names が指定されている場合、そのシグナルのみ変化データを収集する（高速化）。
    """
    timescale = "1ns"
    id_to_name: Dict[str, str] = {}
    id_to_width: Dict[str, int] = {}
    changes: Dict[str, List] = {}

    # signal_names を ID セットに変換するために一度名前→IDマップが必要
    # → ヘッダを先に収集し、その後 ID セットを決定する

    in_header = True
    header_buf = []
    current_time = 0
    end_time = 0

    # フィルタする signal_name セット（ヘッダ後に確定）
    filter_ids: Optional[Set[str]] = None

    for raw_line in lines:
        line = raw_line.strip()

        # --- ヘッダ収集フェーズ ---
        if in_header:
            header_buf.append(raw_line)
            if "$enddefinitions" in raw_line:
                in_header = False
                buf = "".join(header_buf)
                ts_out = [timescale]
                _parse_header_buf(buf, ts_out, id_to_name, id_to_width)
                timescale = ts_out[0]
                # フィルタ ID セットを確定
                if signal_names is not None:
                    sig_set = set(signal_names)
                    filter_ids = {
                        vid for vid, nm in id_to_name.items() if nm in sig_set
                    }
                    # 取得する信号の changes だけ初期化
                    for vid in filter_ids:
                        changes[vid] = []
                else:
                    for vid in id_to_name:
                        changes[vid] = []
            continue

        # --- 値変化フェーズ ---
        if not line or line == "$end":
            continue

        # 時刻スタンプ
        if line.startswith("#"):
            try:
                t = int(line[1:])
                end_time = max(end_time, t)
                current_time = t
            except ValueError:
                pass
            continue

        # t_end 超過 → 早期終了
        if t_end is not None and current_time > t_end:
            break

        # t_start 未満はスキップ（変化データは収集しない）
        in_range = current_time >= t_start

        # 1ビット値変化: 0! 1! x! z!
        if len(line) >= 2 and line[0] in "01xzXZ" and not line.startswith("b"):
            vcd_id = line[1:]
            if filter_ids is None:
                lst = changes.get(vcd_id)
            else:
                lst = changes.get(vcd_id) if vcd_id in filter_ids else None
            if lst is not None and in_range:
                lst.append([current_time, line[0].lower()])
            continue

        # 多ビット値変化: b0101 !
        if line.startswith(("b", "B")):
            parts = line.split()
            if len(parts) >= 2:
                bin_str = parts[0][1:].lower()
                vcd_id = parts[1]
                if filter_ids is None:
                    lst = changes.get(vcd_id)
                else:
                    lst = changes.get(vcd_id) if vcd_id in filter_ids else None
                if lst is not None and in_range:
                    lst.append([current_time, bin_str])
            continue

    return timescale, id_to_name, id_to_width, changes, end_time
