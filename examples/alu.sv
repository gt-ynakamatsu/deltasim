// alu.sv — 4 演算 ALU（adder を階層インスタンス）
// op: 00=ADD  01=SUB  10=AND  11=OR
module alu #(
    parameter WIDTH = 8
) (
    input  logic [WIDTH-1:0]  a,
    input  logic [WIDTH-1:0]  b,
    input  logic [1:0]        op,
    output logic [WIDTH-1:0]  result,
    output logic              carry_out
);
    logic [WIDTH-1:0] add_sum;
    logic             add_carry;

    adder #(.WIDTH(WIDTH)) u_add (
        .a(a), .b(b), .sum(add_sum), .carry_out(add_carry)
    );

    always_comb begin
        carry_out = 1'b0;
        case (op)
            2'b00: begin result = add_sum;  carry_out = add_carry; end
            2'b01: result = a - b;
            2'b10: result = a & b;
            2'b11: result = a | b;
            default: result = '0;
        endcase
    end
endmodule
