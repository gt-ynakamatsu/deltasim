"""Python 参照モデル: counter.sv の動作を模倣する。

インタフェース
-----------
  model = CounterModel(width=8)
  model.reset()                    # 任意: リセット状態に戻す
  outputs = model.step(inputs)     # inputs = {'clk': ..., 'rst_n': ...}
                                   # outputs = {'count': <int>}
"""


class CounterModel:
    """非同期アクティブ Low リセット付き N ビット自由カウンタ。"""

    def __init__(self, width: int = 8):
        self.width = int(width)
        self._mask = (1 << self.width) - 1
        self.count = 0

    def reset(self) -> None:
        self.count = 0

    def step(self, inputs: dict) -> dict:
        """posedge clk 時の動作を計算して期待出力を返す。

        Parameters
        ----------
        inputs:
            DUT の入力ポート値の辞書。rst_n を含む。

        Returns
        -------
        dict
            期待される出力ポート値の辞書。count を含む。
        """
        rst_n = inputs.get("rst_n", 1)
        if not rst_n:
            self.count = 0
        else:
            self.count = (self.count + 1) & self._mask
        return {"count": self.count}
