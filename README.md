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
┌──────────────────────────────────────────────────┐
│  Parser (deltasim/parser/)                        │
│  ・lexer.py   — 正規表現ベースのトークナイザ         │
│  ・parser.py  — 再帰降下パーサ → AST              │
│  ・ast_nodes.py — データクラスで定義した AST ノード  │
└──────────────────────────────────────────────────┘
   │ Module AST
   ▼
┌──────────────────────────────────────────────────┐
│  Elaborator (deltasim/elab/elaborator.py)         │
│  ・パラメータ解決（定数式を評価）                     │
│  ・信号テーブル構築（ビット幅・方向）                  │
│  ・プロセス分類 (SeqProc/CombProc/AssignProc)      │
└──────────────────────────────────────────────────┘
   │ ElabDesign
   ▼
┌──────────────────────────────────────────────────┐
│  SimEngine (deltasim/sim/engine.py)               │
│  ・イベント駆動：クロック/リセットエッジで             │
│    SeqProc (always_ff) を起動                      │
│  ・NBA キュー：ノンブロッキング代入を一括適用          │
│  ・デルタサイクルループ：AssignProc / CombProc が   │
│    収束するまで繰り返す                               │
│  ・信号履歴を history[] に保存                       │
└──────────────────────────────────────────────────┘
   │ SimResult + signal changes
   ▼
┌──────────────────────────────────────────────────┐
│  VCDWriter (deltasim/vcd/writer.py)               │
│  ・GTKWave で開ける IEEE-1364 VCD ファイルを出力     │
└──────────────────────────────────────────────────┘
```

### データの流れ

1. **parse_file(path)** → `Module` AST
2. **elaborate(module, overrides)** → `ElabDesign`（信号テーブル・プロセスリスト）
3. **SimEngine(design).run(cycles)** → `SimResult`（サイクルごとの信号スナップショット）
4. **VCDWriter.record(time, signals)** → `.vcd` ファイルに差分を書き出す

---

## インストール

```bash
pip install -e ".[dev]"
```

Python 3.10 以上が必要です。

---

## 使い方

### CLI

```bash
# 基本実行: 100 サイクルシミュレートして VCD を出力
deltasim run examples/counter.sv --top counter --cycles 100 --vcd out.vcd

# パラメータ上書き
deltasim run examples/counter.sv --top counter --cycles 30 --param WIDTH=4

# 組み合わせ回路（クロックなし）
deltasim run examples/mux2.sv --top mux2 --cycles 1

# 詳細ログ ($display 出力を表示)
deltasim run examples/counter.sv --top counter --cycles 50 --verbose

# AST 確認 (デバッグ用)
deltasim parse examples/counter.sv
```

主なオプション:

| オプション | デフォルト | 説明 |
|------------|-----------|------|
| `--top` | 最初のモジュール | トップモジュール名 |
| `--cycles` | 100 | シミュレーションサイクル数 |
| `--vcd` | なし | VCD 出力ファイルパス |
| `--reset-cycles` | 10 | リセット保持サイクル数 |
| `--half-period` | 5 | クロック半周期 (ns) |
| `--param NAME=VALUE` | なし | パラメータ上書き（複数指定可） |
| `--clk` | 自動検出 | クロック信号名 |
| `--rst` | 自動検出 | リセット信号名 |
| `--rst-active-high` | — | リセットをアクティブ High として扱う |

### Python API

```python
from deltasim.parser.parser import parse_file
from deltasim.elab.elaborator import elaborate
from deltasim.sim.engine import SimEngine
from deltasim.vcd.writer import VCDWriter

# パース → エラボレート → シミュレート
mod    = parse_file("examples/counter.sv")
design = elaborate(mod, param_overrides={"WIDTH": 4})
eng    = SimEngine(design, reset_cycles=5)

with VCDWriter("out.vcd", design) as vcd:
    vcd.record_initial(0, eng.signals)
    eng.vcd_writer = vcd
    result = eng.run(cycles=30)

print(result.get_value("count", 10))  # cycle 10 の count 値
```

---

## クロック/リセット自動検出

ポート名パターンで自動認識します。

**クロック**: `clk`, `clock`, `sys_clk`, `*clk`, `*clock`

**アクティブ Low リセット**: `rst_n`, `resetn`, `reset_n`, `rst_b`, `nrst`, など

**アクティブ High リセット**: `rst`, `reset`, `arst`, `srst`

認識されない場合は `--clk` / `--rst` オプションで明示指定してください。

---

## サンプル RTL

| ファイル | 説明 |
|----------|------|
| `examples/counter.sv` | N ビット自由カウンタ（非同期 Active-Low リセット） |
| `examples/shift_reg.sv` | 4 ビット SIPO シフトレジスタ（同期リセット） |
| `examples/mux2.sv` | 2 入力 MUX（純組み合わせ回路） |

---

## テスト

```bash
pytest
# カバレッジ付き
pytest --cov=deltasim
```

---

## 対応 SystemVerilog サブセット

### 対応済み（マイルストーン 1）

- `module` / `endmodule`（ANSI ポートスタイル）
- `input` / `output` / `inout`
- `logic` / `wire` / `reg`（内部宣言含む）
- `parameter` / `localparam`（パラメータ付きモジュール）
- `assign`（継続代入）
- `always_ff @(posedge/negedge …)` / `always_comb` / `always @(…)`
- ブロッキング代入（`=`）/ ノンブロッキング代入（`<=`）
- `if` / `else`
- `case` / `casex` / `casez` / `endcase`
- ビット演算子: `&`, `|`, `^`, `~`, リダクション
- 算術演算子: `+`, `-`, `*`, `/`, `%`
- 比較演算子: `==`, `!=`, `===`, `!==`, `<`, `>`, `<=`, `>=`
- 論理演算子: `&&`, `||`, `!`
- シフト: `<<`, `>>`, `<<<`, `>>>`
- 三項演算子: `? :`
- ビット選択 `a[i]` / パーツ選択 `a[hi:lo]`
- 連接 `{a, b, c}` / リプリケーション `{n{a}}`
- 特殊リテラル: `'0`, `'1`, `'x`, `'z`
- `$display` / `$finish` / `$stop`（基本のみ）
- VCD 出力（GTKWave 対応）

### 未対応（今後のマイルストーン）

- `struct` / `union` / `enum` / `typedef`
- `interface` / `modport`
- SystemVerilog Assertions (SVA)
- クラス（OOP）
- 階層インスタンス（サブモジュール）
- `generate` / `genvar`
- `for` / `while` ループ（always 内）
- `initial` ブロックの実行
- 4 値論理の厳密な X 伝搬
- `$monitor` / `$strobe`
- ファンクション / タスク
- VHDL

---

## ライセンス

MIT License
