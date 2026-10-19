"""EvalContext: bundles all evaluation-time metadata for the expression evaluator.

Introduced in M4 to cleanly add signed arithmetic, struct field access,
unpacked arrays, and X/Z bit-level tracking without changing the signature
of every helper function.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class StructFieldEntry:
    name: str
    hi: int    # MSB index within the parent signal
    lo: int    # LSB index
    width: int


@dataclass
class EvalContext:
    """Evaluation context passed down through expression evaluation.

    All fields are optional (default = empty / disabled) so that code
    not yet updated to pass an EvalContext continues to work.
    """
    # signed[signal_name] = True → sign-extend when reading
    signs: Dict[str, bool] = field(default_factory=dict)

    # struct_fields[type_name] = [StructFieldEntry, ...]
    struct_fields: Dict[str, List[StructFieldEntry]] = field(default_factory=dict)

    # signal_types[signal_name] = type_name
    signal_types: Dict[str, str] = field(default_factory=dict)

    # arrays[array_name] = list of element values (Optional[int])
    arrays: Dict[str, List[Optional[int]]] = field(default_factory=dict)

    # arrays_elem_width[array_name] = element bit-width
    arrays_elem_width: Dict[str, int] = field(default_factory=dict)

    # x_bits[signal_name] = bitmask of X bits (M4: per-bit X tracking)
    x_bits: Dict[str, int] = field(default_factory=dict)


_EMPTY_CTX = EvalContext()
