module dff (input logic clk, input logic rst, input logic d, output logic q);
  assign q = rst ? 1'b0 : d;
endmodule
