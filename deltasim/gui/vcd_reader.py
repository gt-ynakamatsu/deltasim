"""VCD ファイルリーダー — 波形データを GUI 向けの JSON 形式に変換する。

GUI 仕様書 §2 §7 の要件:
- 信号の時刻と値の変化リストを返す
- 多ビット値は 2 進文字列（4 値 0/1/X/Z）として返す
- 大規模波形に備え時間範囲フィルタに対応

返却形式:
{
    "timescale": "1ns",
    "signals": {
        "<name>": {
            "width": int,
            "changes": [[time_ns, "value_str"], ...]
        }
    },
    "end_time": int
}
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class VCDReadError(Exception):
    pass


def read_vcd(
    path: str,
    *,
    t_start: int = 0,
    t_end: Optional[int] = None,
    signals: Optional[List[str]] = None,
) -> dict:
    """VCD ファイルを読み込んで信号変化データを返す。

    Parameters
    ----------
    path:
        VCD ファイルパス
    t_start:
        フィルタ開始時刻（ns）。この時刻以降のデータを返す。
    t_end:
        フィルタ終了時刻（ns）。None = 全範囲。
    signals:
        返す信号名リスト。None = 全信号。

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
        content = f.read()

    timescale, id_to_name, id_to_width, changes = _parse_vcd(content)

    # 信号フィルタ
    if signals is not None:
        signals_set = set(signals)
        id_to_name = {k: v for k, v in id_to_name.items() if v in signals_set}

    # 結果構築
    out_signals: Dict[str, dict] = {}
    end_time = 0

    for vcd_id, name in id_to_name.items():
        width = id_to_width.get(vcd_id, 1)
        raw_changes = changes.get(vcd_id, [])

        filtered = []
        for t, val in raw_changes:
            if t_end is not None and t > t_end:
                break
            if t >= t_start:
                filtered.append([t, val])
            end_time = max(end_time, t)

        out_signals[name] = {"width": width, "changes": filtered}

    # end_time を全変化から計算
    for vcd_id, raw_changes in changes.items():
        for t, _ in raw_changes:
            end_time = max(end_time, t)

    return {
        "timescale": timescale,
        "signals": out_signals,
        "end_time": end_time,
    }


# ---------------------------------------------------------------------------
# 内部パーサ
# ---------------------------------------------------------------------------

def _parse_vcd(content: str):
    """VCD テキストをパースして (timescale, id_to_name, id_to_width, changes) を返す。"""
    timescale = "1ns"
    id_to_name: Dict[str, str] = {}
    id_to_width: Dict[str, int] = {}
    changes: Dict[str, List[Tuple[int, str]]] = {}

    lines = content.splitlines()
    current_time = 0
    in_dumpvars = False
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i].strip()

        if not line or line == "$end":
            i += 1
            continue

        # --- $timescale ---
        if line.startswith("$timescale"):
            ts_parts = []
            if "$end" in line:
                inner = re.search(r"\$timescale\s+(.+?)\s+\$end", line)
                if inner:
                    timescale = inner.group(1).strip()
                i += 1
                continue
            i += 1
            while i < n and "$end" not in lines[i]:
                ts_parts.append(lines[i].strip())
                i += 1
            timescale = " ".join(ts_parts).strip()
            i += 1
            continue

        # --- $var ---
        if line.startswith("$var"):
            # $var wire 8 ! count $end
            m = re.match(
                r"\$var\s+\w+\s+(\d+)\s+(\S+)\s+(\S+)(?:\s+\[[\d:]+\])?\s+\$end",
                line,
            )
            if m:
                width = int(m.group(1))
                vcd_id = m.group(2)
                name = m.group(3)
                id_to_name[vcd_id] = name
                id_to_width[vcd_id] = width
                changes.setdefault(vcd_id, [])
            i += 1
            continue

        # --- $dumpvars ---
        if line.startswith("$dumpvars"):
            in_dumpvars = True
            i += 1
            continue

        # --- $enddefinitions ---
        if line.startswith("$enddefinitions"):
            i += 1
            continue

        # --- 時刻スタンプ ---
        if line.startswith("#"):
            try:
                current_time = int(line[1:])
            except ValueError:
                pass
            i += 1
            continue

        # --- 値変化 ---
        # 1ビット: 0!, 1!, x!, z!
        if len(line) >= 2 and line[0] in "01xzXZ" and not line.startswith("b"):
            val = line[0].lower()
            vcd_id = line[1:]
            if vcd_id in changes:
                changes[vcd_id].append((current_time, val))
            i += 1
            continue

        # 多ビット: b0101 !
        if line.startswith(("b", "B")):
            parts = line.split()
            if len(parts) >= 2:
                bin_str = parts[0][1:].lower()  # 先頭の 'b' を除く
                vcd_id = parts[1]
                if vcd_id in changes:
                    changes[vcd_id].append((current_time, bin_str))
            i += 1
            continue

        i += 1

    return timescale, id_to_name, id_to_width, changes
