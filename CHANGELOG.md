# CHANGELOG

このファイルはリリースごとの変更点をまとめています。  
バージョン番号は [Semantic Versioning](https://semver.org/) に準じます。

---

## [0.3.0] — 2026-10-22

### 追加
- **`deltasim gui`** コマンドが正式利用可能になりました（v0.3.0 以降）
  - ブラウザ内 Web UI（`http://127.0.0.1:8765`）で RTL の読み込み・テスト実行・波形表示が可能
  - ループバック（127.0.0.1）限定。外部ネットワークからのアクセスは不可
  - GUI のテスト結果 JSON に `deltasim_version`・`seed`・`elapsed_ms` フィールドを統一追加
  - SSE 進捗配信（start / progress / case_done / finish）
  - Canvas 波形ビューア（クロック・多ビット・X/Z 色分け・ズーム/パン）
  - CodeMirror エディタ（SV/YAML 構文ハイライト）
  - `pip install "deltasim[gui] @ git+..."` で FastAPI + uvicorn が追加インストールされます

### 変更
- インストール先を PyPI から Git リポジトリ直接（`@v0.3.0` タグ）に変更
- Windows のインストール手順を README に追記（PowerShell / cmd 両対応）

---

## [0.2.2] — 2026-10-19

### 修正
- **パーサ無限ループ修正**（§16）: `endmodule` が無い入力など、壊れた SV ファイルを渡すとパーサが無限ループになる不具合を修正
  - 根本原因: EOF トークンの `value` が `""` だったため `_at("EOF")` が常に `False` を返していた
  - 修正: `value = "EOF"` に変更し、全ループに EOF ガードを追加
  - 閉じていない `/*` コメントや未閉文字列リテラルを `LexError`（E001）で検出
  - `endmodule` なし → E001: `"endmodule がありません（module 'X' は N 行目で開始）"`

---

## [0.2.1] — 2026-10-19

### 追加
- **ANSI 複数ポート名宣言**対応: `input logic clk, rst, d` のように 1つの方向・型・幅の後に複数名を並べる書き方を受け付ける（IEEE 1800 準拠）
- **無限ループ対策**:
  - `E_DELTA_LIMIT`: 組み合わせ収束ループ上限（既定 10,000 delta）。`--max-delta N` / `max_delta=N`
  - `E_LOOP_LIMIT`: for/while/forever 反復上限（既定 1,000,000）。`--max-loop N` / `max_loop=N`
  - `deltasim run` コマンドにも `--max-delta` / `--max-loop` を追加
  - `--json` 付きのとき想定外例外でも JSON を出力（E_INTERNAL）
- `ds.run()` に `max_delta` / `max_loop` 引数追加

---

## [0.2.0] — 2026-10-19

### 追加
- **バージョン情報の単一定義**: `deltasim/_version.py` が唯一の定義箇所。`pyproject.toml` は `dynamic = ["version"]` で読む
- **`deltasim_version` フィールド**: `TestResult.to_dict()` と `deltasim test --json` のトップレベルに追加
- `deltasim --version` が正確なバージョンを返す

---

*v0.1.x 以前の変更履歴は省略しています。*
