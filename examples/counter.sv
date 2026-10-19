// counter.sv
// 非同期アクティブ Low リセット付き N ビット自由カウンタ

module counter #(
    parameter WIDTH = 8
) (
    input  logic             clk,
    input  logic             rst_n,
    output logic [WIDTH-1:0] count
);

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            count <= '0;
        else
            count <= count + 1;
    end

endmodule
