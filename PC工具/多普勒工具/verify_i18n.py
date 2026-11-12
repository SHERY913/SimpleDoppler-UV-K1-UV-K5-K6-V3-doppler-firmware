# -*- coding: utf-8 -*-
"""i18n 回归守门（verify_i18n.py）。

三条判据，红一条就退出码 1：
  1. doppler_tool.py / doppler_core.py 里所有**非 docstring** 的中文字面量，
     必须直接作为 tr(...) 的第一参数出现 —— 中文裸奔进界面 = 红。
  2. 每个 tr("字面量") 的键必须存在于 lang_en.json，且英文值不得再含中文。
  3. 中英文模板的 {占位符} 集合（名字/类型/格式）必须完全一致，
     漏一个 .format 参数当场 KeyError，切英文的现场比这里红难看得多。
"""
import ast
import io
import os
import re
import sys

if hasattr(sys.stdout, "write"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES = ["doppler_tool.py", "doppler_core.py"]
CATALOG = os.path.join(HERE, "lang_en.json")
CJK = re.compile(r"[\u4e00-\u9fff]")
FIELDS = re.compile(r"\{[^{}]*\}")

# 语言切换按钮显示"当前语言"：中文界面写"中"、英文界面写"EN"，
# 这个"中"本来就该是汉字，不进译文表。
EXEMPT = {"中"}

fails = []


def docstring_ids(tree):
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                ids.add(id(body[0].value))
    return ids


def main():
    import json
    with open(CATALOG, encoding="utf-8") as f:
        cat = json.load(f)

    for fname in SOURCES:
        path = os.path.join(HERE, fname)
        src = open(path, encoding="utf-8").read()
        tree = ast.parse(src, filename=fname)
        docs = docstring_ids(tree)
        # 数据表这类"永远中文"的字面量用行尾 `# i18n-skip` 申报豁免
        skip_lines = {i for i, ln in enumerate(src.splitlines(), 1) if "i18n-skip" in ln}
        # import 期 assert 的文案是给开发者看的，改错常量当场炸，不进译文表
        assert_ids = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        assert_ids.add(id(sub))

        # tr("字面量") 第一参数位置的常量 —— 允许
        tr_keys = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name) and node.func.id == "tr"
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                tr_keys.add(id(node.args[0]))

        # 1) 所有中文字面量都得在 tr() 里。
        #    例外：字面量的值本身就是 lang_en.json 的一个键 —— 那是双语键源
        #    （控件创建时的 text="卫星" 之类，语言钩子会重画），放行。
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docs and CJK.search(node.value)
                    and id(node) not in tr_keys and id(node) not in assert_ids
                    and node.lineno not in skip_lines
                    and node.value not in cat
                    and node.value not in EXEMPT):
                fails.append(f"{fname}:{node.lineno} 中文没走 tr(): {node.value[:40]!r}")

        # 2)+3) 每个键有译文、译文无中文、占位符一致
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name) and node.func.id == "tr"
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                key = node.args[0].value
                if not CJK.search(key):
                    continue        # 纯运行期透传（tr(s.blurb) 之类是 Name，不是这分支）
                if key not in cat:
                    fails.append(f"{fname}:{node.lineno} lang_en.json 缺键: {key[:50]!r}")
                    continue
                val = cat[key]
                if CJK.search(val):
                    fails.append(f"{fname}:{node.lineno} 译文仍含中文: {key[:40]!r} -> {val[:40]!r}")
                zh_fields = sorted(FIELDS.findall(key))
                en_fields = sorted(FIELDS.findall(val))
                if zh_fields != en_fields:
                    fails.append(f"{fname}:{node.lineno} 占位符不一致 {key[:36]!r}\n"
                                 f"    zh={zh_fields}\n    en={en_fields}")

    # 表里有源码已经不再引用的死键 —— 只警告不判红（清理用）。
    # "引用"含两种：tr("字面量")，以及双语键源（控件 text="卫星" / heads 字典等，
    # 值本身就是键，运行期经 tr(t) 消费）。
    used = set()
    for fname in SOURCES:
        tree = ast.parse(open(os.path.join(HERE, fname), encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                used.add(node.value)
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name) and node.func.id == "tr"
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                used.add(node.args[0].value)
    dead = [k for k in cat if k not in used]
    for k in dead:
        print(f"  (提示) 死键（lang_en.json 有、源码没引用）: {k[:60]!r}")

    if fails:
        print(f"RED —— {len(fails)} 处不合格：")
        for x in fails:
            print("  " + x)
        return 1
    print(f"GREEN —— {len(cat)} 条译文，两份源码中文字面量全部走 tr()，占位符一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
