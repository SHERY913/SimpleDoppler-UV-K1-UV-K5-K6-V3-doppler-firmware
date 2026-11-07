/**
 * 预览假数据。刻意塞了最长的名字和读数：
 *  `ISS (ZARYA)` / `FOX-1B (AO-91)` 会把 `ellipsis(name, 12)` 和自动缩排逼到极限，
 *  `145.987654 MHz` / `-12.7 kHz` 用来验 `|signed` 与 `fmt.freq(...)` 的宽度。
 */
import type { JsonObject, JsonValue } from '../core/jsonc'

const now = (): JsonObject => {
  const d = new Date()
  const pad = (x: number): string => String(x).padStart(2, '0')
  return {
    ms: Date.now(),
    hhmm: pad(d.getHours()) + ':' + pad(d.getMinutes()),
    hhmmss: pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds()),
    utc: d.toISOString().slice(11, 19) + 'Z',
    date: pad(d.getMonth() + 1) + '-' + pad(d.getDate()),
  }
}

const PASSES: JsonObject[] = [
  {
    i: 1, aos: Date.now() + 740000, los: 0, aosText: '20:41', losText: '20:49',
    date: '11-07', durS: 486, durText: '8 分 06 秒', maxEl: 62, maxElText: '62',
    maxElAt: 0, azAos: 212, azLos: 34, azText: '212°→34°',
    countdown: '还有 12 分 20 秒', ms: 740000, live: false, norad: 25544,
    name: 'ISS (ZARYA)', inCart: true,
    sub: 'NORAD 25544 · LEO', time: '20:41', elText: '62° · 8 分', has: true, elGood: true,
    fav: true, tleText: 'TLE 2 天前', showTle: true, tleBad: false,
  },
  {
    i: 2, aos: 0, los: 0, aosText: '21:03', losText: '21:09',
    date: '11-07', durS: 342, durText: '5 分 42 秒', maxEl: 28, maxElText: '28',
    maxElAt: 0, azAos: 18, azLos: 158, azText: '18°→158°',
    countdown: '还有 34 分 41 秒', ms: 2081000, live: false, norad: 43017,
    name: 'CAS-4B', inCart: false,
    sub: 'NORAD 43017 · LEO', time: '21:03', elText: '28° · 5 分', has: true, elGood: true,
    fav: false, tleText: 'TLE 4 天前', showTle: true, tleBad: true,
  },
  {
    i: 3, aos: 0, los: 0, aosText: '21:22', losText: '21:31',
    date: '11-07', durS: 528, durText: '8 分 48 秒', maxEl: 74, maxElText: '74',
    maxElAt: 0, azAos: 350, azLos: 96, azText: '350°→96°',
    countdown: '还有 53 分 02 秒', ms: 3182000, live: false, norad: 7530,
    name: 'FOX-1B (AO-91)', inCart: true,
    sub: 'NORAD 7530 · LEO', time: '21:22', elText: '74° · 8 分', has: true, elGood: true,
    fav: true, tleText: 'TLE 1 天前', showTle: true, tleBad: false,
  },
  {
    i: 4, aos: 0, los: 0, aosText: '22:14', losText: '22:19',
    date: '11-07', durS: 296, durText: '4 分 56 秒', maxEl: 12, maxElText: '12',
    maxElAt: 0, azAos: 96, azLos: 244, azText: '96°→244°',
    countdown: '还有 1 时 45 分', ms: 6300000, live: false, norad: 40967,
    name: 'AO-73 (FUNcube-1)', inCart: false,
    sub: 'NORAD 40967 · LEO', time: '22:14', elText: '12° · 4 分', has: true, elGood: false,
    fav: false, tleText: 'TLE 9 天前', showTle: true, tleBad: true,
  },
  {
    i: 5, aos: 0, los: 0, aosText: '23:07', losText: '23:13',
    date: '11-07', durS: 371, durText: '6 分 11 秒', maxEl: 41, maxElText: '41',
    maxElAt: 0, azAos: 302, azLos: 71, azText: '302°→71°',
    countdown: '还有 2 时 38 分', ms: 9480000, live: false, norad: 43678,
    name: 'CAS-4A', inCart: false,
    sub: 'NORAD 43678 · LEO', time: '23:07', elText: '41° · 6 分', has: true, elGood: true,
    fav: false, tleText: 'TLE 3 天前', showTle: true, tleBad: false,
  },
]

const TX: JsonObject[] = [
  { downMhz: 145.987654, upMhz: 435.123456, mode: 'FM', type: '主', desc: '语音 / APRS' },
  { downMhz: 145.825, upMhz: 145.825, mode: 'FM', type: '备', desc: 'APRS 中继' },
  { downMhz: 437.8, upMhz: 145.9, mode: 'SSB', type: '测控', desc: '遥测信标' },
]

const SETTINGS_ROWS: JsonObject[] = [
  { kind: 'section', key: '', key2: '', title: '跟踪', sub: '', value: '', b2: '', on: false, warn: false, hasSub: false, line: false, isSection: true, isToggle: false, isSelect: false, isStep: false, isInput: false, isBig: false, isNote: false, isBtn1: false, isBtn2: false, isLink: false, isHold: false },
  { kind: 'toggle', key: 'notify', key2: '', title: '过境提醒', sub: 'AOS 前提醒', value: '', b2: '', on: true, warn: false, hasSub: true, line: true, isSection: false, isToggle: true, isSelect: false, isStep: false, isInput: false, isBig: false, isNote: false, isBtn1: false, isBtn2: false, isLink: false, isHold: false },
  { kind: 'step', key: 'minEl', key2: '', title: '最小仰角', sub: '', value: '10°', b2: '', on: false, warn: false, hasSub: false, line: true, isSection: false, isToggle: false, isSelect: false, isStep: true, isInput: false, isBig: false, isNote: false, isBtn1: false, isBtn2: false, isLink: false, isHold: false },
  { kind: 'select', key: 'band', key2: '', title: '波段', sub: '', value: '2m / 70cm', b2: '', on: false, warn: false, hasSub: false, line: true, isSection: false, isToggle: false, isSelect: true, isStep: false, isInput: false, isBig: false, isNote: false, isBtn1: false, isBtn2: false, isLink: false, isHold: false },
]

export function previewScope(): JsonObject {
  const nextMs = 740000
  return {
    now: now(),
    prefs: {
      freqDigits: 5, showTleAge: true, minEl: 10, searchHours: 24, timelineHours: 6,
      rxOnly: false, listMode: 'table', notifyOn: true, notifyLeadS: 300,
      callsign: 'BG0FPJ', night: true, accent: 'acc', sortMode: 'aos', sortLabel: '按 AOS',
      favPin: true, radioName: 'UV-K1', demo: true, keepAwake: true, releaseOnLeave: true,
      autoReconnect: true, compassGain: 1, simPass: false, playMode: 'voice',
      playVoice: true, playSstv: false,
    },
    sat: {
      name: 'ISS (ZARYA)', norad: 25544, downMhz: 145.987654, upMhz: 145.99,
      toneHz: 67, mode: 'FM', band: '2m', orbit: 'LEO', inclDeg: 51.64,
      source: 'TLE', rxOk: true, hasFreq: true,
      blurb: '国际空间站业余无线电中继。跨区过境时 SSTV 和语音都在这颗星上，AOS 前的多普勒斜率变化最快，跟踪页的读数也最难看。',
      tleAgeDays: 2, fav: true, curDownMhz: 145.987654, curUpMhz: 145.99,
      curToneHz: 67, curMode: 'FM', txLabel: 'FM · 67.0 Hz', tx: TX,
    },
    next: {
      has: true, ms: nextMs, countdown: '还有 12 分 20 秒', aos: Date.now() + nextMs,
      aosText: '20:41', losText: '20:49', maxEl: 62, maxElText: '62',
      maxElAt: Date.now() + nextMs + 240000, durS: 486, durText: '8 分 06 秒',
      azAos: 212, azLos: 34, live: false, norad: 25544, satName: 'ISS (ZARYA)',
    },
    passes: PASSES,
    cart: {
      count: 2, satCount: 2, bytes: 4096, fill: 0.0625, tooMany: false, max: 32,
      items: [
        { norad: 25544, name: 'ISS (ZARYA)', aos: Date.now() + nextMs, aosText: '20:41', durS: 486, maxEl: 62 },
        { norad: 7530, name: 'FOX-1B (AO-91)', aos: 0, aosText: '21:22', durS: 528, maxEl: 74 },
      ],
    },
    track: {
      doppHz: -12700, azDeg: 212, elDeg: 41, rangeKm: 1842.6, rateKmS: -5.42,
      remainS: 312, progress: 0.36, inPass: true, aosMs: 0, losMs: 0, trimHz: -300,
      errDeg: 2.4, aligned: true, sim: false, marks: 3, live: true, stationOk: true,
    },
    list: {
      rows: PASSES.map((p) => ({ ...p })),
      tl: {
        rows: [
          { norad: 25544, name: 'ISS (ZARYA)', sub: '20:41 · 62°' },
          { norad: 43017, name: 'CAS-4B', sub: '21:03 · 28°' },
          { norad: 7530, name: 'FOX-1B (AO-91)', sub: '21:22 · 74°' },
          { norad: 40967, name: 'AO-73 (FUNcube-1)', sub: '22:14 · 12°' },
          { norad: 43678, name: 'CAS-4A', sub: '23:07 · 41°' },
        ],
        t0: Date.now(), spanMs: 21600000, stepMs: 1800000,
      },
    },
    sky: {
      count: 27, rx: 3, busy: false, off: 0, selNorad: 25544,
      sel: { has: true, name: 'ISS (ZARYA)', az: 212, el: 41, mode: 'FM', rxOk: true, los: false },
      next: { has: true, name: 'CAS-4B', countdown: '还有 34 分 41 秒' },
      dots: [
        { norad: 25544, az: 212, el: 41, name: 'ISS (ZARYA)' },
        { norad: 43017, az: 18, el: 12, name: 'CAS-4B' },
        { norad: 7530, az: 350, el: 8, name: 'FOX-1B (AO-91)' },
      ],
    },
    settings: { rows: SETTINGS_ROWS, aboutRows: 4, head: '设置' },
    ui: {
      // 详情页
      loading: false, empty: false, stationOk: true, sortEl: 10, minEl: 10, count: 5, inCart: 2,
      // 列表页
      sub: '24 小时内 · 5 次', hits: 5, filtered: true,
      filterSummary: '2m · FM · ≥10°', noStation: false, computing: false,
      progress: 0.62, scanText: '扫描 627 颗', query: '', isTable: true, isTimeline: false,
      sheetFilter: false, sheetSort: false, sortLabel: '按 AOS', sortPopular: false,
      sortAos: true, sortName: false, sortNorad: false, pin: 0,
      span3: false, span6: true, span12: false, span24: false, spanHours: 6,
      tleHint: 'TLE 2 天前', tleBad: false, b2m: true, b70: true, b23cm: false,
      b6m: false, bOther: false, mFM: true, mSSB: false, mCW: false, favOnly: false,
      up: '↑ 145.990', soon: '12 分',
      // 跟踪页
      connected: true, notConnected: false, connHint: '',
      status: '锁定', statusColor: 'ok', statusFill: 'ok@14',
      linkText: 'USB · 57600', rxText: '接收中', rxColor: 'ok',
      txLabel: '发射', txText: '145.990', txColor: 'acc',
      doppText: '-12.7 kHz', doppColor: 'warn', txShiftText: '↓ 12.7',
      rangeText: '1842.6 km', rateText: '-5.42 km/s',
      azElText: '212° / 41°', azElColor: 'txt', remainText: '5 分 12 秒',
      remainColor: 'txt', errLabel: '罗盘', errText: '2.4°', errColor: 'dim',
      scaleText: '±50 kHz', hint: '按住 M 预听', hintColor: 'dim',
      trimText: '-300 Hz', trimColor: 'acc', progressPct: '36%',
      progressText2: '36%', windowText: '20:41 → 20:49', note: '',
      framesText: '48 帧', canMark: true, simOn: false, compassAz: 212,
      compassEl: 41, lineAz: 212, lineEl: 41, lineDashed: true, lineLabel: 'ISS',
      axis: 'AZ', gain: 1,
      // 设置页
      banner: '演示数据开着，过境是假的', hasBanner: true, isErr: false, night: true,
      seedHex: '#35C7E8', preset: '海蓝', themeLine: 'H 196° S 76% V 91%',
      hueText: '196°', satText: '76%', valText: '91%',
      verLine: '2.0.0-m5 · build 117', aboutFoot: 'BG0FPJ · F4HWN',
    },
    item: PASSES[0],
    i: 1,
    sel: { has: true, name: 'ISS (ZARYA)', az: 212, el: 41, mode: 'FM', rxOk: true, los: false },
    tl: { rows: 12, t0: Date.now(), spanMs: 21600000, stepMs: 1800000 },
  }
}

export const FIXTURE_NAMES = ['ISS (ZARYA)', 'CAS-4B', 'FOX-1B (AO-91)']
export const FIXTURE_READINGS = ['145.987654 MHz', '-12.7 kHz']

/** 给 list 节点取行数组：`items` 是裸路径。 */
export function rowsAt(scope: JsonValue, path: string): JsonValue[] | null {
  let cur: unknown = scope
  for (const part of path.split('.')) {
    if (cur === null || cur === undefined || typeof cur !== 'object') return null
    if (Array.isArray(cur)) {
      const idx = Number(part)
      cur = Number.isFinite(idx) ? cur[idx] : undefined
    } else {
      cur = (cur as Record<string, unknown>)[part]
    }
  }
  return Array.isArray(cur) ? cur : null
}
