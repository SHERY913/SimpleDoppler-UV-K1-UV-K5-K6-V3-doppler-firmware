# -*- coding: utf-8 -*-
"""轻量双语：中文原文当键，JSON 表映射英文。无 gettext、无编译步骤。

    from i18n import tr
    w.config(text=tr("卫星"))                                  # 固定文案
    lbl.config(text=tr("写入 {n} B").format(n=4096))           # 带值的用 {占位}

set_language("en") 后 tr() 查 lang_en.json；"zh" 时原样返回。
查不到的键原样返回中文 —— 漏译不会弄崩界面，会被 verify_i18n.py 抓成红。
"""
from __future__ import annotations

import json
import os
import sys

_MAP = {}
_LANG = "zh"


def _res_dir():
    """源码运行在脚本目录，exe 模式在 PyInstaller 解包目录。"""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def set_language(code: str) -> str:
    """切到 "en"/"zh"，返回真正生效的语言。"""
    global _MAP, _LANG
    _LANG = code if code in ("zh", "en") else "zh"
    if _LANG == "zh":
        _MAP = {}
        return _LANG
    path = os.path.join(_res_dir(), "lang_en.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    # 值里仍有中文 = 这条没译，当缺项走回落，界面上不会中英混排
    _MAP = {k: v for k, v in data.items() if not _cjk(v)}
    return _LANG


def get_language() -> str:
    return _LANG


def tr(s: str) -> str:
    if _LANG == "zh" or not isinstance(s, str):
        return s
    return _MAP.get(s, s)


def _cjk(v) -> bool:
    return isinstance(v, str) and any("\u4e00" <= c <= "\u9fff" for c in v)
