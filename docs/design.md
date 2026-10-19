# deltasim 設計ドキュメント

## 確定した YAML スペック書式（マイルストーン 2 で確定）

```yaml
version: 1
top: counter           # モジュール名（省略可、CLI --top より優先度低）

clock:
  signal: clk          # 省略時: ポート名パターンから自動検出
  period_ns: 10        # デフォルト: 10 ns（100 MHz）

reset:
  signal: rst_n        # 省略時: 自動検出
  active_low: true     # デフォルト: true（_n/_b 接尾辞から判定）
  duration_cycles: 10  # デフォルト: 10

sim:
  cycles: 30           # シミュレーションサイクル数
  seed: 42             # ランダム刺激の乱数シード（再現性確保）
  vcd: out.vcd         # VCD 出力パス（省略可）

# 入力刺激
inputs:
  # 固定値（単一サイクル）
  - at: 15
    set: {d: 1}

  # 固定値（サイクル範囲 [start, end] inclusive）
  - at: [15, 20]
    set: {d: 1}

  # 一様ランダム
  - at: [11, 30]
    random:
      data: {range: [0, 255]}

  # 重み付き分布（dist）
  - at: [11, 30]
    random:
      flag:
        dist:
          - {value: 0, weight: 10}
          - {value: 1, weight: 1}

# 期待値チェック
expected:
  # 完全一致
  - at: 11
    check: {count: 1}

  # Python 式（_cycle = 1-indexed 現在サイクル、信号名を変数として使用可）
  - at: [11, 30]
    expr: "count == _cycle - 10"

# Python 参照モデル（省略可）
model:
  file: counter_model.py      # スペックファイルからの相対パス
  class: CounterModel
  params: {width: 8}
```

---

## 今後のマイルストーン案

仕様書 v0.1 の記述に基づき、マイルストーン 2 以降の開発計画を示します。

---

## マイルストーン 1（完了）: 最小試作

- 合成可能 SV サブセットのパース
- イベント駆動＋デルタサイクルシミュレータ
- VCD 出力
- CLI: `deltasim run / parse`

---

## マイルストーン 2（完了）: テストベンチ/シナリオ自動生成

仕様書 §4.2 / §4.3 対応。

### 実装済み

| 機能 | 詳細 |
|------|------|
| `deltasim gen` | TB SV + YAML スペックスケルトン自動生成 |
| `deltasim test` | YAML スペック駆動のシミュレーション + 判定 |
| YAML スペック | 上記の確定書式（clock/reset/sim/inputs/expected/model） |
| 固定刺激 | 指定サイクルに固定値を注入 |
| ランダム刺激 | シード付き一様ランダム (range) + 重み付き分布 (dist) |
| 参照モデル比較 | Python クラスの `step(inputs)` と毎サイクル比較 |
| JUnit XML | CI 連携用 XML レポート出力 |
| ターミナル出力 | カラー PASS/FAIL サマリー |
| `initial` ブロック実行 | `#n` / `forever` / `repeat` / `@(edge)` をコルーチンで実行 |
| `$display` フォーマット | `%d`, `%h`, `%b`, `%0d` 等の基本フォーマット文字列 |

---

## マイルストーン 3（完了）: 階層インスタンス + X/Z 4 値論理 + typedef enum + generate

仕様書 §2 の拡張。

### 実装済み

| 機能 | 詳細 |
|------|------|
| **階層インスタンス** | `ModName [#(params)] instName (ports);` — サブモジュールをフラット展開 |
| シグナル名前変換 | ポートはそのまま、内部信号は `inst.signal` に prefixed |
| パラメータ上書き | `#(.P(v))` 名前付き / `#(v)` 位置指定 |
| **generate for/if** | コンパイル時ループ・条件選択でモジュール項目を展開 |
| **typedef enum** | `typedef enum logic [N:0] { VAL=... } type_t;` — 幅推論 + enum 定数を params に追加 |
| typedef struct packed | 総幅を計算（フィールドアクセスは M4） |
| package / import | `import pkg::*;` — catalog にある package から params/typedefs をコピー |
| **always 内 for/while** | `_Executor` でループを動的実行（最大 100K 反復） |
| **X/Z 4 値論理** | `'x`/`'z` リテラルが `None` として伝搬、VCD に `x` 出力 |
| **initial ブロック `notify_edge` バグ修正** | `self._waiting` 上書きバグを修正 |
| **pre-posedge comb 再評価** | 入力変化後に always_comb を再評価してから posedge を処理 |

### 階層インスタンス設計

```
parent module                    child module (adder)
────────────                     ────────────────────
signals: a, b, op, result        signals: a(in), b(in), sum(out), carry_out(out)
         add_sum, add_carry

InstDecl: adder #(.WIDTH=8) u_add (.a(a), .b(b), .sum(add_sum), .carry_out(add_carry))

Flattened:
  mapping = {a→a, b→b, sum→add_sum, carry_out→add_carry}
  renamed assign: assign {add_carry, add_sum} = a + b   ← adder のプロセスを親に flatten
```

### 完了条件

- pytest 87 passed (0 failed)
- `deltasim gen adder.sv alu.sv --top alu` → TB SV + YAML 生成
- `deltasim test adder.sv alu.sv --spec alu_spec.yaml` → 6/6 PASS
- `deltasim test seq_detect.sv --spec seq_detect_spec.yaml` → 6/6 PASS（typedef enum FSM）
- `deltasim run counter_tb.sv counter.sv --top counter_tb --reset-cycles 0` → "PASS: count = 10"

---

仕様書 §2 の拡張。

| 機能 | 詳細 |
|------|------|
| 階層インスタンス | サブモジュールのインライン展開、ポートマッピング |
| 4 値論理 | `Value4` 型で X/Z を厳密に伝搬 |
| `generate` / `genvar` | パラメトリック生成ブロック |
| `struct` / `enum` | パックト構造体・列挙型 |
| `-f files.f` | ファイルリスト対応（複数ファイル） |

**階層インスタンスの実装方針**:
- エラボレーターにモジュールカタログを持ち、インスタンス宣言を見つけたら再帰的に展開
- 信号名は `instance.signal` 形式で階層プレフィックスを付与

---

## マイルストーン 4: 対話デバッグ

仕様書 §4.5 対応。

| 機能 | 詳細 |
|------|------|
| `deltasim run -i` | 対話デバッグモードで起動 |
| `run N` | N サイクル進める |
| `force sig value` | 信号を強制値に固定 |
| `noforce sig` | force を解除 |
| `examine sig` | 信号値を表示 |
| `bp cycle N` | 時刻ブレークポイント |
| `bp change sig` | 信号変化ブレークポイント |
| `restart` | シミュレーションをリセット |
| `do file.tcl` | Tcl スクリプト実行（Questa 互換サブセット） |

Tcl サブセット: `set`, `if`, `for`, `proc`（変数・ループ・関数）程度。

---

## マイルストーン 5: ローカル LLM 連携

仕様書 §4.6 対応。

| 機能 | 詳細 |
|------|------|
| `deltasim ask "…"` | 自然言語コマンドを変換・確認して実行 |
| シナリオ生成補助 | RTL コメントから YAML を下書き（人が確認前提） |
| 失敗解析補助 | 不一致前後の信号を要約して LLM に渡し、原因候補を提示 |
| 設定 | `simconfig.yaml` で Ollama 等の接続先・モデル名を指定 |

LLM なしでも全機能が動くこと（LLM は任意機能）。

---

## 技術的負債とリスク

| 項目 | 内容 | 対策 |
|------|------|------|
| パーサ完全性 | 商用 SV は複雑。未対応構文は `ParseError` | M3 以降で対応範囲を拡大 |
| 4 値論理 (X/Z) | 現在は 0 として扱う | `Value4` 型を engine/evaluator に追加（M3） |
| 階層インスタンス | M1-M2 では単一モジュールのみ | Elaborator にインライン展開パスを追加（M3） |
| パフォーマンス | Python は遅い | コアを C++/Rust に移植（pyO3/pybind11）（将来） |
| initial ブロック並行性 | 現在は単純コルーチン | 本格的な 4 領域スケジューラ（将来） |

---

## ディレクトリ構成

```
deltasim/
├── deltasim/           # メインパッケージ
│   ├── cli.py          # CLI (click): run / gen / test / parse
│   ├── parser/         # レキサー・パーサー・AST ノード
│   ├── elab/           # エラボレーター（パラメータ解決・信号テーブル）
│   ├── sim/            # シミュレーションエンジン（NBA / デルタ / initial）
│   ├── tb/             # テストベンチ生成 / YAML spec / テストランナー
│   ├── report/         # ターミナル出力 / JUnit XML
│   └── vcd/            # VCD ライター
├── examples/           # サンプル RTL / spec / 参照モデル
├── tests/              # pytest テスト
├── docs/               # 設計ドキュメント（本ファイル）
├── README.md
└── pyproject.toml
```
