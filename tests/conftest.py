"""pytest 共通フィクスチャ"""
import os
import pytest

EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "examples")


@pytest.fixture
def counter_sv():
    return os.path.join(EXAMPLES_DIR, "counter.sv")


@pytest.fixture
def shift_reg_sv():
    return os.path.join(EXAMPLES_DIR, "shift_reg.sv")


@pytest.fixture
def mux2_sv():
    return os.path.join(EXAMPLES_DIR, "mux2.sv")
