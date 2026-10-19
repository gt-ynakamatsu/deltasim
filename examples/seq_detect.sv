// seq_detect.sv — 1011 シーケンス検出器（Moore FSM, typedef enum 使用）
module seq_detect (
    input  logic clk,
    input  logic rst_n,
    input  logic din,
    output logic detected
);

    typedef enum logic [1:0] {
        S_IDLE = 2'b00,
        S_1    = 2'b01,
        S_10   = 2'b10,
        S_101  = 2'b11
    } state_t;

    state_t state, nxt;

    // レジスタ（非同期アクティブ Low リセット）
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) state <= S_IDLE;
        else        state <= nxt;
    end

    // 次状態ロジック（組み合わせ）
    always_comb begin
        case (state)
            S_IDLE: nxt = din ? S_1   : S_IDLE;
            S_1:    nxt = din ? S_1   : S_10;
            S_10:   nxt = din ? S_101 : S_IDLE;
            S_101:  nxt = din ? S_1   : S_10;
            default: nxt = S_IDLE;
        endcase
    end

    // 出力ロジック（Mealy: state==S_101 かつ din==1 のとき detected）
    assign detected = (state == S_101) && din;

endmodule
