# deltasim 公開 API 仕様 (docs/api.md)

> **注意**: このドキュメントは GUI 仕様書 v0.1 §2「インターフェース契約」に忠実に基づきます。  
> 独自拡張は行いません。変更時は仕様担当に相談のうえ両係へ周知してください。  
> `docs/api.md` は契約の内容を忠実に記述し、独自拡張しません。

---

## 概要

```python
import deltasim.api as ds
```

CLI で可能なことはすべて API でも可能です（CLI は API のラッパー）。

---

## §2.1 公開関数

### `load(files_or_design, top, params) → DesignInfo`

RTL ファイルを解析・エラボレートし設計オブジェクトを返します。

| 引数 | 型 | 説明 |
|------|----|------|
| `files_or_design` | `list[str\|Path]` または `DesignInfo` | SV ファイルパスのリスト、または既にロード済みの DesignInfo |
| `top` | `str \| None` | トップモジュール名 (省略時は最初のモジュール) |
| `params` | `dict[str, int] \| None` | パラメータ上書き |

**戻り値** `DesignInfo`:

```json
{
  "schema_version": "2",
  "status": "PASS",
  "module": "counter",
  "ports": [
    {"name": "clk",   "direction": "input",  "width": 1, "signed": false},
    {"name": "rst_n", "direction": "input",  "width": 1, "signed": false},
    {"name": "count", "direction": "output", "width": 8, "signed": false}
  ],
  "params": {"WIDTH": 8},
  "signals": [
    {"name": "clk",   "width": 1, "direction": "input",  "signed": false},
    {"name": "count", "width": 8, "direction": "output", "signed": false}
  ],
  "hierarchy": {
    "name": "counter", "module": "counter",
    "signals": ["clk", "rst_n", "count"],
    "children": []
  },
  "errors": []
}
```

---

### `gen(files_or_design, top, params, out_dir, tb_path, spec_path) → dict`

テストベンチ SV と YAML スペックスケルトンを生成します。

- `out_dir` が `None` かつ `tb_path` / `spec_path` が `None` の場合:  
  **文字列を返します** → `{'tb_sv': str, 'spec_yaml': str}`
- `out_dir` を指定した場合:  
  **ファイルを書き込みます** → `{'tb_path': str, 'spec_path': str}`

---

### `test(files_or_design, spec, *, top, params, seed, vcd, on_progress, cancel) → TestResult`

YAML スペックに従ってシミュレーションを実行し、結果を返します。

| 引数 | 型 | 説明 |
|------|----|------|
| `files_or_design` | `list[str\|Path]` または `DesignInfo` | RTL または設計オブジェクト |
| `spec` | `str \| Path` | YAML スペックファイル |
| `top` | `str \| None` | トップモジュール |
| `params` | `dict \| None` | パラメータ上書き |
| `seed` | `int \| None` | 乱数シード上書き |
| `vcd` | `str \| None` | VCD 出力パス |
| `on_progress` | `callable \| None` | 進捗コールバック (§2.4) |
| `cancel` | `CancelToken \| None` | キャンセルトークン |

**戻り値** `TestResult` (§2.2 参照)

---

### `run(files_or_design, *, top, params, cycles, vcd, reset_cycles, half_period, keep_changes, on_progress, cancel) → RunResult`

スペックなしでシミュレーションを実行します。

`keep_changes=True` を指定すると信号変化が記録され `RunResult.get_signal_changes()` で取得できます。

**戻り値** `RunResult`:

```json
{
  "schema_version": "2",
  "module": "counter",
  "status": "PASS",
  "cycles": 100,
  "time_ns": 1000,
  "cancelled": false,
  "elapsed_ms": 12.3,
  "vcd_path": "out.vcd",
  "log": [],
  "errors": []
}
```

---

### `test_events(files, spec, ...) → Generator[dict]`

`test()` と同じですが、進捗イベントをジェネレータとして yield します。

```python
for ev in ds.test_events(["counter.sv"], "spec.yaml"):
    print(ev["type"], ev.get("phase"))
```

---

## §2.2 結果 JSON 構造

`test()` の戻り値と `deltasim test --json out.json` の出力は **同一構造** です。

```json
{
  "schema_version": "2",
  "module": "counter",
  "status": "PASS",
  "seed": 42,
  "elapsed_ms": 12.3,
  "cancelled": false,
  "log": [],
  "checks": [
    {
      "name": "count@cycle11[spec]",
      "status": "PASS",
      "signal": "count",
      "cycle": 11,
      "time_ns": 110,
      "expected": "00000001",
      "actual": "00000001",
      "source": "spec",
      "seed": 42,
      "elapsed_ms": 0.5,
      "message": null
    }
  ],
  "errors": []
}
```

### `status` の値

| 値 | 意味 |
|----|------|
| `"PASS"` | 全チェック合格 |
| `"FAIL"` | 1 件以上のチェック不合格 |
| `"ERROR"` | パース/エラボ/シミュエラー |
| `"SKIP"` | スキップ (将来拡張用) |

### CLI でのJSON出力

```bash
deltasim test counter.sv --spec counter_spec.yaml --json result.json
deltasim test counter.sv --spec counter_spec.yaml --json-events  # JSON Lines
```

---

## §2.3 構造化エラー

```json
{
  "file": "counter.sv",
  "line": 5,
  "column": 3,
  "severity": "error",
  "message": "expected ';', got '?'",
  "code": "E001"
}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `file` | `str` | ファイルパス (空文字 = 不明) |
| `line` | `int` | 行番号 (0 = 不明) |
| `column` | `int` | 列番号 (0 = 不明) |
| `severity` | `"error" \| "warning"` | 深刻度 |
| `message` | `str` | エラーメッセージ |
| `code` | `str` | エラーコード (例: `"E001"`) |

---

## §2.4 進捗イベント

`on_progress` コールバックには以下のイベント dict が渡されます。  
`deltasim test --json-events` で JSON Lines として標準出力に出力されます。

### `start`

```json
{"type": "start", "phase": "parse", "timestamp_ms": 1000}
```

### `progress`

```json
{
  "type": "progress",
  "phase": "sim",
  "cycle": 15,
  "total_cycles": 30,
  "time_ns": 150,
  "timestamp_ms": 1005
}
```

`phase` は `"parse"` → `"elab"` → `"sim"` → `"check"` の順。

### `case_done`

```json
{
  "type": "case_done",
  "name": "count@cycle11[spec]",
  "status": "PASS",
  "signal": "count",
  "expected": "00000001",
  "actual": "00000001",
  "timestamp_ms": 1010
}
```

### `finish`

```json
{
  "type": "finish",
  "status": "PASS",
  "elapsed_ms": 50.2,
  "cancelled": false,
  "timestamp_ms": 1015
}
```

### キャンセル

```python
from deltasim.result import CancelToken
token = CancelToken()
result = ds.test(..., cancel=token)
# 別スレッドで token.cancel() を呼ぶ
# → result.cancelled == True、途中結果を含む
```

---

## §2.5 対話デバッグ Simulator

```python
sim = ds.Simulator(["counter.sv"], reset_cycles=10)
```

または `DesignInfo` を渡すことも可能:

```python
di = ds.load(["counter.sv"])
sim = ds.Simulator(di)
```

### メソッド一覧

| メソッド | 戻り値 | 説明 |
|---------|--------|------|
| `step(n=1)` | `SimSnapshot` | n サイクル進める |
| `step_delta()` | `SimSnapshot` | 1 デルタサイクル評価 |
| `run_until(time_ns)` | `SimSnapshot` | 指定時刻まで実行 |
| `get(signal)` | `int \| None` | 信号値を取得 (None = X/Z) |
| `set(signal, value)` | — | 信号を設定 (1 クロックのみ) |
| `force(signal, value)` | — | 信号を強制固定 |
| `release(signal)` | — | force を解除 |
| `list_signals()` | `list[dict]` | 全信号を階層パス付きで返す |
| `add_breakpoint(expr)` | `int` | Python 式ブレークポイント追加 |
| `remove_breakpoint(id)` | — | ブレークポイント削除 |
| `list_breakpoints()` | `list[dict]` | ブレークポイント一覧 |
| `reset()` | — | シミュレーションを初期状態に |
| `snapshot()` | `dict` | 現在の信号状態辞書を返す |
| `get_signal_changes(sig, start, end)` | `list[SignalChange]` | 変化リスト取得 |

### プロパティ

| プロパティ | 型 | 説明 |
|-----------|-----|------|
| `time_ns` | `int` | 現在のシミュレーション時刻 (ns) |
| `cycle` | `int` | 現在のクロックサイクル数 |

### 使用例

```python
sim = ds.Simulator(["counter.sv"], reset_cycles=2)

# ブレークポイントを設定してラン
bp = sim.add_breakpoint("count == 10")
snap = sim.step(100)  # count==10 でブレーク

print(snap.time_ns, snap.breakpoint_hit)
sim.remove_breakpoint(bp)

# 信号一覧（階層パス付き）
for s in sim.list_signals():
    print(f"{s['path']}: {s['value']}")

# 強制固定
sim.force("rst_n", 0)
sim.step(3)          # リセット中
sim.release("rst_n")
```

### `SimSnapshot` 構造

```json
{
  "schema_version": "2",
  "time_ns": 110,
  "signals": {"clk": 1, "rst_n": 1, "count": 10},
  "changes": [["count", 10]],
  "breakpoint_hit": 0
}
```

### CLI デバッグモード

```bash
deltasim sim counter.sv --top counter --reset-cycles 2
```

プロンプト例:

```
sim> step 10
t=100ns  cycle=10
sim> get count
count = 10  (0xA)  (00001010)
sim> bp count == 15
bp [0] 追加: count == 15
sim> step 100
[bp 0 hit]  t=150ns
sim> signals
  clk     1b (input)  = 1
  rst_n   1b (input)  = 1
  count   8b (output) = 00001111
```

---

## §2.6 信号変化 API

`run(keep_changes=True)` または `Simulator(keep_changes=True)` で変化を記録します。

```python
# run() で記録
result = ds.run(["counter.sv"], cycles=100, keep_changes=True)
changes = result.get_signal_changes("count", start_ns=50, end_ns=100)

# Simulator で記録
sim = ds.Simulator(["counter.sv"], keep_changes=True)
sim.step(20)
changes = sim.get_signal_changes("count")
```

**戻り値** `list[SignalChange]`:

```json
[
  {"time_ns": 55, "value": "00000001"},
  {"time_ns": 65, "value": "00000010"}
]
```

大規模波形は `start_ns` / `end_ns` で範囲を指定して取得できます。

---

## §2.7 値の表現 (4 値文字列)

| 幅 | 例 |
|----|----|
| 1 ビット | `"0"` / `"1"` / `"x"` / `"z"` |
| N ビット | `"01xz10"` (MSB 先頭、長さ = N) |

- `x` = 不定 (Unknown)
- `z` = ハイインピーダンス
- 表示基数 (2/8/10/16進、符号付き) への変換は GUI 側で行います

変換ヘルパー:

```python
from deltasim.result import val_to_4state, val_from_4state

val_to_4state(5, 8)     # → "00000101"
val_to_4state(None, 8)  # → "xxxxxxxx"
val_from_4state("00000101")  # → 5
val_from_4state("0x01xxxx")  # → None  (X ビットあり)
```

---

## §2.8 GUI エントリポイント (M6 以降)

```bash
pip install deltasim[gui]
deltasim gui
```

GUI は FastAPI で公開 API を包む薄い層です。  
コア (`deltasim` 単体) は FastAPI に依存しません。

---

## §12 API 詳細決定（2026-10-19 確定）

以下は仕様担当が §12 として確定した事項です。これらが本 API 仕様の根拠です。

---

### §12.1 SKIP ステータスの発生条件

| 条件 | 挙動 |
|------|------|
| YAML に `skip: true` | status=SKIP, log に skip_reason |
| `skip_reason` フィールド | 任意（空可） |
| キャンセル時に未実行のケース | status=SKIP, message="cancelled" |
| 参照モデルが見つからない等の不備 | status=ERROR（SKIP ではない） |
| `--case` で絞り込まれたケース | 結果に含めない |

```yaml
# YAML に skip 指定する例:
skip: true
skip_reason: "環境依存テスト — CI では無効"
```

```python
# キャンセル前に token.cancel() → status=SKIP
result = ds.test(files, spec, cancel=token)
assert result.status == "SKIP"
```

### §12.2 case_done の粒度

`case_done` イベントはテストケース（シナリオ）単位で **1 回だけ** 発行されます。
個々のチェックの失敗はそのケース結果の `checks` リストに入ります。

```json
{
  "type": "case_done",
  "name": "counter",
  "status": "FAIL",
  "elapsed_ms": 12.3
}
```

### §12.3 信号変化 DB のメモリ制御

| 設定 | 内容 |
|------|------|
| 既定の記録対象 | トップポート ＋ 仕様ファイルで参照された信号 |
| `--record-all` / `record_all=True` | 全信号を記録 |
| メモリ上限 | 512 MB（既定）|
| `--wave-mem N` / `wave_mem_mb=N` | MB 単位で上限変更 |
| 上限超過時 | 記録停止 ＋ warning イベント ＋ `truncated_at`（停止時刻 ns）|
| VCD 出力 | **上限の影響を受けず** 全サイクル出力 |

`truncated_at` は `TestResult.to_dict()` の `truncated_at` フィールドに出力されます。

### §12.4 4 値の文字列表現（小文字統一）

| 値 | 文字列 |
|----|--------|
| 0 | `"0"` |
| 1 | `"1"` |
| X (unknown) | `"x"` |
| Z (high-impedance) | `"z"` |

- 多ビット: `"01xz10"` (MSB 先頭, 長さ = N)
- **X と Z を区別します**（Z は `"z"` であり `"x"` ではありません）
- 大文字は使用しません（すべて小文字）

### §12.5 elapsed_ms の定義

> **§13.1 により更新（2026-10-19）**

| フィールド | 意味 |
|-----------|------|
| `elapsed_ms` | 全体の実時間 (= `summary.elapsed_ms`, 非推奨) |
| `summary.elapsed_ms` | 全体の実時間（推奨フィールド） |
| `cases[].elapsed_ms` | **そのケース（シナリオ）の実時間**（§13.1 の確定定義）|

```json
{
  "elapsed_ms": 50.3,
  "summary": {"elapsed_ms": 50.3},
  "cases": [
    {"name": "basic",   "elapsed_ms": 12.3, ...},
    {"name": "advanced","elapsed_ms": 15.1, ...}
  ]
}
```

トップレベルの `elapsed_ms` は後方互換のため残しますが、**非推奨**です。
新しいコードは `summary.elapsed_ms` または `cases[].elapsed_ms` を使用してください。

---

## §13 追加決定（2026-10-19 15:35 確定）

### §13.1 elapsed_ms の意味（§12.5 を更新）

上記「§12.5 elapsed_ms の定義」を参照してください。

### §13.2 YAML マルチケース書式

トップレベルに `cases:` リストを置き、各要素にケースの設定を記述します。

#### 新書式（`cases:` あり）

```yaml
version: 1
top: counter

# ファイル共通の設定（ケース側で上書き可）
clock:
  signal: clk
  period_ns: 10
reset:
  signal: rst_n
  active_low: true
  duration_cycles: 10
sim:
  cycles: 30
  seed: 42

cases:
  - name: basic              # 必須・一意（重複は E001 エラー）
    expected:
      - at: 11
        check: {count: 1}

  - name: overflow
    cycles: 260              # sim.cycles の上書き
    seed: 100                # sim.seed の上書き
    expected:
      - at: 258
        check: {count: 255}

  - name: skip_example
    skip: true               # §12.1: このケースをスキップ
    skip_reason: "CI対象外"

  - name: custom_clock
    clock:                   # clock の上書き
      period_ns: 20
    reset:                   # reset の上書き
      duration_cycles: 5
    inputs:
      - at: 5
        set: {d: 1}
    expected:
      - at: 6
        check: {q: 1}
```

各ケースの要素:

| フィールド | 必須 | 説明 |
|-----------|------|------|
| `name` | ✅ | ケース名（一意、重複は ScenarioError） |
| `skip` | — | `true` でスキップ |
| `skip_reason` | — | スキップ理由（任意） |
| `seed` | — | `sim.seed` の上書き |
| `cycles` | — | `sim.cycles` の上書き |
| `clock` | — | clock 設定の上書き |
| `reset` | — | reset 設定の上書き |
| `inputs` / `stimulus` | — | 刺激（空の場合はトップレベルを使用） |
| `expected` / `expect` | — | 期待値（空の場合はトップレベルを使用） |
| `model` | — | 参照モデルの上書き |

#### 旧書式（`cases:` なし、後方互換）

`cases:` キーが無い場合、ファイル名の語幹を `name` とする 1 ケースとして扱います。

```yaml
# counter_spec.yaml → name = "counter_spec"
version: 1
top: counter
inputs: [...]
expected: [...]
```

### §13.2 マルチケース対応の `test()` 戻り値

```json
{
  "schema_version": "2",
  "status": "FAIL",
  "elapsed_ms": 50.3,
  "summary": {"elapsed_ms": 50.3},
  "cases": [
    {
      "name": "basic",
      "status": "PASS",
      "seed": 42,
      "elapsed_ms": 12.3,
      "checks": [...],
      "log": []
    },
    {
      "name": "overflow",
      "status": "FAIL",
      "seed": 100,
      "elapsed_ms": 15.1,
      "checks": [{"status": "FAIL", ...}],
      "log": []
    },
    {
      "name": "skip_example",
      "status": "SKIP",
      "elapsed_ms": 0.1,
      "checks": [],
      "log": ["CI対象外"]
    }
  ],
  "checks": [...]   // 全ケースの集計（後方互換）
}
```

### §13.2 `--case` フィルタ

```bash
deltasim test counter.sv --spec spec.yaml --case basic
deltasim test counter.sv --spec spec.yaml --case "test_*"    # ワイルドカード
deltasim test counter.sv --spec spec.yaml --case a --case b  # 複数指定
```

- 一致しないケースは **結果に含まれない**（SKIP でも空でもなく、単純に除外）
- `filter_cases` パターンは `fnmatch` ワイルドカードに対応（`*`, `?` 等）
- すべてのケースが除外された場合も `TestResult` を返す（`cases=[]`, `status="PASS"`）
- 戻り値は常に `TestResult`（`None` にはならない）

---

1. **`SKIP` の発生条件**: テストが条件付きスキップになるケースが未定義。
2. **`case_done` の粒度**: 全チェックに対して発行するか、サイクル単位かは未定。
3. **`run()` の変化 DB 上限**: 大規模シミュレーション時のメモリ上限ポリシー未定。
4. **Z 値と X の区別**: 現在は両方とも `None` → `"x"` として扱う。`"z"` の区別は将来対応。
5. **`Simulator.force()` の精度**: 現在の実装では pre-posedge hook で適用するため、NBA の影響を完全に抑制できない場合がある (完全な force は将来対応)。
6. **階層シグナルパスの VCD**: 現在は `$scope` なしのフラット展開。`top.u_add.sum` 形式は将来対応。
7. **`test()` の elapsed_ms**: 各 check の `elapsed_ms` は概算 (チェック1件あたりの処理時間)。
