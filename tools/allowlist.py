# -*- coding: utf-8 -*-
"""业余卫星白名单（数据/amateur_allow.json）的读取、匹配与剔除记录。

为什么要有这个模块
------------------
名单只有一份（`多普勒/数据/amateur_allow.json`），但读它的地方有三处生成器
（PC `gen_sat_freq.py`、App `doppler-ng/tools/build_catalog.py`、
`embed_transponders.py`）加一个守卫（`verify_amateur.py`）。同一个匹配规则
各写一遍，早晚会漂 —— 这个项目复发最多的 bug 就是"三处各自写一遍同一个约定"。
所以规则只在这里实现一次。

匹配规则（操作员定案）
----------------------
1. 先按 NORAD 命中；
2. 条目没有 NORAD（只有国际编号/名字）时，按 name 精确匹配名单里的名字
   —— 去空白、忽略大小写；
3. 都不中 → 剔除，并在 `tools/_dropped_from_source.txt` 记一行；
   没有 NORAD 的记 `no-norad <name>`，有 NORAD 但不在名单的记 `<norad> <name>`。

名单文件缺失或 keep 为空 → 直接 SystemExit(2)，绝不静默放行全部。
"""
import io
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))          # …/多普勒/tools
ROOT = os.path.dirname(HERE)                               # …/多普勒
DEFAULT_PATH = os.path.join(ROOT, "数据", "amateur_allow.json")
DROPPED_PATH = os.path.join(HERE, "_dropped_from_source.txt")

SECTION_ORDER = ["sat_freq.json", "freqs.json", "transponders.json"]
_WS = re.compile(r"\s+")


def norm(name):
    """名字归一：去掉所有空白、忽略大小写。`OSCAR 7 (AO-7)` == `oscar7(ao-7)`。"""
    return _WS.sub("", str(name or "")).casefold()


class Allow(object):
    def __init__(self, norads, names, unverified, path):
        self.norads = norads            # set[int]
        self.names = names              # set[str]（已 norm）
        self.unverified = unverified    # set[int]，本地值未经 AMSAT 核对的
        self.path = path

    def match(self, norad, name=None):
        """NORAD 优先；NORAD 缺失/不中时按名字精确匹配。"""
        if norad not in (None, ""):
            try:
                if int(norad) in self.norads:
                    return True
            except (TypeError, ValueError):
                pass
        if name:
            return norm(name) in self.names
        return False

    def is_unverified(self, norad):
        try:
            return int(norad) in self.unverified
        except (TypeError, ValueError):
            return False


def load(path=None):
    path = path or DEFAULT_PATH
    if not os.path.exists(path):
        raise SystemExit(
            "缺业余卫星白名单：%s\n"
            "没有它就不知道该留哪些星 —— 拒绝继续（不会静默放行全部）。\n"
            "生成：python 多普勒/tools/build_amateur_allow.py --write" % path)
    with io.open(path, encoding="utf-8") as f:
        doc = json.load(f)
    keep = doc.get("keep") or []
    norads, names = set(), set()
    for e in keep:
        n = e.get("norad")
        if n in (None, ""):
            continue
        try:
            norads.add(int(n))
        except (TypeError, ValueError):
            continue
        if e.get("name"):
            names.add(norm(e["name"]))
    if not norads:
        raise SystemExit(
            "业余卫星白名单的 keep 是空的：%s\n"
            "空名单会把所有星都剔掉，按规则拒绝继续。" % path)
    unv = set()
    for n in doc.get("unverified") or []:
        try:
            unv.add(int(n))
        except (TypeError, ValueError):
            continue
    return Allow(norads, names, unv, path)


# ── 剔除清单：按小节写同一个文件，重复运行不会重复堆 ────────────────────────

def _read_sections(path):
    sections, order, cur = {}, [], None
    if not os.path.exists(path):
        return sections, order
    with io.open(path, encoding="utf-8") as f:
        for ln in f:
            s = ln.rstrip("\n")
            m = re.match(r"^\[(.+)\]$", s)
            if m:
                cur = m.group(1)
                sections[cur] = []
                if cur not in order:
                    order.append(cur)
            elif cur is not None:
                sections[cur].append(s)
    return sections, order


def record_dropped(section, lines):
    """把某处生成器的剔除清单写进 `_dropped_from_source.txt` 的 [section] 小节。

    lines 里的每一行不带前缀，调用方给什么写什么（`no-norad <name>` 或
    `<norad> <name>`）。小节内容整段替换，所以同一份数据跑一百遍也只留一份。
    """
    sections, order = _read_sections(DROPPED_PATH)
    sections[section] = list(lines)
    if section not in order:
        order.append(section)
    head = [
        "# 非业余卫星剔除清单（生成器自动写，别手改）",
        "# 规则：先按 NORAD 匹配 数据/amateur_allow.json；NORAD 缺失按 name",
        "#       （去空格、忽略大小写）；都不中即剔除。",
        "# no-norad <name> = 既没有 NORAD 也没有同名条目；<norad> <name> = 有 NORAD 但不在名单。",
    ]
    known = [s for s in SECTION_ORDER if s in sections]
    rest = [s for s in order if s not in SECTION_ORDER]
    out = list(head)
    for name in known + rest:
        out.append("[%s]" % name)
        body = sections[name]
        out.extend(body if body else ["# （无）"])
    with io.open(DROPPED_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")


def dropped_line(norad, name):
    if norad in (None, ""):
        return "no-norad %s" % (name or "?")
    return "%s %s" % (norad, name or "?")
