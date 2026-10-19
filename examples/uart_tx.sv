// uart_tx.sv — 8N1 UART 送信器（typedef enum ステートマシン）
// CLK_DIV クロックに1ビット送信（最大 CLK_DIV=7 で 3 ビット fit）
module uart_tx #(
    parameter CLK_DIV = 4   // クロック / ボーレート
) (
    input  logic       clk,
    input  logic       rst_n,
    input  logic [7:0] data,    // 送信データ
    input  logic       send,    // 1 サイクルパルスで送信開始
    output logic       tx,      // シリアル出力（アイドル時 High）
    output logic       busy     // 送信中 High
);

    typedef enum logic [1:0] {
        S_IDLE  = 2'd0,
        S_START = 2'd1,
        S_DATA  = 2'd2,
        S_STOP  = 2'd3
    } state_t;

    state_t       state;
    logic [2:0]   bit_cnt;    // データビット番号 0..7
    logic [7:0]   shift_reg;
    logic [2:0]   clk_cnt;    // ボーレート分周カウンタ

    assign busy = (state != S_IDLE);
    assign tx   = (state == S_START) ? 1'b0       :
                  (state == S_DATA)  ? shift_reg[0] :
                                       1'b1;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state     <= S_IDLE;
            bit_cnt   <= 3'd0;
            shift_reg <= 8'd0;
            clk_cnt   <= 3'd0;
        end else begin
            case (state)
                S_IDLE: begin
                    if (send && !busy) begin
                        state     <= S_START;
                        shift_reg <= data;
                        clk_cnt   <= 3'd0;
                    end
                end
                S_START: begin
                    if (clk_cnt == CLK_DIV[2:0] - 3'd1) begin
                        state   <= S_DATA;
                        bit_cnt <= 3'd0;
                        clk_cnt <= 3'd0;
                    end else
                        clk_cnt <= clk_cnt + 3'd1;
                end
                S_DATA: begin
                    if (clk_cnt == CLK_DIV[2:0] - 3'd1) begin
                        clk_cnt <= 3'd0;
                        if (bit_cnt == 3'd7) begin
                            state <= S_STOP;
                        end else begin
                            shift_reg <= {1'b0, shift_reg[7:1]};
                            bit_cnt   <= bit_cnt + 3'd1;
                        end
                    end else
                        clk_cnt <= clk_cnt + 3'd1;
                end
                S_STOP: begin
                    if (clk_cnt == CLK_DIV[2:0] - 3'd1) begin
                        state   <= S_IDLE;
                        clk_cnt <= 3'd0;
                    end else
                        clk_cnt <= clk_cnt + 3'd1;
                end
                default: state <= S_IDLE;
            endcase
        end
    end

endmodule
