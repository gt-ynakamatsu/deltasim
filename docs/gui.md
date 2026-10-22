# deltasim GUI 操作ガイド

> **注意**: このドキュメントは GUI 係の担当分（§18.4/§18.8）です。  
> インストール手順・CLI 仕様は README および docs/api.md を参照してください。

## 目次

1. [起動方法](#1-起動方法)
2. [画面構成](#2-画面構成)
3. [操作ガイド](#3-操作ガイド)
4. [HTTP API リファレンス](#4-http-api-リファレンス)
5. [SSE イベント仕様](#5-sse-イベント仕様)

---

## 1. 起動方法

```bash
deltasim gui                         # デフォルト (127.0.0.1:8765)
deltasim gui --port 9000             # ポート変更
deltasim gui counter.sv counter_spec.yaml  # ファイルを引数で開く
```

**セキュリティ**: GUI はファイルを読み書きできるため、`127.0.0.1` のみで待ち受けます。  
`--host 0.0.0.0` など外部アドレスを指定すると終了コード 2 でエラーになります（§17.2）。

ブラウザで `http://127.0.0.1:8765` を開いてください（`--no-browser` で自動オープンを抑制）。

---

## 2. 画面構成

```
┌─ ツールバー ────────────────────────────────────────────────────┐
│ 📂 開く  サンプル  ⚙ 生成  ▶ テスト実行  ▶ シミュ実行         │
│ ■ 停止  💾 保存  🔬 デバッグ  シード:___  ケース:___  基数:HEX │
├─ サイドバー ─┬─ エディタ（中央上）──────────────────────────────┤
│ 最近のPJ    │ RTL / YAML ファイル表示・編集                      │
│ サンプル    │                                                     │
│ RTL ファイル├─ テスト結果（右）──────────────────────────────────┤
│ スペック    │ ケース一覧（上段）/ チェック詳細（下段）            │
│ トップ選択  │ ⬇JSON  ⬇JUnit  （テスト完了後に表示）             │
├─────────────┴─────────────────────────────────────────────────┤
│ 波形ビューア（中央下）                                           │
│ ズーム± / 全体表示 / カーソルA・B / HEX・BIN・DEC              │
├──────────────────────────────────────────────────────────────┤
│ ログ / コンソール                                               │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. 操作ガイド

### 3.1 プロジェクトを開く

- **ファイルブラウザ** (`📂 開く`): ホームディレクトリとカレントディレクトリ以下を閲覧。  
  `.sv`/`.v` を選ぶと RTL として、`.yaml`/`.yml` を選ぶとスペックとして読み込みます。
- **サンプル**: `examples/` フォルダのサンプルを即座に読み込みます。
- **最近使ったプロジェクト**: サイドバー上部に最大10件表示されます。  
  存在しないパスは ⚠ で表示されます。
- **CLI 引数**: `deltasim gui counter.sv counter_spec.yaml` で起動時に読み込まれます。

設定は `deltasim.project.yaml` に保存し、次回以降も使えます。

### 3.2 トップモジュールの選択

RTL 読み込み後、「トップモジュール」セレクトに候補が表示されます。

- **トップ候補** (optgroup "トップ候補"): どこからもインスタンス化されていないモジュール
- **その他のモジュール**: サブモジュールなど

設計に1モジュールしかない場合はグループなしで表示されます。

> **§18.5 更新**: トップ候補の判定は `deltasim.api.find_top_candidates()` を使用しています。
> `generate` ブロック内のインスタンス化も含めて検索するため、PR #20 時点の既知の制限は解消されました。
> 複数のトップ候補が存在する場合（`E_AMBIGUOUS_TOP`）は、
> サイドバーのドロップダウンに候補一覧が表示されます。`--top` で明示することを推奨します。

### 3.3 生成（⚙ 生成ボタン）

RTL を読み込んだ状態で `⚙ 生成` をクリックすると、テストベンチ SV と YAML スペックのスケルトンを生成してエディタに表示します。

生成後に「保存」ダイアログが開きます:
- **保存ディレクトリ**: 既定はプロジェクトのフォルダ（SV ファイルのある場所）
- **TB SV / スペック YAML**: 個別に保存するか選択可能
- **既存ファイルがある場合**: 確認ダイアログが出ます。了承した場合のみ上書きします
- 保存後は最近使ったプロジェクトに自動追加されます

### 3.4 テスト実行（▶ テスト実行）

- シードを入力するか空欄にする（空欄 = ランダム）
- ケースフィルタに名前やワイルドカード (`counter_*`) を入力
- `▶ テスト実行` をクリック

**波形の自動生成**: `spec.yaml` に `sim.vcd` が無くても、テスト完了後に自動で一時 VCD が生成されて波形ビューアに表示されます。  
一時ファイルは OS 一時ディレクトリ（`tempfile.gettempdir()`）に作成され、次の実行時またはサーバー終了時に削除されます。

結果が表示されたら、ケースをクリックして詳細（失敗した check の時刻・期待値・実測値）を確認できます。

### 3.5 シミュ実行（▶ シミュ実行）

テスト判定なしでシミュレーションを実行して波形を取得します。  
「終了:」入力欄でサイクル数を指定してください（デフォルト 100 サイクル）。

### 3.6 このシードで再実行

ケース詳細パネルに表示される **「↺ このシードで再実行 (seed=N)」** ボタンをクリックすると、そのケースと同じシードでテストを再実行します。

### 3.7 エクスポート（⬇ JSON / ⬇ JUnit）

テスト完了後、結果パネル上部に **⬇ JSON** と **⬇ JUnit** ボタンが表示されます。  
- **JSON**: CLI の `test --json` と同一構造の JSON をダウンロード
- **JUnit**: JUnit XML 形式（CI ツールで利用可）をダウンロード

### 3.8 波形ビューア

| 操作 | 説明 |
|------|------|
| クリック | カーソル A を配置 |
| Shift+クリック | カーソル B を配置 |
| ＋/－/⊡ | ズームイン/アウト/全体表示 |
| HEX/BIN/DEC | 全体の表示基数を切替 |

信号の値変化は遅延取得で描画します（最大 200 信号まで同時表示）。

#### 信号の追加・削除・並べ替え

- **＋信号** ボタン → 信号追加ピッカーが開く。VCD の信号一覧から選択して追加
- 各信号行の **▴/▾** ボタン → 上下に移動
- **✕** ボタン → 表示から削除
- 信号名をクリック → その信号をエッジ検索のアクティブ信号として選択（黄色でハイライト）

#### エッジ検索

ツールバーの「エッジ:」セレクトで種別（任意/立上り/立下り）を選び、**◀/▶** ボタンで前後のエッジを探してカーソル A を移動します。

アクティブ信号（信号名をクリックして選択）に対して検索を行います。

**エッジ種別の仕様（§18.4.2 確定）**:
| 種別 | 1ビット信号 | 多ビット信号 |
|------|------------|------------|
| 任意 | 値の変化（0→1, 1→0 等）| 値の変化 |
| 立上り | L→H（0/z/x → 1）| **値の変化**（立上り/立下りは未定義のため任意と同一動作） |
| 立下り | H→L（1 → 0/z/x）| **値の変化**（同上） |

> **既知の制限**: 多ビット信号で「立上り」「立下り」を指定しても、実際には値の変化を探します。
> 多ビット信号の立上り/立下り判定は将来のバージョンで定義予定。

VCD に含まれない信号はエッジ検索のアクティブ信号として選択できません（選択しても検索をスキップします）。

#### 符号付き表示

各信号行の基数ボタン（**HEX/BIN/U/S**）をクリックするたびに切り替わります:
- **HEX**: 16進数（`0xFF`）
- **BIN**: 2進数（`b11111111`）
- **U**: 符号なし10進数
- **S**: 符号付き10進数（2の補数。x/z を含む値はそのまま表示）

#### 波形表示セットの保存・読込

- **💾** ボタン → 現在の信号リストと各信号の基数設定を名前をつけて保存
- **📂** ボタン → 保存済みのセットを読み込む

保存先はユーザー設定ディレクトリ内のプロジェクトごとのファイルです:
- Windows: `%APPDATA%\deltasim\wavesets\<project_hash>.json`
- Linux: `~/.config/deltasim/wavesets/<project_hash>.json`

#### FAIL 時の信号ハイライト

テスト結果でケースを選択したとき:
1. 失敗した check の対象信号（`checks[].signal`）が波形に未表示なら自動追加
2. その信号行を赤くハイライト
3. カーソル A を最初の失敗の `time_ns` にジャンプ

### 3.9 対話デバッグ（🔬 デバッグ）

`🔬 デバッグ` ボタンで対話デバッグパネルを開きます。

- **Step**: N サイクル進める
- **Until ns**: 指定時刻まで実行
- **Force / Set / Release**: 信号値を強制設定・解除
- **ブレークポイント**: 条件式（例: `count == 5`）で停止

---

## 4. HTTP API リファレンス

すべてのエンドポイントは `http://127.0.0.1:<port>` に対してアクセスします。  
`Host` ヘッダが `127.0.0.1`・`localhost`・`[::1]` 以外の場合は **403** を返します。

### 4.1 ヘルスチェック

```
GET /health
```

**レスポンス例**:
```json
{"ok": true}
```

---

### 4.2 RTL ロード

```
POST /api/load
```

**リクエスト**:
```json
{
  "files": ["/path/to/counter.sv"],
  "top": "counter",
  "params": {"WIDTH": 8}
}
```

**レスポンス**:
```json
{
  "module": "counter",
  "ports": [{"name": "clk", "direction": "input", "width": 1, "signed": false}],
  "params": {"WIDTH": 8},
  "signals": [...],
  "hierarchy": {...},
  "status": "PASS",
  "errors": [],
  "all_modules": [
    {"name": "counter", "is_top_candidate": true},
    {"name": "counter_sub", "is_top_candidate": false}
  ]
}
```

`all_modules`: 解析したすべてのモジュール。`is_top_candidate=true` はどこからもインスタンス化されていないもの（トップ候補）。

---

### 4.3 テストベンチ・スペック生成

```
POST /api/gen
```

**リクエスト**:
```json
{
  "sv_files": ["/path/to/counter.sv"],
  "top": "counter",
  "out_dir": null,
  "force": false
}
```

**レスポンス（ファイル不要モード: `out_dir`/`tb_path`/`spec_path` を省略）**:
```json
{
  "status": "PASS",
  "schema_version": "2",
  "module": "counter",
  "tb_sv": "module counter_tb; ...",
  "spec_yaml": "version: 1\ntop: counter\n..."
}
```

**ファイルモード（`out_dir` を指定）**:
- `force=false`（既定）: 既存ファイルがある場合 **409** を返す
  ```json
  { "conflict": ["/path/counter_tb.sv"], "detail": "ファイルが既に存在します..." }
  ```
- `force=true`: 既存ファイルを上書きして書き出す
  ```json
  { "status": "PASS", "tb_path": "/path/counter_tb.sv", "spec_path": "/path/counter_spec.yaml" }
  ```

---

### 4.10 エッジ検索

```
GET /api/waveform/edge?path=...&signal=clk&from_ns=100&direction=next&edge=rising
```

| パラメータ | 説明 |
|-----------|------|
| `path` | VCD ファイルパス |
| `signal` | 対象信号名 |
| `from_ns` | 基準時刻 (ns) |
| `direction` | `next`（後方検索）または `prev`（前方検索） |
| `edge` | `any`（任意変化）/ `rising`（L→H）/ `falling`（H→L） |

**レスポンス**:
```json
{"signal": "clk", "time_ns": 110, "found": true}
```

---

### 4.11 値デコード（符号付き変換）

```
GET /api/waveform/decode?value=1111&width=4&radix=signed
```

**レスポンス**:
```json
{"value": "1111", "width": 4, "radix": "signed", "result": "-1"}
```

`radix`: `bin` / `hex` / `unsigned` / `signed`

---

### 4.12 波形表示セット

```
GET /api/waveset?sv_files=/path/counter.sv&spec=/path/spec.yaml
POST /api/waveset
DELETE /api/waveset
```

**GET レスポンス**:
```json
{
  "sets": [
    {"name": "デフォルト", "signals": ["clk", "count"], "radix": {"count": "signed"}}
  ]
}
```

**POST リクエスト**:
```json
{
  "sv_files": ["/path/counter.sv"],
  "spec": "/path/spec.yaml",
  "name": "デフォルト",
  "signals": ["clk", "count"],
  "radix": {"count": "signed"}
}
```

**DELETE リクエスト**（ボディに JSON）:
```json
{"sv_files": [...], "spec": "...", "name": "デフォルト"}
```

---

### 4.4 テスト実行（SSE）

#### ジョブ開始

```
POST /api/test/start
```

**リクエスト**:
```json
{
  "sv_files": ["/path/to/counter.sv"],
  "spec": "/path/to/counter_spec.yaml",
  "top": "counter",
  "seed": 42,
  "vcd_out": null,
  "filter_cases": ["test_*"]
}
```

`vcd_out` が `null` かつ spec に `sim.vcd` が無い場合、一時 VCD を自動生成します（§18.4）。

**レスポンス**:
```json
{"job_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"}
```

#### SSE イベント購読

```
GET /api/test/{job_id}/events
```

→ [5. SSE イベント仕様](#5-sse-イベント仕様) を参照

#### 結果取得

```
GET /api/test/{job_id}/result
```

**レスポンス**:
```json
{
  "done": true,
  "result": {
    "schema_version": "2",
    "deltasim_version": "0.3.0",
    "module": "counter",
    "status": "PASS",
    "seed": 42,
    "elapsed_ms": 12.3,
    "summary": {"total": 1, "pass": 1, "fail": 0, "error": 0, "skip": 0, "elapsed_ms": 12.3},
    "cases": [...],
    "checks": [...],
    "errors": [],
    "warnings": [],
    "cancelled": false,
    "truncated_at": null,
    "vcd_path": "/tmp/deltasim_gui_abc123.vcd"
  }
}
```

`vcd_path`: 自動生成された一時 VCD のパス（UI が波形 API に渡す用途）。

#### キャンセル

```
POST /api/test/{job_id}/cancel
```

#### テスト結果エクスポート（§18.4）

```
GET /api/test/{job_id}/export?fmt=json
GET /api/test/{job_id}/export?fmt=junit
```

`fmt=json`: CLI `test --json` と同一構造の JSON ファイルをダウンロード  
`fmt=junit`: JUnit XML をダウンロード

---

### 4.5 テスト実行（同期・後方互換）

```
POST /api/test
```

リクエスト/レスポンス構造は非同期版と同じ。  
`vcd_out` が `null` かつ spec に `sim.vcd` が無い場合、一時 VCD を自動生成します。

---

### 4.6 シミュレーション実行（SSE）

```
POST /api/run/start
```

**リクエスト**:
```json
{
  "sv_files": ["/path/to/counter.sv"],
  "top": "counter",
  "cycles": 100,
  "vcd_out": null,
  "half_period": 5,
  "reset_cycles": 10
}
```

`vcd_out` が `null` の場合、一時 VCD を自動生成します。

---

### 4.7 波形データ

```
GET /api/waveform/index?path=/tmp/out.vcd
```

信号定義のみ取得（高速）。

```
GET /api/waveform/range?path=/tmp/out.vcd&signals=clk,count&t_start=0&t_end=500
```

指定信号・時間範囲の値変化を取得（遅延読み込み用）。

---

### 4.8 最近使ったプロジェクト（§18.4）

```
GET /api/recent-projects
```

**レスポンス例**:
```json
{
  "recent": [
    {
      "name": "counter",
      "sv_files": ["/path/to/counter.sv"],
      "top": "counter",
      "spec": "/path/to/counter_spec.yaml",
      "missing": false,
      "missing_paths": []
    },
    {
      "name": "old_project",
      "sv_files": ["/moved/file.sv"],
      "missing": true,
      "missing_paths": ["/moved/file.sv"]
    }
  ]
}
```

`missing=true`: 存在しないパスがある（UI で ⚠ 表示）

```
POST /api/recent-projects/add
```

**リクエスト**:
```json
{
  "sv_files": ["/path/to/counter.sv"],
  "top": "counter",
  "spec": "/path/to/counter_spec.yaml",
  "name": "counter"
}
```

設定ファイルの保存先:
- **Windows**: `%APPDATA%\deltasim\recent_projects.json`
- **Linux/Mac**: `$XDG_CONFIG_HOME/deltasim/recent_projects.json`（未設定時は `~/.config/deltasim/`）

---

### 4.9 その他のエンドポイント

| エンドポイント | 説明 |
|-------------|------|
| `GET /api/examples` | サンプルプロジェクト一覧 |
| `GET /api/browse/roots` | ファイルブラウザ許可ルート |
| `GET /api/browse?path=...` | ディレクトリ内容 |
| `GET /api/file/read?path=...` | ファイル内容取得 |
| `POST /api/file/write` | ファイル書き込み |
| `GET /api/project?path=...` | プロジェクトファイル読み込み |
| `POST /api/project` | プロジェクトファイル保存 |
| `GET /api/spec/cases?path=...` | スペックのケース名一覧 |
| `POST /api/simulator` | Simulator セッション作成（§8） |

---

## 5. SSE イベント仕様

`GET /api/test/{job_id}/events` は `text/event-stream` 形式でイベントを配信します。

### イベント形式

```
data: {"event": "<type>", ...}\n\n
```

### イベント一覧

| event | フィールド | 説明 |
|-------|----------|------|
| `start` | `spec` | テスト開始 |
| `progress` | `cycle`, `total`, `phase`, `time_ns` | 進捗 |
| `case_done` | `name`, `status`, `checks`, `seed` | ケース完了 |
| `finish` | — | すべて完了（ストリーム終了） |
| `error` | `message` | 実行時エラー |

### `progress` イベント例

```json
{
  "event": "progress",
  "cycle": 50,
  "total": 100,
  "phase": "sim",
  "time_ns": 500
}
```

### `case_done` イベント例

```json
{
  "event": "case_done",
  "name": "counter_spec",
  "status": "PASS",
  "seed": 42,
  "checks": [
    {"name": "count@cycle5[spec]", "status": "PASS", "cycle": 5, "time_ns": 45, "offset_ns": 0}
  ]
}
```

ストリームは `finish` または `error` イベントで終了します。  
`GET /api/test/{job_id}/result` で最終結果を取得してください。

---

## 6. コンソール入力欄（§18.4 / §18.1 対話デバッグ）

### 6.1 使い方

ログパネルの **「コンソール」** ボタンをクリックするとコンソール入力欄が表示されます。

```
vsim> examine count
0x05
vsim> run 20
# t=120ns  cycle=12
vsim> bp -time 200
# bp [1] @ 200ns
vsim> run -all
# ブレークポイント停止: @ 200ns (t=200ns)
```

- **Enterキー** または **「実行」ボタン** でコマンドを送信
- **上下矢印キー** でコマンド履歴をたどれます（最大 200 件）
- **「■ 中断」ボタン** で `run -all` 等の長時間コマンドをキャンセル
- `quit` / `exit` はセッションを終了します（サーバーは継続中）

コンソールはデバッグパネル（🔬）と **同一セッション** を共有します。  
セッションがない場合は、デバッグパネルを開いてセッションを作成してください。

### 6.2 使えるコマンド（§18.1 対応）

詳細は `docs/interactive.md` を参照してください。

| コマンド | 説明 |
|---------|------|
| `run N[ns\|us\|ps]` | N 時間進める |
| `run -all` | 終了またはブレークポイントまで実行 |
| `run -continue` | 次のブレークポイントまで実行 |
| `examine [-radix ...] SIGNAL` | 信号値を表示 |
| `force SIGNAL VALUE` | 信号を強制固定 |
| `noforce SIGNAL` | 強制固定を解除 |
| `restart` | t=0 にリセット |
| `bp -time T` | 時刻ブレークポイント設定 |
| `bp -list` | ブレークポイント一覧 |
| `bp -delete ID` | ブレークポイント削除 |
| `when {COND} {CMDS}` | 条件監視（False→True 遷移で発火） |
| `do FILE` | Tcl スクリプトファイルを実行 |
| `quit` / `exit` | セッション終了 |
| `help` | コマンド一覧 |
| Tcl: `set var value`, `if`, `for`, `foreach`, `while`, `proc`, `expr`, `puts` | Tcl サブセット |

> **セキュリティ**: 実行できるのは deltasim の対話コマンドのみです。  
> シェルコマンド（`ls`、`dir` 等）、Tcl の `exec`、Python の `eval`/`exec` は使えません。  
> `do <file>` のパスはサーバーの許可ルートで検証されます。

### 6.3 ブレークポイントと Questa との違い

**bp の停止タイミング**:  
deltasim は「その時刻の処理（NBA 反映・デルタサイクル収束・VCD 書き込み）が完了してから停止」します。  
Questa は即時停止のため、同一時刻でも内部状態が異なる場合があります。

**when コマンド**:  
`when` は信号変化のたびに条件を評価し、False→True に変化したときに本体コマンドを実行します。  
true のままの変化は二度目以降は発火しません（Questa と同様）。

### 6.4 コンソール HTTP API

**コマンド実行開始**:
```
POST /api/console/{session_id}/exec/start
{"command": "run 100"}
→ {"job_id": "...", "session_id": "..."}
```

**SSE 進捗取得**:
```
GET /api/console/{session_id}/exec/{job_id}/events
```

SSE イベント:
| event | フィールド | 説明 |
|-------|---------|------|
| `output` | `lines: [str]` | コマンド出力（リアルタイム） |
| `finish` | `time_ns, cycle, output, paused_bp, quit` | 完了 |
| `error` | `message` | 実行エラー |

`finish` の `paused_bp` フィールド（null または以下）:
```json
{
  "id": 1,
  "label": "@ 200ns",
  "time_ns": 200
}
```
bp で停止した場合は UI が波形カーソルを `time_ns` に移動します。

**コマンドキャンセル**:
```
POST /api/console/{session_id}/exec/{job_id}/cancel
→ {"ok": true, "job_id": "..."}
```

**セッション状態取得**:
```
GET /api/console/{session_id}/state
→ {"session_id": "...", "time_ns": 200, "cycle": 20, "breakpoints": [...], "whens": [...]}
```

---

*このドキュメントは §18.8 の GUI 係担当分です。HTTP API・CLI 仕様の詳細は docs/api.md を参照してください。*

---

## §18.5 新エラーコードの表示

### `E_AMBIGUOUS_TOP`
複数のトップ候補がある（どれをトップにするか不明）場合に発生します。

ログパネルに候補一覧が表示されるので、サイドバーの「トップモジュール」ドロップダウンから選択して再度ロードしてください。または RTL ファイル引数や `deltasim.project.yaml` で `top:` を指定してください。

### `E_UNKNOWN_PORT`
スペック YAML に書かれた信号名が RTL モジュールのポートに存在しない場合に発生します。

テスト結果パネルまたはログパネルにメッセージが表示されます。スペックの `check:` / `set:` で使用している信号名を確認してください。

### `E_NO_CHECK`
テストケースに有効なチェック（`expect:`）が1件もない場合に `ERROR` として扱います（PASS にしません）。

スペック YAML の `cases[].expect` に少なくとも1つのチェックを追加してください。

### `--log` オプションと LLM 実行
PR #25 以降、CLI の `test`/`run`/`gen`/`parse` コマンドはデフォルトで `deltasim.log` を作業フォルダに書き出します。

GUI の LLM 機能（コマンド提案の実行）でこれらのコマンドを起動する場合、**PR #26** で実装された §18.5.1 の方針により、実行時の作業フォルダをプロジェクトフォルダに設定します。そのため `deltasim.log` はプロジェクトフォルダに書かれ、CLI を直接使った場合と同じ動作になります。コマンドへの余分な引数追加は行いません（表示コマンドと実行が一致）。
## 7. LLM 連携（§18.3）

### 7.1 設定

`simconfig.yaml` に LLM 設定を追加してください（詳細は `docs/llm.md`）:

```yaml
llm:
  endpoint: http://127.0.0.1:11434
  model: llama3
```

設定がない場合、🤖 LLM ボタンに ❌ マークが表示され、LLM 機能は無効になります。他の機能（テスト実行・波形表示等）は影響を受けません。

### 7.2 コマンド提案（§18.3.2 仕様確定）

LLM パネルのテキスト入力に「counter をテストして」などと入力して「提案」ボタンを押します。

- 提案コマンドが表示されます（例: `deltasim test counter.sv --spec counter_spec.yaml`）
- 書き込みファイルがある場合はその一覧も表示されます
- **「実行」ボタンを押したときだけ実行されます**（提案だけでは何も実行されません）
- `safe=False`（シェル記号・再帰等を含む）の提案は実行ボタンが無効になり、理由を表示します

**LLM に渡すコンテキスト（§18.3.2）**: ファイル名・モジュール名・ポート一覧のみ。RTL ファイルの中身は渡しません。YAML 下書きと失敗の解析には中身も渡します。

**実行結果の追記（§18.3.2）**: 実行ごとに「時刻+コマンド」の見出しを付けて追記します。「クリア」ボタンで消去できます。最新 2000 行を保持します。

**セキュリティ**: サーバーはサーバーが発行した提案 ID のみを受け付けます。任意のコマンド文字列を受け取って実行する API は存在しません。

### 7.3 YAML スペック下書き生成（§18.3.2 仕様確定）

「スペック YAML 下書き生成」ボタンで LLM が RTL を解析してスペックを生成します。生成結果はエディタに表示され、検証に通らない場合は警告を表示します。

**保存先（§18.3.2）**: 「💾 保存」ダイアログが開きます。
- **top が設定されている場合**: 
- **top が未設定の場合**: 最初の SV ファイルの stem を使った 
- 保存先はプロジェクトフォルダ
- 同名ファイルがある場合は上書き確認ダイアログが表示されます

### 7.4 テスト失敗の解析

テスト実行後に FAIL/ERROR ケースの詳細パネルに「🤖 失敗を解析」ボタンが表示されます。RTL・スペック・失敗結果を LLM に送って日本語の解析を返します。**出力は常にテキストとして表示します（HTML として解釈しません）**。

**中断（§18.3.2）**: 「■」ボタンを押すと即座に「中断しました」と表示され、UI が操作可能に戻ります。バックグラウンドの HTTP リクエストは残りますが、返ってきた結果は破棄されます。中断後すぐに新しい要求を送れます。

### 7.5 エラーコード

| コード | 説明 |
|--------|------|
| `E_LLM_NOT_CONFIGURED` | simconfig.yaml に llm 設定がない |
| `E_LLM_CONNECT` | LLM サーバーへの接続失敗・タイムアウト |
| `E_LLM_UNSAFE_CMD` | LLM が返したコマンドが安全でない（実行ボタン無効） |

### 7.6 LLM HTTP API

```
GET /api/llm/status
→ {"configured": true/false, "model": "...", "endpoint": "..."}

POST /api/llm/suggest/start
{"text": "counter をテストして", "sv_files": [...], "spec": "..."}
→ {"job_id": "..."}

GET /api/llm/suggest/{job_id}/events  (SSE)
→ finish: {"suggestion_id": "...", "command_line": "...", "safe": true, "write_paths": [...]}
→ error:  {"code": "E_LLM_NOT_CONFIGURED", "message": "..."}

POST /api/llm/run/start
{"suggestion_id": "xxxxxxxx-..."}   ← suggestion_id フィールドのみ。任意コマンド文字列は不可。
→ {"job_id": "..."}

GET /api/llm/run/{job_id}/events  (SSE)
→ finish: {"exit_code": 0, "stdout": "...", "stderr": "..."}

POST /api/llm/draft-spec/start
{"sv_files": [...], "top": "counter"}
→ {"job_id": "..."}

GET /api/llm/draft-spec/{job_id}/events  (SSE)
→ finish: {"yaml_text": "...", "valid": true, "errors": [...]}

POST /api/llm/explain/start
{"result": {...}, "sv_files": [...], "spec": "..."}
→ {"job_id": "..."}

GET /api/llm/explain/{job_id}/events  (SSE)
→ finish: {"explanation": "テスト失敗の原因は..."}
```

---

## 8. .f ファイルリスト（§18.2）

ファイルブラウザで `.f` ファイルを選択すると `load_filelist` を使って解析し、解決した SV ファイルをプロジェクトに追加します。`W_FILELIST_OPT`（未対応オプション）などの警告とエラーは、`code`・`message`・`file:line` 付きでバナーに表示されます。

**HTTP API**:
```
POST /api/filelist/load
{"path": "/path/to/files.f"}
→ {
    "files": ["counter.sv", ...],
    "warnings": [{"code": "W_FILELIST_OPT", "message": "...", "file": "...", "line": 3}],
    "errors": [...]
  }
```

---

## 9. `deltasim.log` の生成について（§18.5.1）

PR #25 以降、CLI の `test`/`run`/`gen`/`parse` コマンドは実行時に作業フォルダへ `deltasim.log` を書き出します（既定）。

GUI の LLM 機能でコマンド提案を実行する場合（`/api/llm/run/start`）:

- **作業フォルダ**: プロジェクトの SV ファイルが置かれているフォルダ（`project_dir`）
- **コマンド引数**: 変更なし（表示コマンドと実際の実行が一致する）
- **`deltasim.log` の保存先**: プロジェクトフォルダ（CLI を直接使った場合と同じ）

`/api/llm/run/start` のリクエスト:
```json
{
  "suggestion_id": "xxxxxxxx-...",
  "project_dir": "/path/to/project"
}
```

`project_dir` が指定されない場合は実行プロセスの現在ディレクトリで実行されます。
