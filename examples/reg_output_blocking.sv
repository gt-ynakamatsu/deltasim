// スタイル上の誤り: always_ff 内でブロッキング代入 = を使用（本来は <= を使う）
// この単純な 1FF 構成では観測可能な動作が正しいコードと同一になる
// ⚠️ 整数サイクル検査では PASS する（PASS_KNOWN_LIMITATION）
// 理由: 単一 FF の場合、ブロッキング代入と非ブロッキング代入は同じ結果を生成する
module d_ff(
    input  logic clk,
    input  logic rst,
    input  logic d,
    output logic q
);
    always_ff @(posedge clk) begin
        if (rst) q = 1'b0;  // 誤り: <= を使うべき
        else     q = d;      // 誤り: <= を使うべき
    end
endmodule
