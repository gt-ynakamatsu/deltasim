# 対話デバッグコマンドリファレンス（§18）

`deltasim run -i` または `deltasim sim` コマンドで起動する対話デバッグセッションのコマンド一覧です。

---

## 起動方法

```bash
# どちらも同じ
deltasim run -i examples/counter.sv
deltasim sim    examples/counter.sv
```

起動すると `vsim> ` プロンプトが表示されます。stdin にスクリプトを流すことも可能です。

---

## Questa 風コマンド

### `run`

シミュレーションを進めます。

```tcl
run 10ns         # 10 ns 進める
run 5 us         # 5 us (5000 ns) 進める
run 100 ps       # 100 ps 進める（1 ns 未満は切り捨て）
run -all         # 最大サイクルまで実行
run -continue    # 次のブレークポイントまで実行
```

時間単位: `ns`（デフォルト）, `us`, `ps`

### `examine`

信号値を表示します。

```tcl
examine count              # デフォルト（16進）
examine -radix hex   count
examine -radix bin   count
examine -radix dec   count   # 符号なし 10 進
examine -radix unsigned count
examine -radix signed count  # 符号付き 10 進
```

### `force` / `noforce`

信号を強制的に値に固定 / 解除します。

```tcl
force  count 42    # count を 42 に固定
noforce count      # 固定を解除
```

### `restart`

シミュレーションを t=0 にリセットします。

```tcl
restart
```

### `do`

Tcl スクリプトファイルを実行します。CRLF 改行にも対応しています。

```tcl
do script.do
do /path/to/setup.do
```

### `quit` / `exit`

セッションを終了します。

---

## ブレークポイント

### `bp` — ブレークポイント追加・管理

```tcl
bp examples/counter.sv:15   # 行番号ブレークポイント
bp -time 100ns               # 時刻ブレークポイント
bp -list                     # 一覧表示
bp -delete 1                 # ID 1 を削除
bp -delete 2
```

時刻ブレークポイントは `run -all` または `run -continue` で止まります。

### `when` — 条件付きアクション

```tcl
when {count == 10} {puts "count reached 10"}
when {count != 0} {examine count}
when {rst_n == 0} {puts "reset asserted!"}
```

`{COND}` は SV 式サブセット（下記「式文法」参照）で評価されます。
`{CMDS}` は Tcl スクリプトです。

---

## 後方互換コマンド（旧 `deltasim sim`）

> **⚠ 互換性のない変更（§18.1.1 / v0.4.0）**: `set <信号名> <値>` は v0.3.0 では
> 信号値を書き換える独自コマンドでしたが、v0.4.0 以降は Tcl の変数代入として動作します。
> 信号名と一致する場合は「信号に値を入れるには `force` を使ってください」と警告が表示されます。
>
> 信号に値を入れるには `force <信号名> <値>` を使ってください。
>
> YAML スペックの `stimulus/set:` の意味は変わっていません。

---

## 行ブレークポイントの動作方針（§18.1.1 確定）

### 停止タイミング: deferred（その時刻の処理をすべて終えてから停止）

行ブレークポイント (`bp file:line`) は、その文を含む時刻の処理（posedge → NBA 適用 → 組み合わせループ → negedge → VCD 記録）をすべて完了してから停止します。

> **Questa との違い**: Questa は文を実行する前に止まります（pre-execution 方式）。  
> deltasim は **post-execution 方式（deferred）** を採用しているため、止まった時点で `examine` すると、  
> その文や NBA が反映された後の値が見えます。

**この方式を採用した理由**:  
「bp を置いても置かなくても、シミュレーション結果（信号値と VCD の値変化）が完全に一致すること」を優先しました。  
即時停止方式では NBA キューが不完全なまま negedge や VCD 記録がスキップされ、  
bp の有無で結果が変わってしまいます。

**動作例**:
```
vsim> bp counter.sv:7   # q <= q + d が line 7
vsim> run -all
# t=10ns  cycle=1
# t=20ns  cycle=2
# bp [1] counter.sv:7 t=25ns
            ↑ t=25 の posedge + NBA + negedge + VCD 記録がすべて完了してから停止
vsim> examine q
q = 01      ← この文（q <= q + d）の NBA が反映された後の値
```

`run -continue` で次にその行を実行するところまで進みます。

```tcl
step [N]          # N サイクル進める（デフォルト 1）
until TIME_NS     # 指定時刻まで実行
get SIGNAL        # 信号値を表示
signals           # 全信号一覧
reset             # restart と同じ
help              # コマンド一覧
```

---

## 組み込み Tcl コマンド

| コマンド | 説明 |
|---------|------|
| `set VAR VALUE` | 変数に値を設定 |
| `set VAR` | 変数の値を取得 |
| `incr VAR [N]` | 変数を N 増やす（デフォルト 1） |
| `if {COND} {BODY} ?elseif {COND} {BODY}? ?else {BODY}?` | 条件分岐 |
| `for {INIT} {COND} {INCR} {BODY}` | for ループ |
| `foreach VAR LIST BODY` | リストのループ |
| `while {COND} {BODY}` | while ループ |
| `expr EXPR` | 式を評価して返す |
| `puts ?-nonewline? STRING` | 出力 |
| `proc NAME {PARAMS} {BODY}` | プロシージャ定義 |
| `return ?VALUE?` | プロシージャから返す |
| `break` | ループを抜ける |
| `continue` | 次の反復へ |
| `list ARGS...` | リスト作成 |
| `lindex LIST INDEX` | リストの要素を取得 |
| `llength LIST` | リストの長さ |
| `lappend VAR ITEM...` | リストに追加 |
| `append VAR STRING...` | 文字列を追加 |
| `string length\|index\|range\|toupper\|tolower\|equal STRING...` | 文字列操作 |
| `format FMT ARGS...` | フォーマット |
| `error MSG` | エラーを発生 |

---

## Tcl 構文サマリ

### 引用

```tcl
set a {$b}        # {} の中は展開しない → a = "$b"
set a "$b"        # "" の中は $var と [] を展開する
set a [expr {5+3}] # [] はコマンド置換
```

### 変数展開

```tcl
$varname          # 変数を展開
${varname}        # 名前に特殊文字を含む場合
```

### 区切り

セミコロン `;` と改行 `\n` がコマンドの区切りです。

### スクリプトファイル例

```tcl
# setup.do — 初期化スクリプト
run 20ns
bp -time 100ns
when {count == 5} {
    puts "count=5 reached!"
    examine -radix bin count
}
run -all
```

---

## ループ制限

無限ループ防止のため、各ループは最大 1,000,000 回で打ち切られます（`E_LOOP_LIMIT` エラー）。

---

## スクリプトの流し込み（E2E 使用例）

```bash
echo "run 100ns
examine count
quit" | deltasim sim examples/counter.sv
```

Windows では：

```powershell
@"
run 100ns
examine count
quit
"@ | deltasim sim examples\counter.sv
```
