# YAML スペックファイル書式

`deltasim test --spec spec.yaml` で使う YAML ファイルの書式リファレンスです。

---

## 全フィールド一覧

```yaml
version: 1               # スペック書式バージョン（現在は 1 固定）
top: counter             # モジュール名（省略時: SV ファイルから自動検出）

# クロック設定
clock:
  signal: clk            # クロック信号名（省略時: 自動検出）
  period_ns: 10          # クロック周期 ns（デフォルト: 10）

# リセット設定
reset:
  signal: rst_n          # リセット信号名（省略時: 自動検出）
  active_low: true       # active-low リセット（デフォルト: true）
  duration_cycles: 10    # リセット期間サイクル（デフォルト: 10）

# シミュレーション設定
sim:
  cycles: 50             # シミュレーションサイクル数（デフォルト: 100）
  seed: 42               # 乱数シード（デフォルト: 0）
  vcd: out.vcd           # VCD 出力パス（省略時: 出力しない）

# ケース一覧（§13.2 マルチケース書式）
cases:
  - name: basic          # ケース名（必須・一意）
    skip: false          # スキップするか（デフォルト: false）
    skip_reason: ""      # スキップ理由（skip: true のとき表示）
    seed: 0              # このケースのシード（省略時: 上位の seed）
    cycles: 30           # このケースのサイクル数（省略時: 上位の cycles）
    clock:               # クロック上書き（省略時: 上位の clock）
      signal: clk
      period_ns: 20
    reset:               # リセット上書き（省略時: 上位の reset）
      signal: rst_n
      active_low: true
      duration_cycles: 5
    stimulus:            # inputs の別名（どちらでも可）
      - at: 5
        set:
          d: 1
        offset_ns: 0     # サブサイクルオフセット（省略時: 0）
    expected:            # expect の別名（どちらでも可）
      - at: 6
        check:
          q: 1
        offset_ns: 0
    model:               # 参照モデル（省略可）
      file: counter_model.py
      class: CounterModel
      params:
        width: 8
```

---

## `at` フィールド

`at` にはチェック・刺激を適用するサイクル番号を指定します（1-indexed）。

```yaml
# 1 サイクル
at: 5

# サイクル範囲（5〜10 の全サイクル）
at: [5, 10]
```

---

## `set` と `random`

`stimulus` の各アイテムには `set`（固定値）または `random`（ランダム）を指定します。

### `set` — 固定値

```yaml
- at: 5
  set:
    d: 1
    load: 1
```

### `random` — ランダム値

```yaml
# 一様分布 (range)
- at: [5, 20]
  random:
    data:
      range: [0, 255]

# 重み付き分布 (dist)
- at: [5, 20]
  random:
    flag:
      dist:
        - {value: 0, weight: 10}
        - {value: 1, weight: 1}
```

---

## `check` と `expr`

`expected` の各アイテムには `check`（完全一致）または `expr`（Python 式）を指定します。

### `check` — 完全一致

```yaml
- at: 11
  check:
    count: 1
```

### `expr` — 式チェック

```yaml
# SV 式サブセット（§18.4）を使用
# _cycle: 現在サイクル番号（1-indexed）
- at: [11, 30]
  expr: "count == _cycle - 10"
```

---

## `offset_ns` — サブサイクルタイミング（§15）

クロックエッジ後の細かいタイミングを指定します。

```yaml
# 35ns のとき（posedge@30ns + offset 5ns）
- at: 4
  offset_ns: 5
  check:
    q: 1

# 38ns でリセットアサート
- at: 4
  offset_ns: 8
  set:
    rst: 1
```

制約: `0 ≤ offset_ns < period_ns`（違反は `E_OFFSET_RANGE` エラー）。

---

## 型の一覧

| フィールド | 型 | デフォルト | 説明 |
|-----------|-----|-----------|------|
| `version` | `int` | 1 | スペック書式バージョン |
| `top` | `string?` | 自動検出 | トップモジュール名 |
| `clock.signal` | `string?` | 自動検出 | クロック信号名 |
| `clock.period_ns` | `int` | 10 | クロック周期 ns |
| `reset.signal` | `string?` | 自動検出 | リセット信号名 |
| `reset.active_low` | `bool` | `true` | active-low か |
| `reset.duration_cycles` | `int` | 10 | リセット期間サイクル |
| `sim.cycles` | `int` | 100 | シミュレーションサイクル数 |
| `sim.seed` | `int` | 0 | 乱数シード |
| `sim.vcd` | `string?` | なし | VCD 出力パス |
| `cases[].name` | `string` | 必須 | ケース名（一意） |
| `cases[].skip` | `bool` | `false` | スキップするか |
| `cases[].skip_reason` | `string` | `""` | スキップ理由 |
| `at` | `int` or `[int, int]` | 必須 | サイクル番号 |
| `offset_ns` | `int` | 0 | サブサイクルオフセット ns |

---

## 参照モデルのインタフェース

```python
class MyModel:
    def reset(self) -> None:
        """シミュレーション開始時に呼ばれる（省略可）。"""
        self.count = 0

    def step(self, inputs: dict) -> dict:
        """1 サイクルごとに呼ばれる。
        inputs: {"信号名": 値} の辞書（現サイクルのシミュレータ出力）
        return: {"信号名": 期待値} の辞書
        """
        self.count += 1
        return {"count": self.count}
```

`step()` が返した値は `check` と同様に比較されます。
`reset()` は省略可能です（省略時は呼ばれません）。

---

## 後方互換：単一ケース形式（§13.2）

`cases:` キーが無い場合は、ファイル全体を1つのケースとして扱います（旧形式）。

```yaml
version: 1
top: counter
clock:
  signal: clk
  period_ns: 10
reset:
  signal: rst_n
  active_low: true
  duration_cycles: 10
sim:
  cycles: 30
inputs:             # stimulus の別名
  - at: 15
    set: {d: 1}
expected:           # expect の別名
  - at: 20
    check: {q: 1}
```
