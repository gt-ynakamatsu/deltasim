// shift_reg.sv
// 4 ビット SIPO (Serial-In Parallel-Out) シフトレジスタ
// 同期アクティブ Low リセット

module shift_reg (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       d,
    output logic [3:0] q
);

    always_ff @(posedge clk) begin
        if (!rst_n)
            q <= 4'b0000;
        else
            q <= {q[2:0], d};
    end

endmodule
