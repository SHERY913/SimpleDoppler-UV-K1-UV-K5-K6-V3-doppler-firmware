/* 宿主端假电台：事件记录器 + 虚拟时钟 + 闪存/芯片模型。
 *
 * 口径：doppler.c 原样编进来，所有"写硬件"的调用都落成一条事件。
 * 断言看事件/芯片最终状态，不看屏幕字符串。
 */
#ifndef SIM_H
#define SIM_H

#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>

/* ---------------- 事件 ---------------- */

typedef enum {
    EV_RX_FREQ = 0,   /* a = freq10（10 Hz 单位），b = 0 */
    EV_TX_FREQ,       /* a = freq10 */
    EV_CTCSS,         /* a = tone10 */
    EV_CTCSS_OFF,     /* 退出亚音 */
    EV_SQUELCH,       /* a..f 打包不了，b 存 RSSI 门限 */
    EV_POWER,         /* a = OUTPUT_POWER_*，b = 对应 VFO 频率 */
    EV_FRONTEND,      /* a = 想要 UHF(1)/VHF(0) */
    EV_AUDIO,         /* a = 1 打开 / 0 关闭 */
    EV_DISPLAY,       /* a = GUI_DisplayType_t */
    EV_FLASH,         /* a = 地址，b = 长度 */
    EV_RADIO,         /* a = 1 开始发射 / 0 结束发射 */
    EV_APP,           /* 其它（PTT 事件、GPIO 等） */
    EV_KIND_N
} ev_kind_t;

typedef struct {
    uint32_t t10;     /* 事件发生时的虚拟时钟，单位 10 ms */
    uint8_t  kind;
    int32_t  a;
    int32_t  b;
} sim_ev_t;

#define SIM_MAX_EV 400000u
extern sim_ev_t g_ev[SIM_MAX_EV];
extern uint32_t g_ev_n;

const char *sim_ev_name(ev_kind_t k);

/* 记录一条事件并立刻刷到 stdout */
void sim_emit(ev_kind_t kind, int32_t a, int32_t b);

/* 不属于"写硬件"的旁记（按键、场景注释），也进事件流但带自己的名字 */
void sim_mark(const char *what);

/* 脚本里的场景分隔/说明，只打印不记事件 */
void sim_note(const char *fmt, ...);

/* ---------------- 虚拟时钟 ---------------- */

uint32_t sim_now10(void);
uint32_t sim_now_ms(void);
void     sim_clock_reset(void);

/* PTT 意图：由场景脚本置位/清除，决定 BK4819_SetFrequency 记成 TX 还是 RX。
 * 不能只看 gCurrentFunction —— 固件在 DPH_PttEvent 里先写频点后调
 * GENERIC_Key_PTT，写那一刻还是 FUNCTION_RECEIVE（doppler.c:3312 vs 3318）。 */
void sim_ptt_intent(bool on);

/* 推进 ms 毫秒：每 10 ms 递增一拍并调用一次 DOPPLER_Tick10ms() */
void sim_run_ms(uint32_t ms);

/* 同上但**不**调 tick —— 用来验证"没有 tick 就不会动芯片" */
void sim_idle_ms(uint32_t ms);

/* ---------------- 外部 SPI flash ---------------- */

#define SIM_FLASH_SIZE 0x00200000u   /* 2 MiB，够到表区 + 校准记录 0x1FF000 */

void            sim_flash_blank(void);
bool            sim_flash_load(const char *path, uint32_t at_addr, uint32_t max_len);
uint8_t        *sim_flash_ptr(void);
const uint8_t  *sim_flash_const(void);

/* ---------------- 芯片模型（最后一次写入的值） ---------------- */

typedef struct {
    uint32_t freq10;
    uint32_t ctcss10;
    uint8_t  squelch_open_rssi;
    uint8_t  filter_bw;
    bool     uhf_lna;
    bool     vhf_lna;
    uint32_t sub_au_exits;
    uint32_t register_writes;
} sim_chip_t;

extern sim_chip_t g_chip;

/* ---------------- 电台初始状态（gEeprom / VFO 指针） ---------------- */

void sim_radio_init(uint32_t rx10, uint32_t tx10, uint8_t power);

/* ---------------- 事件查询（断言用） ---------------- */

/* 从 idx 开始找第一条 kind 事件，返回下标；没有返回 -1 */
int  sim_find(ev_kind_t kind, uint32_t from);
/* 最后一条 kind 事件的下标，没有返回 -1 */
int  sim_find_last(ev_kind_t kind);
uint32_t sim_count(ev_kind_t kind);
/* 统计 [from_t10, to_t10] 之间某类事件条数 */
uint32_t sim_count_range(ev_kind_t kind, uint32_t from_t10, uint32_t to_t10);

void sim_dump(FILE *f, ev_kind_t kind);

#endif
