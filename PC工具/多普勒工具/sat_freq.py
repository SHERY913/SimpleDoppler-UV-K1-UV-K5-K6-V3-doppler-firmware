"""Known downlink frequencies for the amateur birds that are actually workable.

celestrak gives orbits, not frequencies - and half of the "amateur" group is
dead or telemetry-only, so guessing 145.800 for everything just produces a
list of satellites that cannot be tuned.  Only birds with a verified downlink
are listed here; anything not in this table is dropped from the window.

Format: norad: (downlink_MHz, mode, tone, APRS_MHz, SSTV_mode)

For the ISS the entry above is the **cross-band repeater** (437.800 down /
145.990 up, 67 Hz) -- that is what the live push and the table tune. The same
station also runs plain voice and SSTV on a completely different band, so those
live in their own dicts below: VOICE (145.800 down / 144.490 up in ITU region 2
and 3, 145.200 in region 1) and SSTV (145.800, receive only). 437.550 is the
station's UHF packet channel, NOT a voice downlink -- it was mislabelled as the
ISS downlink for a long time and that is why 437.550 used to show up here.
All norad ids below were checked against celestrak's amateur group.
"""

FREQ = {
    # --- the ones people actually work ---------------------------------
    25544: (437.800,  "FM 中继",   "67.0 Hz", 145.825, "PD120"),   # ISS cross-band repeater
    7530:  (145.975,  "SSB/CW",    "",        0.0,     ""),        # AO-7 (mode B)
    43017: (145.960,  "FM",        "67.0 Hz", 0.0,     ""),        # AO-91 / FOX-1B
    27607: (436.795,  "FM",        "67.0 Hz", 0.0,     ""),        # SO-50
    24278: (435.800,  "SSB/CW",    "",        0.0,     ""),        # FO-29
    44909: (435.610,  "SSB/CW",    "",        0.0,     ""),        # RS-44
    39444: (145.935,  "CW/SSB",    "",        0.0,     ""),        # AO-73 / FUNcube-1
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
}


BLURB = {
    25544: "国际空间站上的业余电台。三种玩法：中继（上行 145.990 / 哑音 67 Hz，下行 437.800）、语音直通（下行 145.800，上行 144.490，欧洲用 145.200）、SSTV（145.800 只收，PD120）。437.550 是它的 UHF 分组频道，不是语音。2025 年 11 月服务舱电台故障，SSTV 停了很久，2026 年 10 月才恢复。",
    7530:  "1974 年发射，目前仍在工作的最老业余卫星。模式 B 线性转发器：上行 432.125~432.175 SSB/CW，下行 145.975。只在卫星被太阳照到时工作，阴影期会关机。",
    43017: "Fox-1B，AMSAT 的 FM 跨频卫星：上行 435.250、下行 145.960，哑音 67 Hz。主要覆盖美洲，在亚洲只有低仰角掠过的机会。",
    27607: "沙特 SO-50，FM 跨频：上行 145.850（先发 74.4 Hz 哑音唤醒），下行 436.795（67 Hz 哑音）。唤醒后约 10 分钟内可用。",
    24278: "JAS-2（日本），SSB/CW 线性转发器：上行 145.900~146.000，下行 435.800~435.900。信号强、覆盖面广，是练 SSB 卫星的好目标。",
    44909: "俄罗斯 RS-44，SSB/CW 线性转发器。下行 435.610 附近，转发器带宽较宽，在欧洲和亚洲都能听到。",
    39444: "FUNcube-1（AO-73），SSB 线性转发器：上行 435.130~435.150，下行 145.935。教育卫星，长期工作，信号稳定。",
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
}

# SSTV downlink, kept apart from FREQ on purpose: FREQ holds the voice /
# repeater downlink (what the table and the live push both tune), while SSTV
# pictures arrive on a different frequency entirely. 145.800 for the ISS.
SSTV = {
    25544: 145.800,
}

# Plain voice (no repeater, no offset): the station transmits on 145.800 and
# listens on 144.490 in ITU regions 2 and 3 -- which includes China -- and on
# 145.200 in region 1 (Europe/Africa).  Same 145.800 carrier as SSTV, which is
# why one radio channel covers both.
VOICE = {
    25544: (145.800, 144.490),
}


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
    return sat
