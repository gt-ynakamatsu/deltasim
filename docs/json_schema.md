# JSON スキーマ（schema_version "2"）

`deltasim test --json` の出力と `deltasim.api.test()` の戻り値の JSON 構造です。

---

## TestResult（トップレベル）

```json
{
  "schema_version":   "2",
  "deltasim_version": "0.3.0",
  "module":           "counter",
  "status":           "PASS",
  "seed":             42,
  "elapsed_ms":       12.3,
  "summary":          {"elapsed_ms": 12.3},
  "cancelled":        false,
  "truncated_at":     null,
  "log":              [],
  "cases":            [ ... ],
  "checks":           [ ... ],
  "errors":           [],
  "warnings":         []
}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `schema_version` | `"2"` | 固定（§12 以降） |
| `deltasim_version` | `string` | ツールバージョン（例: `"0.3.0"`） |
| `module` | `string` | トップモジュール名 |
| `status` | `"PASS"` \| `"FAIL"` \| `"ERROR"` \| `"SKIP"` | 全体ステータス |
| `seed` | `int` | 使用した乱数シード |
| `elapsed_ms` | `float` | 実行時間 ms（= `summary.elapsed_ms`） |
| `summary` | `{elapsed_ms: float}` | サマリ |
| `cancelled` | `bool` | キャンセルされたか |
| `truncated_at` | `int?` | 信号変化 DB の打ち切り時刻 ns（null = 打ち切りなし） |
| `log` | `string[]` | `$display` 等の出力 |
| `cases` | `CaseResult[]` | ケース毎の結果（後述） |
| `checks` | `CheckInfo[]` | 全ケース集計（後方互換） |
| `errors` | `ErrorInfo[]` | エラー一覧（常に `[]` または非空） |
| `warnings` | `WarningInfo[]` | 警告一覧（常に `[]`） |

---

## CaseResult（`cases[]` の要素）

```json
{
  "name":         "basic",
  "status":       "PASS",
  "seed":         42,
  "elapsed_ms":   4.5,
  "message":      null,
  "cancelled":    false,
  "truncated_at": null,
  "checks":       [ ... ],
  "log":          [],
  "errors":       [],
  "warnings":     []
}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `name` | `string` | ケース名 |
| `status` | `"PASS"` \| `"FAIL"` \| `"ERROR"` \| `"SKIP"` | ケースステータス |
| `seed` | `int` | このケースのシード |
| `elapsed_ms` | `float` | このケースの実行時間 ms |
| `message` | `string?` | エラー要約（§14.3）|
| `cancelled` | `bool` | キャンセルされたか |
| `truncated_at` | `int?` | 打ち切り時刻 ns |
| `checks` | `CheckInfo[]` | このケースのチェック結果 |
| `log` | `string[]` | このケースの `$display` 出力 |
| `errors` | `ErrorInfo[]` | エラー一覧（常に `[]`） |
| `warnings` | `WarningInfo[]` | 警告一覧（常に `[]`） |

---

## CheckInfo（`checks[]` の要素）

```json
{
  "name":      "count@cycle11[spec]",
  "status":    "PASS",
  "signal":    "count",
  "cycle":     11,
  "time_ns":   105,
  "offset_ns": 0,
  "expected":  "00000001",
  "actual":    "00000001",
  "source":    "spec",
  "seed":      42,
  "elapsed_ms": 0.5,
  "message":   null
}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `name` | `string` | チェック識別子（`signal@cycleN[spec]` 形式） |
| `status` | `"PASS"` \| `"FAIL"` | チェック結果 |
| `signal` | `string` | 信号名または `"<expr>"` |
| `cycle` | `int` | チェックしたサイクル番号（1-indexed） |
| `time_ns` | `int` | チェックした時刻 ns |
| `offset_ns` | `int` | サブサイクルオフセット ns（§15、0 のときも常に出力）|
| `expected` | `string` | 期待値の 4 値文字列 |
| `actual` | `string` | 実際の値の 4 値文字列 |
| `source` | `"spec"` \| `"model"` | チェックの種別 |
| `seed` | `int` | このチェックのシード |
| `elapsed_ms` | `float` | 経過時間 ms |
| `message` | `string?` | 補足メッセージ |

---

## ErrorInfo（`errors[]` の要素）

```json
{
  "file":     "counter.sv",
  "line":     0,
  "column":   0,
  "severity": "error",
  "message":  "E_DELTA_LIMIT: 0ns でデルタサイクルが 5 回を超えました",
  "code":     "E_DELTA_LIMIT"
}
```

詳細は [error_codes.md](error_codes.md) 参照。

---

## WarningInfo（`warnings[]` の要素）

```json
{
  "code":    "W_UNKNOWN_KEY",
  "message": "cases[0].skpi: 不明なキーは無視されます"
}
```

---

## 4 値文字列表現

信号値は 4 値（0/1/x/z）のビット文字列で表します。

```
"0"        → 1 ビット 0
"1"        → 1 ビット 1
"x"        → 1 ビット x（不定）
"z"        → 1 ビット z（ハイインピーダンス）
"00000001" → 8 ビット 1（MSB 先頭）
"xxxx0001" → 下位 4 ビット有効、上位 4 ビット x
```

---

## schema_version の考え方

`schema_version` は JSON の**構造的互換性**を示します。

- フィールドの追加: `schema_version` は変わりません（後方互換）。
- フィールドの削除・型変更: `schema_version` を上げます。

現在は `"2"` です（`deltasim_version`・`warnings`・`cases[].errors` などの追加は全て後方互換のため `"2"` のまま）。
