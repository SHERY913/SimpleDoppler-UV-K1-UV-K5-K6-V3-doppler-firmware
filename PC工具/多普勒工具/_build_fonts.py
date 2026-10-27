"""Regenerate fonts_data.js for the layout editor from the firmware source.

The editor draws with the real glyph bitmaps, so it needs the four tables out of
App/font.c. They were hand-exported once and that file got cleaned up with the
other build leftovers, so keep the generator next to the tool.
"""

import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, '..', '_src', 'uvk1-v6.3.0', 'App', 'font.c')

src = io.open(SRC, encoding='utf-8', errors='replace').read()


def body_after(marker):
    """Everything between the first '{' after marker and its matching '};'."""
    i = src.index(marker)
    i = src.index('{', i)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == '{':
            depth += 1
        elif src[j] == '}':
            depth -= 1
            if depth == 0:
                return src[i + 1:j]
    raise ValueError('unbalanced braces for ' + marker)


def numbers(text):
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    text = re.sub(r'//[^\n]*', '', text)
    return [int(t, 16) if t.lower().startswith('0x') else int(t)
            for t in re.findall(r'0x[0-9A-Fa-f]+|\b\d+\b', text)]


def flat(marker):
    return numbers(body_after(marker))


def nested(marker, width):
    vals = numbers(body_after(marker))
    return [vals[i:i + width] for i in range(0, len(vals), width)]


tiny = nested('gFont3x5[][3]', 3)
small = flat('gFontSmallPacked[FONT_SMALL_PACKED_SIZE]')
bigp = flat('gFontBigPacked[94][8]')
dictionary = flat('gFontBigDictionary[2][15]')
literals = flat('gFontBigLiterals[126]')

assert len(tiny) == 96, len(tiny)
assert len(small) == 494, len(small)
assert len(bigp) == 752, len(bigp)
assert len(dictionary) == 30, len(dictionary)
assert len(literals) == 126, len(literals)


def js(name, values):
    if values and isinstance(values[0], list):
        inner = ','.join('[' + ','.join(str(v) for v in row) + ']' for row in values)
        return 'const %s = [%s];\n' % (name, inner)
    return 'const %s = [%s];\n' % (name, ','.join(str(v) for v in values))


out = ''.join([
    '/* generated from App/font.c by _build_fonts.py */\n',
    js('TINY', tiny),
    js('SMALL', small),
    js('BIGP', bigp),
    js('DICT', dictionary),
    js('LIT', literals),
])

path = os.path.join(HERE, 'fonts_data.js')
io.open(path, 'w', encoding='utf-8', newline='\n').write(out)
print('fonts_data.js %d bytes  tiny=%d small=%d bigp=%d' %
      (len(out), len(tiny), len(small), len(bigp)))
