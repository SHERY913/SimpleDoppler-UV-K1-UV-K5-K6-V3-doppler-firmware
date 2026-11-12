/* 场景运行器：把"按了什么键、时间走了多久"翻译成对 doppler.c 的调用，
 * 再把芯片侧发生的事打出来。
 *
 * 用法：
 *   sim.exe m0     [表镜像]                 空场景：只验证进页/退页
 *   sim.exe s1     [表镜像]                 预听页 PTT 松手（交接-26 ①）
 *   sim.exe s2     [表镜像]                 就绪页 PTT 松手（交接-26 ②）
 *   sim.exe s3     [表镜像]                 进页改功率再退页（交接-26 ③）
 *   sim.exe all    [表镜像]                 三条都跑
 *   sim.exe script <脚本文件> [表镜像]       逐行解释按键脚本，只打事件流不判定
 *
 * 脚本语法（一行一条，# 后面是注释）：
 *   KEY_MENU / KEY_UP / KEY_DOWN / KEY_EXIT / KEY_6 ...   短按
 *   KEY_MENU HOLD 600                                     长按（held 连发档）
 *   KEY_SIDE1 [HOLD ms] / KEY_SIDE2
 *   PTT_ON / PTT_OFF
 *   ADVANCE 200                                           推进虚拟时钟
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

#include "sim.h"
#include "app/doppler.h"
#include "driver/keyboard.h"
#include "radio.h"
#include "settings.h"
#include "functions.h"
#include "misc.h"
#include "driver/st7565.h"      /* gFrameBuffer / FRAME_LINES / LCD_WIDTH */
#include "ui/ui.h"
#include "audio.h"              /* gBeepToPlay：s11 要断"拒绝时有没有响" */

static int g_fail = 0;

static void ok(const char *what)
{
    printf("[ PASS ] %s\n", what);
    fflush(stdout);
}

static void fail(const char *what)
{
    printf("[ FAIL ] %s\n", what);
    fflush(stdout);
    g_fail++;
}

static void check(bool cond, const char *what)
{
    if (cond)
        ok(what);
    else
        fail(what);
}

static void check_freq(uint32_t got, uint32_t want, const char *what)
{
    if (got == want)
    {
        printf("[ PASS ] %s  (芯片 = %u)\n", what, (unsigned)got);
    }
    else
    {
        printf("[ FAIL ] %s  (芯片 = %u，应为 %u)\n", what, (unsigned)got, (unsigned)want);
        g_fail++;
    }
    fflush(stdout);
}

/* ---------------- 表镜像读取（不经过固件，测试自己算期望值） ---------------- */

typedef struct
{
    char     name[16];
    uint32_t freq10;
    uint32_t data_off;
    uint32_t up10;
    uint16_t aos_min;
    uint16_t dur_s;
    uint16_t pts;
    uint16_t step_ms;
    uint16_t ctcss10;
    uint8_t  flags;
    uint8_t  max_el;
} __attribute__((packed)) sim_entry_t;

static void flash_entry(int idx, sim_entry_t *e)
{
    memcpy(e, sim_flash_const() + 0x162010 + (uint32_t)idx * 40u, sizeof(*e));
}

/* 表里的第 k 个样点偏移（Hz）。与 doppler.c:DPH_Sample 同一口径：
 * 物理地址 = 条目里的 data_off + k*6。 */
static int16_t flash_sample(const sim_entry_t *e, int k)
{
    int16_t v = 0;
    memcpy(&v, sim_flash_const() + e->data_off + (uint32_t)k * 6u, 2u);
    return v;
}

/* doppler.c:1907 DPH_Hz2Ten —— 四舍五入到 10 Hz 单位，负数向零取整 */
static int32_t hz2ten(int32_t hz)
{
    return (hz >= 0) ? (hz + 5) / 10 : -((5 - hz) / 10);
}

/* ---------------- 按键 ---------------- */

/* 短按：按下 → 30 ms → 抬起 → 30 ms。app.c 的去抖/重复档在这里被简化成
 * "一次按下、一次抬起"，bKeyHeld 恒假（与真机上短按的派发一致）。 */
static void key_tap(KEY_Code_t k)
{
    printf("# key %d 短按\n", (int)k);
    DOPPLER_ProcessKeys(k, true, false);
    sim_run_ms(30);
    DOPPLER_ProcessKeys(k, false, false);
    sim_run_ms(30);
}

/* 长按：按下（held=false）→ hold_ms → held=true 那一档 → 30 ms → 抬起。
 * doppler.c 的长按分支只认 bKeyHeld，see doppler.c:3539（▲▼ 连发）、
 * doppler.c:3555（MENU 进预听）、doppler.c:3519（F 开关键盘锁）。 */
static void key_hold(KEY_Code_t k, uint32_t hold_ms)
{
    printf("# key %d 长按 %ums（真机顺序：短按 20ms → held 400ms）\n",
           (int)k, (unsigned)hold_ms);

    DOPPLER_ProcessKeys(k, true, false);   /* app.c:1869 短按事件先到 */
    sim_run_ms(20);
    DOPPLER_ProcessKeys(k, true, true);    /* app.c:1884 held 事件随后到 */
    sim_run_ms(hold_ms > 20u ? hold_ms - 20u : 10u);
    DOPPLER_ProcessKeys(k, false, false);  /* app.c:1862 抬起 */
    sim_run_ms(30);
}

static void side1_tap(void)
{
    printf("# SIDE1 短按（74.4 Hz 按住发射）\n");
    sim_ptt_intent(true);
    DOPPLER_KeySide1(true);
    sim_run_ms(100);
    DOPPLER_KeySide1(false);
    sim_ptt_intent(false);
    sim_run_ms(30);
}

static void side1_hold(uint32_t ms)
{
    printf("# SIDE1 长按 %ums\n", (unsigned)ms);
    sim_ptt_intent(true);
    DOPPLER_KeySide1(true);
    sim_run_ms(ms);
    DOPPLER_KeySide1(false);
    sim_ptt_intent(false);
    sim_run_ms(30);
}

static void side2(void)
{
    printf("# SIDE2（换上行亚音）\n");
    DOPPLER_KeySide2(true);
    DOPPLER_KeySide2(false);
    sim_run_ms(30);
}

static void ptt_down(uint32_t ms)
{
    sim_ptt_intent(true);
    printf("# PTT 按下 %ums\n", (unsigned)ms);
    DOPPLER_ProcessKeys(KEY_PTT, true, false);
    sim_run_ms(ms);
}

static void ptt_up(uint32_t ms)
{
    printf("# PTT 抬起 → %ums\n", (unsigned)ms);
    sim_ptt_intent(false);                  /* 手指先抬起，固件随后才反应 */
    DOPPLER_ProcessKeys(KEY_PTT, false, false);
    sim_run_ms(ms);
}

/* ---------------- 脚本解释 ---------------- */

static KEY_Code_t key_by_name(const char *s, bool *found)
{
    static const struct { const char *n; int k; } tab[] = {
        { "KEY_0", KEY_0 }, { "KEY_1", KEY_1 }, { "KEY_2", KEY_2 },
        { "KEY_3", KEY_3 }, { "KEY_4", KEY_4 }, { "KEY_5", KEY_5 },
        { "KEY_6", KEY_6 }, { "KEY_7", KEY_7 }, { "KEY_8", KEY_8 },
        { "KEY_9", KEY_9 }, { "KEY_MENU", KEY_MENU }, { "KEY_UP", KEY_UP },
        { "KEY_DOWN", KEY_DOWN }, { "KEY_EXIT", KEY_EXIT }, { "KEY_STAR", KEY_STAR },
        { "KEY_F", KEY_F },
    };

    *found = false;
    for (unsigned i = 0; i < sizeof(tab) / sizeof(tab[0]); i++)
        if (strcmp(s, tab[i].n) == 0)
        {
            *found = true;
            return (KEY_Code_t)tab[i].k;
        }
    return KEY_INVALID;
}

/* 返回 0 正常，-1 未知指令 */
static int exec_line(const char *line)
{
    char     buf[128];
    char     a[32] = {0};
    char     b[32] = {0};
    uint32_t ms   = 0;
    bool     found;

    while (isspace((unsigned char)*line))
        line++;
    if (*line == '\0' || *line == '#' || *line == '\n')
        return 0;

    snprintf(buf, sizeof(buf), "%s", line);
    (void)sscanf(buf, "%31s %31s %u", a, b, &ms);

    if (strcmp(a, "ADVANCE") == 0)
    {
        char *p = b;
        while (*p && !isdigit((unsigned char)*p))
            p++;
        ms = (uint32_t)strtoul(p, NULL, 10);
        printf("# 推进 %ums\n", (unsigned)ms);
        sim_run_ms(ms);
        return 0;
    }
    if (strcmp(a, "PTT_ON") == 0)
    {
        ptt_down(ms ? ms : 100u);
        return 0;
    }
    if (strcmp(a, "PTT_OFF") == 0)
    {
        ptt_up(ms ? ms : 200u);
        return 0;
    }
    if (strcmp(a, "KEY_SIDE1") == 0)
    {
        if (strcmp(b, "HOLD") == 0)
            side1_hold(ms ? ms : 300u);
        else
            side1_tap();
        return 0;
    }
    if (strcmp(a, "KEY_SIDE2") == 0)
    {
        side2();
        return 0;
    }

    KEY_Code_t k = key_by_name(a, &found);
    if (!found)
    {
        printf("# !! 未知指令：%s\n", a);
        return -1;
    }

    if (strcmp(b, "HOLD") == 0)
        key_hold(k, ms ? ms : 600u);
    else
        key_tap(k);

    return 0;
}

static int run_script_file(const char *path)
{
    FILE *fp = fopen(path, "r");
    char  line[256];
    int   rc = 0;

    if (fp == NULL)
    {
        printf("# 打不开脚本 %s\n", path);
        return 2;
    }

    while (fgets(line, sizeof(line), fp) != NULL)
        if (exec_line(line) != 0)
            rc = 1;

    fclose(fp);
    return rc;
}

/* ---------------- 公共场景准备 ---------------- */

static const sim_entry_t *prepare(const char *table, uint8_t power)
{
    static sim_entry_t e;

    sim_radio_init(14580000u, 14599000u, power);

    if (table == NULL || !sim_flash_load(table, 0x00162000u, 0x00010000u))
    {
        printf("# 没有表镜像，用 s1/s2/s3 之前必须给一个（tests/sim/data/table_sim.bin）\n");
        return NULL;
    }

    flash_entry(0, &e);
    printf("# 表项 0：%s  down=%u  up=%u  pts=%u step=%ums dur=%us  ctcss=%u\n",
           e.name, (unsigned)e.freq10, (unsigned)e.up10, (unsigned)e.pts,
           (unsigned)e.step_ms, (unsigned)e.dur_s, (unsigned)e.ctcss10);
    return &e;
}

/* ---------------- 场景 ---------------- */

static void scenario_m0(const char *table)
{
    sim_note("M0 空场景：验证 doppler.c 能在 PC 上跑起来");

    sim_radio_init(14580000u, 14599000u, OUTPUT_POWER_LOW1);

    if (table != NULL && sim_flash_load(table, 0x00162000u, 0x00010000u))
        sim_note("已装入表镜像 %s", table);
    else
        sim_note("表区为空（全 0xFF），预期 phase = DPH_EMPTY");

    sim_mark("DOPPLER_Start");
    DOPPLER_Start();

    printf("# phase = %u (0=OFF 1=READY 2=RUN 3=DONE 4=EMPTY 5=EXT 6=PRE 7=POST)\n",
           (unsigned)DPH_PhaseGet());
    sim_run_ms(50);

    sim_mark("tick only, no key");
    sim_run_ms(60);

    sim_mark("EXIT");
    key_tap(KEY_EXIT);

    printf("# phase = %u\n", (unsigned)DPH_PhaseGet());
    sim_note("事件共 %lu 条", (unsigned long)g_ev_n);

    check(g_ev_n > 0u, "空场景产生了事件流");
}

/* 场景①：进页 → 长按 M 进预听 → PTT 100 ms → 松手 → 快进 200 ms
 *
 * 断言口径（交接-26 ①）：DPH_TxApply 不写 last_freq 时，松手那一次
 * DPH_Tune(DPH_Rx10WithOff(hold_off)) 会因 "freq == last_freq" 直接 return，
 * 芯片就停在上行频点上。所以看的是**松手之后芯片里到底是什么数**。 */
static void scenario_s1(const char *table)
{
    const sim_entry_t *e = prepare(table, OUTPUT_POWER_LOW1);
    uint32_t exp_pre, ev_mark;
    int      idx;

    if (e == NULL)
    {
        fail("s1 需要表镜像");
        return;
    }

    exp_pre = (uint32_t)((int32_t)e->freq10 + hz2ten(flash_sample(e, 0)));

    sim_note("场景①：进页 → 长按 M 进预听 → PTT 100 ms → 松手 → 快进 200 ms");
    printf("# 预听频点应为 %u（表频点 %u + 样点0 %d Hz→%d）\n",
           (unsigned)exp_pre, (unsigned)e->freq10,
           (int)flash_sample(e, 0), (int)hz2ten(flash_sample(e, 0)));

    sim_mark("DOPPLER_Start");
    DOPPLER_Start();
    sim_run_ms(50);
    check_freq(g_chip.freq10, e->freq10, "进页后芯片在表频点");

    sim_mark("长按 MENU 进预听（真机派发顺序：短按先到 → 长按 600 ms）");
    key_hold(KEY_MENU, 600);
    printf("# phase = %u (6=PRE)\n", (unsigned)DPH_PhaseGet());
    check(DPH_PhaseGet() == 6u, "进了预听页（DPH_PRE=6）");
    check_freq(g_chip.freq10, exp_pre, "预听页芯片停在 AOS 样点频点");

    ev_mark = g_ev_n;

    sim_mark("PTT 按下 100ms");
    ptt_down(100);

    sim_mark("PTT 抬起 + 200ms");
    ptt_up(200);

    printf("# phase = %u\n", (unsigned)DPH_PhaseGet());
    check_freq(g_chip.freq10, exp_pre, "松手后芯片回到预听频点（交接-26 ①）");

    idx = sim_find(EV_RX_FREQ, ev_mark);
    if (idx < 0)
        fail("松手后没有任何 RX_FREQ 事件（芯片被留在了上行频点）");
    else
        check_freq((uint32_t)g_ev[idx].a, exp_pre, "松手后确实下发了一条 RX_FREQ");

    sim_note("最后一条 TX_FREQ = %ld（上行，仅供对照）",
             (long)(sim_find_last(EV_TX_FREQ) >= 0 ? g_ev[sim_find_last(EV_TX_FREQ)].a : -1));
}

/* 场景①'：按真机派发顺序走"长按 M 进预听"与"短按 M 开跑"，两头都要成立
 *
 * 代码里有证据（交接-27 修好之前的时序）：
 *   app.c:1869  gDebounceCounter == key_debounce_10ms(2)      → ProcessKey(Key, true, false)
 *   app.c:1884  gDebounceCounter == key_repeat_delay_10ms(40) → ProcessKey(Key, true, true)
 * 按住的那一刻"短按"事件先派发出去，400 ms 后才轮到 held。原来短按 MENU 分支
 * 在 phase==DPH_READY 时直接 DPH_BeginRun()，等 held 到达时 phase 已是 DPH_RUN，
 * 预听入口 (READY || PRE) 就此永不成立 —— 连带 auto_armed 与 T-60/T-10 提醒一起死。
 * 交接-27 的修法：按下只挂 menu_tap_pending，松手才开跑；长按生效时作废挂起。
 * 所以这一场现在断两头：长按 → PRE，短按 → RUN。 */
static void scenario_s1h(const char *table)
{
    const sim_entry_t *e = prepare(table, OUTPUT_POWER_LOW1);

    if (e == NULL)
    {
        fail("s1h 需要表镜像");
        return;
    }

    sim_note("场景①'：真机派发顺序（短按 → held）下按 M，看进的是哪一相");

    sim_mark("DOPPLER_Start");
    DOPPLER_Start();
    sim_run_ms(50);

    sim_mark("按 M 且按住 600 ms");
    key_hold(KEY_MENU, 600);

    printf("# phase = %u（1=READY 2=RUN 6=PRE）\n", (unsigned)DPH_PhaseGet());

    /* 交接-27 之前这里断言的是"进了 RUN（预听进不去）"，那是 bug 的复现；
     * 修法是把短按 M 推迟到松手才执行（doppler.c 松手分支的 menu_tap_pending）。
     * 现在长按必须落在预听页，短按必须立刻开跑 —— 两头都要断，只断一头会被
     * "把短按彻底删掉"这种改法骗过去。 */
    if (DPH_PhaseGet() == 6u)
        ok("长按 M 落在预听页（DPH_PRE=6）");
    else
    {
        printf("# 意外：phase = %u，长按没进预听\n", (unsigned)DPH_PhaseGet());
        fail("长按 M 应当进预听页");
    }

    /* 回到就绪页再试短按 */
    sim_mark("EXIT 回就绪页");
    key_tap(KEY_EXIT);
    printf("# phase = %u（1=READY）\n", (unsigned)DPH_PhaseGet());
    check(DPH_PhaseGet() == 1u, "EXIT 从预听回到就绪页");

    sim_mark("短按 M（按下 30 ms 就抬起）");
    key_tap(KEY_MENU);
    printf("# phase = %u（2=RUN）\n", (unsigned)DPH_PhaseGet());

    if (DPH_PhaseGet() == 2u)
        ok("短按 M 仍然立刻开跑（松手那一下执行 menu_tap_pending）");
    else
    {
        printf("# 意外：phase = %u，短按没有开跑\n", (unsigned)DPH_PhaseGet());
        fail("短按 M 应当开跑");
    }
}

/* 场景②：进页 → 就绪页 PTT 100 ms → 松手 → 快进 200 ms
 *
 * 断言口径（交接-26 ②）：READY 页松手分支原本不存在，松手后既不重调
 * DPH_Tune 也不动芯片，芯片就留在刚才写进去的上行频点上。 */
static void scenario_s2(const char *table)
{
    const sim_entry_t *e = prepare(table, OUTPUT_POWER_LOW1);
    uint32_t ev_mark;
    int      idx;

    if (e == NULL)
    {
        fail("s2 需要表镜像");
        return;
    }

    sim_note("场景②：进页 → 就绪页 PTT 100 ms → 松手 → 快进 200 ms");

    sim_mark("DOPPLER_Start");
    DOPPLER_Start();
    sim_run_ms(50);
    printf("# phase = %u (1=READY)\n", (unsigned)DPH_PhaseGet());
    check(DPH_PhaseGet() == 1u, "进页后停在就绪页");

    ev_mark = g_ev_n;

    sim_mark("PTT 按下 100ms");
    ptt_down(100);

    sim_mark("PTT 抬起 + 200ms");
    ptt_up(200);

    check_freq(g_chip.freq10, e->freq10, "松手后芯片回到下行表频点（交接-26 ②）");

    idx = sim_find(EV_RX_FREQ, ev_mark);
    if (idx < 0)
        fail("松手后没有任何 RX_FREQ 事件（交接-26 ② 的原始缺陷）");
    else
        check_freq((uint32_t)g_ev[idx].a, e->freq10, "松手后下发了一条 RX_FREQ");
}

/* 场景③：信道功率设 1 W → 进页 → 退页 → 查 gEeprom 里那条信道的 OUTPUT_POWER
 *
 * 断言口径（交接-26 ③）：DOPPLER_Start 里那句"进页把功率顶到 HIGH"必须在
 * DPH_Enter()（DPH_SaveRadio 之后 VFO 指针已指向 dph_vfo 副本）之后，
 * 才改不到操作员那条信道。挪到 DPH_Enter() 之前就会把 gEeprom.VfoInfo[0]
 * 的 OUTPUT_POWER 永久改成 HIGH。 */
static void scenario_s3(const char *table)
{
    const sim_entry_t *e = prepare(table, OUTPUT_POWER_LOW1);

    if (e == NULL)
    {
        fail("s3 需要表镜像");
        return;
    }

    sim_note("场景③：信道功率设 1 W → 进页 → 退页 → 查 gEeprom 里那条信道的 OUTPUT_POWER");
    printf("# 进页前 gEeprom.VfoInfo[0].OUTPUT_POWER = %u\n",
           (unsigned)gEeprom.VfoInfo[0].OUTPUT_POWER);
    check(gEeprom.VfoInfo[0].OUTPUT_POWER == OUTPUT_POWER_LOW1, "初始功率就是 1 W");

    sim_mark("DOPPLER_Start");
    DOPPLER_Start();
    sim_run_ms(50);
    printf("# 页内 gRxVfo->OUTPUT_POWER = %u（正被顶到 HIGH = %u）\n",
           (unsigned)gRxVfo->OUTPUT_POWER, (unsigned)OUTPUT_POWER_HIGH);
    check(gRxVfo->OUTPUT_POWER == OUTPUT_POWER_HIGH, "页内确实把功率顶到了 HIGH");

    sim_mark("EXIT 退页");
    key_tap(KEY_EXIT);
    sim_run_ms(50);

    printf("# 退页后 gEeprom.VfoInfo[0].OUTPUT_POWER = %u\n",
           (unsigned)gEeprom.VfoInfo[0].OUTPUT_POWER);
    check(gEeprom.VfoInfo[0].OUTPUT_POWER == OUTPUT_POWER_LOW1,
          "退页后操作员那条信道的功率仍是 1 W（交接-26 ③）");
}



/* 场景④（正面用例，不破坏代码）：整趟过境。
 *
 * 表项 SIM-PASS-A：600 s / 120 样点 / 5000 ms 一点，多普勒偏移抛物线
 * -3200 → +3200 → -3200 Hz（每点 401 Hz 步进）。虚拟时钟一拍 10 ms，
 * 600 s = 60000 拍，10 毫秒跑完 —— 这就是"把 15 分钟压缩到 10 毫秒"。
 *
 * 中途按住 PTT 30 s，用来验发射路径：
 *   - 发射只在 PTT 期间发生（时间窗硬断言）
 *   - 发射中频率每 100 拍（1 s）才写一次 —— doppler.c:3187 的 tx_div 节流
 *   - 发射中不写接收频率（doppler.c:3185 只做 DPH_RxShow，不动 PLL）
 *   - 松手后接收频率接着过境走，不会卡在按下 PTT 那一刻
 */
static void scenario_s4(const char *table)
{
    const sim_entry_t *e = prepare(table, OUTPUT_POWER_LOW1);
    uint32_t ev0;
    uint32_t t_ptt_a, t_ptt_b;
    uint32_t rx_first = 0, rx_last = 0, rx_min = 0xFFFFFFFFu, rx_max = 0;
    uint32_t want_min = 0xFFFFFFFFu, want_max = 0;
    uint32_t tx_n = 0, tx_bad_interval = 0, tx_outside = 0, rx_bad_range = 0;
    int      rx_turns = 0, rx_prev_dir = 0;
    uint32_t rx_backs = 0;          /* PTT 窗口之外的"向上跳"次数 */
    uint32_t rx_back_t10 = 0;
    uint32_t rx_back_from = 0, rx_back_to = 0;
    uint32_t rx_hi    = 0;          /* 当前方向上的极值，用于忽略细抖 */
    uint32_t rx_seen = 0;
    uint32_t rx_prev = 0;
    uint32_t prev_tx_t10 = 0;
    bool     tx_first = true;

    if (e == NULL)
    {
        fail("s4 需要表镜像");
        return;
    }

    sim_note("场景④（正面用例）：整趟 600 s 过境，虚拟时钟 60000 拍，中途 PTT 30 s");

    /* 期望的多普勒范围：自己从表镜像算，不经过固件 */
    for (uint16_t k = 0; k < e->pts; k++)
    {
        uint32_t f = (uint32_t)((int32_t)e->freq10 + hz2ten(flash_sample(e, k)));

        if (f < want_min)
            want_min = f;
        if (f > want_max)
            want_max = f;
    }

    printf("# 表内多普勒范围：%u .. %u（跨度 %u，即 %d Hz ~ %d Hz）\n",
           (unsigned)want_min, (unsigned)want_max,
           (unsigned)(want_max - want_min),
           (int)(want_min - e->freq10) * 10, (int)(want_max - e->freq10) * 10);

    sim_mark("DOPPLER_Start");
    DOPPLER_Start();
    sim_run_ms(100);
    check(DPH_PhaseGet() == 1u, "进页后停在就绪页");

    sim_mark("短按 MENU 进跟踪（真机短按派发）");
    key_tap(KEY_MENU);
    sim_run_ms(100);
    printf("# phase = %u\n", (unsigned)DPH_PhaseGet());
    check(DPH_PhaseGet() == 2u, "短按 MENU 后进了跟踪页（DPH_RUN=2）");

    ev0 = g_ev_n;

    /* 前 100 s 纯接收 */
    sim_mark("纯接收 100 s");
    sim_run_ms(100000u);

    /* 100 s .. 130 s 按住 PTT */
    t_ptt_a = sim_now10();
    sim_mark("PTT 按下 30 s");
    ptt_down(30000u);
    t_ptt_b = sim_now10();
    sim_mark("PTT 抬起");
    ptt_up(100u);

    /* 一路跑到过境结束（600 s）再多给 5 s 进尾音页 */
    sim_mark("跑完剩余过境");
    sim_run_ms(475000u);

    printf("# 最后 phase = %u（7=POST 尾音页）\n", (unsigned)DPH_PhaseGet());
    printf("# PTT 窗口 t=%ums .. %ums\n",
           (unsigned)(t_ptt_a * 10u), (unsigned)(t_ptt_b * 10u));
    printf("# 事件共 %lu 条\n", (unsigned long)g_ev_n);

    /* ---- 逐条过事件流 ---- */
    for (uint32_t i = ev0; i < g_ev_n; i++)
    {
        const sim_ev_t *ev = &g_ev[i];
        uint32_t        f  = (uint32_t)ev->a;

        if (ev->kind == EV_RX_FREQ)
        {
            rx_seen++;
            if (rx_seen == 1u)
                rx_first = f;
            rx_last = f;
            if (f < rx_min) rx_min = f;
            if (f > rx_max) rx_max = f;

            if (f < 2900000u || f > 130000000u)
                rx_bad_range++;

            /* 单调性（先升后降，方向只翻一次）。
             *
             * 判据带 TOL：DPH_OffsetAt 的定点插值在每个样点边界上会抖 1
             * 个单位（交接-27 之前末段还会整段回放一格，现在钉在 LOS 样点，
             * 见下面 rx_backs 那条断言）。TOL = 25 个 10 Hz 单位，远小于
             * 整趟 635 个单位的摆幅，翻转数仍然可信。 */
            if (rx_seen > 1u)
            {
                if (rx_prev_dir >= 0)
                {
                    if (f >= rx_hi)
                    {
                        rx_hi     = f;
                        rx_prev_dir = 1;
                    }
                    else if (rx_hi - f > 25u)
                    {
                        rx_turns++;
                        rx_prev_dir = -1;
                        rx_hi     = f;
                    }
                }
                else
                {
                    if (f <= rx_hi)
                        rx_hi = f;
                    else if (f - rx_hi > 25u)
                    {
                        rx_turns++;
                        rx_prev_dir = 1;
                        rx_hi     = f;
                    }
                }

            }
            else
            {
                rx_hi = f;
            }
            /* 向上跳：过境曲线在一路下降/上升时，芯片频率反而往上跳。
             * PTT 松手那一下会跳（30 s 里曲线走了 65 个单位，收频要追上），
             * 那是设计如此；窗口外的跳变才是问题。 */
            if (rx_seen > 1u && f > rx_prev + 5u
                && !(ev->t10 + 100u >= t_ptt_a && ev->t10 <= t_ptt_b + 100u))
            {
                if (rx_backs == 0u)
                {
                    rx_back_t10  = ev->t10;
                    rx_back_from = rx_prev;
                    rx_back_to   = f;
                }
                rx_backs++;
            }

            rx_prev = f;
        }
        else if (ev->kind == EV_TX_FREQ)
        {
            tx_n++;

            if (f < 2900000u || f > 130000000u)
                rx_bad_range++;

            if (ev->t10 < t_ptt_a || ev->t10 > t_ptt_b)
                tx_outside++;

            if (!tx_first && (ev->t10 - prev_tx_t10) != 100u)
                tx_bad_interval++;

            tx_first      = false;
            prev_tx_t10   = ev->t10;
        }
    }

    printf("# RX_FREQ 事件 %u 条，范围 %u .. %u，反向跳变（>=5 单位）%u 次，方向翻转 %d 次\n",
           (unsigned)rx_seen, (unsigned)rx_min, (unsigned)rx_max,
           (unsigned)rx_backs, rx_turns);
    printf("# TX_FREQ 事件 %u 条，间隔不等 100 拍的 %u 条，落在 PTT 窗口外的 %u 条\n",
           (unsigned)tx_n, (unsigned)tx_bad_interval, (unsigned)tx_outside);

    check(rx_seen > 1000u, "整趟有足够多的 RX_FREQ 事件");
    check(rx_min == want_min && rx_max == want_max,
          "RX_FREQ 极值与表内多普勒范围吻合");

    /* 进 RUN 后的第一拍已经晚了约 10 ms，四舍五入到 10 Hz 允许差 1~2 个单位 */
    {
        int32_t want0 = (int32_t)e->freq10 + hz2ten(flash_sample(e, 0));
        int32_t d     = (int32_t)rx_first - want0;

        check(d >= -2 && d <= 2, "首条 RX_FREQ 落在 AOS 样点（±2 个 10 Hz 单位内）");
        printf("# 首条 RX_FREQ = %u，AOS 样点算出来是 %d\n",
               (unsigned)rx_first, (int)want0);
    }
    check(rx_last == (uint32_t)((int32_t)e->freq10 + hz2ten(flash_sample(e, e->pts - 1))),
          "末条 RX_FREQ 等于 LOS 样点频点");
    printf("# PTT 窗口外向上跳 %u 次", (unsigned)rx_backs);
    if (rx_backs)
        printf("：t=%ums  %u → %u", (unsigned)(rx_back_t10 * 10u),
               (unsigned)rx_back_from, (unsigned)rx_back_to);
    printf("\n");
    /* 交接-27 之前这里是 ==1（用来复现"尾段再走一遍"）。bug 修掉之后必须为 0：
     * 越过末样点就直接钉在 LOS 频点上，曲线全程单调，不该再有任何一次回跳。
     * 反过来，如果谁把那条钳位改动删了，这条断言立刻变红 —— 这就是它的阳性对照。 */
    check(rx_backs == 0u,
          "过境尾段不再回放（交接-27：DPH_OffsetAt / DPH_AngleAt 越界钉在 LOS 样点）");
    check(rx_turns <= 1, "RX_FREQ 序列先升后降（方向最多翻转一次）");
    check(rx_bad_range == 0u, "全程没有超出 29~1300 MHz 的频点（DPH_Freq10Ok 会拒绝）");
    check(tx_outside == 0u, "TX 只发生在 PTT 期间");
    check(tx_bad_interval == 0u, "发射中每 100 拍才写一次 TX 频点（tx_div 节流）");
    check(tx_n >= 25u && tx_n <= 35u, "30 s 发射里 TX_FREQ 条数约 30 条");
    check(DPH_PhaseGet() == 7u, "过境跑完自动进尾音页（POST）");
}

/* ---------------- 交接-28 E1~E5 的断言 ---------------- */

/* 屏幕上某块区域里点了几个像素（gFrameBuffer 是 8 行 × 128 字节的点阵，
 * 第 y 像素 = gFrameBuffer[y>>3][x] 的第 (y&7) 位，见 doppler.c:4104 DPH_Pixel）。
 * 版面断言只能用这一个口径：仿真没有屏幕字符串，只有帧缓冲。 */
static uint32_t count_pixels(uint8_t x0, uint8_t x1, uint8_t y0, uint8_t y1)
{
    uint32_t n = 0;
    uint8_t  x, y;

    for (y = y0; y <= y1; y++)
        for (x = x0; x <= x1; x++)
            if ((gFrameBuffer[y >> 3][x] >> (y & 7u)) & 1u)
                n++;

    return n;
}

/* 场景⑤（E1）：就绪页按 PTT 起发 → 发射中 EXIT 退页 → 松手
 *
 * 代码里有证据（交接-28 E1 的真因）：
 *   app.c:2731  if (gCurrentFunction == FUNCTION_TRANSMIT) {
 *   app.c:2738      if (Key == KEY_PTT) { GENERIC_Key_PTT(bKeyPressed); goto Skip; }
 * 发射中的 PTT 抬起从来走不到 DOPPLER_ProcessKeys，DPH_PttEvent(false) 就没人调，
 * 而 ptt_down 只在 doppler.c:3310 那一处被写成 false —— 于是它永远残留 true，
 * 之后每次按侧键一 `if (!ptt_down) DPH_PttEvent(true)`（doppler.c:3506）都是假：
 * 亚音换了、载波不起，正是操作员报的"必须再按一次 PTT 才发"。
 *
 * 修法两条，仿真只覆盖得到第二条：
 *   ①  app.c 把发射中的"抬起"送回这一页（app.c 不进 sim，见 check_dispatch.py）
 *   ②  DPH_PttEvent 在准入失败时也收载波（本场景）
 * 这一场走的是"退页"这条准入失败的松手路径：DPH_Leave 只清 ptt_down 和亚音，
 * 不碰载波（doppler.c:2735），所以松手那一刻必须有人把载波收掉。 */
static void scenario_s5(const char *table)
{
    const sim_entry_t *e = prepare(table, OUTPUT_POWER_LOW1);
    uint32_t ev_mark;
    int      i, released = -1;

    if (e == NULL)
    {
        fail("s5 需要表镜像");
        return;
    }

    sim_note("场景⑤（E1）：就绪页按 PTT 起发 → 发射中 EXIT 退页 → 松手");

    sim_mark("DOPPLER_Start");
    DOPPLER_Start();
    sim_run_ms(50);
    check(DPH_PhaseGet() == 1u, "进页后停在就绪页");

    ev_mark = g_ev_n;

    /* 交接-31 第二轮（操作员原话「ready界面PTT键页不许发射」）：就绪页按下
     * PTT **必须被拒**，一声双响，一个载波都不许起。侧键一在 READY 早就拒了，
     * PTT 这条按下沿的后门一直开着 —— app.c 只把"发射中的抬起"送回这一页，
     * 按下那一刻是直接 GENERIC_Key_PTT 起载波的。 */
    sim_mark("PTT 按下 200 ms（就绪页：必须拒）");
    ptt_down(200);
    check(gCurrentFunction != FUNCTION_TRANSMIT,
          "就绪页按 PTT 起不了载波（交接-31：READY 不许发射）");
    check(DPH_PhaseGet() == 1u, "拒绝之后仍然停在就绪页");
    ptt_up(200);

    /* 准入失败时的松手必须把载波收掉（E1 的 doppler.c 那一半）。就绪页现在
     * 根本起不了发，这条路径只能这么造：预听页起发 → EXIT 退回就绪页
     * （DPH_BackToReady，不碰载波）→ 相位已不在准入集合里 → 松手。
     * 按下和松手之间相位换了，正是当年"松手没人收、整台电台一直发着"那种错位。 */
    sim_mark("长按 M 进预听页");
    key_hold(KEY_MENU, 600);
    sim_run_ms(100);
    check(DPH_PhaseGet() == 6u, "长按 M 进了预听页（6=PRE）");

    ev_mark = g_ev_n;

    sim_mark("预听页按住 PTT");
    ptt_down(200);
    check(gCurrentFunction == FUNCTION_TRANSMIT, "预听页按 PTT 起了载波（准入成立）");

    sim_mark("发射中按 EXIT：退回就绪页，载波不收");
    key_tap(KEY_EXIT);
    sim_run_ms(50);
    printf("# phase = %u（1=READY）\n", (unsigned)DPH_PhaseGet());
    check(DPH_PhaseGet() == 1u, "预听页 EXIT 退回了就绪页");
    check(gCurrentFunction == FUNCTION_TRANSMIT,
          "退回就绪页这一步不碰载波（所以松手必须有人收，否则整台电台一直发着）");

    sim_mark("PTT 抬起（此时相位已不在准入集合里）");
    ptt_up(200);

    /* EV_RADIO 的三个来源（sim_hw.c:372/386）：APP_EndTransmission → a=0,b=0；
     * GENERIC_Key_PTT(true) → a=1,b=0；GENERIC_Key_PTT(false) → a=0,b=1。
     * 只认第三种：这一场要的是"松手那句 GENERIC_Key_PTT(false) 真的调到了"。 */
    for (i = (int)ev_mark; i < (int)g_ev_n; i++)
        if (g_ev[i].kind == EV_RADIO && g_ev[i].a == 0 && g_ev[i].b == 1)
            released = i;

    if (released < 0)
        fail("准入失败时的松手没有调 GENERIC_Key_PTT(false)（E1：载波没人关）");
    else
        ok("准入失败时的松手仍然收掉了载波（E1 的 doppler.c 那一半）");

    check(gCurrentFunction != FUNCTION_TRANSMIT, "松手后不再处于发射状态");

    /* 后半场（E6 + E1）：
     * ① 重进页面时相位是 READY —— 侧键一在这里**必须被拒**。真机第三轮报的
     *    就是这条："在 ready 界面按侧键 1 也能发射，而且按完会持续发射，关不掉"。
     *    判据只能是"载波起没起"：拒的时候什么都不写芯片。
     * ② 短按 MENU 进跟踪页，再按住侧键一，这时才该发射。这一步同时守住 E1：
     *    `DOPPLER_KeySide1()` 起发那句是 `if (!ptt_down) DPH_PttEvent(true);`
     *    —— ptt_down 只要还停在 true，侧键一就**只换亚音、不起载波**，那正是
     *    E1 在真机上的原症状。所以必须按着不放地断一次，不能按完再断。 */
    sim_mark("重进页面（READY）→ 按住侧键一（E6：这里必须拒）");
    DOPPLER_Start();
    sim_run_ms(50);
    check(DPH_PhaseGet() == 1u, "重进页面后停在就绪页");
    sim_ptt_intent(true);
    DOPPLER_KeySide1(true);
    sim_run_ms(100);
    check(gCurrentFunction != FUNCTION_TRANSMIT,
          "就绪页按侧键一起不了载波（E6：READY 不在准入相位里）");
    DOPPLER_KeySide1(false);
    sim_ptt_intent(false);
    sim_run_ms(30);

    sim_mark("短按 MENU 进跟踪页 → 按住侧键一");
    key_tap(KEY_MENU);
    sim_run_ms(100);
    if (DPH_PhaseGet() != 2u)
    {
        fail("短按 MENU 没能进跟踪页，后半场断不了");
        return;
    }
    sim_ptt_intent(true);
    DOPPLER_KeySide1(true);
    sim_run_ms(100);
    check(gCurrentFunction == FUNCTION_TRANSMIT,
          "跟踪页按住侧键一能起发（ptt_down 没有残留成 true）");
    DOPPLER_KeySide1(false);
    sim_ptt_intent(false);
    sim_run_ms(30);
}

/* 场景⑥（E2）：纯接收条目（表里 up10 == 0）上的侧键二
 *
 * 代码里有证据（两条成因，交接-28 E2）：
 *   ① DPH_CycleTone 原来第一件事就是 `if (!ext_ctrl && cur.up10 == 0u) { 双响; return; }`
 *      —— 纯接收星上侧键二**必然**只双响，与"概率"无关（DOPPLER_KeySide2
 *      的下落路径也只看这一处，见 doppler.c:3451）。
 *   ② DPH_DrawPage 原来把含 $Z（上行亚音）的整条一起跳过，按键之后屏幕上
 *      什么都不显示 —— 操作员无法判断键坏没坏。
 * 所以这一场断两件事：亚音真的换了（走 DOPPLER_ExtToneGet，与 0x0775 同源）、
 * $Z 那格真的画出来了（帧缓冲里有点）。 */
static void scenario_s6(const char *table)
{
    sim_entry_t        e1;
    uint16_t           tone_before, tone_after;
    uint32_t           z_pixels;

    if (prepare(table, OUTPUT_POWER_LOW1) == NULL)
    {
        fail("s6 需要表镜像");
        return;
    }

    flash_entry(1, &e1);
    printf("# 表项 1：%s  down=%u  up=%u（up == 0 就是纯接收）ctcss=%u\n",
           e1.name, (unsigned)e1.freq10, (unsigned)e1.up10, (unsigned)e1.ctcss10);

    if (e1.up10 != 0u)
    {
        fail("表项 1 必须有上行 0，这个场景才测得到纯接收星");
        return;
    }

    sim_note("场景⑥（E2）：切到纯接收条目 → 长按 M 进预听 → 侧键二换亚音");

    sim_mark("DOPPLER_Start");
    DOPPLER_Start();
    sim_run_ms(50);

    sim_mark("▼ 切到条目 1（纯接收）");
    key_tap(KEY_DOWN);
    check_freq(g_chip.freq10, e1.freq10, "▼ 切到条目 1 的频点");

    sim_mark("长按 M 进预听（真机派发顺序：短按先到 → held 600 ms）");
    key_hold(KEY_MENU, 600);
    printf("# phase = %u (6=PRE)\n", (unsigned)DPH_PhaseGet());
    check(DPH_PhaseGet() == 6u, "进了预听页（DPH_PRE=6）");

    /* 版面（操作员 11-02 定稿）：$Z 在第七页是 {7,0,2,113,22}，右对齐到
     * x=113，最坏串 "T254.0" 6 字符 × 4 px = 24 px → 90..113；y=22 是像素行，
     * tiny 高 6 → 22..27。同一行左边是 "TX $Y"（5..52）、"$Q"（68..75），
     * 右边没有别的东西，所以 90..113 这一段只有 $Z 会点亮。 */
    sim_mark("按侧键二之前先画一帧");
    UI_DisplayDoppler();
    z_pixels = count_pixels(90, 113, 22, 27);
    printf("# $Z 那一格的像素数 = %lu\n", (unsigned long)z_pixels);

    if (z_pixels == 0u)
        fail("纯接收条目上 $Z 那格是空的（E2：整条被 DrawPage 藏掉了）");
    else
        ok("纯接收条目上 $Z 照样画出来（E2：按键之后屏上会说话）");

    tone_before = DOPPLER_ExtToneGet();

    sim_mark("按下侧键二换上行亚音");
    side2();

    tone_after = DOPPLER_ExtToneGet();
    printf("# 侧键二前后：%u → %u（10 Hz 单位，0 = 没有亚音）\n",
           (unsigned)tone_before, (unsigned)tone_after);

    if (tone_after == tone_before)
        fail("纯接收条目上侧键二没有改变上行亚音（E2：必然那一条）");
    else
        ok("纯接收条目上侧键二改了上行亚音（E2：不再只双响）");

    /* 换过之后再画一帧：$Z 应当显示新值，而不是留在旧字上（差分 blit 只在
     * 变了才推，字符宽度不同时右对齐起点也会变）。 */
    UI_DisplayDoppler();
    printf("# 换音后 $Z 像素数 = %lu\n",
           (unsigned long)count_pixels(90, 113, 22, 27));
    check(count_pixels(90, 113, 22, 27) > 0u, "换音之后 $Z 仍然有内容");
}

/* 场景⑦（E5）：跟踪页刚开跑就按 ▼（el 合法为负）
 *
 * 代码里有证据（交接-28 E5 的真因）：
 *   doppler.c  DPH_StepKey
 *     el = (int32_t)(SCHEDULER_GetTick10ms() - t0_10ms) + dir * step;
 *     if ((uint32_t)el >= total) el = (int32_t)total - 1;      ← 强转无符号
 * 负 el 是**合法**的（AOS 之前手动开跑、自己往前挪提前量），强转成 uint32 后
 * 是 40 亿级，恒 >= total，一律被钳成 total-1；下一拍 tick 的
 * `elapsed >= cur.dur_s * 100` 立刻成立 → DPH_FinishRun → 直接进尾音页。
 * 操作员看到的就是"按一下左右键这趟就结束了"。 */
static void scenario_s7(const char *table)
{
    const sim_entry_t *e = prepare(table, OUTPUT_POWER_LOW1);
    uint32_t           f_run_start;

    if (e == NULL)
    {
        fail("s7 需要表镜像");
        return;
    }

    sim_note("场景⑦（E5）：跟踪页开跑 100 ms 就按 ▼，el 变成负数");

    sim_mark("DOPPLER_Start");
    DOPPLER_Start();
    sim_run_ms(50);

    sim_mark("短按 MENU 开跑");
    key_tap(KEY_MENU);
    sim_run_ms(100);
    printf("# phase = %u (2=RUN)\n", (unsigned)DPH_PhaseGet());
    if (DPH_PhaseGet() != 2u)
    {
        fail("s7 需要先进入跟踪页");
        return;
    }

    f_run_start = g_chip.freq10;
    printf("# 开跑时芯片 = %u（表频点 %u + 首样点）\n",
           (unsigned)f_run_start, (unsigned)e->freq10);

    /* 步进与带宽两格常驻（操作员 11-02 定稿）："$*s" 在 x=46 y=39
     * （最坏 "10" 2 字符 → 46..53，放宽到 57）、"BW $-" 在 x=64 y=39
     * （最坏 "BW 25k" 6 字符 24 px → 64..87）。两格都在 y=39，行高 6 → 39..44。
     * 左边 $H km/s 收在 38，右边 $C 从 95 起。 */
    {
        uint32_t step_px = count_pixels(46, 57, 39, 44);
        uint32_t bw_px   = count_pixels(64, 87, 39, 44);

        printf("# 步进那格的像素数 = %lu（x46..57 y39..44）\n",
               (unsigned long)step_px);
        printf("# 带宽那格的像素数 = %lu（x64..87 y39..44）\n",
               (unsigned long)bw_px);
        check(step_px > 0u,
              "跟踪页一进来就印出步进（E9：常驻，不再 3 秒闪现）");
        check(bw_px > 0u,
              "跟踪页一进来就印出带宽（E9：常驻，不再 3 秒闪现）");
    }

    /* 方向与钳制（操作员 11-02 定案）：
     *   - **▼ 往前**（el += step）、**▲ 往后**（el -= step），与屏上"距 LOS
     *     还剩多少"同一个口径；
     *   - el 严格落在 [0, total-1]，过界就停住，不许负。
     * nudge 默认 2 s，刚跑 100 ms 时 el 约 10，按 ▲ 会算到 -190，钳回 0，
     * 于是停在首样点上。 */
    sim_mark("刚开跑就按 ▲（往后）");
    key_tap(KEY_UP);
    sim_run_ms(50);

    printf("# 按 ▲ 之后 phase = %u（2=RUN 7=POST）\n", (unsigned)DPH_PhaseGet());
    if (DPH_PhaseGet() == 7u)
        fail("按一下 ▲ 直接跳进了尾音页（E5：(uint32_t)el 把越界值钳到了末样点）");
    else
        check(DPH_PhaseGet() == 2u, "在起点按 ▲ 不会倒退到 0 以下（E5）");

    check_freq(g_chip.freq10, f_run_start,
               "钳到 0 之后停在首样点上（不是末样点）");

    sim_mark("再按一次 ▼（往前）");
    key_tap(KEY_DOWN);
    sim_run_ms(50);
    printf("# 按 ▼ 之后 phase = %u\n", (unsigned)DPH_PhaseGet());
    check(DPH_PhaseGet() == 2u, "接着按 ▼ 也仍在跟踪页（对称那一路）");

    /* E7（真机第三轮）："按 ▲▼ 没有改变进度"。
     * 这个夹具的过境几乎是平频的（实测中段自然漂移 300 ms 只有 1 个 10 Hz
     * 单位），拿芯片频率当判据根本分辨不出来。所以判据换成**时间轴本身**：
     * 推到离终点 2 s 左右（总长 600 s）—— 干等 300 ms 还到不了尾音页，
     * 再按两下 ▼（默认步进 2 s）才顶到终点。只有 t0_10ms 真的被挪了才会这样；
     * 旧版"只置 hud_until、不动 t0"的写法在这里会原地不动。
     * 起点已经不是 0：上面按过 ▲（钳到 0）和 ▼（+2 s），累计约 2.1 s。 */
    sim_mark("推到离终点约 2 s");
    sim_run_ms(596500);
    printf("# 终点前 phase = %u（2=RUN 7=POST）\n", (unsigned)DPH_PhaseGet());
    check(DPH_PhaseGet() == 2u, "推到终点前仍在跟踪页");

    sim_mark("干等 300 ms：对照组，不该到终点");
    sim_run_ms(300);
    check(DPH_PhaseGet() == 2u, "干等 300 ms 还没到尾音页（对照组）");

    sim_mark("按一下 ▲：应当往回退，不该越过终点");
    key_tap(KEY_UP);
    printf("# 按 ▲ 之后 phase = %u（2=RUN 7=POST）\n", (unsigned)DPH_PhaseGet());
    check(DPH_PhaseGet() == 2u,
          "▲ 是往回退：离终点 1 s 时按它不会跳进尾音页（方向对了才这样）");

    sim_mark("再按 ▼：应当越过终点");
    key_tap(KEY_DOWN);
    sim_run_ms(50);
    key_tap(KEY_DOWN);
    sim_run_ms(50);
    key_tap(KEY_DOWN);
    sim_run_ms(50);
    key_tap(KEY_DOWN);
    sim_run_ms(100);
    printf("# 按 ▼ 之后 phase = %u（7=POST）\n", (unsigned)DPH_PhaseGet());
    check(DPH_PhaseGet() == 7u,
          "▼ 挪的是时间轴：连按两下就真的把进度推过了终点（E7）");
}

/* 场景⑧（E4）：预听页/尾音页必须印出上行频点 $Y
 *
 * 版面里有证据：page 7/8 各多了一条 `{7/8,0,0,2,24,"TX $Y"}`（交接-28 E4）。
 * 断言口径只能是"那一块像素是不是亮的"——固件没有屏幕字符串可查。 */
static void scenario_s8(const char *table)
{
    const sim_entry_t *e = prepare(table, OUTPUT_POWER_LOW1);
    uint32_t           n_pre, n_post;

    if (e == NULL)
    {
        fail("s8 需要表镜像");
        return;
    }

    sim_note("场景⑧（E4）：预听页 / 尾音页上的上行频点那一格");

    sim_mark("DOPPLER_Start");
    DOPPLER_Start();
    sim_run_ms(50);

    sim_mark("长按 M 进预听");
    key_hold(KEY_MENU, 600);
    check(DPH_PhaseGet() == 6u, "进了预听页");

    /* {7,0,0,5,22,"TX $Y"}：x 从 5 起，最坏 "TX 145.99000" = 12 字符 × 4 px
     * = 48 px → 5..52；y=22 是像素行，tiny 高 6 → 22..27。
     * 同一行右边是 $Q（68..75）和右对齐 113 的 $Z（90..113），都够远。 */
    UI_DisplayDoppler();
    n_pre = count_pixels(5, 52, 22, 27);
    printf("# 预听页 TX 那一格的像素数 = %lu\n", (unsigned long)n_pre);
    if (n_pre == 0u)
        fail("预听页看不到上行频点（E4：page 7 没有 TX $Y）");
    else
        ok("预听页印出了上行频点（E4：page 7 的 TX $Y）");

    sim_mark("EXIT 回就绪页");
    key_tap(KEY_EXIT);
    check(DPH_PhaseGet() == 1u, "EXIT 从预听回到就绪页");

    /* 尾音页：让整趟跑完（表 600 s，虚拟时钟一次推到底）。 */
    sim_mark("短按 M 开跑 → 推到 LOS 进尾音页");
    key_tap(KEY_MENU);
    sim_run_ms(100);
    sim_run_ms((uint32_t)e->dur_s * 1000u + 1000u);
    printf("# phase = %u（7=POST）\n", (unsigned)DPH_PhaseGet());
    check(DPH_PhaseGet() == 7u, "过境跑完进了尾音页");

    UI_DisplayDoppler();
    n_post = count_pixels(5, 52, 22, 27);
    printf("# 尾音页 TX 那一格的像素数 = %lu\n", (unsigned long)n_post);
    if (n_post == 0u)
        fail("尾音页看不到上行频点（E4：page 8 没有 TX $Y）");
    else
        ok("尾音页印出了上行频点（E4：page 8 的 TX $Y）");
}

/* 场景⑨（B4）：校准 ppm 真的落进 flash，断电重读不再被 CRC 清零
 *
 * 交接-30/31 修过一条 CAL-CRC：`sizeof(c) - sizeof(c.crc)` 把 crc 自己也当
 * 输入，写入侧 0x0109、读出侧 0xABB0，于是**每次进页** DPH_LoadCal() 都判
 * 失败、把 clk_ppm 清 0 —— 校准功能等于从来没生效过。这条只在真机上才看得
 * 出来（PC 读不到 0x1FF000），所以这里把它变成一条可复现的断言：
 *
 *   ① 校准页走完一次完整流程（M 开始计数 → 顶 60 s → 输 00:01:01 → M），
 *      1 s 误差 = 1e6/60 = 16666 ppm，必被 DPH_PPM_MAX(±76) 钳住 —— 也就是
 *      说"能存进去的值只有 0 或 ±76"，这一点本身就是设计事实（doppler.h:116）。
 *   ② 直接读 flash 0x1FF000：magic "DPC1" / version 1 / ppm 76。
 *   ③ 重进页面（DPH_Enter → DPH_LoadCal）：ppm 仍是 76。以前这里恒为 0。
 *   ④ 把 magic 打坏再重进：回到 0（守卫还在，不许把野数据当校准）。
 *
 * 真机侧 B4 只能看到 0x0772 的 ppm 字段，而且 PC 没有写 ppm 的协议 —— 所以
 * "非零校准"这条路只能在仿真里走完，真机只做"写进去之后断电回读仍是非零"。
 */
static void scenario_s9(const char *table)
{
    const uint8_t *flash;
    const char    *want = "DPC1";
    uint8_t        ppm_field;
    uint8_t        ver_field;
    uint32_t       i;
    bool           magic_ok = true;
    uint8_t       *w;

    if (prepare(table, OUTPUT_POWER_LOW1) == NULL)
    {
        fail("s9 需要表镜像");
        return;
    }

    sim_note("场景⑨（B4）：校准 ppm 写 flash → 重进页面回读");

    sim_mark("DOPPLER_Start");
    DOPPLER_Start();
    sim_run_ms(50);

    check(DPH_PpmGet() == 0, "空 flash 上电：clk_ppm = 0（没有记录就是没校过）");

    sim_mark("进校准页（就绪页按 9）");
    key_tap(KEY_9);
    sim_run_ms(50);

    sim_mark("按 M 开始计数，顶满 60 s");
    key_tap(KEY_MENU);
    sim_run_ms(60000);

    sim_mark("输入 00:01:01（真实 61 s，固件数到 60 s）→ 按 M 提交");
    key_tap(KEY_0);
    key_tap(KEY_0);
    key_tap(KEY_0);
    key_tap(KEY_1);
    key_tap(KEY_0);
    key_tap(KEY_1);
    key_tap(KEY_MENU);
    sim_run_ms(100);

    printf("# 提交后 clk_ppm = %d（合法区间 ±76）\n", (int)DPH_PpmGet());
    check(DPH_PpmGet() == DPH_PPM_MAX,
          "1 s 误差 = 16666 ppm 被钳到 +76：校准页只能给出 0 或 ±76（设计事实）");

    /* ② 直接看 flash：这一步是 sim 独有的能力（PC 读不到表区外） */
    flash = sim_flash_const();
    for (i = 0; i < 4u; i++)
        if (flash[0x1FF000u + i] != (uint8_t)want[i])
            magic_ok = false;
    check(magic_ok, "0x1FF000 落盘了 magic \"DPC1\"（擦 + 写都成功，不是只在 RAM 里）");

    ver_field = flash[0x1FF004u];
    /* dph_cal_t 的布局（doppler.h:258）：magic[4] @0、version @4、填充 @5..7、
     * ppm(int32, 小端) @8、crc(uint16) @12 —— offsetof(crc)==12 与
     * DPH_LoadCal 里的 CRC 口径一致，所以 ppm 一定在 8..11。 */
    ppm_field = (uint8_t)0;
    printf("# flash 里 version = %u，ppm = %d（@8，小端）\n",
           (unsigned)ver_field,
           (int)(int32_t)((uint32_t)flash[0x1FF008u]
                          | ((uint32_t)flash[0x1FF009u] << 8)
                          | ((uint32_t)flash[0x1FF00Au] << 16)
                          | ((uint32_t)flash[0x1FF00Bu] << 24)));
    check(ver_field == 1u, "version 字段 = 1（DPH_CAL_VERSION）");
    check((int32_t)((uint32_t)flash[0x1FF008u]
                    | ((uint32_t)flash[0x1FF009u] << 8)
                    | ((uint32_t)flash[0x1FF00Au] << 16)
                    | ((uint32_t)flash[0x1FF00Bu] << 24)) == DPH_PPM_MAX,
          "flash 里的 ppm 字段就是生效值 +76");

    /* ③ 断电重读：DPH_Enter → DPH_LoadCal，正是 CAL-CRC 那条修复守的路径 */
    sim_mark("重进页面（等价于断电重启后 DPH_LoadCal）");
    DOPPLER_Start();
    sim_run_ms(50);
    printf("# 重进后 clk_ppm = %d\n", (int)DPH_PpmGet());
    check(DPH_PpmGet() == DPH_PPM_MAX,
          "断电重读后 ppm 仍是 +76（CAL-CRC：以前这里必回 0）");
    check(DPH_PhaseGet() == 1u, "重进后停在就绪页（顺带守 DPH_Enter 的初值）");

    /* ④ 守卫：magic 坏掉就必须当"没校过"，不能拿野数据去改晶振 */
    sim_mark("打坏 magic 再重进（守卫）");
    w = sim_flash_ptr();
    w[0x1FF000u] = 0x00;
    DOPPLER_Start();
    sim_run_ms(50);
    printf("# magic 打坏后 clk_ppm = %d\n", (int)DPH_PpmGet());
    check(DPH_PpmGet() == 0, "magic 不符 → clk_ppm 归 0（野数据不许当校准）");
}

/* 场景⑩（TONE-KEYUP）：跟踪页侧键二换到 74.4 → 按 PTT，亚音必须先于 PA 落地。
 *
 * 真机症状：屏上 $Z 与 0x0775 都报 74.4，空中发的却是操作员信道那颗音（或者
 * 根本没有音）。两条成因叠在一起：
 *   ① app.c:2943 的 gFlagReconfigureVfos 在 :2959 的 gFlagPrepareTX **之前**跑，
 *      RADIO_SelectVfos 把 gCurrentVfo 甩回 gEeprom.VfoInfo[]；radio.c:1194 于是
 *      按操作员信道写 REG_51/REG_07，而不是 dph_vfo 上刚换上去的那颗；
 *   ② key-up 之前 doppler.c 从没把结构体里的亚音写进芯片，全靠 radio.c 那两句。
 * 断言只看事件流（不看屏幕、不看协议）：空中那颗音就是这两条事件的关系。
 *   a) 一条 CTCSS(744 = 74.4 Hz) 出现在 PA(GPIO1_PIN29) 打开之前；
 *   b) PA 打开之后最后一次落到芯片的 CTCSS 仍是 744；
 *   c) 发射中 DOPPLER_ExtToneGet() == 744（0.1 Hz 单位；芯片回读真能反解）；
 *   d) 发射中把芯片改成 67.0 → 0x0775 跟着变成 6700（真值读芯片）。
 * s10 同时是 negctl 17/18 的被测场景。 */
static void scenario_s10(const char *table)
{
    const sim_entry_t *e = prepare(table, OUTPUT_POWER_LOW1);
    uint16_t tone_before, tone_after;
    uint32_t ev_from;
    int      i, i_pa = -1, i_pre = -1, i_last = -1;

    if (e == NULL)
    {
        fail("s10 需要表镜像");
        return;
    }

    printf("# 表项 0：%s  down=%u  up=%u  ctcss=%u\n",
           e->name, (unsigned)e->freq10, (unsigned)e->up10, (unsigned)e->ctcss10);

    sim_note("场景⑩（TONE-KEYUP）：侧键二换到 74.4 → 按 PTT，亚音必须先于 PA");

    sim_mark("DOPPLER_Start");
    DOPPLER_Start();
    sim_run_ms(50);

    sim_mark("短按 MENU 进跟踪页（只有 RUN 有 PTT 准入）");
    key_tap(KEY_MENU);
    sim_run_ms(100);
    printf("# phase = %u（2=RUN）\n", (unsigned)DPH_PhaseGet());
    if (DPH_PhaseGet() != 2u)
    {
        fail("s10 没能进跟踪页，测不到 TONE-KEYUP");
        return;
    }

    /* 先在结构体上真换一次音（不是"本来就叫 74.4"的空转）：音环是
     * {670, 744, 1413, 0}，从表值 744 出发要绕到 1413 再转回来。 */
    tone_before = DOPPLER_ExtToneGet();
    sim_mark("侧键二换音（离开 74.4）");
    side2();
    tone_after = DOPPLER_ExtToneGet();
    printf("# 侧键二：%u → %u（10 Hz 单位）\n",
           (unsigned)tone_before, (unsigned)tone_after);
    check(tone_after != tone_before, "侧键二在跟踪页确实改了上行亚音");

    for (i = 0; i < 5 && DOPPLER_ExtToneGet() != 744u; i++)
        side2();
    printf("# 绕回 74.4：现在是 %u\n", (unsigned)DOPPLER_ExtToneGet());
    if (DOPPLER_ExtToneGet() != 744u)
    {
        fail("s10 绕不回 74.4 Hz（音环改了？）");
        return;
    }

    /* 模拟 app.c:2943 → RADIO_SelectVfos：三个指针被甩回操作员信道，而那条
     * 信道带 67.0（CTCSS_Options 第 0 档）。真机按下 PTT 那一刻就是这个现场。 */
    sim_mark("模拟 app.c RADIO_SelectVfos：指针甩回操作员信道（67.0）");
    gEeprom.VfoInfo[0].freq_config_TX.CodeType = CODE_TYPE_CONTINUOUS_TONE;
    gEeprom.VfoInfo[0].freq_config_TX.Code     = 0u;
    gRxVfo      = &gEeprom.VfoInfo[0];
    gTxVfo      = &gEeprom.VfoInfo[0];
    gCurrentVfo = &gEeprom.VfoInfo[0];

    sim_mark("PTT 按下 100 ms");
    ev_from = g_ev_n;
    ptt_down(100);

    for (i = (int)ev_from; i < (int)g_ev_n; i++)
        if (g_ev[i].kind == EV_PA && g_ev[i].a == 1)
        {
            i_pa = i;
            break;
        }

    printf("# PA 打开事件下标 = %d（窗口起点 %lu）\n", i_pa, (unsigned long)ev_from);
    check(i_pa >= 0, "PTT 按下后 PA 被打开（key-up 走到了 radio.c:1186）");

    if (i_pa >= 0)
        for (i = (int)ev_from; i < i_pa; i++)
            if (g_ev[i].kind == EV_CTCSS && g_ev[i].a == 744)
                i_pre = i;

    printf("# 74.4(744) 落地事件下标 = %d（必须 < PA 的 %d）\n", i_pre, i_pa);
    check(i_pre >= 0 && i_pre < i_pa,
          "CTCSS 74.4(744) 在 PA 打开之前落地（TONE-KEYUP：key-up 前预写亚音）");

    for (i = (int)ev_from; i < (int)g_ev_n; i++)
        if (g_ev[i].kind == EV_CTCSS)
            i_last = i;

    if (i_last >= 0)
        printf("# 最后一次 CTCSS = %d（期望 744；670 = 写成了操作员信道那颗）\n",
               (int)g_ev[i_last].a);

    check(i_last >= 0 && g_ev[i_last].a == 744,
          "PA 之后最后落到芯片的亚音仍是 74.4(744)（radio.c 自己那一遍也读对了 VFO）");

    printf("# 芯片回读 ctcss10 = %u\n", (unsigned)g_chip.ctcss10);
    check(gCurrentFunction == FUNCTION_TRANSMIT, "按 PTT 之后确实在发射");

    printf("# 发射中 0x0775 报 %u\n", (unsigned)DOPPLER_ExtToneGet());
    check(DOPPLER_ExtToneGet() == 744u,
          "发射中 0x0775 报 74.4（读结构体：DPH_TxCssRead 里装的那颗）");

    sim_mark("发射中把芯片改成 67.0（模拟别的路径直写 REG_51/REG_07）");
    BK4819_SetCTCSSFrequency(670u);
    printf("# 芯片被改后 0x0775 报 %u\n", (unsigned)DOPPLER_ExtToneGet());
    check(DOPPLER_ExtToneGet() == 744u,
          "芯片被别的路径改掉 0x0775 也不跟（老实现只报结构体，不读芯片）");

    ptt_up(50);
}

/* 场景⑪（AUTO-ARM）：长按 M 武装之后，AOS 到了必须自己进 RUN
 *
 * 现场：操作员长按 M，屏幕倒计时走到 0，页面却停在预听/就绪页；必须再短按一次
 * M 才开跑。倒计时是 `$R` 用 DPH_AosLeft() 独立画的，**和 auto_armed 无关**，
 * 所以"看见倒计时归零"不能证明武装成功 —— 这一场把两件事分开断：
 *   a) 时钟没校（DPH_AosLeft() == -1）：长按必须留痕（双响），且不得报 armed；
 *   b) 时钟校好、AOS 在 +5 s（表项 aos 字段 = 10 s 本地日，*2 换算；时钟取 5 s）：长按 → PRE + armed(bit3)，到点自动进 RUN，
 *      并且芯片被调到 AOS 样点（RX_FREQ），不需要第二次按键。
 * 本场必须排在 all 的最后：它会把 clk_valid 置真，后面的场景再想要"未校时"
 * 就回不去了（doppler.c 没有清 clk_valid 的入口）。 */
static void scenario_s11(const char *table)
{
    const sim_entry_t *e = prepare(table, OUTPUT_POWER_LOW1);
    uint32_t exp_aos, ev_mark;
    int      i, idx;
    uint8_t  st;

    if (e == NULL)
    {
        fail("s11 需要表镜像");
        return;
    }

    /* 表项 0：aos 字段 = 10 s 本地日（doppler.c 里 *_2 换算），过境日 9802。
     * 所以 DOPPLER_SetClock(9802, 5) ⇒ DPH_AosDelta = +5 s。 */
    exp_aos = (uint32_t)((int32_t)e->freq10 + hz2ten(flash_sample(e, 0)));

    sim_note("场景⑪（AUTO-ARM）：武装必须到点真会跑，跑不了就必须当场出声");

    /* ---- a) 时钟未设：长按 M 只能进预听，武装必须被"说出来"---- */
    sim_mark("DOPPLER_Start（时钟未设，DPH_AosLeft 应为 -1）");
    DOPPLER_Start();
    sim_run_ms(50);
    check(DPH_PhaseGet() == 1u, "时钟未设时也能进就绪页");

    gBeepToPlay = BEEP_NONE;
    sim_mark("长按 M 600 ms（时钟未设：能预听，但武装不了）");
    key_hold(KEY_MENU, 600);

    printf("# phase = %u（6=PRE）  ExtState=0x%02X  beep=%u\n",
           (unsigned)DPH_PhaseGet(), (unsigned)DOPPLER_ExtState(),
           (unsigned)gBeepToPlay);
    check(DPH_PhaseGet() == 6u, "长按 M 进了预听页（与时钟无关）");
    check((DOPPLER_ExtState() & 0x08u) == 0u,
          "时钟未设时不得谎报 auto_armed（bit3）");
    check(gBeepToPlay == BEEP_500HZ_60MS_DOUBLE_BEEP_OPTIONAL,
          "武装被拒时有双响（不能静默吞掉这次长按）");

    sim_mark("EXIT ×2 退出页面");
    key_tap(KEY_EXIT);
    key_tap(KEY_EXIT);
    check(DPH_PhaseGet() == 0u, "两次 EXIT 退回主界面");

    /* ---- b) 手机还占着 PLL（ext_ctrl）时同样必须拒绝，不能偷偷挂上 ---- */
    check(DOPPLER_SetClock(9802u, 5u), "DOPPLER_SetClock 接受这组时间");
    sim_mark("DOPPLER_Start（校时后，AOS 在 +5 s）");
    DOPPLER_Start();
    sim_run_ms(50);
    check(DPH_PhaseGet() == 1u, "校时后进页停在就绪页");

    sim_mark("DOPPLER_SetExtControl(true)：手机接管（没推第二套页面）");
    gSerialConfigCountDown_500ms = 200u;      /* 串口活着，别让看门狗当掉线 */
    check(DOPPLER_SetExtControl(true), "接管被接受");
    printf("# 接管后 phase = %u（1=READY）  ExtState=0x%02X\n",
           (unsigned)DPH_PhaseGet(), (unsigned)DOPPLER_ExtState());

    gBeepToPlay = BEEP_NONE;
    sim_mark("接管中长按 M 600 ms：必须双响拒绝，不得置 auto_armed");
    key_hold(KEY_MENU, 600);
    st = DOPPLER_ExtState();
    printf("# 接管中长按后 phase = %u  ExtState=0x%02X  beep=%u\n",
           (unsigned)DPH_PhaseGet(), (unsigned)st, (unsigned)gBeepToPlay);
    check((st & 0x08u) == 0u, "手机接管时不得谎报武装（bit3）");
    check(gBeepToPlay == BEEP_500HZ_60MS_DOUBLE_BEEP_OPTIONAL,
          "手机接管时武装被拒要双响（不能偷偷挂上）");

    if (DPH_PhaseGet() == 6u)
    {
        key_tap(KEY_EXIT);                    /* 预听 → 就绪 */
    }
    sim_mark("交还控制权并退页");
    DOPPLER_SetExtControl(false);
    key_tap(KEY_EXIT);
    gSerialConfigCountDown_500ms = 0u;
    printf("# phase = %u（0=OFF）\n", (unsigned)DPH_PhaseGet());
    check(DPH_PhaseGet() == 0u, "交还后两次 EXIT 回到主界面");

    /* ---- c) 时钟可信、没人抢：到 AOS 必须自己进 RUN ---- */
    /* 重新校时：前面的 a/b 段已经吃掉几秒，AOS 必须从现在算起（1 s ⇒ +9 s）。 */
    check(DOPPLER_SetClock(9802u, 1u), "重新校时（AOS 在 +9 s）");
    sim_mark("DOPPLER_Start（校时，AOS 在 +9 s）");
    DOPPLER_Start();
    sim_run_ms(50);
    check(DPH_PhaseGet() == 1u, "重新进页停在就绪页");

    gBeepToPlay = BEEP_NONE;
    sim_mark("长按 M 600 ms → 预听 + 武装");
    key_hold(KEY_MENU, 600);
    st = DOPPLER_ExtState();
    printf("# phase = %u（6=PRE）  ExtState=0x%02X（bit3=armed）  beep=%u\n",
           (unsigned)DPH_PhaseGet(), (unsigned)st, (unsigned)gBeepToPlay);
    check(DPH_PhaseGet() == 6u, "长按 M 落在预听页");
    check((st & 0x08u) != 0u, "长按 M 之后 auto_armed 置真（bit3）");

    sim_mark("推进 4 s（AOS 前）：仍应停在预听页");
    sim_run_ms(4000);
    printf("# AOS 前 phase = %u（6=PRE）  ExtState=0x%02X\n",
           (unsigned)DPH_PhaseGet(), (unsigned)DOPPLER_ExtState());
    check(DPH_PhaseGet() == 6u, "AOS 前 5 s 仍停在预听页（没有提前开跑）");
    check((DOPPLER_ExtState() & 0x08u) != 0u, "倒计时途中武装位仍然在");

    sim_mark("再推进到 AOS 之后（每拍 10 ms，最多 6 s）");
    ev_mark = g_ev_n;
    for (i = 0; i < 600 && DPH_PhaseGet() != 2u; i++)
        sim_run_ms(10);

    printf("# AOS 后 phase = %u（2=RUN 6=PRE）  芯片 = %u（AOS 样点 %u）\n",
           (unsigned)DPH_PhaseGet(), (unsigned)g_chip.freq10, (unsigned)exp_aos);
    check(DPH_PhaseGet() == 2u,
          "AOS 倒计时归零后自动进跟踪页（不需要再短按 M）");

    if (DPH_PhaseGet() == 2u)
    {
        sim_run_ms(50);
        idx = sim_find(EV_RX_FREQ, ev_mark);
        if (idx < 0)
            fail("自动开跑后没有任何 RX_FREQ 事件（没换到 AOS 样点）");
        else
            printf("# 自动开跑后的第一条 RX_FREQ = %ld（AOS 样点 %u）\n",
                   (long)g_ev[idx].a, (unsigned)exp_aos);

        check((int32_t)g_chip.freq10 - (int32_t)exp_aos <= 200
              && (int32_t)g_chip.freq10 - (int32_t)exp_aos >= -200,
              "自动开跑后芯片被调到 AOS 样点附近（±2 kHz）");
    }
}

/* ---------------- 交接-32：侧键二的“关”站（只验音环） ---------------- */

/* 就绪页 $Z 的右对齐起点 = 版面 x(127) - 4 px × 字符数。
 * 字符步进 4 px（doppler.c:4260 DPH_TextWidth，font 2 → DPH_SMALL_STEP），
 * 所以这个列号就等于“屏上印了几个字符”：
 *   "T88.5" / "T74.4" / "T67.0" 5 字符 → 107
 *   "T141.3"                    6 字符 → 103
 *   "T--"                       3 字符 → 115
 * 仿真没有屏幕字符串，量列宽是唯一能分开“印了 5 个字符”和“印了 3 个字符”
 * 的口径。$Z 这一格现在只有这两种形态（DCS 只在 DCS 信道上出现）。
 * UI_DisplayClear 在仿真里是空桩（sim_hw.c:546），帧缓冲只加不减：每次量之前
 * 自己清一遍，等于模拟真机清屏，否则上一帧的像素会把起点往左拉。 */
static int first_lit_col(uint8_t x0, uint8_t x1, uint8_t y0, uint8_t y1)
{
    uint8_t x, y;

    for (x = x0; x <= x1; x++)
        for (y = y0; y <= y1; y++)
            if ((gFrameBuffer[y >> 3][x] >> (y & 7u)) & 1u)
                return (int)x;

    return -1;
}

/* 画一帧就绪页，断言 $Z 的起点列与 want 的字符数一致。 */
static void s12_z(const char *want, const char *what)
{
    int col;
    int exp = 127 - 4 * (int)strlen(want);

    memset(gFrameBuffer, 0, sizeof(gFrameBuffer));
    UI_DisplayDoppler();
    col = first_lit_col(88, 127, 21, 28);
    printf("# %s：$Z 起点 = %d（\"%s\" → 期望 %d）\n", what, col, want, exp);
    check(col == exp, what);
}

/* 场景⑫（TONE-OFF）：侧键二的环里必须有“关”这一站，而且“关”之后按需接
 * 第 5 站（表值）。断言只看环本身 —— 芯片真值那套（T?? / T? / 闪烁）已撤，
 * $Z 回到“读结构体”的老实现，环是这一场唯一的主体。
 *
 *   670 → 744 → 1413 → 关(0) → 表值(非 0 且不在环里时) → 670
 * 表值本身在环里（或为 0）时不加第 5 站，环是 4 项、关之后直接回 67.0。
 * negctl.sh 21 把“关”这一站删掉，这一场必须红。 */
static void scenario_s12(const char *table)
{
    const sim_entry_t *e;
    sim_entry_t        t0;
    sim_entry_t       *fp;
    /* 表值 885（不在环里）→ 5 项环。885 是环里第 5 站，所以第一下从 885
     * 落到环首 67.0，之后才是 74.4 → 141.3 → 关 → 885。 */
    static const char *z885[5] = { "T67.0", "T74.4", "T141.3", "T--", "T88.5" };
    /* 表值 670（本来就在环里）→ 4 项环，关之后直接回 67.0。 */
    static const char *z670[3] = { "T74.4", "T141.3", "T--" };
    char               msg[160];
    int                i;

    e = prepare(table, OUTPUT_POWER_LOW1);
    if (e == NULL)
    {
        fail("s12 需要表镜像");
        return;
    }

    /* 条目 0 的表值改成 885（不在 670/744/1413 里）→ 环 5 项；
     * 条目 1 的表值改成 670（本来就在环里）→ 环仍 4 项。
     * 为什么改镜像而不动 data/table_sim.bin：那张表是 s1..s11 的共同基线
     * （它们按“条目 0 = 744”写断言），在本场景的闪存副本上改字段最省事，
     * 每次 prepare 都重新 load，不会漏给下一场。 */
    fp = (sim_entry_t *)(sim_flash_ptr() + 0x162010u);
    fp[0].ctcss10 = 885u;
    fp[1].ctcss10 = 670u;
    flash_entry(0, &t0);
    printf("# 本场把表项 0 的 ctcss 改成 %u（不在环里 → 5 项环），表项 1 改成 670\n",
           (unsigned)t0.ctcss10);

    sim_note("场景⑫（TONE-OFF）：音环 670 → 744 → 1413 → 关 → 表值");

    sim_mark("DOPPLER_Start");
    DOPPLER_Start();
    sim_run_ms(50);
    printf("# 进页后 phase = %u  $Z = %u（表值）\n",
           (unsigned)DPH_PhaseGet(), (unsigned)DOPPLER_ExtToneGet());
    check(DPH_PhaseGet() == 1u, "s12 停在就绪页");
    check(DOPPLER_ExtToneGet() == 885u, "进页时结构体装的就是表值 885");
    s12_z("T88.5", "进页时 $Z 印表值 T88.5");

    for (i = 0; i < 5; i++)
    {
        side2();
        snprintf(msg, sizeof(msg), "表值 885 第 %d 下：$Z = %s", i + 1, z885[i]);
        printf("# 实际 tone = %u\n", (unsigned)DOPPLER_ExtToneGet());
        check(DPH_PhaseGet() == 1u, "侧键二不换页");
        s12_z(z885[i], msg);
    }

    /* ▼ 换到条目 1：表值 670 本来就在环里，不加第 5 站。 */
    sim_note("表值 670 的条目（条目 1）：环 4 项，关之后直接回 670");
    key_tap(KEY_DOWN);
    printf("# ▼ 之后 phase = %u  $Z = %u\n",
           (unsigned)DPH_PhaseGet(), (unsigned)DOPPLER_ExtToneGet());
    check(DPH_PhaseGet() == 1u, "▼ 之后仍在就绪页");
    check(DOPPLER_ExtToneGet() == 670u, "切到表值 670 的条目，结构体装的是 670");
    s12_z("T67.0", "切条目后 $Z 印 T67.0");

    for (i = 0; i < 3; i++)
    {
        side2();
        snprintf(msg, sizeof(msg), "表值 670 第 %d 下：$Z = %s", i + 1, z670[i]);
        printf("# 实际 tone = %u\n", (unsigned)DOPPLER_ExtToneGet());
        s12_z(z670[i], msg);
    }

    /* 第 4 下：关之后直接回 670（表值在环里 → 没有第 5 站）。 */
    side2();
    printf("# 表值 670 第 4 下：$Z = %u\n", (unsigned)DOPPLER_ExtToneGet());
    check(DOPPLER_ExtToneGet() == 670u, "表值 670 在环里 → 关之后直接回 670");
    s12_z("T67.0", "第 4 下回到 T67.0（4 项环闭合，没有第 5 站）");
}

int main(int argc, char **argv)
{
    const char *scn   = (argc > 1) ? argv[1] : "m0";
    const char *table = (argc > 2) ? argv[2] : NULL;

    setvbuf(stdout, NULL, _IONBF, 0);
    sim_flash_blank();

    if (strcmp(scn, "script") == 0)
    {
        const char *path = (argc > 2) ? argv[2] : NULL;

        table = (argc > 3) ? argv[3] : NULL;
        if (path == NULL)
        {
            printf("usage: sim.exe script <file> [table]\n");
            return 2;
        }
        prepare(table, OUTPUT_POWER_LOW1);
        sim_mark("DOPPLER_Start");
        DOPPLER_Start();
        run_script_file(path);
        printf("# phase = %u，事件 %lu 条\n",
               (unsigned)DPH_PhaseGet(), (unsigned long)g_ev_n);
    }
    else if (strcmp(scn, "m0") == 0)
    {
        scenario_m0(table);
    }
    else if (strcmp(scn, "s1") == 0)
    {
        scenario_s1(table);
    }
    else if (strcmp(scn, "s1h") == 0)
    {
        scenario_s1h(table);
    }
    else if (strcmp(scn, "s2") == 0)
    {
        scenario_s2(table);
    }
    else if (strcmp(scn, "s3") == 0)
    {
        scenario_s3(table);
    }
    else if (strcmp(scn, "s4") == 0)
    {
        scenario_s4(table);
    }
    else if (strcmp(scn, "s5") == 0)
    {
        scenario_s5(table);
    }
    else if (strcmp(scn, "s6") == 0)
    {
        scenario_s6(table);
    }
    else if (strcmp(scn, "s7") == 0)
    {
        scenario_s7(table);
    }
    else if (strcmp(scn, "s8") == 0)
    {
        scenario_s8(table);
    }
    else if (strcmp(scn, "s9") == 0)
    {
        scenario_s9(table);
    }
    else if (strcmp(scn, "s10") == 0)
    {
        scenario_s10(table);
    }
    else if (strcmp(scn, "s11") == 0)
    {
        scenario_s11(table);
    }
    else if (strcmp(scn, "s12") == 0)
    {
        scenario_s12(table);
    }
    else if (strcmp(scn, "all") == 0)
    {
        scenario_s1(table);
        printf("\n");
        scenario_s2(table);
        printf("\n");
        scenario_s3(table);
        printf("\n");
        scenario_s5(table);
        printf("\n");
        scenario_s6(table);
        printf("\n");
        scenario_s7(table);
        printf("\n");
        scenario_s8(table);
        printf("\n");
        scenario_s9(table);
        printf("\n");
        scenario_s10(table);
        printf("\n");
        scenario_s12(table);
        printf("\n");
        scenario_s11(table);      /* 最后一场：它会把 clk_valid 置真 */
    }
    else
    {
        printf("unknown scenario: %s\n", scn);
        return 2;
    }

    printf("\n%s  (fail=%d)\n", g_fail == 0 ? "RESULT: OK" : "RESULT: FAIL", g_fail);
    return g_fail == 0 ? 0 : 1;
}
