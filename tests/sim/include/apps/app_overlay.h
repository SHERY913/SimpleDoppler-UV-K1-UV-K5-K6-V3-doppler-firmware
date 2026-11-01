#ifndef SIM_APPS_APP_OVERLAY_H
#define SIM_APPS_APP_OVERLAY_H
#include <stdint.h>
/* Only the flash-map constants doppler.c asserts on. Values copied from
 * App/apps/app_overlay.h (48 slots of 8 KiB at 0x102000). */
#define APP_REGION_BASE   0x00102000u
#define APP_SLOT_STRIDE   0x00002000u
#define APP_SLOT_COUNT    48u
#define APP_SLOT_BASE(s)  (APP_REGION_BASE + (uint32_t)(s) * APP_SLOT_STRIDE)
#endif
