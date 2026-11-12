#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出 doppler_tool.py 里所有"给用户看的字面量"清单 → _ui_strings.txt。

只算**会出现在界面上/日志里**的字符串参数：
  - tk/ttk 控件的 text=、title()、lbl_status.config(text=...)
  - messagebox.* 的标题与正文
  - log_line(tag, text)
不算：docstring、hexdump、协议字节、异常类名、内部字典键。

用法：python dump_ui_strings.py [--out _ui_strings.txt]
"""
import ast
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "doppler_tool.py")
CJK = re.compile(r"[\u4e00-\u9fff]")

WIDGETS = {"Label", "Button", "Checkbutton", "Radiobutton", "Entry", "Spinbox",
           "LabelFrame", "Frame", "Toplevel", "Menu", "Canvas", "Text"}


def call_name(node):
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def render(node):
    """把 text= 的值还原成源码形式（f-string 保留 {…} 占位）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    try:
        return ast.unparse(node)
    except Exception:                                   # pragma: no cover
        return "<expr>"


def collect():
    src = io.open(SRC, encoding="utf-8").read()
    tree = ast.parse(src)
    rows = []          # (line, where, text)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = call_name(node)
        # 1) 控件 text=
        if name in WIDGETS:
            for kw in node.keywords:
                if kw.arg == "text":
                    rows.append((node.lineno, name + ".text", render(kw.value)))
            # Entry.insert(0, str)
        elif name == "insert" and len(node.args) >= 2:
            rows.append((node.lineno, "insert", render(node.args[1])))
        elif name == "title" and node.args:
            rows.append((node.lineno, "title", render(node.args[0])))
        # 2) messagebox
        elif name in ("showerror", "showwarning", "showinfo", "askyesno", "askokcancel"):
            parts = [render(a) for a in node.args]
            rows.append((node.lineno, "messagebox." + name, " | ".join(parts)))
        # 3) log_line(tag, text)
        elif name == "log_line" and len(node.args) >= 2:
            rows.append((node.lineno, "log_line", render(node.args[1])))
        # 4) xxx.config(text=...)
        elif name == "config":
            for kw in node.keywords:
                if kw.arg == "text":
                    rows.append((node.lineno, "config.text", render(kw.value)))

    # 控件文案只留含中文的（纯 ASCII 的控件名/协议常量不是文案）；
    # log_line 例外：写表日志里 TABLE/HELLO 这类行全 ASCII，但人要读它。
    rows = [r for r in rows if CJK.search(r[2]) or r[1] == "log_line"]
    rows.sort(key=lambda r: (r[0], r[1]))
    # 去重同一行同一处
    seen, out = set(), []
    for r in rows:
        k = (r[0], r[1], r[2])
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def main():
    ap_out = os.path.join(HERE, "_ui_strings.txt")
    if "--out" in sys.argv:
        ap_out = sys.argv[sys.argv.index("--out") + 1]
    rows = collect()
    lines = ["# doppler_tool.py 用户可见文案清单（dump_ui_strings.py 生成）",
             "# 共 %d 条；格式：行号 | 位置 | 原文" % len(rows), ""]
    for ln, where, text in rows:
        lines.append("%d | %s | %s" % (ln, where, text.replace("\n", "\\n")))
    io.open(ap_out, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
    print("%d 条 → %s" % (len(rows), ap_out))


if __name__ == "__main__":
    main()
