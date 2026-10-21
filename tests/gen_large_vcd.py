"""大規模 VCD 生成・性能計測スクリプト

生成仕様:
  - 信号数: N_SIGNALS（デフォルト 10000）
  - 値変化総数: N_CHANGES（デフォルト 1000000）
  - 時間範囲: 0 〜 END_TIME

実行:
    python3 tests/gen_large_vcd.py            # デフォルト(10k sigs, 1M changes)
    python3 tests/gen_large_vcd.py --mini      # 小規模 (1000 sigs, 100k changes)
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def gen_vcd(path: str, n_signals: int, n_changes: int, end_time: int = 0) -> None:
    """大規模 VCD ファイルを生成する。"""
    rng = random.Random(42)
    if end_time == 0:
        # 1変化あたり平均 1ns として end_time を決める
        end_time = max(n_changes * 2, 1000)

    with open(path, "w", encoding="ascii") as f:
        f.write("$date\n    Synthetic large VCD for performance test\n$end\n")
        f.write("$version deltasim_gentest $end\n")
        f.write("$timescale 1ns $end\n")
        f.write(f"$scope module top $end\n")

        # 信号定義
        ids = []
        for i in range(n_signals):
            vcd_id = _int_to_id(i)
            width = rng.choice([1, 1, 1, 4, 8, 8, 16])
            name = f"sig_{i}"
            f.write(f"$var wire {width} {vcd_id} {name} $end\n")
            ids.append((vcd_id, width))

        f.write("$upscope $end\n")
        f.write("$enddefinitions $end\n")

        # $dumpvars
        f.write("$dumpvars\n")
        for vcd_id, width in ids:
            if width == 1:
                f.write(f"0{vcd_id}\n")
            else:
                f.write(f"b{'0'*width} {vcd_id}\n")
        f.write("$end\n")

        # 値変化を生成（時刻をランダムに選んで変化を追加）
        times = sorted(rng.randint(1, end_time) for _ in range(n_changes))
        prev_time = -1

        for t in times:
            sig_idx = rng.randrange(n_signals)
            vcd_id, width = ids[sig_idx]

            if t != prev_time:
                f.write(f"#{t}\n")
                prev_time = t

            if width == 1:
                val = rng.randint(0, 1)
                f.write(f"{val}{vcd_id}\n")
            else:
                bits = bin(rng.randint(0, (1 << width) - 1))[2:].zfill(width)
                f.write(f"b{bits} {vcd_id}\n")

        f.write(f"#{end_time}\n")


def _int_to_id(n: int) -> str:
    chars = [chr(c) for c in range(33, 127)]
    base = len(chars)
    result = []
    while True:
        result.append(chars[n % base])
        n //= base
        if n == 0:
            break
    return "".join(reversed(result))


def measure(path: str, n_signals: int) -> dict:
    from deltasim.gui.vcd_reader import read_vcd_index, read_vcd_range

    # 1. index 取得（ヘッダのみ）
    t0 = time.monotonic()
    idx = read_vcd_index(path)
    index_sec = time.monotonic() - t0

    # 2. 先頭20信号の全範囲値変化取得
    sig_names = list(idx["signals"].keys())[:20]
    t0 = time.monotonic()
    rng = read_vcd_range(path, signal_names=sig_names, t_start=0, t_end=None)
    range20_sec = time.monotonic() - t0

    # 3. 全信号の全範囲値変化取得（最大100）
    sig_names_100 = list(idx["signals"].keys())[:100]
    t0 = time.monotonic()
    rng100 = read_vcd_range(path, signal_names=sig_names_100, t_start=0, t_end=None)
    range100_sec = time.monotonic() - t0

    # 変化件数を集計
    total_changes = sum(len(v["changes"]) for v in rng["signals"].values())
    total_changes_100 = sum(len(v["changes"]) for v in rng100["signals"].values())

    file_size = os.path.getsize(path)

    return {
        "file_size_mb": round(file_size / 1024 / 1024, 2),
        "n_signals_in_file": len(idx["signals"]),
        "end_time_ns": rng["end_time"],
        "index_sec": round(index_sec, 3),
        "range20_sec": round(range20_sec, 3),
        "range20_changes": total_changes,
        "range100_sec": round(range100_sec, 3),
        "range100_changes": total_changes_100,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mini", action="store_true", help="小規模テスト (1k sigs, 100k changes)")
    parser.add_argument("--out", default="/tmp/deltasim_large_test.vcd")
    args = parser.parse_args()

    if args.mini:
        n_signals, n_changes = 1000, 100_000
    else:
        n_signals, n_changes = 10_000, 1_000_000

    print(f"VCD 生成中: {n_signals} 信号, {n_changes} 変化 → {args.out}")
    t0 = time.monotonic()
    gen_vcd(args.out, n_signals, n_changes)
    gen_sec = time.monotonic() - t0
    print(f"  生成時間: {gen_sec:.2f}s  ファイルサイズ: {os.path.getsize(args.out)/1024/1024:.1f} MB")

    print("性能計測中…")
    m = measure(args.out, n_signals)
    print(f"\n=== 計測結果 ===")
    print(f"  ファイルサイズ: {m['file_size_mb']} MB")
    print(f"  信号数: {m['n_signals_in_file']}")
    print(f"  終端時刻: {m['end_time_ns']} ns")
    print(f"  index 取得 (ヘッダのみ): {m['index_sec']} s")
    print(f"  range 取得 (20信号 全範囲, {m['range20_changes']} 変化): {m['range20_sec']} s")
    print(f"  range 取得 (100信号 全範囲, {m['range100_changes']} 変化): {m['range100_sec']} s")

    return m


if __name__ == "__main__":
    main()
