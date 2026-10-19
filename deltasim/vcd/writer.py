"""VCD (Value Change Dump) writer.

Produces a VCD file that can be opened with GTKWave or any other
waveform viewer that supports the IEEE-1364 VCD format.

Design notes
------------
* Each signal gets a short ASCII identifier (!, ", #, …).
* Only 4-state values are needed in M1; we use '0'/'1' for 1-bit
  signals and 'b<binary>' for wider signals.
* The writer records only *changed* values at each timestamp to keep
  file sizes manageable.
"""

from __future__ import annotations

import os
from typing import Dict, IO, Optional

from deltasim.elab.elaborator import ElabDesign, SignalInfo


class VCDWriter:
    """Write a Value Change Dump file.

    Usage::

        with VCDWriter("out.vcd", design) as vcd:
            engine.vcd_writer = vcd
            engine.run(100)
    """

    def __init__(self, path: str, design: ElabDesign, timescale: str = "1ns"):
        self._path = path
        self._design = design
        self._timescale = timescale
        self._fh: Optional[IO[str]] = None
        self._ids: Dict[str, str] = {}
        self._prev: Dict[str, int] = {}
        self._last_time: int = -1
        self._open()

    def _open(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self._path)), exist_ok=True)
        self._fh = open(self._path, "w", encoding="ascii")
        self._write_header()

    def _write_header(self) -> None:
        fh = self._fh
        fh.write("$date\n    Created by deltasim\n$end\n")
        fh.write(f"$version deltasim 0.1.0 $end\n")
        fh.write(f"$timescale {self._timescale} $end\n")
        fh.write(f"$scope module {self._design.module_name} $end\n")

        # Assign VCD identifiers to signals
        ident_base = 33  # '!' in ASCII
        i = 0
        for name, info in self._design.signals.items():
            vcd_id = _int_to_id(ident_base + i)
            self._ids[name] = vcd_id
            w = info.width
            fh.write(f"$var wire {w} {vcd_id} {name} $end\n")
            i += 1

        fh.write("$upscope $end\n")
        fh.write("$enddefinitions $end\n")

    def record(self, time_ns: int, signals: Dict[str, int]) -> None:
        """Record all signal changes at *time_ns*."""
        fh = self._fh
        if fh is None:
            return

        changed_lines = []
        for name, vcd_id in self._ids.items():
            val = signals.get(name, 0)
            prev = self._prev.get(name)
            if prev != val:
                self._prev[name] = val
                w = self._design.signals[name].width
                changed_lines.append(_format_value(vcd_id, val, w))

        if not changed_lines:
            return

        if time_ns != self._last_time:
            fh.write(f"#{time_ns}\n")
            self._last_time = time_ns

        for line in changed_lines:
            fh.write(line)

    def record_initial(self, time_ns: int, signals: Dict[str, int]) -> None:
        """Write $dumpvars section with initial state."""
        fh = self._fh
        if fh is None:
            return
        fh.write("$dumpvars\n")
        for name, vcd_id in self._ids.items():
            val = signals.get(name, 0)
            w = self._design.signals[name].width
            fh.write(_format_value(vcd_id, val, w))
            self._prev[name] = val
        fh.write("$end\n")
        self._last_time = time_ns

    def close(self) -> None:
        if self._fh:
            self._fh.flush()
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "VCDWriter":
        return self

    def __exit__(self, *_) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _int_to_id(n: int) -> str:
    """Convert an integer to a short VCD identifier string."""
    # Use printable ASCII 33..126 (94 chars), then multi-char
    chars = [chr(c) for c in range(33, 127)]
    base = len(chars)
    result = []
    while True:
        result.append(chars[n % base])
        n //= base
        if n == 0:
            break
    return "".join(reversed(result))


def _format_value(vcd_id: str, val: int, width: int) -> str:
    """Format a signal value as VCD text."""
    if width == 1:
        return f"{val & 1}{vcd_id}\n"
    # Multi-bit: binary representation
    bits = bin(val & ((1 << width) - 1))[2:].zfill(width)
    return f"b{bits} {vcd_id}\n"
