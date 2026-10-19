"""VCD (Value Change Dump) writer.

M6 additions
------------
* $scope/$upscope: hierarchical scopes from dot-separated signal names.
  e.g. ``u_add.sum``  →  ``$scope module u_add ... $var wire N id sum ...``
* X/Z distinction: output 'x' for None and 'z' for Z_VALUE sentinel.

Design notes
------------
* Each signal gets a short ASCII identifier (!, ", #, …).
* Only changed values are emitted to keep file sizes manageable.
* VCD output is NOT subject to SignalChangeDB memory limits (§12.3).
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, IO, List, Optional

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
        self._prev: Dict = {}
        self._last_time: int = -1
        self._open()

    def _open(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._path, "w", encoding="ascii", newline="\n")
        self._write_header()

    def _write_header(self) -> None:
        fh = self._fh
        fh.write("$date\n    Created by deltasim\n$end\n")
        fh.write("$version deltasim 0.1.0 $end\n")
        fh.write(f"$timescale {self._timescale} $end\n")

        # Assign VCD identifiers
        ident_base = 33  # '!' in ASCII
        for i, name in enumerate(self._design.signals):
            self._ids[name] = _int_to_id(ident_base + i)

        # Emit hierarchy with $scope/$upscope (§M6)
        top_name = self._design.module_name
        fh.write(f"$scope module {top_name} $end\n")

        # Sort by hierarchy path so sibling / child signals are grouped
        sorted_names = sorted(
            self._design.signals.keys(),
            key=lambda n: (n.split(".")[:-1], n.split(".")[-1]),
        )

        current_scope: List[str] = []  # scope path stack (minus top)

        for name in sorted_names:
            info = self._design.signals[name]
            parts = name.split(".")
            sig_path = parts[:-1]   # hierarchy components (no local name)
            sig_local = parts[-1]   # local signal name within scope

            # Compute common prefix with current scope
            common = 0
            for a, b in zip(current_scope, sig_path):
                if a == b:
                    common += 1
                else:
                    break

            # Upscope out of current branches that differ
            for _ in range(len(current_scope) - common):
                fh.write("$upscope $end\n")

            # Scope into new sub-scopes
            for component in sig_path[common:]:
                fh.write(f"$scope module {component} $end\n")

            current_scope = sig_path

            vcd_id = self._ids[name]
            fh.write(f"$var wire {info.width} {vcd_id} {sig_local} $end\n")

        # Close remaining open scopes (excluding top)
        for _ in range(len(current_scope)):
            fh.write("$upscope $end\n")

        fh.write("$upscope $end\n")   # close top scope
        fh.write("$enddefinitions $end\n")

    def record(self, time_ns: int, signals: Dict[str, object]) -> None:
        """Record all signal changes at *time_ns*. Values may be None (X) or Z_VALUE."""
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

    def record_initial(self, time_ns: int, signals: Dict[str, object]) -> None:
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
    chars = [chr(c) for c in range(33, 127)]
    base = len(chars)
    result = []
    while True:
        result.append(chars[n % base])
        n //= base
        if n == 0:
            break
    return "".join(reversed(result))


def _format_value(vcd_id: str, val, width: int) -> str:
    """Format a signal value as VCD text (§12.4: 0/1/x/z 小文字統一)."""
    from deltasim.result import Z_VALUE, _ZValue
    if val is None:
        if width == 1:
            return f"x{vcd_id}\n"
        return f"b{'x' * width} {vcd_id}\n"
    if isinstance(val, _ZValue):
        if width == 1:
            return f"z{vcd_id}\n"
        return f"b{'z' * width} {vcd_id}\n"
    if width == 1:
        return f"{val & 1}{vcd_id}\n"
    bits = bin(val & ((1 << width) - 1))[2:].zfill(width)
    return f"b{bits} {vcd_id}\n"
