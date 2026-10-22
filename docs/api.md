# deltasim 公開 API 仕様 (docs/api.md)

> このドキュメントは GUI 仕様書 v0.1 §2「インターフェース契約」に基づく公開 API の仕様書です。  
> 変更時は仕様担当に相談のうえ両係へ周知してください。

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
  "deltasim_version": "0.3.0",
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
      "time_ns": 105,  // §15修正: posedge時刻は(N-1)*period+hp=105ns
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
| `code` | `str` | エラーコード |

### §16 エラーコード E001（パース・構文エラー）

`E001` はパーサまたはレキサーが入力を解析できなかったときに発生します。
**どんな入力でも有限の時間で終わります**（v0.2.2 で修正）。

| 状況 | message の例 |
|------|------------|
| `endmodule` がない | `endmodule がありません（module 'foo' は 1 行目で開始）` |
| `end` がない（begin…end ブロック） | `unexpected EOF inside begin…end block` |
| 括弧が閉じていない | `expected ')', got 'endmodule'` |
| module 宣言がない（空ファイル等） | `No module declaration found` |
| 閉じていない `/* ... */` コメント | `閉じていないブロックコメント /* が EOF に達しました` |
| 閉じていない文字列リテラル | `閉じていない文字列リテラルが EOF に達しました` |
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
  "time_ns": 105,  // §15修正: posedge時刻は(N-1)*period+hp=105ns
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
- すべてのケースが除外された場合は §14.1 に従い E_NO_CASE エラーを返す（`cases=[]`）
- 戻り値は常に `TestResult`（`None` にはならない）

---

1. **`SKIP` の発生条件**: テストが条件付きスキップになるケースが未定義。
2. **`case_done` の粒度**: 全チェックに対して発行するか、サイクル単位かは未定。
3. **`run()` の変化 DB 上限**: 大規模シミュレーション時のメモリ上限ポリシー未定。
4. **Z 値と X の区別**: 現在は両方とも `None` → `"x"` として扱う。`"z"` の区別は将来対応。
5. **`Simulator.force()` の精度**: 現在の実装では pre-posedge hook で適用するため、NBA の影響を完全に抑制できない場合がある (完全な force は将来対応)。
6. **階層シグナルパスの VCD**: 現在は `$scope` なしのフラット展開。`top.u_add.sum` 形式は将来対応。
7. **`test()` の elapsed_ms**: 各 check の `elapsed_ms` は概算 (チェック1件あたりの処理時間)。

---

## §14 PR #8 仮決め点の確定（2026-10-19 15:55）

### §14.1 `--case` で全ケースが除外された場合

```python
# 全除外時の TestResult
{
  "schema_version": "2",
  "status": "ERROR",        # PASS ではなく ERROR
  "cases": [],
  "errors": [
    {
      "code": "E_NO_CASE",
      "severity": "error",
      "message": "--case フィルタ 'xyz' に一致するケースがありません"
    }
  ]
}
```

| 動作 | 説明 |
|------|------|
| `status` | `"ERROR"` |
| `cases` | `[]` |
| `errors[].code` | `"E_NO_CASE"` |
| `errors[].message` | 指定パターンを含む |
| CLI 終了コード | 非 0 |
| `--json` | 正しい JSON を指定ファイルに出力（非 0 終了）|
| 戻り値型 | `TestResult`（`None` にはならない）|

CIで「何も実行されずにPASS」を防ぐための仕様です。

### §14.2 `--case` の fnmatch 記法

`--case` および `filter_cases` は Python `fnmatch` に準拠します:

| パターン | 説明 | 例 |
|---------|------|-----|
| `*` | 任意の文字列 | `test_*` → test_a, test_b |
| `?` | 任意の 1 文字 | `test_?1` → test_a1 |
| `[abc]` | 文字クラス | `test_[ab]?` → test_a1, test_b2 |
| `[a-z]` | 文字範囲 | `case_[0-9]` → case_0, case_1 |

複数パターンはいずれかに一致すれば実行されます（OR 条件）。

### §14.3 `cases[].message` フィールド

各 `CaseResult` の `message` は状態ごとに以下の値を持ちます。

| status | message の値 |
|--------|-------------|
| `SKIP` | `skip_reason`（空なら `"skipped"`） |
| `SKIP`（キャンセル） | `"cancelled"` |
| `ERROR` | エラー要約（`errors[0].message`） |
| `PASS` | `""` ← **§14 では未定義 → 空文字列を使用**（後述） |
| `FAIL` | `""` ← **§14 では未定義 → 空文字列を使用**（後述） |

`case_done` イベントにも `message` が含まれます:

```json
{"type": "case_done", "name": "my_case", "status": "SKIP",
 "message": "CI対象外", "elapsed_ms": 0.1}
```

> **未定義事項 (§14.3)**: PASS / FAIL の `message` に何を入れるかは仕様書に記載がありません。
> 現実装は空文字列（`""`）としており、最終報告に記載しています。

---


---

## §15 サイクル途中の刺激と確認（offset_ns）（2026-10-19 17:10 確定）

### §15.1 現行の時刻の決まり

`at: N`（1始まり）はN回目の立ち上りで、時刻 = `(N-1)×period_ns + period_ns/2`。  
**例**: period_ns=10 で at:N → `10N-5ns`（5, 15, 25, 35…）

> **注意**: docs §2.2 の例に `cycle: 11, time_ns: 110` とありますが、  
> 正しくは `time_ns: 105` です（posedge の時刻）。

### §15.2 offset_ns 書式

stimulus / expect の各項目に `offset_ns`（整数、省略時 = 0）を追加できます:

```yaml
cases:
  - name: hold_between_edges
    stimulus:
      - {at: 3, set: {d: 1}}                 # 25ns（省略 = offset_ns:0）
      - {at: 3, offset_ns: 3, set: {d: 0}}   # 28ns
    expect:
      - {at: 3, offset_ns: 4, check: {q: 1}} # 29ns
```

- 時刻 = `(at:N の時刻) + offset_ns`
- 値: `0 ≤ offset_ns < period_ns`（ケースの実効 period_ns）
- `at` が範囲 / `"*"` の場合は各サイクルに同じ offset_ns を適用
- `offset_ns: 0` と省略は完全に同じ動き（name も同じ）

### §15.3 同一時刻での処理順

各時刻 T で以下の順に処理します:
1. `offset_ns=0` のみ: エンジンのリセット制御（§15.5）
2. その時刻の刺激をすべて書き込む（YAML記載順・同一信号は後勝ち）
3. 値が変わった信号のエッジ検出 → NBA反映 → デルタサイクル収束（§15.4）
4. T がクロックエッジならクロックを変えて NBA反映 → 収束
5. その時刻の expect を確認
6. `offset_ns=0` のみ: 参照モデルとの比較

### §15.4 刺激でのエッジ検出

刺激で値が変わった信号について、`posedge`/`negedge` を感度に持つ `always_ff` を起動します。

### §15.5 リセット信号の扱い

エンジンのリセット制御が書き込むのは **2回のみ**:
1. 開始時（シミュレーション初期化）: リセットをアサート
2. `duration_cycles` 経過時: 解除（`N = duration_cycles+1` のポーズ前）

解除後はリセット信号を普通の入力として扱います（刺激で上書き可）。  
※ リセット期間中に刺激でリセット信号を書くことは推奨しません（解除時刻に解除されます）。

### §15.6 範囲外エラー (E_OFFSET_RANGE)

| 条件 | 挙動 |
|------|------|
| 負の値 | `status="ERROR"`, `errors[].code="E_OFFSET_RANGE"` |
| `period_ns` 以上 | 同上 |
| 整数でない（小数・文字列・bool） | 同上 |

エラーメッセージ例:  
`cases[hold_between_edges].expect[2]: offset_ns=10 は範囲外です（0 以上 10 未満の整数）`

仕様YAML読み込み時（シミュレーション開始前）に全項目を検査し、まとめて報告します。

### §15.7 結果JSON の変更点

```json
{
  "checks": [
    {
      "name": "q@cycle3[spec]",     // offset_ns=0: 従来どおり
      "cycle": 3,
      "time_ns": 25,                // 実際の確認時刻
      "offset_ns": 0                // 追加フィールド
    },
    {
      "name": "q@cycle3+4ns[spec]", // offset_ns>0: +Xns が付く
      "cycle": 3,
      "time_ns": 29,
      "offset_ns": 4
    }
  ]
}
```

- `time_ns`: at:N の時刻 + offset_ns（実際の確認時刻）
- `offset_ns`: 追加フィールド（省略時は 0）
- `schema_version`: "2" のまま（任意フィールドの追加のため）

### §15.12 未知キーの扱い (W_UNKNOWN_KEY)

仕様YAMLに知らないキーがあると、結果JSON の `warnings[]` に `W_UNKNOWN_KEY` を出力します:

```json
{"warnings": [{"code": "W_UNKNOWN_KEY", "message": "stimulus[0].foo: 不明なキーは無視されます"}]}
```

---

## §15.13 PR #10 残り事項の確定（2026-10-19 確定）

### §15.13.1 W_UNKNOWN_KEY の適用範囲

§15.12 から拡張し、YAML のどの階層に未知キーがあっても `W_UNKNOWN_KEY` を出します。
`message` にはキーの場所をパス形式で含めます。

**既知キー一覧（全階層）**

| 階層 | 既知キー |
|------|---------|
| トップレベル | `version`, `top`, `clock`, `reset`, `sim`, `inputs`, `stimulus`, `expected`, `expect`, `model`, `skip`, `skip_reason`, `cases` |
| `clock` | `signal`, `period_ns` |
| `reset` | `signal`, `active_low`, `duration_cycles` |
| `sim` | `cycles`, `seed`, `vcd` |
| `cases[]` 各ケース | `name`, `skip`, `skip_reason`, `seed`, `cycles`, `clock`, `reset`, `inputs`, `stimulus`, `expected`, `expect`, `model` |
| `cases[].clock` | `signal`, `period_ns` |
| `cases[].reset` | `signal`, `active_low`, `duration_cycles` |
| `stimulus` / `inputs` 各アイテム | `at`, `set`, `random`, `offset_ns` |
| `expect` / `expected` 各アイテム | `at`, `check`, `expr`, `offset_ns` |

**メッセージの例:**

`message` はキーまでを含むドット区切りパスで始まります（`{path}: 不明なキーは無視されます`）。
トップレベルはパスなしでキー名がそのまま先頭になります。

| 未知キーの場所 | message の例 |
|--------------|------------|
| トップレベル | `BOGUS: 不明なキーは無視されます` |
| `clock` 配下 | `clock.jitter: 不明なキーは無視されます` |
| `reset` 配下 | `reset.polarity: 不明なキーは無視されます` |
| `sim` 配下 | `sim.timeout: 不明なキーは無視されます` |
| `cases[0]` 直下 | `cases[0].skpi: 不明なキーは無視されます` |
| `cases[1].clock` 配下 | `cases[1].clock.edge: 不明なキーは無視されます` |
| `cases[2].reset` 配下 | `cases[2].reset.hold: 不明なキーは無視されます` |
| `stimulus[0]` アイテム（トップレベル or `cases[i]` 内） | `stimulus[0].foo: 不明なキーは無視されます` |
| `cases[0].stimulus[2]` アイテム | `cases[0].stimulus[2].ofset_ns: 不明なキーは無視されます` |
| `cases[0].inputs[0]` アイテム（`inputs` と書いた場合） | `cases[0].inputs[0].foo: 不明なキーは無視されます` |
| `expect[1]` アイテム | `expect[1].bar: 不明なキーは無視されます` |

### §15.13.2 `warnings` の常時出力

`TestResult` および `CaseResult` の両方について、`to_dict()` が返す JSON オブジェクトに
`warnings` キーを常に含めます。警告がない場合は `[]` として出力します。
これにより `schema_version` は `"2"` のままです（任意フィールドの追加のため互換）。

**出力例（警告なし）:**

```json
{
  "schema_version": "2",
  "status": "PASS",
  "warnings": [],
  "cases": [
    {
      "name": "basic",
      "status": "PASS",
      "warnings": []
    }
  ]
}
```

**移行ガイド:**  
`"warnings" in result_dict` のような存在チェックは不要になります。
旧 API は `warnings` が空のとき省略していたため、既存コードが `d.get("warnings", [])` を
使っている場合はそのまま動作します。

---


## バージョン管理

### ツール版 (`deltasim_version`)

`TestResult.to_dict()` および `deltasim test --json` の出力のトップレベルに `deltasim_version` フィールドが含まれます。  
値は `deltasim.__version__`（`pyproject.toml` の `version` から自動取得）と常に一致します。

```json
{
  "schema_version": "2",
  "deltasim_version": "0.3.0"
}
```

- `schema_version` はJSON構造の互換性を表し、任意フィールドの追加では変わりません（現在 `"2"`）。
- `deltasim_version` はツール自体のリリース版を表します。
- `--json-events` の各イベントには `deltasim_version` を含みません（開始イベント等は対象外）。

### バージョン定義の場所

| 場所 | 内容 |
|------|------|
| `deltasim/_version.py` の `__version__` | **唯一の定義箇所**（ここを変えるだけで全箇所に反映） |
| `deltasim.__version__` | `deltasim._version.__version__` を import（常に一致） |
| `pyproject.toml` の `dynamic = ["version"]` | `[tool.setuptools.dynamic]` 経由で `_version.__version__` を参照 |
| `deltasim --version` | `deltasim.__version__` を表示 |
| `TestResult.to_dict()["deltasim_version"]` | `deltasim.__version__` と同値 |

---

## v0.2.1 変更点

### ANSI 複数ポート名宣言

IEEE 1800 の ANSI スタイルで、1つの方向・型・幅の後に複数の名前を並べる書き方を受け付けます。

```systemverilog
module dff(input logic clk, rst, d, output logic q);
  always_ff @(posedge clk) if (!rst) q <= 0; else q <= d;
endmodule
```

後続の名前は直前の方向・型・幅を引き継ぎます。複数行にまたがる書き方も動作します。

### 無限ループ対策（`E_DELTA_LIMIT` / `E_LOOP_LIMIT`）

#### `E_DELTA_LIMIT` — 組み合わせループ検出

`assign y = ~y;` のように収束しない組み合わせ論理が delta サイクル上限を超えると `E_DELTA_LIMIT` エラーになります。

| 項目 | 内容 |
|------|------|
| デフォルト上限 | 10,000 delta サイクル |
| CLI オプション | `--max-delta N` |
| 公開 API 引数 | `max_delta=N`（`test()` の引数） |
| エラーコード | `E_DELTA_LIMIT` |
| message 例 | `E_DELTA_LIMIT: 0ns でデルタサイクルが 5 回を超えました（変化し続けている信号: y）` |

#### `E_LOOP_LIMIT` — 無限ループ検出

`while (1)`、`for (i = 0; 1; ...)` などのループが反復上限を超えると `E_LOOP_LIMIT` エラーになります。

| 項目 | 内容 |
|------|------|
| デフォルト上限 | 1,000,000 反復/ループ実行 |
| CLI オプション | `--max-loop N` |
| 公開 API 引数 | `max_loop=N`（`test()` の引数） |
| エラーコード | `E_LOOP_LIMIT` |
| message 例 | `E_LOOP_LIMIT: 5ns の for loop が 100 回を超えました` |

#### エラー時の動作

- `status = "ERROR"`, `errors[]` に `code` と `message` を含む `ErrorInfo` が入ります。
- 該当ケース (`cases[].status`) も `"ERROR"` になり `cases[].errors[]` にも入ります。
- トップレベル `errors[]` にも伝播します。
- **他のケースは続行します**（最初のエラーケースで中断しません）。
- VCD はエラー発生時点までを正しく閉じます。

#### `--json` 時の常時出力

`deltasim test --json path.json` を付けた場合、想定外の例外（`RecursionError` 含む）でも必ず JSON を出力します。想定外例外のコードは `E_INTERNAL`、`message` に例外の型と内容、トレースバックは `stderr` に出力されます。

`schema_version` は `"2"` のまま変わりません。


---

## §18.2 ファイルリスト API

### `load_filelist(path) → FileList`

`.f` ファイルを解析して `FileList` を返します。

```python
from deltasim.filelist import load_filelist

fl = load_filelist("files.f")
for w in fl.warnings:
    print(f"W ({w.code}): {w.file}:{w.line}: {w.message}")
result = ds.test(fl.files, spec="spec.yaml")
```

`FileList` フィールド:

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `files` | `List[str]` | 絶対パスに解決された SV ファイルリスト |
| `incdirs` | `List[str]` | `+incdir+` ディレクトリリスト（**保持のみ。まだパーサに渡されません**） |
| `defines` | `Dict[str, str]` | `+define+NAME[=VAL]` マクロ辞書（**保持のみ。まだパーサに渡されません**） |
| `warnings` | `List[FileListWarning]` | 未対応オプション警告（`W_FILELIST_OPT`） |
| `errors` | `List[FileListError]` | 存在しないファイルのエラー |

### `expand_filelist_args(sv_files, filelist_args) → (all_files, combined_filelist)`

CLI コマンドから呼ばれる内部ヘルパー。`sv_files` と `-f` ファイルを結合します。


---

## §18.5 新規 API

### `find_top_candidates(mods, catalog=None) → List[str]`

どこからもインスタンスされていないモジュール名のリストを返します（GUI から使用可能）。

```python
from deltasim.api import find_top_candidates, _parse_files
mods, cat = _parse_files(["counter.sv", "tb.sv"])
candidates = find_top_candidates(mods, cat)
# → ["counter"]  (tb からインスタンスされている counter は除外)
```

`catalog` 引数は将来の拡張用（現在は未使用）。generate ブロック内のインスタンスも検索します。

**関連エラーコード**: `E_AMBIGUOUS_TOP` — `--top` 省略時に候補が複数ある場合

---

## §18.5 `--log` オプション

`run`・`test`・`gen`・`parse` に `--log PATH` を追加しました（既定: `deltasim.log`）。実行ログ（`$display` 出力等）を UTF-8・LF 改行で書き出します。

---

## §18.6 `constraints:` 制約付き乱数生成

YAML スペックの `constraints:` に信号間の式を書くことで、乱数刺激の値を制約できます。

```yaml
constraints:
  - "a + b < 16"
  - "a != b"
max_constraint_attempts: 10000  # 既定値、省略可
```

- リジェクションサンプリングで実装。制約を満たす値が `max_constraint_attempts` 回以内に見つからない場合は `E_CONSTRAINT` エラー。
- `constraints:` はスペック全体（全ケースに適用）またはケースごとに指定できます。
- 式は既存の SV 式パーサ（Python `eval` 不使用）で評価します。
- 同じ `seed` なら Windows/Linux で同じ値列になります（`random.Random(seed)` を使用）。

**関連エラーコード**:
- `E_CONSTRAINT` — 制約を満たす値が見つからなかった
- `E_CONSTRAINT_EXPR` — 制約式の構文エラーまたは未知の信号名

---

## §18.7 4値論理

### 内部表現

`FourState(aval, bval, width)` の2マスク形式（IEEE 1800 Annex A 準拠）:

| aval bit | bval bit | 意味 |
|---------|---------|------|
| 0 | 0 | 0 |
| 1 | 0 | 1 |
| 0 | 1 | z |
| 1 | 1 | x |

### YAML での使用

`expected/check` に 4値リテラル文字列を書けます:

```yaml
cases:
  - name: tristate
    expected:
      - at: 3
        check:
          y: "1'bz"     # z と一致するか (=== 相当)
          q: "4'b1x??"  # ? はワイルドカード (==? 相当)
          data: "8'hxx"  # 全ビット x
```

- 文字列 expected: `===` 相当の比較（`?` を含む場合は `==?` 相当）
- 整数 expected: 純2値比較。actual に x/z があれば FAIL

`stimulus/set` にも x/z を書けます:

```yaml
stimulus:
  - at: 3
    set:
      d: "1'bx"   # 入力を x に設定
```

### `deltasim test --json` の出力

`checks[].expected` / `checks[].actual` は 4値文字列（例: `"1x0z"`）として出力されます。既存の数値フィールド形式は変わりません。

### 初期値

現バージョン（0.3.0）では `logic`/`reg` の初期値は `0` です（将来的に `x` に変更予定）。`input` ポートの初期値 `0` は §18.6 の制約式評価でも使われます。

### 信号名 x/z の衝突（Fix §18.7.6）

`x` / `z` という名前の信号ポートが制約式・RTL 両方で使えます:
- 環境（signals dict）に存在する場合は識別子として扱う
- 存在しない場合は4値リテラルとして扱う
