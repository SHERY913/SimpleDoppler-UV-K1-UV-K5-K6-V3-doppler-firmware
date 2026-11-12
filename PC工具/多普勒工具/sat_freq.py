"""Known downlink frequencies for the amateur birds that are actually workable.

celestrak gives orbits, not frequencies - and half of the "amateur" group is
dead or telemetry-only, so guessing 145.800 for everything just produces a
list of satellites that cannot be tuned.  Only birds with a verified downlink
are listed here; anything not in this table is dropped from the window.

Format: norad: (downlink_MHz, mode, tone, APRS_MHz, SSTV_mode)

For the ISS the FREQ entry is the **cross-band repeater** (437.800 down /
145.990 up, 67 Hz). The station's other channels live in `transponders`
(loaded from 数据/sat_freq.json, the single source of truth) and in the SSTV /
VOICE dicts below:

    FM 中继   437.800 down / 145.990 up, 67 Hz   (2026-10 未按计划开启，状态不稳)
    SSTV      437.550 down, Robot 36, +-10 kHz   (2026-04-10 起；145.800 已不用于 SSTV)
    语音直通   145.800 down / 144.490 up          (欧洲 145.200)
    APRS      437.825 both ways                  (到 2026 年底；145.825 是旧值)

`transponders` is what the UI lists and what the table writer uses.  Entry 0 is
always identical to the legacy fields (down/mode/tone/up) so every old caller
keeps working unchanged.
"""

import io
import json
import os

FREQ = {
    # --- the ones people actually work ---------------------------------
    # ISS：默认是 FM 中继；SSTV 437.550 / APRS 437.825 见 transponders
    25544: (437.800,  "FM 中继",   "67.0 Hz", 437.825, "Robot 36"),
    7530:  (145.975,  "SSB/CW",    "",        0.0,     ""),        # AO-7 (mode B)
    43017: (145.960,  "FM",        "67.0 Hz", 0.0,     ""),        # AO-91 / FOX-1B
    27607: (436.795,  "FM",        "67.0 Hz", 0.0,     ""),        # SO-50
    24278: (435.800,  "SSB/CW",    "",        0.0,     ""),        # FO-29
    44909: (435.610,  "SSB/CW",    "",        0.0,     ""),        # RS-44
    39444: (145.950,  "CW/SSB",    "",        0.0,     ""),        # AO-73 / FUNcube-1
    40967: (145.980,  "FM",        "",        0.0,     ""),        # AO-85 / FOX-1A
    43770: (145.920,  "FM",        "",        0.0,     ""),        # AO-95 / FOX-1CLIFF
    53109: (435.310,  "SSB/CW",    "",        0.0,     ""),        # IO-117 Greencube
    50466: (435.725,  "FM/CW",     "",        0.0,     ""),        # XW-3 / CAS-9
    43678: (145.900,  "FM",        "",        0.0,     ""),        # DIWATA-2B / PO-101
    43803: (145.855,  "FM",        "",        0.0,     ""),        # JO-97
    # --- still alive, lower traffic ------------------------------------
    14129: (145.825,  "SSB/CW",    "",        0.0,     ""),        # AO-10
    22825: (436.795,  "FM",        "",        0.0,     ""),        # AO-27
    25397: (435.225,  "FM",        "",        0.0,     ""),        # GO-32
    26931: (145.825,  "APRS",      "",        145.825, ""),        # NO-44 PCSAT
    20442: (437.125,  "CW/SSB",    "",        0.0,     ""),        # LO-19
    27844: (437.470,  "CW",        "",        0.0,     ""),        # CO-55
    27848: (437.470,  "CW",        "",        0.0,     ""),        # CO-57
    28895: (437.345,  "CW",        "",        0.0,     ""),        # XI-V
    14781: (145.825, "遥测", "", 0.0, ""),  # UO-11
    27939: (435.790, "SSB/CW", "", 0.0, ""),  # HO-68
    32785: (437.275, "CW", "", 0.0, ""),  # CO-65
    32791: (437.485, "语音", "", 0.0, ""),  # CO-66
    32953: (435.215, "遥测", "", 0.0, ""),  # RS-30
    37841: (437.425, "CW", "", 0.0, ""),  # SRMSAT
    39417: (437.356, "遥测", "", 0.0, ""),  # ZACUBE-1
    39430: (437.250, "遥测", "", 0.0, ""),  # GOMX-1
    39440: (437.445, "遥测", "", 0.0, ""),  # LO-74
    40908: (437.200, "CW", "", 0.0, ""),  # LILACSAT-2
    41603: (437.325, "遥测", "", 0.0, ""),  # Lapan A3
    41847: (435.710, "CW", "", 0.0, ""),  # CAS-2T
    42829: (435.950, "CW", "", 0.0, ""),  # TechnoSat
    43186: (435.950, "遥测", "", 0.0, ""),  # S-Net D
    43187: (435.950, "遥测", "", 0.0, ""),  # S-Net B
    43188: (435.950, "遥测", "", 0.0, ""),  # S-Net A
    43189: (435.950, "遥测", "", 0.0, ""),  # S-Net C
    43677: (437.390, "数据", "", 0.0, ""),  # Ten-Koh
    43758: (437.250, "遥测", "", 0.0, ""),  # MinXSS-2
    43782: (437.625, "遥测", "", 0.0, ""),  # SNUSat 2
    43784: (437.275, "遥测", "", 0.0, ""),  # SnugLite
    43786: (145.860, "遥测", "", 0.0, ""),  # ITASat 1
    43790: (435.200, "遥测", "", 0.0, ""),  # EAGLET 1
    43792: (145.895, "语音", "", 0.0, ""),  # ESEO
    43804: (437.775, "遥测", "", 0.0, ""),  # Suomi 100
    43880: (435.600, "遥测", "", 0.0, ""),  # UWE-4
    44530: (436.760, "FM", "67.0 Hz", 0.0, ""),  # Taurus 1
    44881: (145.915, "SSB/CW", "", 0.0, ""),  # TO-108
    46495: (435.950, "遥测", "", 0.0, ""),  # SALSAT
    48274: (145.985, "FM", "", 0.0, ""),  # CSS (TIANHE-1)
    49396: (437.450, "CW", "", 0.0, ""),  # TeikyoSat 4
    49399: (145.875, "遥测", "", 0.0, ""),  # Z-Sat
    49402: (435.525, "遥测", "", 0.0, ""),  # KOSEN 1
    52899: (436.490, "遥测", "", 0.0, ""),  # SNUGLITE 2
    57172: (437.625, "遥测", "", 0.0, ""),  # UmKA 1 (RS40S)
    57175: (436.990, "遥测", "", 0.0, ""),  # CubeBel-2 (EU11S)
    57189: (437.825, "遥测", "", 0.0, ""),  # RS-38S
    59112: (145.825, "CW", "", 0.0, ""),  # SONATE-2
    60237: (436.785, "遥测", "", 0.0, ""),  # GRBBeta
    60242: (436.240, "遥测", "", 0.0, ""),  # Curium One
    60243: (436.750, "遥测", "", 0.0, ""),  # ROBUSTA-3A
    60246: (437.185, "遥测", "", 0.0, ""),  # CatSat
    61781: (435.400, "FM", "", 0.0, ""),  # AO-123
    62391: (436.925, "遥测", "", 0.0, ""),  # LASARsat
    62394: (436.775, "遥测", "", 0.0, ""),  # CroCube
    63210: (435.185, "遥测", "", 0.0, ""),  # JINJUSat 1B
    63211: (436.200, "遥测", "", 0.0, ""),  # NUSHSAT 1
    63213: (436.400, "FM", "", 0.0, ""),  # TEVEL2-4
    63214: (436.400, "FM", "", 0.0, ""),  # TEVEL2-5
    63215: (436.400, "FM", "", 0.0, ""),  # TEVEL2-6
    63217: (436.400, "FM", "", 0.0, ""),  # TEVEL2-1
    63218: (436.400, "FM", "", 0.0, ""),  # TEVEL2-3
    63219: (436.400, "FM", "", 0.0, ""),  # TEVEL2-2
    63237: (436.400, "FM", "", 0.0, ""),  # TEVEL2-9
    63238: (436.400, "FM", "", 0.0, ""),  # TEVEL2-7
    63239: (436.400, "FM", "", 0.0, ""),  # TEVEL2-8
    66657: (436.650, "遥测", "", 0.0, ""),  # SPIRONE
    66660: (437.180, "遥测", "", 0.0, ""),  # K-HERO
    66661: (436.788, "遥测", "", 0.0, ""),  # SNUGLITE-III DURI
    67290: (437.350, "遥测", "", 0.0, ""),  # SAKHACUBE-CHOLBON
    67291: (436.950, "FM", "67.0 Hz", 0.0, ""),  # RS95S (QMR-KWT-2)
    68261: (435.875, "CW", "", 0.0, ""),  # Ten-Koh2
    68417: (436.300, "遥测", "", 0.0, ""),  # JACK-002
    68431: (437.075, "遥测", "", 0.0, ""),  # DISCO 2
    68446: (436.875, "数据", "", 0.0, ""),  # HADES-SA (SpinnyONE)
    68456: (437.195, "遥测", "", 0.0, ""),  # PARUS-6U1
    68458: (436.965, "遥测", "", 0.0, ""),  # SAL-E
    68792: (437.175, "遥测", "", 0.0, ""),  # FSI-SAT 2
    68795: (437.505, "遥测", "", 0.0, ""),  # OrigamiSat 2
    68796: (436.830, "遥测", "", 0.0, ""),  # ARICA 2
    68797: (437.200, "遥测", "", 0.0, ""),  # WASEDA-SAT-ZERO-II
    68798: (436.325, "遥测", "", 0.0, ""),  # MANGARO-II-Piscus
    69912: (145.875, "CW", "", 0.0, ""),  # CO-128
    69920: (436.680, "CW", "", 0.0, ""),  # MARINA
}


CNNAME = {
    25544: "ISS-国际空间站",
    7530:  "AO-7",
    43017: "AO-91",
    27607: "SO-50",
    24278: "FO-29",
    44909: "RS-44",
    39444: "AO-73",
    40967: "AO-85",
    43770: "AO-95",
    53109: "IO-117",
    50466: "XW-3 希望三号",
    43678: "PO-101",
    43803: "JO-97",
    14129: "AO-10",
    22825: "AO-27",
    25397: "GO-32",
    26931: "NO-44",
    20442: "LO-19",
    27844: "CO-55",
    27848: "CO-57",
    28895: "XI-V",
    14781: "UO-11",
    27939: "HO-68",
    32785: "CO-65",
    32791: "CO-66",
    32953: "RS-30",
    37841: "SRMSAT",
    39417: "ZACUBE-1",
    39430: "GOMX-1",
    39440: "LO-74",
    40908: "LILACSAT-2",
    41603: "Lapan A3",
    41847: "CAS-2T",
    42829: "TechnoSat",
    43186: "S-Net D",
    43187: "S-Net B",
    43188: "S-Net A",
    43189: "S-Net C",
    43677: "Ten-Koh",
    43758: "MinXSS-2",
    43782: "SNUSat 2",
    43784: "SnugLite",
    43786: "ITASat 1",
    43790: "EAGLET 1",
    43792: "ESEO",
    43804: "Suomi 100",
    43880: "UWE-4",
    44530: "Taurus 1",
    44881: "TO-108",
    46495: "SALSAT",
    48274: "CSS (TIANHE-1)",
    49396: "TeikyoSat 4",
    49399: "Z-Sat",
    49402: "KOSEN 1",
    52899: "SNUGLITE 2",
    57172: "UmKA 1 (RS40S)",
    57175: "CubeBel-2 (EU11S)",
    57189: "RS-38S",
    59112: "SONATE-2",
    60237: "GRBBeta",
    60242: "Curium One",
    60243: "ROBUSTA-3A",
    60246: "CatSat",
    61781: "AO-123",
    62391: "LASARsat",
    62394: "CroCube",
    63210: "JINJUSat 1B",
    63211: "NUSHSAT 1",
    63213: "TEVEL2-4",
    63214: "TEVEL2-5",
    63215: "TEVEL2-6",
    63217: "TEVEL2-1",
    63218: "TEVEL2-3",
    63219: "TEVEL2-2",
    63237: "TEVEL2-9",
    63238: "TEVEL2-7",
    63239: "TEVEL2-8",
    66657: "SPIRONE",
    66660: "K-HERO",
    66661: "SNUGLITE-III DURI",
    67290: "SAKHACUBE-CHOLBON",
    67291: "RS95S (QMR-KWT-2)",
    68261: "Ten-Koh2",
    68417: "JACK-002",
    68431: "DISCO 2",
    68446: "HADES-SA (SpinnyONE)",
    68456: "PARUS-6U1",
    68458: "SAL-E",
    68792: "FSI-SAT 2",
    68795: "OrigamiSat 2",
    68796: "ARICA 2",
    68797: "WASEDA-SAT-ZERO-II",
    68798: "MANGARO-II-Piscus",
    69912: "CO-128",
    69920: "MARINA",
}


BLURB = {
    25544: "国际空间站。中继 收 437.800 / 发 145.990（67 Hz，2026-10 未按计划开启，状态不稳）；SSTV 收 437.550（Robot 36，±10 kHz，2026-04-10 起）；语音直通 收 145.800 / 发 144.490（欧洲 145.200）；APRS 437.825（到 2026 年底）。145.800 已不用于 SSTV，145.825 是旧 APRS 值。",
    7530:  "1974 年发射，目前仍在工作的最老业余卫星。模式 B 线性转发器：上行 432.125~432.175 SSB/CW，下行 145.975。只在卫星被太阳照到时工作，阴影期会关机。",
    43017: "Fox-1B，AMSAT 的 FM 跨频卫星：上行 435.250、下行 145.960，哑音 67 Hz。主要覆盖美洲，在亚洲只有低仰角掠过的机会。",
    27607: "沙特 SO-50，FM 跨频：上行 145.850（先发 74.4 Hz 哑音唤醒），下行 436.795（67 Hz 哑音）。唤醒后约 10 分钟内可用。",
    24278: "JAS-2（日本），SSB/CW 线性转发器：上行 145.900~146.000，下行 435.800~435.900。信号强、覆盖面广，是练 SSB 卫星的好目标。",
    44909: "俄罗斯 RS-44，SSB/CW 线性转发器。下行 435.610 附近，转发器带宽较宽，在欧洲和亚洲都能听到。",
    39444: "FUNcube-1（AO-73），SSB 线性转发器：上行 435.130~435.150，下行 145.950。教育卫星，长期工作，信号稳定。",
    40967: "Fox-1A（AO-85），FM 跨频：下行 145.980，哑音 67 Hz。",
    43770: "Fox-1Cliff（AO-95），FM 跨频，下行 145.920。",
    53109: "Greencube（IO-117），以数字 digipeater 出名，也可用 SSB 线性转发器，下行 435.310。",
    50466: "XW-3（CAS-9），中国业余卫星，线性转发器 + FM，下行 435.725。",
    43678: "DIWATA-2（PO-101），菲律宾微卫星，FM 下行 145.900，哑音 141.3 Hz。",
    43803: "JO-97，日本 FM 卫星，下行 145.855。",
    14129: "AO-10，1983 年发射的老星，SSB/CW 转发器，轨道很高，通联窗口长但需要较好天线。",
    22825: "AO-27，FM 跨频，下行 436.795。",
    25397: "GO-32，FM 下行 435.225。",
    26931: "PCSAT（NO-44），APRS 数字中继，145.825。",
    20442: "LO-19（Lusat），CW/SSB 信标卫星，下行 437.125。",
    27844: "CO-55，CW 信标，437.470。",
    27848: "CO-57，CW 信标，437.470。",
    28895: "XI-V，CW 信标，437.345。",
    14781: "英国 UoSAT-2，145.825 发 1200bd 遥测信标，已失效。",
    27939: "希望一号（XW-1），线性转发器：上行 145.825、下行 435.675~435.790。",
    32785: "日本 CUTE 星，437.475 发 CW/AFSK 遥测，已退役。",
    32791: "东工大 CUTE-1.7，437.485 发 AFSK 遥测，已失效。",
    32953: "俄罗斯 RS-30，435.315 发 DOKA 格式遥测，仅信标无转发器。",
    37841: "印度 SRM 大学卫星，437.425 发 1200bd 遥测，已失效。",
    39417: "南非 TshepisoSAT，437.356 发 9k6 遥测信标，现役。",
    39430: "丹麦 GOMX-1，437.250 发 9k6 遥测，已失效。",
    39440: "阿根廷 CubeBug-2，437.445 发 1k2/9k6 遥测，无转发器。",
    40908: "哈工大 CAS-3H，437.200 发 CW/BPSK 遥测，转发器停用。",
    41603: "印尼 LAPAN-A3，437.425 发 FFSK 遥测，非业余载荷。",
    41847: "中国 CAS-2T，现仅 435.710 发 CW 信标，FM 转发器停用。",
    42829: "柏林工大 TechnoSat，435.950 发 GMSK 遥测，已失效。",
    43186: "柏林工大 S-Net 四星之一，435.950 发 FFSK 遥测，无转发器。",
    43187: "柏林工大 S-Net 四星之一，435.950 发 FFSK 遥测，无转发器。",
    43188: "柏林工大 S-Net 四星之一，435.950 发 FFSK 遥测，无转发器。",
    43189: "柏林工大 S-Net 四星之一，435.950 发 FFSK 遥测，无转发器。",
    43677: "日本九州工大 Ten-Koh，437.390 发 1k2/9k6 遥测，已失效。",
    43758: "美国 MinXSS-2 科学星，437.250 发 9k6 遥测，已失效。",
    43782: "首尔大学 SNUSat-2，437.625 发 9k6 BPSK 遥测，已失效。",
    43784: "首尔大学 SnugLite，437.275 发 9k6 GMSK 遥测，已失效。",
    43786: "巴西 ITA 卫星，145.860 发 1200bd BPSK 遥测，已失效。",
    43790: "意大利 EAGLET-1，435.200 发遥测，已失效。",
    43792: "欧空局 ESEO，437.000 发 GMSK 遥测，转发器从未启用。",
    43804: "芬兰 Suomi 100 纪念星，437.775 发 9k6 遥测，已失效。",
    43880: "维尔茨堡大学 UWE-4，435.600 发遥测，已失效。",
    44530: "立陶宛 Taurus-1，436.760 V/U FM 转发器，67Hz 亚音。",
    44881: "CAS-6，145.925 线性转发器下行，上行 435.280。",
    46495: "柏林工大 SALSAT，435.950 发 1k2 AFSK 遥测，已失效。",
    48274: "中国空间站天和舱，436.510 V/U FM 中继，上行 145.875。",
    49396: "帝京大 TeikyoSat-4，437.450 发遥测，已失效。",
    49399: "三菱 Z-Sat，145.875 发 9k6 GMSK 遥测，BBS 中继已停。",
    49402: "高专 KOSEN-1，435.525 发遥测，另做 HF 接收试验。",
    52899: "首尔大学 SNUGLITE-II，436.490 发 9k6 遥测，现役。",
    57172: "俄 RS40S/UmKA-1，437.625 发 GMSK 遥测与 SSTV。",
    57175: "白俄 CubeBel-2，436.990 发 4k8/9k6 GMSK 遥测。",
    57189: "俄 RS-38S(VIZARD)，437.825 发 GMSK 遥测。",
    59112: "维尔茨堡大学 SONATE-2，437.025 发遥测，另有 APRS。",
    60237: "捷克 1.5U，436.785 发 9k6 GMSK/CW 遥测，在役。",
    60242: "436.240 发 50k GMSK 遥测，ESA 教育星。",
    60243: "法国蒙彼利埃 3U，436.750 发 9k6 GMSK 遥测，在役。",
    60246: "437.185 发 2k4 FSK AX.100，复位后曾改用 437.250。",
    61781: "ASRTU-1，FM 转发器 435.400/145.850，9k6 遥测。",
    62391: "436.925 发 9k6 GFSK 与 CW 信标，U/U 数字中继。",
    62394: "克罗地亚 1U，436.775 发 CW 与 9k6 GFSK 遥测。",
    63210: "韩国 1U，435.185 发 9k6 BPSK AX.25 遥测。",
    63211: "新加坡国大附中 1U，436.200 发 1k2/2k4 AX.100 遥测。",
    63213: "以色列学生星，FM 转发器 436.400/145.970，9k6 信标。",
    63214: "以色列学生星，FM 转发器 436.400/145.970，9k6 信标。",
    63215: "以色列学生星，FM 转发器 436.400/145.970，9k6 信标。",
    63217: "以色列学生星，FM 转发器 436.400/145.970，9k6 信标。",
    63218: "以色列学生星，FM 转发器 436.400/145.970，2026 年恢复。",
    63219: "以色列学生星，FM 转发器 436.400/145.970，9k6 信标。",
    63237: "以色列学生星，FM 转发器 436.400/145.970，9k6 信标。",
    63238: "以色列学生星，FM 转发器 436.400/145.970，2026 年恢复。",
    63239: "以色列学生星，FM 转发器 436.400/145.970，9k6 信标。",
    66657: "2025-11 发射的 1U，436.650 发 9k6 GMSK 遥测。",
    66660: "2025-11 发射，437.180 发 4k8 MSK AX.100 遥测。",
    66661: "首尔大学 2U，436.788 发 9k6 GMSK AX.25 遥测。",
    67290: "萨哈 1U，437.350 发 2k4/9k6 GMSK 遥测与 SSTV。",
    67291: "巴林 1U，FM 转发器 436.950/145.920，67 Hz 亚音。",
    68261: "日本 2U，CW 信标 435.875；435.9 线性转发器暂未启用。",
    68417: "436.300 发 9k6 FSK 遥测，引用 ITU 登记。",
    68431: "丹麦 3U，437.075 发 4k8 FSK 遥测，S 波段 2432.5。",
    68446: "西班牙 1U，436.875 发 800bps FSK 与 SSDV。",
    68456: "437.195 发 9k6 GMSK TT&C，另 2402 MHz。",
    68458: "436.965 发 9k6 FSK AX.25 信标。",
    68792: "2026-04 发射，437.175 发 9k6 GMSK 与 CW。",
    68795: "东京工业大学 1U，437.505 发 1k2 AFSK 遥测与 CW。",
    68796: "会津大学 2U，436.830 发 4k8 GMSK 与 CW 信标。",
    68797: "早稻田 1U，437.200 发 1k2 FSK 遥测与 8wpm CW。",
    68798: "2026-04 发射，436.325 发 1k2/9k6 数字遥测。",
    69912: "MARMOTSat，145.875 发 VHF CW 信标。",
    69920: "2026-07 发射，436.680 发 CW 信标。",
}



# Uplink (MHz) and CTCSS (Hz) for the birds that actually carry a repeater.
# Anything missing here is receive-only in our tool: the firmware then leaves
# the transmit frequency alone instead of guessing.
UP = {
    25544: (145.990, 67.0),   # ISS FM cross-band repeater
    43017: (435.250, 67.0),   # AO-91
    27607: (145.850, 74.4),   # SO-50: 74.4 Hz 亚音唤醒（67 Hz 打不开）
    24278: (145.900, 0.0),    # FO-29 线性转发器，没有亚音
    44909: (145.965, 0.0),    # RS-44 线性转发器，没有亚音
    43770: (435.300, 67.0),   # AO-95：上行 435.300，不是自己的下行
    40967: (435.170, 67.0),   # AO-85：上行 435.170
    7530:  (432.150, 0.0),    # AO-7 mode B, no tone
    39444: (435.150, 0.0),    # AO-73
    43803: (435.100, 67.0),   # JO-97：上行 435.100
    50466: (145.850, 0.0),    # XW-3 线性转发器
    43678: (437.500, 67.0),   # PO-101：上行 437.500
    22825: (145.850, 0.0),
    25397: (145.850, 0.0),
    27939: (145.825, 0.0),
    43677: (435.280, 0.0),
    44530: (145.820, 67.0),
    44881: (435.270, 0.0),
    48274: (145.850, 0.0),
    61781: (145.850, 0.0),
    63213: (145.970, 0.0),
    63214: (145.970, 0.0),
    63215: (145.970, 0.0),
    63217: (145.970, 0.0),
    63218: (145.970, 0.0),
    63219: (145.970, 0.0),
    63237: (145.970, 0.0),
    63238: (145.970, 0.0),
    63239: (145.970, 0.0),
    67291: (145.920, 67.0),
    68446: (145.875, 0.0),
}

# SSTV downlink, kept apart from FREQ on purpose: FREQ holds the voice /
# repeater downlink (what the table and the live push tune by default), while
# SSTV pictures arrive on a different frequency entirely. 437.550 for the ISS
# (Robot 36, since 2026-04-10); 145.800 is no longer used for SSTV.
SSTV = {
    25544: 437.550,
}

# Plain voice (no repeater, no offset): the station transmits on 145.800 and
# listens on 144.490 in ITU regions 2 and 3 -- which includes China -- and on
# 145.200 in region 1 (Europe/Africa).  Voice only: SSTV moved to 437.550.
VOICE = {
    25544: (145.800, 144.490),
}


# ---------------------------------------------------------------- transponders
class Transponder:
    """One selectable channel of a satellite.  MHz in, 10 Hz units out."""

    def __init__(self, name, down_mhz, up_mhz=0.0, tone_hz=0.0, note=""):
        self.name = str(name or "下行")
        self.down_mhz = float(down_mhz or 0.0)
        self.up_mhz = float(up_mhz or 0.0)
        self.tone_hz = float(tone_hz or 0.0)
        self.note = str(note or "")

    @property
    def label(self):
        return f"{self.down_mhz:.4f} MHz · {self.name}"

    @property
    def freq10(self):
        return int(round(self.down_mhz * 1e6 / 10))

    @property
    def up10(self):
        return int(round(self.up_mhz * 1e6 / 10))

    @property
    def ctcss10(self):
        return int(round(self.tone_hz * 10))

    def __repr__(self):
        return f"<Transponder {self.label}>"


# 界面下拉里"自定义标称频率"那一项的文案。它只是个显示项，不在
# sat.transponders 里 —— 索引等于 len(transponders) 时就是它。
CUSTOM_TRANS_LABEL = "自定义…"

# 量程按"这张表能表达什么"定，不是按手台的调谐范围定：上行/下行都按
# u32 的 10 Hz 单位落表，13000 MHz（13 GHz）盖得住 QO-100 的 10.489 GHz
# 下行；0.1 MHz 以下一律当输入错误（多半是少打了小数点）。
CUSTOM_MHZ_MIN = 0.1
CUSTOM_MHZ_MAX = 13000.0


def make_custom_transponder(down_text, up_text, base_tone_hz):
    """把界面上的两个框变成一条 Transponder（标称 = AOS 时刻的频率）。

    返回 None 表示这两个框现在给不出可用频率：调用方必须保留上一条有效值。
    绝不能在这里退化成 0 —— up10=0 在写表契约里是"仅接收"，会把发射频点
    悄悄写没；下行 0 更会让整趟多普勒按 0 Hz 算。

    上行留空是合法的"仅接收"（up_mhz=0）；写了但看不懂同样返回 None，
    不静默当 0。亚音不在这里猜，由调用方把该星主转发器的 tone 传进来。
    """
    try:
        down = float(str(down_text).strip())
    except (TypeError, ValueError):
        return None
    if not (CUSTOM_MHZ_MIN <= down <= CUSTOM_MHZ_MAX):
        return None
    up_s = "" if up_text is None else str(up_text).strip()
    if not up_s:
        up = 0.0
    else:
        try:
            up = float(up_s)
        except (TypeError, ValueError):
            return None
        if not (CUSTOM_MHZ_MIN <= up <= CUSTOM_MHZ_MAX):
            return None
    return Transponder("自定义", down, up, base_tone_hz, "标称")


# --- EMBEDDED_TRANS begin（gen: PC工具/多普勒工具/embed_transponders.py，别手改）---
EMBEDDED_TRANS = {
    7530: [("SSB/CW", 145.975, 432.15, 0.0)],
    14129: [("SSB/CW", 145.825, 0.0, 0.0)],
    14781: [("遥测", 145.825, 0.0, 0.0)],
    20442: [("CW", 437.125, 0.0, 0.0)],
    22825: [("FM", 436.795, 145.85, 0.0)],
    24278: [("SSB/CW", 435.8, 145.9, 0.0)],
    25397: [("FM", 435.225, 145.85, 0.0)],
    25544: [("FM 中继", 437.8, 145.99, 67.0), ("SSTV", 437.55, 0.0, 0.0), ("语音直通", 145.8, 144.49, 0.0), ("APRS", 437.825, 437.825, 0.0)],
    26931: [("APRS", 145.825, 0.0, 0.0)],
    27607: [("FM", 436.795, 145.85, 74.4)],
    27844: [("CW", 437.47, 0.0, 0.0)],
    27848: [("CW", 437.47, 0.0, 0.0)],
    27939: [("SSB/CW", 435.79, 145.825, 0.0)],
    28895: [("CW", 437.345, 0.0, 0.0)],
    32785: [("CW", 437.275, 0.0, 0.0)],
    32791: [("语音", 437.485, 0.0, 0.0)],
    32953: [("遥测", 435.215, 0.0, 0.0)],
    37841: [("CW", 437.425, 0.0, 0.0)],
    39417: [("遥测", 437.356, 0.0, 0.0)],
    39430: [("遥测", 437.25, 0.0, 0.0)],
    39440: [("遥测", 437.445, 0.0, 0.0)],
    39444: [("SSB/CW", 145.95, 435.15, 0.0)],
    40908: [("CW", 437.2, 0.0, 0.0)],
    40967: [("FM", 145.98, 435.18, 67.0)],
    41603: [("遥测", 437.325, 0.0, 0.0)],
    41847: [("CW", 435.71, 0.0, 0.0)],
    42829: [("CW", 435.95, 0.0, 0.0)],
    43017: [("FM", 145.96, 435.25, 67.0)],
    43186: [("遥测", 435.95, 0.0, 0.0)],
    43187: [("遥测", 435.95, 0.0, 0.0)],
    43188: [("遥测", 435.95, 0.0, 0.0)],
    43189: [("遥测", 435.95, 0.0, 0.0)],
    43677: [("数据", 437.39, 435.28, 0.0)],
    43678: [("FM", 145.9, 437.5, 141.3)],
    43758: [("遥测", 437.25, 0.0, 0.0)],
    43770: [("FM", 145.92, 435.3, 67.0)],
    43782: [("遥测", 437.625, 0.0, 0.0)],
    43784: [("遥测", 437.275, 0.0, 0.0)],
    43786: [("遥测", 145.86, 0.0, 0.0)],
    43790: [("遥测", 435.2, 0.0, 0.0)],
    43792: [("语音", 145.895, 0.0, 0.0)],
    43803: [("FM", 145.855, 435.1, 67.0)],
    43804: [("遥测", 437.775, 0.0, 0.0)],
    43880: [("遥测", 435.6, 0.0, 0.0)],
    44530: [("FM", 436.76, 145.82, 67.0)],
    44881: [("SSB/CW", 145.915, 435.27, 0.0)],
    44909: [("SSB/CW", 435.61, 145.965, 0.0)],
    46495: [("遥测", 435.95, 0.0, 0.0)],
    48274: [("FM", 145.985, 145.85, 0.0)],
    49396: [("CW", 437.45, 0.0, 0.0)],
    49399: [("遥测", 145.875, 0.0, 0.0)],
    49402: [("遥测", 435.525, 0.0, 0.0)],
    50466: [("SSB/CW", 435.725, 145.85, 0.0)],
    52899: [("遥测", 436.49, 0.0, 0.0)],
    53109: [("SSB/CW", 435.31, 0.0, 0.0)],
    57172: [("遥测", 437.625, 0.0, 0.0)],
    57175: [("遥测", 436.99, 0.0, 0.0)],
    57189: [("遥测", 437.825, 0.0, 0.0)],
    59112: [("CW", 145.825, 0.0, 0.0)],
    60237: [("遥测", 436.785, 0.0, 0.0)],
    60242: [("遥测", 436.24, 0.0, 0.0)],
    60243: [("遥测", 436.75, 0.0, 0.0)],
    60246: [("遥测", 437.185, 0.0, 0.0)],
    61781: [("FM", 435.4, 145.85, 0.0)],
    62391: [("遥测", 436.925, 0.0, 0.0)],
    62394: [("遥测", 436.775, 0.0, 0.0)],
    63210: [("遥测", 435.185, 0.0, 0.0)],
    63211: [("遥测", 436.2, 0.0, 0.0)],
    63213: [("FM", 436.4, 145.97, 0.0)],
    63214: [("FM", 436.4, 145.97, 0.0)],
    63215: [("FM", 436.4, 145.97, 0.0)],
    63217: [("FM", 436.4, 145.97, 0.0)],
    63218: [("FM", 436.4, 145.97, 0.0)],
    63219: [("FM", 436.4, 145.97, 0.0)],
    63237: [("FM", 436.4, 145.97, 0.0)],
    63238: [("FM", 436.4, 145.97, 0.0)],
    63239: [("FM", 436.4, 145.97, 0.0)],
    66657: [("遥测", 436.65, 0.0, 0.0)],
    66660: [("遥测", 437.18, 0.0, 0.0)],
    66661: [("遥测", 436.788, 0.0, 0.0)],
    67290: [("遥测", 437.35, 0.0, 0.0)],
    67291: [("FM", 436.95, 145.92, 67.0)],
    68261: [("CW", 435.875, 0.0, 0.0)],
    68417: [("遥测", 436.3, 0.0, 0.0)],
    68431: [("遥测", 437.075, 0.0, 0.0)],
    68446: [("数据", 436.875, 145.875, 0.0)],
    68456: [("遥测", 437.195, 0.0, 0.0)],
    68458: [("遥测", 436.965, 0.0, 0.0)],
    68792: [("遥测", 437.175, 0.0, 0.0)],
    68795: [("遥测", 437.505, 0.0, 0.0)],
    68796: [("遥测", 436.83, 0.0, 0.0)],
    68797: [("遥测", 437.2, 0.0, 0.0)],
    68798: [("遥测", 436.325, 0.0, 0.0)],
    69912: [("CW", 145.875, 0.0, 0.0)],
    69920: [("CW", 436.68, 0.0, 0.0)],
}
# --- EMBEDDED_TRANS end ---


_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "..", "数据", "sat_freq.json")


def _load_transponders():
    """数据/sat_freq.json 是频点真源；读不到就返回空表，fill() 用老字段兜底。"""
    try:
        with io.open(_JSON_PATH, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return _embedded_trans()
    out = {}
    for key, entry in (doc.get("sats") or {}).items():
        try:
            norad = int(key)
        except (TypeError, ValueError):
            continue
        rows = []
        for t in entry.get("transponders") or []:
            try:
                down = float(t.get("down") or 0.0)
            except (TypeError, ValueError):
                continue
            if down <= 0:
                continue
            rows.append(Transponder(t.get("name") or entry.get("mode"),
                                    down, t.get("up"), t.get("tone"), t.get("note")))
        if rows:
            out[norad] = rows
    return out or _embedded_trans()


def _embedded_trans():
    """exe 里读不到 数据/sat_freq.json（PyInstaller 单文件），用打包进来的副本。"""
    return {n: [Transponder(nm, d, u, t) for (nm, d, u, t) in rows]
            for n, rows in EMBEDDED_TRANS.items()}


TRANS = _load_transponders()


def fill(sat):
    """Attach frequency info to a Sat built from a bare TLE.

    没有收录的星必须留 known=False：否则 145.800 这个默认下行会被当成真实
    频率显示出来，还会被 write_radio() 写进电台。
    """
    f = FREQ.get(sat.norad)
    if not f:
        sat.known = False
        return sat
    sat.freq_mhz, sat.mode, sat.tone, sat.aprs, sat.sstv = f
    sat.downlink = f"{sat.freq_mhz:.3f} MHz"
    sat.blurb = BLURB.get(sat.norad, "")
    sat.known = True
    sat.sstv_mhz = SSTV.get(sat.norad, 0.0)
    sat.voice_rx_mhz, sat.voice_tx_mhz = VOICE.get(sat.norad, (0.0, 0.0))

    u = UP.get(sat.norad)
    if u:
        sat.up_mhz, sat.up_tone = u
    else:
        sat.up_mhz, sat.up_tone = 0.0, 0.0

    if not sat.up_tone and sat.tone:
        try:
            sat.up_tone = float(str(sat.tone).replace("Hz", "").strip())
        except ValueError:
            sat.up_tone = 0.0

    sat.transponders = list(TRANS.get(sat.norad) or [])
    if sat.transponders:
        # 第 0 条就是默认条目，必须与老字段一致；数据写歪了按老字段纠正，
        # 免得界面上看着是 437.800、写进表里的却是别的数。
        t0 = sat.transponders[0]
        t0.down_mhz = sat.freq_mhz
        t0.up_mhz = sat.up_mhz
        t0.tone_hz = sat.up_tone
    else:
        sat.transponders = [Transponder(sat.mode, sat.freq_mhz,
                                        sat.up_mhz, sat.up_tone)]

    return sat


# Order the window list: how often the bird is actually workable.
POPULAR = [
    25544,   # ISS - every day, easiest target there is
    7530,    # AO-7 - the famous old one
    43017,   # AO-91
    27607,   # SO-50
    24278,   # FO-29
    44909,   # RS-44
    39444,   # AO-73
    40967,   # AO-85
    43770,   # AO-95
    53109,   # IO-117
    50466,   # XW-3
    43678,   # DIWATA-2B
    43803,   # JO-97
    14129,   # AO-10
    22825,   # AO-27
    25397,   # GO-32
    26931,   # NO-44
    20442,   # LO-19
    27844,   # CO-55
    27848,   # CO-57
    28895,   # XI-V
]


def rank(norad):
    try:
        return POPULAR.index(norad)
    except ValueError:
        return 999


# Satellites the user asked for that are not in celestrak's "amateur" group.
# NORAD -> Chinese label shown in the list.
EXTRA = {
    67290: "乔尔邦 RS18S",
    67291: "科威特之月2号 RS95S",
    67279: "GALAPAGOS-UTE",
    67284: "Lobachevsky RS83S",
    57182: "Monitor-4 RS57S",
    57189: "VIZARD-METEO RS38S",
    57172: "UMKA-1 RS40S",
    57203: "UTMN 2 RS27S",
    54216: "中国空间站·梦天",
    100470: "静安梦想星",
    64560: "山雀 PARUS-T2",
    68446: "HADES-SA",
    68261: "Ten-Koh2",
    40931: "LAPAN-A2 IO-86",
    33591: "NOAA 19",
    25338: "NOAA 15",
    61781: "ASRTU-1 AO-123",
    43700: "Es'hail-2 QO-100",
}

# Downlinks we are sure about (MHz). Everything else stays "unknown".
EXTRA_FREQ = {
    40931: (145.825, "FM", ""),
    33591: (137.100, "APT", ""),
    25338: (137.620, "APT", ""),
}


def fill_extra(sat):
    """Fill a satellite that came from EXTRA rather than the amateur group."""
    got = EXTRA_FREQ.get(sat.norad)
    if got:
        sat.freq_mhz, sat.mode, tone = got
        sat.known = True
        sat.tone = tone
        sat.downlink = f"{sat.freq_mhz:.3f} MHz"
    else:
        sat.known = False
        sat.mode = sat.mode or "未知"
        sat.downlink = "—"
    sat.sstv_mhz = SSTV.get(sat.norad, 0.0)
    sat.voice_rx_mhz, sat.voice_tx_mhz = VOICE.get(sat.norad, (0.0, 0.0))
    sat.blurb = BLURB.get(sat.norad, "业余/科学卫星，本地表里没有下行频率。")
    sat.transponders = list(TRANS.get(sat.norad) or [])
    if not sat.transponders and sat.known:
        up = UP.get(sat.norad, (0.0, 0.0))
        sat.transponders = [Transponder(sat.mode, sat.freq_mhz, up[0], up[1])]
    return sat
