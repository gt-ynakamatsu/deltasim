# エラー・警告コード一覧

`deltasim` のすべてのエラーコード（`errors[].code`）と警告コード（`warnings[].code`）の一覧です。

---

## エラーコード（`errors[].code`）

| コード | 発生場所 | 説明 |
|--------|----------|------|
| `E001` | パーサ / レキサー / .f 解析 | 構文エラー、または .f ファイルに書かれた SV ファイルが存在しない |
| `E002` | エラボレータ | 信号幅・型の矛盾など |
| `E003` | エラボレータ | 未定義のモジュール / 信号参照 |
| `E004` | エラボレータ | その他エラボレーションエラー |
| `E005` | シミュレータ | シミュレーション実行エラー（旧汎用コード） |
| `E006` | テスト実行 | 参照モデルの読み込み失敗 |
| `E_NO_CASE` | テスト実行 | `--case` フィルタで該当ケースなし |
| `E_OFFSET_RANGE` | YAML 解析 | `offset_ns` が有効範囲外（0 ≤ offset_ns < period_ns） |
| `E_DELTA_LIMIT` | シミュレータ | デルタサイクル収束ループ上限超過（既定 10,000）|
| `E_LOOP_LIMIT` | シミュレータ | for/while/forever/repeat ループ反復上限超過（既定 1,000,000）|
| `E_INTERNAL` | CLI | 想定外の例外（RecursionError 含む）|

### E001 メッセージ例

| 状況 | message |
|------|---------|
| `endmodule` なし | `endmodule がありません（module 'X' は N 行目で開始）` |
| `end` なし | `unexpected EOF inside begin…end block` |
| 括弧未閉 | `expected ')', got 'endmodule'` |
| module 宣言なし | `No module declaration found` |
| 未閉 `/*` コメント | `閉じていないブロックコメント /* が EOF に達しました` |
| 未閉文字列 | `閉じていない文字列リテラルが EOF に達しました` |

### E_DELTA_LIMIT メッセージ例

```
E_DELTA_LIMIT: 0ns でデルタサイクルが 5 回を超えました（変化し続けている信号: y）
```

### E_LOOP_LIMIT メッセージ例

```
E_LOOP_LIMIT: 5ns の for loop が 100 回を超えました
```

---

## 警告コード（`warnings[].code`）

| コード | 発生場所 | 説明 |
|--------|----------|------|
| `W_UNKNOWN_KEY` | YAML 解析 | 仕様 YAML に未知のキーがある（無視されます） |
| `W_FILELIST_OPT` | .f ファイル解析 | `.f` ファイルに未対応のオプションがある（無視されます） |
| `E_LLM_NOT_CONFIGURED` | LLM 機能 | `simconfig.yaml` の `llm` 設定がない |
| `E_LLM_CONNECT` | LLM 機能 | LLM サーバーへの接続失敗・タイムアウト |
| `E_LLM_UNSAFE_CMD` | LLM 機能 | LLM が安全でないコマンドを返した（シェル記号・再帰等） |

### W_UNKNOWN_KEY メッセージ例（§15.13）

| 場所 | message |
|------|---------|
| トップレベル | `BOGUS_KEY: 不明なキーは無視されます` |
| `clock` 配下 | `clock.jitter: 不明なキーは無視されます` |
| `cases[0]` 直下 | `cases[0].skpi: 不明なキーは無視されます` |
| `cases[1].clock` | `cases[1].clock.edge: 不明なキーは無視されます` |
| `cases[0].stimulus[2]` | `cases[0].stimulus[2].ofset_ns: 不明なキーは無視されます` |

---

## 既知キー一覧（W_UNKNOWN_KEY の対象外）

| 階層 | 既知キー |
|------|---------|
| トップレベル | `version`, `top`, `clock`, `reset`, `sim`, `inputs`, `stimulus`, `expected`, `expect`, `model`, `skip`, `skip_reason`, `cases` |
| `clock` | `signal`, `period_ns` |
| `reset` | `signal`, `active_low`, `duration_cycles` |
| `sim` | `cycles`, `seed`, `vcd` |
| `cases[]` 各ケース | `name`, `skip`, `skip_reason`, `seed`, `cycles`, `clock`, `reset`, `inputs`, `stimulus`, `expected`, `expect`, `model` |
| `cases[].clock` | `signal`, `period_ns` |
| `cases[].reset` | `signal`, `active_low`, `duration_cycles` |
| `stimulus` / `inputs` アイテム | `at`, `set`, `random`, `offset_ns` |
| `expect` / `expected` アイテム | `at`, `check`, `expr`, `offset_ns` |

---

## エラーの JSON 表現

```json
{
  "file":     "counter.sv",
  "line":     5,
  "column":   3,
  "severity": "error",
  "message":  "endmodule がありません（module 'counter' は 1 行目で開始）",
  "code":     "E001"
}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `file` | `string` | ファイルパス（空文字 = 不明） |
| `line` | `int` | 行番号（0 = 不明） |
| `column` | `int` | 列番号（0 = 不明） |
| `severity` | `"error"` \| `"warning"` | 深刻度 |
| `message` | `string` | 人間が読めるエラーメッセージ |
| `code` | `string` | 機械が読めるエラーコード |

---

## W_FILELIST_OPT — `.f` ファイル未対応オプション警告

`.f` ファイルリストに未対応のオプション（`-v`、`-y`、`+libext+` など）が含まれていると
`W_FILELIST_OPT` 警告が出力されます。

| 項目 | 内容 |
|------|------|
| `code` | `"W_FILELIST_OPT"` |
| `file` | `.f` ファイルのパス |
| `line` | 未対応オプションが書かれた行番号 |
| `message` | `"未対応のオプション '-v' は無視されます"` |

**対応するオプション**: `+incdir+<dir>`、`+define+NAME[=VAL]`、`-f <nested.f>`  
**未対応（スキップ）**: `-v`、`-y`、`+libext+`、その他 `-` または `+` で始まるオプション

| `E_UNKNOWN_PORT` | テスト実行 | YAML スペックで指定した信号名がモジュールのポートに存在しない |
| `E_NO_CHECK` | テスト実行 | スペックにチェックが0件（全ケース skip を除く） |
| `E_AMBIGUOUS_TOP` | ロード | `--top` 省略時に top 候補が複数ある |
