# deltasim 公開 API 仕様 (docs/api.md)

> **注意**: このドキュメントは GUI 仕様書 v0.1 §2「ロジックとGUIの取り決め（インターフェース契約）」に忠実に基づきます。  
> 独自拡張は行いません。変更時は仕様担当に相談のうえ両係へ周知してください。

---

## 概要

`deltasim` のコアロジックは `deltasim.api` モジュールとして公開されます。  
GUI は **このモジュールだけ** を使い、シミュレーション処理をコア側に委譲します。  
CLI で可能なことはすべて API でも可能です（CLI は API のラッパー）。

```python
import deltasim.api as ds
```

---

## §2.1 公開関数 (§2.1)

### `load(files, top, params, catalog) → LoadResult`

RTL ファイルを解析・エラボレートします。

| 引数 | 型 | 説明 |
|------|----|------|
| `files` | `list[str \| Path]` | SV ファイルパスのリスト |
| `top` | `str \| None` | トップモジュール名 (省略時は最初のモジュール) |
| `params` | `dict[str, int] \| None` | パラメータ上書き |
| `catalog` | `dict \| None` | 既解析のモジュールカタログ |

**戻り値** `LoadResult`:

```json
{
  "schema_version": "1",
  "status": "PASS",
  "module": "counter",
  "ports": [
    {"name": "clk", "direction": "input", "width": 1, "signed": false}
  ],
  "params": {"WIDTH": 8},
  "signals": [...],
  "errors": []
}
```

---

### `gen(files, top, params, out_dir, tb_path, spec_path) → dict`

テストベンチ SV と YAML スペックスケルトンを生成します。

**戻り値**:

```json
{
  "schema_version": "1",
  "status": "PASS",
  "module": "counter",
  "tb_path": "./counter_tb.sv",
  "spec_path": "./counter_spec.yaml"
}
```

---

### `test(files, spec, *, top, params, seed, vcd, on_progress, cancel) → TestResult`

YAML スペックに従ってシミュレーションを実行し、結果を返します。

| 引数 | 型 | 説明 |
|------|----|------|
| `files` | `list[str \| Path]` | RTL SV ファイル |
| `spec` | `str \| Path` | YAML スペックファイル |
| `top` | `str \| None` | トップモジュール (spec の `top:` より優先) |
| `params` | `dict \| None` | パラメータ上書き |
| `seed` | `int \| None` | 乱数シード上書き |
| `vcd` | `str \| None` | VCD 出力パス |
| `on_progress` | `callable \| None` | 進捗コールバック (§2.4 参照) |
| `cancel` | `CancelToken \| None` | キャンセルトークン |

**戻り値** `TestResult` (§2.2 参照)

---

### `run(files, *, top, params, cycles, vcd, reset_cycles, half_period, keep_changes, on_progress, cancel) → RunResult`

スペックなしでシミュレーションを実行します。

**戻り値** `RunResult`:

```json
{
  "schema_version": "1",
  "module": "counter",
  "status": "PASS",
  "cycles": 100,
  "time_ns": 1000,
  "cancelled": false,
  "vcd_path": "out.vcd",
  "log": [],
  "errors": []
}
```

---

## §2.2 結果 JSON 構造

`test()` の戻り値および `deltasim test --json out.json` の出力は **同じ構造** です。

```json
{
  "schema_version": "1",
  "module": "counter",
  "status": "PASS",
  "seed": 42,
  "elapsed_ms": 12.3,
  "cancelled": false,
  "log": ["PASS: count = 10"],
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
      "message": null
    }
  ],
  "errors": []
}
```

### `status` フィールド

| 値 | 意味 |
|----|------|
| `"PASS"` | 全チェック合格 |
| `"FAIL"` | 1 件以上のチェック不合格 |
| `"ERROR"` | 解析・エラボレーション・シミュレーションエラー |
| `"SKIP"` | スキップ（将来拡張用） |

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

`on_progress` コールバックには以下の dict が渡されます。  
`--json-events` フラグを使うと CLI が JSON Lines として stdout に出力します。

### `start` イベント

```json
{"type": "start", "phase": "parse", "timestamp_ms": 1000}
```

### `progress` イベント

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

`phase` は `"parse"` → `"elab"` → `"sim"` → `"check"` の順に遷移します。

### `case_done` イベント

```json
{
  "type": "case_done",
  "name": "count@cycle11[spec]",
  "status": "PASS",
  "timestamp_ms": 1010
}
```

### `finish` イベント

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

`cancel.cancel()` を呼ぶと次の進捗チェック時に中断します。  
`TestResult.cancelled == True` となり、**途中結果** (`checks`) が含まれます。

---

## §2.5 対話デバッグ Simulator (M5 以降)

`Simulator` クラスは M5 で実装予定です。

```python
# 予定インタフェース
sim.step(n)               # n サイクル進める
sim.run(until=time_ns)    # 指定時刻まで実行
sim.get(signal)           # 信号値を取得
sim.set(signal, value)    # 信号を設定
sim.force(signal, value)  # 信号を強制固定
sim.release(signal)       # force を解除
sim.add_breakpoint(...)   # ブレークポイント追加
sim.remove_breakpoint(...) # ブレークポイント削除
```

---

## §2.6 信号変化 API

`RunResult.get_signal_changes(signal, start_ns, end_ns)` で信号変化リストを取得します。

```python
result = ds.run(["counter.sv"], keep_changes=True, cycles=100)
changes = result.get_signal_changes("count", start_ns=50, end_ns=100)
# [SignalChange(time_ns=55, value="00000001"), ...]
```

大規模波形は `start_ns` / `end_ns` で範囲を絞って取得できます。

### `SignalChange` 構造

```json
{"time_ns": 55, "value": "00000001"}
```

---

## §2.7 値の表現 (4 値文字列)

| 幅 | 例 |
|----|----|
| 1 ビット | `"0"` / `"1"` / `"x"` / `"z"` |
| N ビット | `"01xz10"` (MSB 先頭、長さ = N) |

- `x` = 不定 (Unknown)  
- `z` = ハイインピーダンス  
- 表示基数 (2/8/10/16進、符号付き) への変換は GUI 側で行います

---

## §2.8 GUI エントリポイント (M5)

```bash
pip install deltasim[gui]
deltasim gui          # 127.0.0.1:8765 で起動
deltasim gui --port 8080 --host 0.0.0.0
```

GUI は FastAPI で公開 API を包む薄い層です。  
コア（`deltasim[core]`）は FastAPI に依存しません。

---

## 未決事項・曖昧点 (要確認)

以下の点は仕様書に明記されていないため、実装者が独断で決定せず報告します。

1. **`SKIP` ステータスの発生条件**: テストが条件付きでスキップされる場合（例: シードが与えられていないランダムケース）の扱いが未定。
2. **`case_done` の発行タイミング**: すべてのチェックに対して発行するのか、サイクル完了時に一括発行するのか未定。
3. **`run()` の signal change DB の最大サイズ**: 大規模シミュレーション時のメモリ上限ポリシーが未定。
4. **`get_signal_changes` の Z 値エンコード**: `"z"` を返すか `"x"` で統合するか未定（現在は `None` を `"x"` として扱っている）。
5. **`Simulator` M5 キャンセル**: 対話デバッグ中のキャンセルポリシーが未定。
6. **`errors` と `checks` の順序**: チェックが失敗して `ERROR` 扱いになる場合、`checks` と `errors` のどちらに入るか。
