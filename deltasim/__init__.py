"""deltasim: SystemVerilog フルスクラッチシミュレータ"""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__: str = version("deltasim")
except PackageNotFoundError:
    # 開発環境で pip install -e . 前に実行したときのフォールバック
    __version__ = "0.0.0.dev"
