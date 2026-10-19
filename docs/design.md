# deltasim 設計ドキュメント

## 今後のマイルストーン案

仕様書 v0.1 の記述に基づき、マイルストーン 1 以降の開発計画を示します。

---

## マイルストーン 1（現在）: 最小試作

**完了条件**:
- 合成可能 SV サブセットをパース
- イベント駆動＋デルタサイクルのシミュレーションコア
- VCD 出力（GTKWave 対応）
- CLI: `deltasim run`
- pytest 通過

**未対応**:
- 階層インスタンス、struct/enum、generate、for ループ

---

## マイルストーン 2: テストベンチ/シナリオ自動生成

仕様書 §4.2 / §4.3 対応。

### 主な実装項目

| 機能 | 詳細 |
|------|------|
| `deltasim gen` コマンド | ポート解析から SV テストベンチ雛形を自動生成 |
| 仕様ファイル (YAML) | クロック・リセット・テスト刺激・期待値を記述 |
| 固定パターン入力 | YAML に記述したサイクルごとの値を自動駆動 |
| シード指定ランダム | `--seed` でシードを固定して再現性を確保 |
| 制約付きランダム | `range` / `dist` / 簡単な信号間条件 |

**仕様ファイル (YAML) 書式案**:

```yaml
clock:
  signal: clk
  period_ns: 10

reset:
  signal: rst_n
  active_low: true
  duration_cycles: 10

inputs:
  - cycle: 15
    values:
      d: 1
  - cycle: 20
    values:
      d: 0

expected:
  - cycle: 19
    signal: q
    value: 0b1111
```

### 生成物

- `<top>_tb.sv` — SystemVerilog テストベンチ（ユーザーが編集・再利用可能）
- `<top>_scenario.yaml` — テストシナリオ雛形

---

## マイルストーン 3: 期待値判定と Python 参照モデル

仕様書 §4.4 対応。

| 機能 | 詳細 |
|------|------|
| YAML 期待値との比較 | サイクル単位で期待値と実測値を照合 |
| Python 参照モデル連携 | `--model model.py` で指定したクラスの出力と比較 |
| 不一致レポート | 時刻・信号名・期待値・実測値を表示 |
| JUnit XML 出力 | CI 連携用（`--junit out.xml`） |

**参照モデルインタフェース**:

```python
class CounterModel:
    def __init__(self, width=8):
        self.count = 0
        self.width = width

    def step(self, rst_n: int) -> dict:
        if not rst_n:
            self.count = 0
        else:
            self.count = (self.count + 1) & ((1 << self.width) - 1)
        return {"count": self.count}
```

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
| パーサ完全性 | 商用 SV は複雑。未対応構文は `ParseError` | M2 以降で対応範囲を拡大 |
| 4 値論理 (X/Z) | 現在は 0 として扱う | `Value4` 型を engine/evaluator に追加 |
| 階層インスタンス | M1 では単一モジュールのみ | Elaborator にインライン展開パスを追加 |
| パフォーマンス | Python は遅い。数千ゲート規模が目安 | コアを C++/Rust に移植（pyO3/pybind11） |
| 移植性 | Windows の行末・パス | テストで `pathlib.Path` を使用 |

---

## ディレクトリ構成

```
deltasim/
├── deltasim/           # メインパッケージ
│   ├── cli.py          # CLI (click)
│   ├── parser/         # レキサー・パーサー・AST
│   ├── elab/           # エラボレーター
│   ├── sim/            # シミュレーションエンジン
│   └── vcd/            # VCD ライター
├── examples/           # サンプル RTL
├── tests/              # pytest テスト
├── docs/               # 設計ドキュメント（本ファイル）
├── README.md
└── pyproject.toml
```
