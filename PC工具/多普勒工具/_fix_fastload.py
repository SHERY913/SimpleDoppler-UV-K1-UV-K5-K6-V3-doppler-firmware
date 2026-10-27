"""Make the satellite list appear instantly when the cache already has it.

`load_group()` used to hit the network first, and with celestrak unreachable
that is an 8 s timeout before the bulk fallback even starts - which reads as
"the list never loads". A cache holding the whole group is enough to build the
list, so use it and let the "更新 TLE" button do the refresh on demand.
"""

import io

p = 'C:/Users/13766/Documents/deepseek-harness/default-workspace/多普勒工具/doppler_tool.py'
s = io.open(p, encoding='utf-8').read()

old = '''        def work():
            try:
                got = core.fetch_group("amateur")'''

new = '''        def work():
            try:
                # A cache that already holds the whole group is enough to build
                # the list, and it costs milliseconds. Going online first means
                # waiting out the celestrak timeout whenever that site is down,
                # which is what made this look like it had hung.
                cache = self._tle_cache()

                if len(cache) >= 50:
                    sats = []

                    for norad_s, v in cache.items():
                        try:
                            norad = int(norad_s)
                            name, l1, l2 = v[0], v[1], v[2]
                            nm = sat_freq.EXTRA.get(norad) or name
                            st = core.Sat(norad, nm, 145.800, "未知", "")
                            st.tle = (name, l1, l2)
                            if norad in sat_freq.EXTRA:
                                sat_freq.fill_extra(st)
                            else:
                                sat_freq.fill(st)
                            sats.append(st)
                        except Exception:
                            continue

                    if sats:
                        self.tle_at = cache[next(iter(cache))][3]
                        self._loaded = (sats, None)
                        return

                got = core.fetch_group("amateur")'''

assert old in s
s = s.replace(old, new, 1)
io.open(p, 'w', encoding='utf-8', newline='\n').write(s)
print('cache-first load installed')
