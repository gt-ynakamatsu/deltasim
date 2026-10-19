// fifo.sv — 同期 FIFO（登録出力方式）
// rd_en アサート時に data を取り込んでから rd_ptr をインクリメント
// → check 時刻と rd_data が 1 クロックレイテンシなしでマッチ
module fifo #(
    parameter WIDTH = 8,
    parameter DEPTH = 4
) (
    input  logic             clk,
    input  logic             rst_n,
    input  logic             wr_en,
    input  logic [WIDTH-1:0] wr_data,
    input  logic             rd_en,
    output logic [WIDTH-1:0] rd_data,
    output logic             full,
    output logic             empty
);
    // アンパック配列メモリ（M4 デモ）
    logic [WIDTH-1:0] mem [DEPTH-1:0];

    logic [1:0] wr_ptr;
    logic [1:0] rd_ptr;
    logic [2:0] cnt;
    logic [WIDTH-1:0] rd_data_reg;

    assign full    = (cnt == 3'd4);
    assign empty   = (cnt == 3'd0);
    assign rd_data = rd_data_reg;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wr_ptr      <= 2'd0;
            rd_ptr      <= 2'd0;
            cnt         <= 3'd0;
            rd_data_reg <= '0;
        end else begin
            // 書き込み
            if (wr_en && !full) begin
                mem[wr_ptr] <= wr_data;
                wr_ptr      <= wr_ptr + 2'd1;
            end
            // 読み出し: データを取り込んで rd_ptr をインクリメント
            if (rd_en && !empty) begin
                rd_data_reg <= mem[rd_ptr];
                rd_ptr      <= rd_ptr + 2'd1;
            end
            // カウンタ更新
            if (wr_en && !full && !(rd_en && !empty))
                cnt <= cnt + 3'd1;
            else if (!(wr_en && !full) && (rd_en && !empty))
                cnt <= cnt - 3'd1;
        end
    end

endmodule
