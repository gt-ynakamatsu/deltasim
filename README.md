# deltasim

SystemVerilog をフルスクラッチで扱う自作シミュレータ＋テストベンチ/テストシナリオ自動生成ツール。

商用シミュレータ（Questa/ModelSim など）や Verilator/Icarus Verilog には一切依存せず、  
Python のみで実装した **イベント駆動＋デルタサイクル** エンジンを搭載しています。

---

## アーキテクチャ概要

```
RTL (.sv)
   │
   ▼
┌──────────────────────────────────────────────────────────┐
│  Parser (deltasim/parser/)                                │
│  ・lexer.py     — 正規表現ベースのトークナイザ               │
│  ・parser.py    — 再帰降下パーサ → AST                    │
│  ・ast_nodes.py — dataclass で定義した全 AST ノード         │
└──────────────────────────────────────────────────────────┘
   │ Module AST
   ▼
┌──────────────────────────────────────────────────────────┐
│  Elaborator (deltasim/elab/elaborator.py)                 │
│  ・パラメータ解決（定数式を評価）                             │
│  ・信号テーブル構築（ビット幅・方向）                          │
│  ・プロセス分類 (SeqProc/CombProc/AssignProc/initial)      │
└──────────────────────────────────────────────────────────┘
   │ ElabDesign
   ▼
┌──────────────────────────────────────────────────────────┐
│  SimEngine (deltasim/sim/engine.py)                       │
│  ・イベント駆動：クロック/リセットエッジで SeqProc を起動       │
│  ・NBA キュー：ノンブロッキング代入を一括適用                  │
│  ・デルタサイクルループ：収束まで繰り返す                      │
│  ・initial ブロック：コルーチン (ジェネレータ) で並行実行       │
│  ・pre/post-posedge フック：テストランナーが刺激/判定を注入    │
└──────────────────────────────────────────────────────────┘
   │ SimResult + signal changes
   ├──────────────────────────────────────────────────────┐
   ▼                                                       ▼
┌──────────────────────────┐  ┌───────────────────────────┐
│  VCDWriter               │  │  TB / Test (deltasim/tb/) │
│  (deltasim/vcd/writer.py)│  │  ・generator.py — TB SV 生成│
│  GTKWave 対応 VCD 出力    │  │  ・scenario.py — YAML spec │
└──────────────────────────┘  │  ・runner.py   — テスト実行 │
                               └───────────────────────────┘
                                              │
                               ┌──────────────▼────────────┐
                               │  Report (deltasim/report/)│
                               │  ・terminal.py — 端末出力  │
                               │  ・junit.py    — JUnit XML │
                               └───────────────────────────┘
```

### データの流れ

1. **parse_file(path)** → `Module` AST
2. **elaborate(module, overrides)** → `ElabDesign`（信号テーブル・プロセスリスト）
3. **load_scenario(spec.yaml)** → `Scenario`（YAML スペック）
4. **TestRunner(design, scenario).run()** → `TestRunResult`（チェック結果）
5. **VCDWriter / JUnit XML** → ファイル出力

---

## インストール

```bash
pip install -e ".[dev]"
```

Python 3.10 以上が必要です。依存パッケージ: `click`, `pyyaml`

---

## 使い方

### CLI — シミュレーション実行

```bash
# 基本実行: 100 サイクル + VCD 出力
deltasim run examples/counter.sv --top counter --cycles 100 --vcd out.vcd

# パラメータ上書き
deltasim run examples/counter.sv --top counter --cycles 30 --param WIDTH=4

# 組み合わせ回路
deltasim run examples/mux2.sv --top mux2 --cycles 1
```

### CLI — テストベンチ自動生成

```bash
# TB SV + YAML スペックスケルトンを生成
deltasim gen examples/counter.sv --top counter

# 出力先ディレクトリ指定
deltasim gen examples/counter.sv --top counter --out-dir ./gen
```

### CLI — テスト実行・判定

```bash
# YAML スペックでテスト実行
deltasim test examples/counter.sv --spec examples/counter_spec.yaml

# JUnit XML も出力（CI 連携用）
deltasim test examples/counter.sv --spec examples/counter_spec.yaml --junit report.xml

# VCD も出力
deltasim test examples/counter.sv --spec examples/counter_spec.yaml \
    --junit report.xml --vcd out.vcd
```

終了コード: `0` = 全 PASS、`1` = 1 件以上 FAIL

### Python API

```python
from deltasim.parser.parser import parse_file
from deltasim.elab.elaborator import elaborate
from deltasim.tb.scenario import load_scenario
from deltasim.tb.runner import TestRunner
from deltasim.report.junit import write_junit

mod    = parse_file("examples/counter.sv")
design = elaborate(mod)
spec   = load_scenario("examples/counter_spec.yaml")
result = TestRunner(design, spec).run()
write_junit(result, "report.xml")

print(f"PASS: {result.n_pass}  FAIL: {result.n_fail}")
```

---

## YAML スペック形式

```yaml
version: 1
top: counter           # モジュール名（省略可）

clock:
  signal: clk          # 省略時: ポート名から自動検出
  period_ns: 10        # デフォルト: 10 (100 MHz)

reset:
  signal: rst_n        # 省略時: 自動検出
  active_low: true     # デフォルト: true
  duration_cycles: 10  # デフォルト: 10

sim:
  cycles: 30
  seed: 42             # ランダム刺激の乱数シード
  vcd: out.vcd         # VCD 出力パス（省略可）

# 入力刺激
inputs:
  # 固定値 (1 サイクル)
  - at: 15
    set: {d: 1}

  # 固定値 (サイクル範囲)
  - at: [15, 20]
    set: {d: 1}

  # シード付きランダム (一様分布)
  - at: [11, 30]
    random:
      data: {range: [0, 255]}

  # 制約付きランダム (dist 重み付き分布)
  - at: [11, 30]
    random:
      flag:
        dist:
          - {value: 0, weight: 10}
          - {value: 1, weight: 1}

# 期待値チェック
expected:
  # 完全一致チェック
  - at: 11
    check: {count: 1}

  # Python 式チェック (_cycle = 現在サイクル 1-indexed)
  - at: [11, 30]
    expr: "count == _cycle - 10"

# Python 参照モデル（省略可）
model:
  file: counter_model.py
  class: CounterModel
  params: {width: 8}
```

---

## クロック/リセット自動検出

ポート名パターンで自動認識します。

**クロック**: `clk`, `clock`, `sys_clk`, `*clk`, `*clock`

**アクティブ Low リセット**: `rst_n`, `resetn`, `reset_n`, `rst_b`, `nrst`, など

**アクティブ High リセット**: `rst`, `reset`, `arst`, `srst`

---

## サンプル RTL

| ファイル | 説明 |
|----------|------|
| `examples/counter.sv` | N ビット自由カウンタ（非同期 Active-Low リセット） |
| `examples/shift_reg.sv` | 4 ビット SIPO シフトレジスタ（同期リセット） |
| `examples/mux2.sv` | 2 入力 MUX（純組み合わせ回路） |

| スペックファイル | 内容 |
|----------------|------|
| `examples/counter_spec.yaml` | counter のテストスペック（固定期待値 + 式チェック + 参照モデル） |
| `examples/mux2_spec.yaml` | mux2 の組み合わせ回路テスト |
| `examples/shift_reg_spec.yaml` | shift_reg のシフトパターン検証 |

| 参照モデル | 内容 |
|-----------|------|
| `examples/counter_model.py` | CounterModel — counter.sv の Python 模倣実装 |

---

## テスト

```bash
pytest                      # 全テスト実行
pytest -v                   # 詳細表示
pytest --cov=deltasim       # カバレッジ付き
```

---

## 対応 SystemVerilog サブセット

### 対応済み（マイルストーン 1 + 2）

**合成可能 RTL**
- `module` / ANSI ポート / `parameter` / `localparam`
- `logic` / `wire` / `reg`（内部宣言含む）
- `assign`（継続代入）
- `always_ff @(posedge/negedge …)` / `always_comb` / `always @(…)` / `always`
- ブロッキング代入（`=`）/ ノンブロッキング代入（`<=`）
- `if/else` / `case/casex/casez`
- 全算術・ビット・論理・比較・シフト演算子
- 三項演算子 `? :`
- ビット選択 `a[i]` / パーツ選択 `a[hi:lo]`
- 連接 `{a,b,c}` / リプリケーション `{n{a}}`
- 特殊リテラル `'0` `'1` `'x` `'z`
- `$display` / `$finish` / `$stop`（フォーマット文字列対応）

**TB / 手続き型**
- `initial` ブロック（コルーチンで並行実行）
- `#n` 遅延（任意タイミングのイベント）
- `forever` / `repeat(n)` / `for` / `while`
- `@(posedge/negedge signal)` 待機

**VCD 出力**: GTKWave 対応 IEEE-1364 形式

**テストベンチ自動生成**: `deltasim gen`

**テスト実行・判定**:
- YAML スペックによる固定刺激 / シード付きランダム / 制約付きランダム（range/dist）
- Python 参照モデルとのサイクル毎比較
- JUnit XML 出力（CI 連携用）
- ターミナルカラー出力

### 未対応（今後のマイルストーン）

| 機能 | 予定マイルストーン |
|------|----------------|
| 階層インスタンス | **M3 完了** |
| `typedef enum` | **M3 完了** |
| `generate for/if` | **M3 完了** |
| `always` 内 `for`/`while` | **M3 完了** |
| X/Z 4 値論理（基本） | **M3 完了**（`'x`/`'z` リテラル伝搬 + VCD 出力） |
| `struct packed` | M4 |
| `interface` / `modport` / クラス / SVA | 未定 |
| 4 値論理の完全なビット単位 X 伝搬 | M4 |
| `package` / `import` | **M3 完了**（基本的なインポート） |
| 配列（1D アンパック）| M4 |
| 符号付き整数（`integer` 型の負値） | M4 |
| 対話デバッグ（force / examine / bp） | M5 |
| LLM 連携 | M6 |

---

## ライセンス

MIT License
