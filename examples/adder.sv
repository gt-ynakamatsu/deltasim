// adder.sv — パラメータ付き N ビット加算器（組み合わせ）
// {carry_out, sum} = a + b
module adder #(
    parameter WIDTH = 8
) (
    input  logic [WIDTH-1:0] a,
    input  logic [WIDTH-1:0] b,
    output logic [WIDTH-1:0] sum,
    output logic             carry_out
);
    assign {carry_out, sum} = a + b;
endmodule
