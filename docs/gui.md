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

> **既知の制限**: `generate` ブロック内のインスタンス化はトップ候補の判定に含まれません。
> `generate for` ブロック内でのみ使われるモジュールも「トップ候補」と誤判定されることがあります。
> 将来のバージョンでは CLI のトップ自動選択（`--top` 省略時の推定）と同じ判定に合わせる予定です。

### 3.3 生成（⚙ 生成ボタン）

RTL を読み込んだ状態で `⚙ 生成` をクリックすると、テストベンチ SV と YAML スペックのスケルトンを生成してエディタに表示します（ファイルには書き出しません）。

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
| ＋/－ | ズームイン/アウト |
| ⊡ 全体 | 全体を表示 |
| HEX/BIN/DEC | 表示基数を切替 |

信号の値変化は遅延取得で描画します（最大 200 信号まで同時表示）。

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
  "top": "counter"
}
```

**レスポンス（ファイル不要モード）**:
```json
{
  "status": "PASS",
  "schema_version": "2",
  "module": "counter",
  "tb_sv": "module counter_tb; ...",
  "spec_yaml": "version: 1\ntop: counter\n..."
}
```

`out_dir`/`tb_path`/`spec_path` を指定するとファイルを書き出します。

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

*このドキュメントは §18.8 の GUI 係担当分です。HTTP API・CLI 仕様の詳細は docs/api.md を参照してください。*
