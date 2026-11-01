#ifndef SIM_DRIVER_GPIO_H
#define SIM_DRIVER_GPIO_H
#include <stdint.h>
#include <stdbool.h>
/* Host stand-in for App/driver/gpio.h: the PY32 LL_GPIO calls become events. */
#define GPIO_MAKE_PIN(Port, PinMask) ((uint32_t)((((uint32_t)(Port)) << 16) | (0xffff & (PinMask))))
#define GPIO_PORT(Pin)               ((Pin) >> 16)
#define GPIO_PIN_MASK(Pin)           (0xffff & (Pin))

enum GPIO_PINS {
    GPIO_PIN_PTT        = GPIO_MAKE_PIN(1, 1u << 10),
    GPIO_PIN_BACKLIGHT  = GPIO_MAKE_PIN(5, 1u << 8),
    GPIO_PIN_FLASHLIGHT = GPIO_MAKE_PIN(2, 1u << 13),
    GPIO_PIN_AUDIO_PATH = GPIO_MAKE_PIN(0, 1u << 8),
};

void GPIO_SetOutputPin(uint32_t Pin);
void GPIO_ResetOutputPin(uint32_t Pin);
void GPIO_TogglePin(uint32_t Pin);
bool GPIO_IsInputPinSet(uint32_t Pin);
static inline void GPIO_EnableAudioPath(void)  { GPIO_SetOutputPin(GPIO_PIN_AUDIO_PATH); }
static inline void GPIO_DisableAudioPath(void) { GPIO_ResetOutputPin(GPIO_PIN_AUDIO_PATH); }
static inline void GPIO_TurnOnBacklight(void)  { GPIO_SetOutputPin(GPIO_PIN_BACKLIGHT); }
static inline void GPIO_TurnOffBacklight(void) { GPIO_ResetOutputPin(GPIO_PIN_BACKLIGHT); }
static inline bool GPIO_IsPttPressed(void)     { return !GPIO_IsInputPinSet(GPIO_PIN_PTT); }
#endif
