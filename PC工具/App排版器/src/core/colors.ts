/**
 * 颜色 token —— 抄自 UiTheme.kt 的暗色主题。
 * 规则：token 名 / `#RRGGBB` / `#AARRGGBB`（**A 在前**，Compose 的 Color(long) 顺序）/
 * `#RGB` 简写，外加 `@NN` 后缀表示不透明度百分比（`acc@30`）。
 */

export interface TokenDef {
  name: string
  hex: string
  note: string
}

export const TOKENS: TokenDef[] = [
  { name: 'bg', hex: '#080A0E', note: '页面底色' },
  { name: 'card', hex: '#12161D', note: '卡片 / 底栏' },
  { name: 'line', hex: '#1E252F', note: '描边 / 分隔' },
  { name: 'grid', hex: '#232B36', note: '网格 / 极弱的块' },
  { name: 'txt', hex: '#E6EAF0', note: '正文' },
  { name: 'dim', hex: '#8A94A3', note: '次要文字' },
  { name: 'acc', hex: '#35C7E8', note: '强调色' },
  { name: 'ok', hex: '#43D17C', note: '正常 / 有信号' },
  { name: 'warn', hex: '#E8B33C', note: '警告' },
  { name: 'err', hex: '#E8574A', note: '错误' },
  { name: 'sel', hex: '#1B2A36', note: '选中态底' },
  { name: 'faint', hex: '#151B24', note: '禁用 / 极弱的底' },
  { name: 'none', hex: '#00000000', note: '透明' },
]

const TOKEN_MAP = new Map(TOKENS.map((t) => [t.name, t]))

export interface Rgba {
  r: number
  g: number
  b: number
  a: number
}

const TRANSPARENT: Rgba = { r: 0, g: 0, b: 0, a: 0 }

export function parseColor(spec: string | undefined): Rgba | null {
  if (!spec) return null
  const raw = spec.trim()
  if (raw === '') return null
  const at = raw.lastIndexOf('@')
  const base = at >= 0 ? raw.slice(0, at).trim() : raw
  const pct = at >= 0 ? Number(raw.slice(at + 1)) : 100
  const mult = Number.isFinite(pct) ? Math.max(0, Math.min(100, pct)) / 100 : 1

  let c: Rgba | null = null
  if (base.startsWith('#')) {
    const h = base.slice(1)
    if (/^[0-9a-fA-F]{8}$/.test(h)) {
      c = {
        a: parseInt(h.slice(0, 2), 16) / 255,
        r: parseInt(h.slice(2, 4), 16),
        g: parseInt(h.slice(4, 6), 16),
        b: parseInt(h.slice(6, 8), 16),
      }
    } else if (/^[0-9a-fA-F]{6}$/.test(h)) {
      c = { a: 1, r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16), b: parseInt(h.slice(4, 6), 16) }
    } else if (/^[0-9a-fA-F]{3}$/.test(h)) {
      const r = h[0] + h[0]
      const g = h[1] + h[1]
      const b = h[2] + h[2]
      c = { a: 1, r: parseInt(r, 16), g: parseInt(g, 16), b: parseInt(b, 16) }
    }
  } else {
    const t = TOKEN_MAP.get(base)
    if (t) c = parseColor(t.hex)
  }
  if (!c) return null
  return { ...c, a: c.a * mult }
}

export function cssColor(spec: string | undefined): string {
  const c = parseColor(spec)
  if (!c) return 'transparent'
  const r = Math.round(c.r)
  const g = Math.round(c.g)
  const b = Math.round(c.b)
  if (c.a >= 1) return `rgb(${r},${g},${b})`
  return `rgba(${r},${g},${b},${Math.round(c.a * 1000) / 1000})`
}

/** 16 进制回显（给属性面板用），永远输出 #RRGGBB。 */
export function hexOf(spec: string): string {
  const c = parseColor(spec)
  if (!c) return '#000000'
  const h = (n: number) => Math.round(n).toString(16).padStart(2, '0')
  return '#' + h(c.r) + h(c.g) + h(c.b)
}

export function isKnownColorSpec(spec: string): boolean {
  return spec === '' || parseColor(spec) !== null
}

export { TRANSPARENT }
