# CHANGELOG

このファイルはリリースごとの変更点をまとめています。  
バージョン番号は [Semantic Versioning](https://semver.org/) に準じます。

---

## [0.3.0+] — 開発中（§18.2 -f ファイルリスト + §18 対話デバッグ）

### 追加（§18.6 constraints: 制約付き乱数生成）

- YAML スペックに `constraints:` フィールドを追加
  - 信号間の式（例: `a + b < 16`）でランダム刺激を制約
  - リジェクションサンプリングで実装（`max_constraint_attempts` 上限、既定 10000）
  - スペック全体またはケースごとに指定可能
  - 新エラーコード: `E_CONSTRAINT`（上限超過）、`E_CONSTRAINT_EXPR`（式エラー）
  - `random.Random(seed)` で seed 固定 → Windows/Linux 同一値列

### 修正（§18.5 不具合修正）

- **TB 生成**: 内部信号（direction=None）をポート接続から除外（E_UNKNOWN_PORT エラーを追加）
- **`gen --strings` 見出し**: ハードコードから実際のモジュール名に変更
- **`$monitor`**: 値変化時のみ出力・同一時刻内重複なし（エンジン共有状態で管理）
- **E_NO_CHECK**: チェック0件の spec → E_NO_CHECK エラー（全 skip は除外）
- **top 自動検出**: インスタンスされていないモジュールを候補に。複数なら E_AMBIGUOUS_TOP
- **`find_top_candidates()`**: 公開 API として追加（GUI から使用可能）
- **`--log`**: run/test/gen/parse に追加（デフォルト `deltasim.log`）
- safety.py `_WRITE_OPTS_LONG` に `--log` を追加

### 追加（§18.3 ローカル LLM 最小実装）

- **`deltasim ask "<文>"`**: 自然言語コマンド提案 + 確認実行
- **`deltasim llm draft-spec`**: RTL → YAML スペック下書き生成
- **`deltasim llm explain`**: テスト失敗の LLM 解析
- 設定: `simconfig.yaml` の `llm` 節（未設定なら通信なし）
- 新エラーコード: `E_LLM_NOT_CONFIGURED`, `E_LLM_CONNECT`, `E_LLM_UNSAFE_CMD`

### 確定事項（§18.1.1）

- **行ブレークポイントの停止タイミング**: deferred 方式（その時刻の処理をすべて終えてから停止）。  
  Questa は文を実行する前に止まるが、deltasim は文の実行後（NBA・negedge・VCD 記録完了後）に止まる。  
  bp を置いても置かなくても信号値と VCD が一致することを優先した設計。

### 追加（§18.2 -f ファイルリスト）

- `deltasim test`・`run`・`gen`・`parse` に `-f FILE.f` オプションを追加
- 複数回の `-f` 指定、通常のソース引数との併用が可能
- **公開関数**: `deltasim.filelist.load_filelist(path) -> FileList`
  - `FileList(files, incdirs, defines, warnings, errors)` を返す
  - `files`: 絶対パスに解決された SV ファイルリスト
  - `incdirs`: `+incdir+` ディレクトリリスト
  - `defines`: `+define+` マクロ辞書
  - `warnings`: `W_FILELIST_OPT` 警告リスト（行番号付き）
  - `errors`: `E_FILE_NOT_FOUND` エラーリスト（行番号付き）
- `deltasim.filelist.expand_filelist_args(sv_files, filelist_args)` も公開
- GUI の `resolve_paths_from_f_file` は `load_filelist` に置き換え（共有）
- 対応書式: `//`・`#` コメント、空行無視、CRLF、UTF-8 BOM
- 相対パスは `.f` ファイルの場所を基準に解決（CWD 非依存）
- 入れ子 `-f`、循環検出、未対応オプション → `W_FILELIST_OPT`
- 存在しないファイル → `E_FILE_NOT_FOUND`（ファイル名・行番号付き）

---

## [0.3.0+] — 開発中（§18 対話デバッグ）

### 互換性のない変更（§18.1.1）

- **`set <信号名> <値>` の挙動変更**: `set` は常に Tcl 変数への代入になりました。信号に値を入れるには **`force <信号名> <値>`** を使ってください。
  - 旧: `set clk 1` → clk 信号を 1 に強制
  - 新: `set clk 1` → Tcl 変数 `clk` に `"1"` を代入（信号は変わらない）。信号名と同じ変数名を使うと警告が1回表示されます。

### 追加（§18 対話デバッグ）

- `deltasim run -i` / `deltasim sim` で Questa 風対話デバッグコマンドを利用可能
- `run [N[ns|ps|us]]`・`run -all`・`run -continue`（ns 精度で停止）
- `examine [-radix hex|bin|dec] SIGNAL`
- `force SIGNAL VALUE`・`noforce SIGNAL`
- `bp FILE:LINE`（行ブレークポイント、文のない行は次の行に自動調整）・`bp -time Tns`・`bp -list`・`bp -delete ID`
- `when {COND} {CMDS}`（False→True 遷移で発火、信号名のみは変化ごとに発火）
- ミニ Tcl インタープリタ（`set`/`if`/`for`/`foreach`/`while`/`proc`/`expr`/`puts` など）
- SV 式サブセットの自前パーサ（Python `eval/exec` 不使用）
- docs 追加: `docs/interactive.md`・`docs/expr_syntax.md`・`docs/error_codes.md`・`docs/json_schema.md`・`docs/spec_format.md`

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
