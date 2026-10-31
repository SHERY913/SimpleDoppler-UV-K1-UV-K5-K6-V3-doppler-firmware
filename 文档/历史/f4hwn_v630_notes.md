# F4HWN (UV-K1/K5 V3) v6.3.0 —— 解析索引（内部记忆，供写 APP 时查）

源码：`_src/uvk1-v6.3.0`  = tag v6.3.0 (ca3a427)，仓库 armel/uv-k1-k5v3-firmware-custom
芯片：PY32F071 (Cortex-M0+)，RAM 16K，Flash 118K @0x08002800

## 1. 内存
- `RAM 0x20000000 + 16K`；`FLASH 0x08002800 + 118K`
- Overlay APP 运行区：**VMA 0x20000280，长度 0x1000**（与 PY25Q16 sector cache 共用，执行期间禁写外置 flash）

## 2. Overlay APP ABI（App/apps/app_api.h）
- 入口：`void app_main(const app_api_t *api);` （源码 `__attribute__((section(".text.entry"), used))`）
- `APP_ABI_MAJOR 1`、`APP_API_LEVEL 3`、`sizeof(app_api_t) == 396`
- 外置 flash：slot 基址 0x00102000，stride 0x2000，48 槽；头 64B（magic "FAP1"=0x31504146、crc32、entry_off、link_vma、asset_size/crc…），code 在 slot+0x1000，asset 在 slot+0x100
- 注册=无注册表：菜单 `APP_MenuOpen()`（App/apps/app_menu.c:246）扫 slot；启动 `APP_LaunchOverlay(slot)`（app_menu.c:390）→ 校验头/CRC/VMA → memcpy 到 0x20000280 → `entry(&app_api)`
- 退出：`return`；EXIT_TO_MAIN 标志决定回主屏；`cfg_save` / `vfo_stage` / `fm_commit` / `beam_save` 都是**延迟提交**，loader 在 app 返回后统一写入

## 3. 写 APP 时用到的 API（api->xxx）
显示：`fb`(gFrameBuffer[7][128]) `display_clear` `status_clear` `draw_line` `draw_rect` `print_normal(s,start,end,line)` `print_bold` `print_inverse` `print_tiny(s,x,y,statusbar,fill)` `print_string(s,start,end,line,width)`(大字体占2页) `display_freq` `draw_battery` `blit_full` `blit_line` `blit_status` `blit_page` `lcd_feed`
文本坐标：line=页号 0..6（8px/页）；start/end=x 像素，**end>start 则水平居中**；小字体 6x7 + 1px 间距
图形坐标：像素 x 0..127, y 0..55
输入：`get_key()` → APP_KEY_0..9=0..9, MENU=10, UP=11, DOWN=12, EXIT=13, STAR=14, F=15, PTT=16, INVALID=19, SAVER=0xFD, WAKE=0xFE
时间：`ticks_ms()`（=SCHEDULER_GetTick10ms()*10）、`delay_ms(ms)`、`cycles_48mhz()`
硬件：`rx_freq()` `rssi_dbm()` `bk_read/bk_write` `set_af` `audio_path` `play_tone_raw` `tones_off_rx` `led(bool)` `backlight_on()`
射频/VFO：`beam_get(&ch)` 读当前 VFO → 改 `ch.rx_frequency(10Hz)` / `tx_offset_frequency` / `tx_offset_direction`(0off,1up,2down) / `modulation`(0FM,1AM,2USB) / `channel_bandwidth`(0wide,1narrow) / `rx_codetype`,`tx_codetype`(0off,1CTCSS,2DCS) / `output_power` / `step_setting` → `vfo_stage(&ch)`（一次运行只 stage 一次，随后自动回主屏）
其它：`rand32()` `asset_read` `uidivmod`/`idivmod`(返回 uint64：低32商、高32余) `cfg_load`/`cfg_save` `handoff` `sys_*`

## 4. 主循环
`App/main.c Main()` → `SYSTICK_Init(); BOARD_Init(); …` → `while(true){ APP_Update(); if(gNextTimeslice){ APP_TimeSlice10ms(); if(gNextTimeslice_500ms) APP_TimeSlice500ms(); } }`
SysTick 10ms：`gNextTimeslice=true`，50 tick 置 500ms 标志（App/scheduler.c:55）

## 5. 工程范式（以 App/apps/vfotest 为准）
- 单文件 `<name>_app.c`，`#include "../app_api.h"`，自己写 `memset`（不链 libc/libgcc）
- `static const app_api_t *A;` → `A = api;` → `for(;;){ k=A->get_key(); if(k==prev){A->delay_ms(20); continue;} prev=k; … }`，EXIT 返回
- `app.ld`：`APP_VMA=0x20000280`、`APP_LENGTH=0x1000`、`ENTRY(app_main)`、`.app` 段先 KEEP(*(.text.entry))
- `build.sh`：`-mcpu=cortex-m0plus -mthumb -Os -std=gnu11 -ffreestanding -fno-builtin -Wall -Wextra -nostdlib -nostartfiles -T app.ld -Wl,--gc-sections` → objcopy -O binary → `python ../pack_app.py --name --ver --api-min --vma`
- 能力探测：`A->api_size < offsetof(app_api_t, f)+sizeof(A->f) || !A->f` 判可用
