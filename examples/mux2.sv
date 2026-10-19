// mux2.sv
// パラメータ付き 2 入力セレクタ（純組み合わせ）

module mux2 #(
    parameter WIDTH = 8
) (
    input  logic             sel,
    input  logic [WIDTH-1:0] a,
    input  logic [WIDTH-1:0] b,
    output logic [WIDTH-1:0] y
);

    assign y = sel ? a : b;

endmodule
