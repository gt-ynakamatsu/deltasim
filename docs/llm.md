# LLM 連携（§18.3）

deltasim に組み込みの LLM クライアント機能です。ローカル LLM（ollama など）と連携して、コマンド提案・スペック生成・失敗解析を行います。

> **設定しなければ通信しない**: `simconfig.yaml` の `llm` 節が設定されていない場合、LLM 機能はエラーを返すだけで、外部へのネットワーク通信は一切行いません。他のコマンド（`test`/`run`/`gen` など）も影響を受けません。

---

## 設定ファイル `simconfig.yaml`

以下の場所を順番に探します:
1. カレントディレクトリ `./simconfig.yaml`
2. ユーザー設定ディレクトリ
   - Windows: `%APPDATA%\deltasim\simconfig.yaml`
   - その他: `$XDG_CONFIG_HOME/deltasim/simconfig.yaml`（未設定なら `~/.config/deltasim/simconfig.yaml`）

### 設定例（ollama）

```yaml
llm:
  endpoint: http://127.0.0.1:11434
  model: llama3
  timeout_s: 60
  api: ollama            # "ollama"（既定）または "openai"
```

### 設定例（OpenAI 互換 API）

```yaml
llm:
  endpoint: http://127.0.0.1:8000
  model: qwen2.5-coder
  timeout_s: 120
  api: openai
```

### 設定項目

| 項目 | 既定値 | 説明 |
|------|--------|------|
| `endpoint` | `http://127.0.0.1:11434` | LLM サーバーの URL |
| `model` | （必須） | モデル名（例: `llama3`, `qwen2.5-coder`）|
| `timeout_s` | `60` | タイムアウト秒数 |
| `api` | `ollama` | API 形式: `ollama` または `openai` |

`model` が設定されていない場合は「LLM が未設定」として扱われます。

---

## コマンド

### `deltasim ask "<文>"`

自然言語で指示して deltasim コマンドを提案・実行します。

```bash
# 確認あり（既定）
deltasim ask "counter.sv をテストして"

# 確認なし（--yes）
deltasim ask "カウンタの spec を作って" --yes
```

- 提案コマンドを表示し `y/N` で確認してから実行
- `--yes` で確認省略
- 実行できるのは `test`/`run`/`gen`/`parse`/`sim` のみ
- シェル記号（`;|>&\`` 等）・再帰（`ask`/`llm`）・複数コマンドは拒否

### `deltasim llm draft-spec <FILES> -o <output.yaml>`

RTL から YAML テストスペックの下書きを生成します。

```bash
deltasim llm draft-spec counter.sv -o counter_spec_draft.yaml
deltasim llm draft-spec -f files.f --top counter -o spec.yaml --force
```

- 出力先に既存ファイルがあれば `--force` が必要
- 生成後に deltasim の検証を実行。検証エラーがあっても保存し、警告を表示
- 終了コード: 0 = 成功、6 = 検証エラーあり（保存済み）

### `deltasim llm explain <result.json>`

テスト失敗を LLM に解析させます。

```bash
deltasim llm explain result.json --rtl counter.sv --spec counter_spec.yaml
deltasim llm explain result.json -f files.f
```

- 失敗がなければ LLM を呼ばずに「失敗はありません」と表示
- `--rtl` / `-f` / `--spec` でファイルを指定（文脈として LLM に渡す）

### 対話セッション内での `ask`

`deltasim sim` や `deltasim run -i` の REPL 内でも `ask` が使えます。

```
vsim> ask "count が 5 になるまで実行して"
提案: run -all
実行しますか？ [y/N] y
# t=50ns  cycle=5
```

---

## Python API

```python
from deltasim.llm import (
    load_llm_config,
    suggest_command,
    run_suggestion,
    draft_spec,
    explain_result,
)

# 設定読み込み
cfg = load_llm_config()           # simconfig.yaml を自動検索
cfg = load_llm_config("path/to/simconfig.yaml")  # パス指定

# コマンド提案（実行しない）
sug = suggest_command("counter をテスト", cfg=cfg)
if sug.safe:
    print(sug.command_line)       # "deltasim test counter.sv --spec ..."

# 実行
rc = run_suggestion(sug, yes=True)

# スペック生成
result = draft_spec(["counter.sv"], top="counter", cfg=cfg)
print(result.yaml_text)
print(result.valid)               # True/False

# 失敗解析
text = explain_result(result_json_dict, rtl_files=["counter.sv"], cfg=cfg)
print(text)
```

### LLM をモックする

すべての公開関数は `llm_fn` 引数でモックを差し込めます:

```python
def mock_llm(prompt: str, cfg) -> str:
    return "deltasim test counter.sv --spec spec.yaml"

sug = suggest_command("テスト", cfg=cfg, llm_fn=mock_llm)
```

---

## エラーコード

| コード | 説明 |
|--------|------|
| `E_LLM_NOT_CONFIGURED` | `simconfig.yaml` の `llm` 設定がない |
| `E_LLM_CONNECT` | LLM サーバーへの接続失敗・タイムアウト |
| `E_LLM_UNSAFE_CMD` | LLM が返したコマンドが安全でない（シェル記号・再帰等） |

---

## 制限事項

- **依存ライブラリ**: 標準ライブラリの `urllib` のみを使用。`httpx`/`requests` は不要。
- **シェルは使わない**: コマンド実行はプロセス内で行い `shell=True` は使いません。
- **+incdir+/+define+**: `-f` で読んだ `+incdir+` と `+define+` はまだパーサに渡されません（保持のみ）。
