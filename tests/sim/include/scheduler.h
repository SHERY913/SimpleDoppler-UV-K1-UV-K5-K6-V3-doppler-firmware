#ifndef SIM_SCHEDULER_H
#define SIM_SCHEDULER_H
#include <stdint.h>
/* Host stand-in for App/scheduler.h: no SysTick, no NVIC. The only thing
 * doppler.c needs is the free-running 10 ms tick, which the simulator drives
 * from its virtual clock. */
static inline void SCHEDULER_Enable(void)  {}
static inline void SCHEDULER_Disable(void) {}
uint32_t SCHEDULER_GetTick10ms(void);
#endif
