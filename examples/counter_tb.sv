// counter_tb.sv — counter.sv のテストベンチ（deltasim run で直接実行可能）
// `deltasim run examples/counter_tb.sv examples/counter.sv --top counter_tb`
`timescale 1ns/1ps

module counter_tb;

    logic       clk;
    logic       rst_n;
    logic [7:0] count;

    // DUT: 8ビット自由カウンタ
    counter #(.WIDTH(8)) dut (
        .clk(clk),
        .rst_n(rst_n),
        .count(count)
    );

    // クロック生成: 100 MHz (10ns 周期)
    initial begin
        clk = 0;
        forever #5 clk = ~clk;
    end

    // リセット生成: 10 サイクル間アサート
    initial begin
        rst_n = 0;
        repeat(10) @(posedge clk);
        rst_n = 1;
    end

    // 刺激＋チェック
    initial begin
        // リセット解除待ち
        repeat(10) @(posedge clk);
        // 20 サイクル後に count == 10 を確認
        repeat(10) @(posedge clk);
        if (count !== 8'd10) begin
            $display("FAIL: count = %0d, expected 10", count);
        end else begin
            $display("PASS: count = %0d", count);
        end
        $finish;
    end

    // VCD 出力
    initial begin
        $dumpfile("counter_tb.vcd");
        $dumpvars(0, counter_tb);
    end

endmodule
