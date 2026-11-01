/* 宿主端假电台的"硬件"侧：doppler.c 引用到的每一个外部符号都在这里落地。
 *
 * 三条原则：
 *  1. 只做"记录 + 最小模型"，绝不替固件做决定；
 *  2. 数字口径与原固件一致（freq10 = 10 Hz 单位，tone10 = 0.1 Hz 单位）；
 *  3. 凡是"真机行为未知"的地方，代码里写清楚这是推测。
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>

#include "sim.h"

/* 真头：与 doppler.c 完全同一份，函数签名由编译器把关 */
#include "app/doppler.h"
#include "app/common.h"
#include "app/generic.h"
#include "audio.h"
#include "bitmaps.h"
#include "dcs.h"
#include "driver/crc.h"
#include "driver/gpio.h"
#include "font.h"
#include "driver/backlight.h"
#include "driver/bk4819.h"
#include "driver/py25q16.h"
#include "driver/st7565.h"
#include "frequencies.h"
#include "functions.h"
#include "helper/battery.h"
#include "misc.h"
#include "radio.h"
#include "scheduler.h"
#include "settings.h"
#include "ui/helper.h"
#include "ui/ui.h"

/* ================================================================= */
/* 事件记录器                                                        */
/* ================================================================= */

sim_ev_t g_ev[SIM_MAX_EV];
uint32_t g_ev_n = 0;

const char *sim_ev_name(ev_kind_t k)
{
    switch (k)
    {
        case EV_RX_FREQ:    return "RX_FREQ";
        case EV_TX_FREQ:    return "TX_FREQ";
        case EV_CTCSS:      return "CTCSS";
        case EV_CTCSS_OFF:  return "CTCSS_OFF";
        case EV_SQUELCH:    return "SQUELCH";
        case EV_POWER:      return "POWER";
        case EV_FRONTEND:   return "FRONTEND";
        case EV_AUDIO:      return "AUDIO";
        case EV_DISPLAY:    return "DISPLAY";
        case EV_FLASH:      return "FLASH";
        case EV_RADIO:      return "RADIO";
        case EV_APP:        return "APP";
        default:            return "?";
    }
}

void sim_emit(ev_kind_t kind, int32_t a, int32_t b)
{
    if (g_ev_n < SIM_MAX_EV)
    {
        g_ev[g_ev_n].t10  = sim_now10();
        g_ev[g_ev_n].kind = (uint8_t)kind;
        g_ev[g_ev_n].a    = a;
        g_ev[g_ev_n].b    = b;
        g_ev_n++;
    }

    printf("t=%7ums  %-9s %ld", (unsigned)(sim_now10() * 10u), sim_ev_name(kind), (long)a);
    if (b != 0)
        printf("  (%ld)", (long)b);
    printf("\n");
    fflush(stdout);
}

void sim_mark(const char *what)
{
    if (g_ev_n < SIM_MAX_EV)
    {
        g_ev[g_ev_n].t10  = sim_now10();
        g_ev[g_ev_n].kind = (uint8_t)EV_APP;
        g_ev[g_ev_n].a    = -1;
        g_ev[g_ev_n].b    = 0;
        g_ev_n++;
    }
    printf("t=%7ums  ---- %s\n", (unsigned)(sim_now10() * 10u), what);
    fflush(stdout);
}

void sim_note(const char *fmt, ...)
{
    va_list ap;

    printf("# ");
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    printf("\n");
    fflush(stdout);
}

int sim_find(ev_kind_t kind, uint32_t from)
{
    for (uint32_t i = from; i < g_ev_n; i++)
        if (g_ev[i].kind == (uint8_t)kind)
            return (int)i;
    return -1;
}

int sim_find_last(ev_kind_t kind)
{
    for (uint32_t i = g_ev_n; i > 0u; i--)
        if (g_ev[i - 1u].kind == (uint8_t)kind)
            return (int)(i - 1u);
    return -1;
}

uint32_t sim_count(ev_kind_t kind)
{
    uint32_t n = 0;

    for (uint32_t i = 0; i < g_ev_n; i++)
        if (g_ev[i].kind == (uint8_t)kind)
            n++;
    return n;
}

uint32_t sim_count_range(ev_kind_t kind, uint32_t from_t10, uint32_t to_t10)
{
    uint32_t n = 0;

    for (uint32_t i = 0; i < g_ev_n; i++)
        if (g_ev[i].kind == (uint8_t)kind
            && g_ev[i].t10 >= from_t10 && g_ev[i].t10 <= to_t10)
            n++;
    return n;
}

void sim_dump(FILE *f, ev_kind_t kind)
{
    for (uint32_t i = 0; i < g_ev_n; i++)
        if (g_ev[i].kind == (uint8_t)kind)
            fprintf(f, "t=%lums  %s %ld\n",
                    (unsigned long)(g_ev[i].t10 * 10u),
                    sim_ev_name(kind), (long)g_ev[i].a);
}

/* ================================================================= */
/* 虚拟时钟                                                          */
/* ================================================================= */

static uint32_t g_tick10 = 0;

/* PTT 意图：由场景脚本在注入 PTT_ON 时置位、PTT_OFF 之后清掉。
 * 它决定 BK4819_SetFrequency 落成 TX_FREQ 还是 RX_FREQ。
 * 不能只看 gCurrentFunction —— 固件在 DPH_PttEvent 里先写频点、
 * 后调 GENERIC_Key_PTT，写那一刻还是 FUNCTION_RECEIVE（见 doppler.c:3312/3318）。 */
static bool g_ptt_intent = false;

void sim_ptt_intent(bool on) { g_ptt_intent = on; }

uint32_t sim_now10(void)      { return g_tick10; }
uint32_t sim_now_ms(void)     { return g_tick10 * 10u; }
void     sim_clock_reset(void) { g_tick10 = 0; }

/* app.c 的门（App/app/app.c:1917）：
 *   if (gScreenToDisplay == DISPLAY_DOPPLER || DOPPLER_IsActive() || DOPPLER_ExtActive())
 *       DOPPLER_Tick10ms();
 * 少了这道门，退页之后每拍还会再进一次 tick，dph_seeded 那种位就永远对不上。 */
void sim_run_ms(uint32_t ms)
{
    uint32_t n = ms / 10u;

    for (uint32_t i = 0; i < n; i++)
    {
        g_tick10++;

        /* app.c:2963 —— 有人（PTT / 结束发射）把屏幕要回主界面时，
         * 只要这一页还活着就把它留住。 */
        if (gRequestDisplayScreen == DISPLAY_MAIN && DOPPLER_IsActive())
            gRequestDisplayScreen = DISPLAY_DOPPLER;

        if (gScreenToDisplay == DISPLAY_DOPPLER
            || DOPPLER_IsActive() || DOPPLER_ExtActive())
            DOPPLER_Tick10ms();
    }
}

void sim_idle_ms(uint32_t ms)
{
    g_tick10 += ms / 10u;
}

uint32_t SCHEDULER_GetTick10ms(void) { return g_tick10; }

/* ================================================================= */
/* 外部 SPI flash（PY25Q16）                                          */
/* ================================================================= */

static uint8_t g_flash[SIM_FLASH_SIZE];

void           sim_flash_blank(void)   { memset(g_flash, 0xFF, sizeof(g_flash)); }
uint8_t       *sim_flash_ptr(void)     { return g_flash; }
const uint8_t *sim_flash_const(void)   { return g_flash; }

bool sim_flash_load(const char *path, uint32_t at_addr, uint32_t max_len)
{
    FILE    *fp = fopen(path, "rb");
    size_t   n;
    uint32_t room = SIM_FLASH_SIZE - at_addr;

    if (fp == NULL)
        return false;

    if (max_len != 0u && max_len < room)
        room = max_len;

    n = fread(g_flash + at_addr, 1u, room, fp);
    fclose(fp);

    return n > 0u;
}

void PY25Q16_ReadBufferPhysical(uint32_t Address, void *pBuffer, uint32_t Size)
{
    if (Address >= SIM_FLASH_SIZE || Size > SIM_FLASH_SIZE - Address)
    {
        memset(pBuffer, 0xFF, Size);
        printf("# PY25Q16_Read 越界 addr=0x%06X len=%lu\n",
               (unsigned)Address, (unsigned long)Size);
        return;
    }
    memcpy(pBuffer, g_flash + Address, Size);
}

bool PY25Q16_WriteBufferPhysical(uint32_t Address, const void *pBuffer, uint32_t Size)
{
    if (Address >= SIM_FLASH_SIZE || Size > SIM_FLASH_SIZE - Address)
        return false;

    memcpy(g_flash + Address, pBuffer, Size);
    sim_emit(EV_FLASH, (int32_t)Address, (int32_t)Size);
    return true;
}

bool PY25Q16_SectorErasePhysical(uint32_t Address)
{
    if (Address >= SIM_FLASH_SIZE || (Address % PY25Q16_SECTOR_SIZE) != 0u)
        return false;

    memset(g_flash + Address, 0xFF, PY25Q16_SECTOR_SIZE);
    sim_emit(EV_FLASH, (int32_t)Address, 0);
    return true;
}

/* ================================================================= */
/* BK4819                                                            */
/* ================================================================= */

sim_chip_t g_chip;

void BK4819_SetFrequency(uint32_t Frequency)
{
    g_chip.freq10 = Frequency;
    /* 口径：RX_FREQ / TX_FREQ 只看"操作员此刻是不是按着 PTT"（g_ptt_intent），
     * 不看 gCurrentFunction —— 松手那一下固件会先把芯片调回收听频点，再调
     * GENERIC_Key_PTT(false) 退回 FUNCTION_RECEIVE（doppler.c:3344 在 :3318 之前
     * 的那一支），按 gCurrentFunction 判会把这次收频写成"发射频点"。 */
    sim_emit(g_ptt_intent ? EV_TX_FREQ : EV_RX_FREQ, (int32_t)Frequency, 0);
}

void BK4819_SetCTCSSFrequency(uint32_t BaudRate)
{
    g_chip.ctcss10 = BaudRate;
    sim_emit(EV_CTCSS, (int32_t)BaudRate, 0);
}

void BK4819_ExitSubAu(void)
{
    g_chip.ctcss10     = 0u;
    g_chip.sub_au_exits++;
    sim_emit(EV_CTCSS_OFF, 0, 0);
}

void BK4819_SetupSquelch(uint8_t SquelchOpenRSSIThresh,
                         uint8_t SquelchCloseRSSIThresh,
                         uint8_t SquelchOpenNoiseThresh,
                         uint8_t SquelchCloseNoiseThresh,
                         uint8_t SquelchCloseGlitchThresh,
                         uint8_t SquelchOpenGlitchThresh)
{
    (void)SquelchCloseRSSIThresh;
    (void)SquelchOpenNoiseThresh;
    (void)SquelchCloseNoiseThresh;
    (void)SquelchCloseGlitchThresh;
    (void)SquelchOpenGlitchThresh;

    g_chip.squelch_open_rssi = SquelchOpenRSSIThresh;
    sim_emit(EV_SQUELCH, (int32_t)SquelchOpenRSSIThresh, 0);
}

void BK4819_SetFilterBandwidth(const BK4819_FilterBandwidth_t Bandwidth,
                               const bool weak_no_different)
{
    (void)weak_no_different;
    g_chip.filter_bw = (uint8_t)Bandwidth;
    sim_emit(EV_APP, (int32_t)Bandwidth, 0);
}

void BK4819_WriteRegister(BK4819_REGISTER_t Register, uint16_t Data)
{
    g_chip.register_writes++;
    (void)Register;
    (void)Data;
}

bool BK4819_IsGpioOutSet(BK4819_GPIO_PIN_t Pin)
{
    if (Pin == BK4819_GPIO3_PIN31_UHF_LNA)
        return g_chip.uhf_lna;
    if (Pin == BK4819_GPIO4_PIN32_VHF_LNA)
        return g_chip.vhf_lna;
    return false;
}

/* 前端路径：真机按频段切两只 LNA。这里只按 280 MHz 分界（与
 * DPH_FrontEndHeal 的 want_uhf 同一口径），不模拟真实插损/灵敏度。 */
void BK4819_PickRXFilterPathBasedOnFrequency(uint32_t Frequency)
{
    bool uhf = (Frequency >= 28000000u);

    g_chip.uhf_lna = uhf;
    g_chip.vhf_lna = !uhf;
    sim_emit(EV_FRONTEND, uhf ? 1 : 0, 0);
}

/* ================================================================= */
/* 电源 / 收发状态（radio.c 的替身）                                  */
/* ================================================================= */

void RADIO_ConfigureSquelchAndOutputPower(VFO_Info_t *pInfo)
{
    if (pInfo == NULL)
        return;
    sim_emit(EV_POWER, (int32_t)pInfo->OUTPUT_POWER,
             (int32_t)(pInfo->pRX ? pInfo->pRX->Frequency : 0u));
}

void RADIO_SetTxParameters(void)
{
    sim_emit(EV_APP, 0x5458, 0);   /* "TX" */
}

bool RADIO_SignalDbm(int16_t *out)
{
    if (out != NULL)
        *out = -120;               /* 没有射频，永远弱到听不见 */
    return true;
}

void RADIO_SignalReset(void) {}

void APP_EndTransmission(void)
{
    sim_emit(EV_RADIO, 0, 0);
}

void APP_HandleEndTransmission(void) {}

/* app.c 的 PTT 前端：真机这里会 RADIO_PrepareTX + FUNCTION_Select 并且等到
 * 载波真的起来。仿真是"立刻切功能、立刻发事件"——时序窗见报告里的边界一节。 */
void GENERIC_Key_PTT(bool bKeyPressed)
{
    if (bKeyPressed)
    {
        gCurrentFunction = FUNCTION_TRANSMIT;
        sim_emit(EV_RADIO, 1, 0);
    }
    else
    {
        gCurrentFunction = FUNCTION_RECEIVE;
        sim_emit(EV_RADIO, 0, 1);
    }
}

void COMMON_KeypadLockToggle(void)
{
    gEeprom.KEY_LOCK = !gEeprom.KEY_LOCK;
    sim_emit(EV_APP, 0x4C4B, gEeprom.KEY_LOCK ? 1 : 0);
}

/* ================================================================= */
/* GPIO / 音频通路                                                    */
/* ================================================================= */

static bool g_pin_state[8][16];

void GPIO_SetOutputPin(uint32_t Pin)
{
    uint32_t port = GPIO_PORT(Pin) & 7u;
    uint32_t mask = GPIO_PIN_MASK(Pin);

    g_pin_state[port][0] |= 0u;   /* no-op, 保持数组被使用 */
    for (uint32_t b = 0; b < 16u; b++)
        if (mask & (1u << b))
            g_pin_state[port][b] = true;

    if (Pin == GPIO_PIN_AUDIO_PATH)
        sim_emit(EV_AUDIO, 1, 0);
}

void GPIO_ResetOutputPin(uint32_t Pin)
{
    uint32_t port = GPIO_PORT(Pin) & 7u;
    uint32_t mask = GPIO_PIN_MASK(Pin);

    for (uint32_t b = 0; b < 16u; b++)
        if (mask & (1u << b))
            g_pin_state[port][b] = false;

    if (Pin == GPIO_PIN_AUDIO_PATH)
        sim_emit(EV_AUDIO, 0, 0);
}

void GPIO_TogglePin(uint32_t Pin)
{
    if (GPIO_IsInputPinSet(Pin))
        GPIO_ResetOutputPin(Pin);
    else
        GPIO_SetOutputPin(Pin);
}

bool GPIO_IsInputPinSet(uint32_t Pin)
{
    uint32_t port = GPIO_PORT(Pin) & 7u;
    uint32_t mask = GPIO_PIN_MASK(Pin);

    for (uint32_t b = 0; b < 16u; b++)
        if (mask & (1u << b))
            return g_pin_state[port][b];
    return false;
}

/* ================================================================= */
/* 显示（全部丢弃，只记"它想画哪一屏"）                               */
/* ================================================================= */

void GUI_SelectNextDisplay(GUI_DisplayType_t Display)
{
    gRequestDisplayScreen = Display;
    gScreenToDisplay      = Display;   /* 真机下一帧就生效，仿真当拍生效 */
    sim_emit(EV_DISPLAY, (int32_t)Display, 0);
}

void ST7565_BlitFullScreen(void) {}
void ST7565_BlitLine(unsigned line) { (void)line; }
void ST7565_BlitStatusLine(void) {}
void UI_DisplayClear(void) {}
void UI_PrintString(const char *pString, uint8_t Start, uint8_t End,
                    uint8_t Line, uint8_t Width)
{
    (void)pString; (void)Start; (void)End; (void)Line; (void)Width;
}
void UI_DrawLineBuffer(uint8_t (*buffer)[128], int16_t x1, int16_t y1,
                       int16_t x2, int16_t y2, bool black)
{
    (void)buffer; (void)x1; (void)y1; (void)x2; (void)y2; (void)black;
}
void UI_DrawRectangleBuffer(uint8_t (*buffer)[128], int16_t x1, int16_t y1,
                            int16_t x2, int16_t y2, bool black)
{
    (void)buffer; (void)x1; (void)y1; (void)x2; (void)y2; (void)black;
}

/* ================================================================= */
/* 频段表 / CRC                                                       */
/* ================================================================= */

typedef struct
{
    uint32_t lower;
} sim_band_t;

/* 与 App/frequencies.c 的 frequencyBandTable 同一组下边界 */
static const uint32_t sim_band_lower[] = {
    26000000u, 108000000u, 13700000u, 17400000u,
    35000000u, 40000000u, 47000000u
};

FREQUENCY_Band_t FREQUENCY_GetBand(uint32_t Frequency)
{
    for (int32_t band = (int32_t)(sizeof(sim_band_lower) / sizeof(sim_band_lower[0])) - 1;
         band >= 0; band--)
        if (Frequency >= sim_band_lower[band])
            return (FREQUENCY_Band_t)band;

    return BAND1_50MHz;
}

/* 与 App/driver/crc.c 逐位一致（CRC-16/XMODEM，初值 0） */
uint16_t CRC_Calculate(const void *pBuffer, uint16_t Size)
{
    const uint8_t *p = (const uint8_t *)pBuffer;
    uint16_t crc = 0u;

    for (uint16_t i = 0; i < Size; i++)
    {
        crc ^= (uint16_t)(p[i] << 8);
        for (int j = 0; j < 8; j++)
            crc = (crc & 0x8000u) ? (uint16_t)((crc << 1) ^ 0x1021u)
                                  : (uint16_t)(crc << 1);
    }
    return crc;
}

void CRC_Init(void) {}

/* ================================================================= */
/* 数据符号                                                           */
/* ================================================================= */

/* CTCSS 表：真表在 dcs.c。这里只放标准 50 音（0.1 Hz 单位）。
 * 只要值的**索引口径**与真表一致，doppler.c 的查表逻辑就是等价的。 */
const uint16_t CTCSS_Options[50] = {
     670u,  693u,  719u,  744u,  770u,  797u,  825u,  854u,  885u,  915u,
     948u,  974u, 1000u, 1035u, 1072u, 1109u, 1148u, 1188u, 1230u, 1273u,
    1318u, 1365u, 1413u, 1462u, 1514u, 1567u, 1598u, 1622u, 1655u, 1679u,
    1713u, 1738u, 1773u, 1799u, 1835u, 1862u, 1991u, 2015u, 2065u, 2181u,
    2257u, 2291u, 2336u, 2418u, 2503u, 2541u, 2599u, 2621u, 2687u, 2741u
};

/* 点阵字模：**故意不抄真字模**，每个字形都填满（3x5 / 6x7 实心块）。
 *
 * 判版面用的断言只需要回答一件事："这一格到底有没有被画出来"（交接-28 E2/E4
 * 都是"整条被跳过"这一类的缺陷）。字形像不像与那个问题无关，而真字模在
 * font.c 里被 #ifdef 包着，抄一份过来就变成"同一份数据两份来源"—— 正是这个
 * 项目反复出事的形态。宽度仍然走固件自己的 DPH_TextWidth（按字符数算），
 * 字符间距也仍是 DPH_DrawTiny/DrawSmall 里的 4 px / 7 px 步进，
 * 所以"画到哪儿、会不会压到隔壁"这些几何结论与真机一致。 */
const uint8_t gFont3x5[96][3]           = {[0 ... 95] = {0x1Fu, 0x1Fu, 0x1Fu}};
const uint8_t gFontSmallPacked[FONT_SMALL_PACKED_SIZE] =
    {[0 ... FONT_SMALL_PACKED_SIZE - 1] = 0xFFu};
const uint8_t gFontKeyLock[9]           = {0};

uint8_t  gFrameBuffer[FRAME_LINES][LCD_WIDTH];
uint8_t  gStatusLine[LCD_WIDTH];

FUNCTION_Type_t gCurrentFunction = FUNCTION_RECEIVE;
uint8_t         gUpdateStatus     = 0;
bool            gUpdateDisplay    = false;
bool            gEnableSpeaker    = false;
BEEP_Type_t     gBeepToPlay       = BEEP_NONE;
uint16_t        gBacklightCountdown_500ms = 0;
uint16_t        gBatteryVoltageAverage    = 740;
GUI_DisplayType_t gScreenToDisplay = DISPLAY_MAIN;
GUI_DisplayType_t gRequestDisplayScreen = DISPLAY_MAIN;

volatile uint8_t gSerialConfigCountDown_500ms = 0;
volatile uint8_t gSerialFlashCountDown_500ms  = 0;
uint8_t          gSetting_set_pwr             = 0;   /* OUTPUT_POWER_USER */

EEPROM_Config_t gEeprom;
VFO_Info_t     *gTxVfo     = NULL;
VFO_Info_t     *gRxVfo     = NULL;
VFO_Info_t     *gCurrentVfo = NULL;

/* ================================================================= */
/* 电台初始状态                                                       */
/* ================================================================= */

void sim_radio_init(uint32_t rx10, uint32_t tx10, uint8_t power)
{
    VFO_Info_t *v = &gEeprom.VfoInfo[0];

    memset(&gEeprom, 0, sizeof(gEeprom));

    v->freq_config_RX.Frequency = rx10;
    v->freq_config_RX.CodeType  = CODE_TYPE_OFF;
    v->freq_config_RX.Code      = 0;

    v->freq_config_TX.Frequency = tx10;
    v->freq_config_TX.CodeType  = CODE_TYPE_OFF;
    v->freq_config_TX.Code      = 0;

    v->pRX = &v->freq_config_RX;
    v->pTX = &v->freq_config_TX;

    v->StepFrequency               = 2500u;   /* 25 kHz */
    v->OUTPUT_POWER                = power;
    v->TXP_CalculatedSetting       = power;
    v->CHANNEL_BANDWIDTH           = BK4819_FILTER_BW_WIDE;
    v->SquelchOpenRSSIThresh       = 10u;
    v->SquelchCloseRSSIThresh      = 10u;
    v->SquelchOpenNoiseThresh      = 100u;
    v->SquelchCloseNoiseThresh     = 100u;
    v->SquelchCloseGlitchThresh    = 100u;
    v->SquelchOpenGlitchThresh     = 200u;
    v->Band                        = (uint16_t)FREQUENCY_GetBand(rx10);
    v->FrequencyReverse            = false;
    strcpy(v->Name, "CHAN");

    gEeprom.SQUELCH_LEVEL = 3u;
    gEeprom.KEY_LOCK      = false;

    gRxVfo      = v;
    gTxVfo      = v;
    gCurrentVfo = v;

    gCurrentFunction = FUNCTION_RECEIVE;

    g_chip.freq10    = 0u;
    g_chip.ctcss10   = 0u;
    g_chip.uhf_lna   = false;
    g_chip.vhf_lna   = false;
    g_chip.register_writes = 0u;
    g_chip.sub_au_exits    = 0u;

    g_ev_n = 0u;
    sim_clock_reset();
    sim_ptt_intent(false);
}
