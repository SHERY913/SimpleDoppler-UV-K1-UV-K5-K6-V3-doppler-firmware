/**
 * 颜色输入：token 芯片 / #RRGGBB / @NN 透明度 / 原生取色器，四者写同一个字符串。
 * 存的永远是作者能读的原文（`acc@30` 不会被改成 rgba）。
 */
import { useState } from 'react'
import { TOKENS, cssColor, parseColor } from '../core/colors'

export interface ColorInputProps {
  value: string
  onChange: (v: string) => void
}

export function ColorInput({ value, onChange }: ColorInputProps): JSX.Element {
  const [open, setOpen] = useState(false)
  const hasAlpha = value.includes('@')
  const base = hasAlpha ? value.slice(0, value.lastIndexOf('@')) : value
  const pct = hasAlpha ? Number(value.slice(value.lastIndexOf('@') + 1)) || 0 : 100
  const c = parseColor(value)

  const setBase = (b: string): void => {
    onChange(hasAlpha ? `${b}@${pct}` : b)
  }
  const setPct = (p: number): void => {
    const clamped = Math.max(0, Math.min(100, Math.round(p)))
    if (clamped === 100) onChange(base)
    else onChange(`${base}@${clamped}`)
  }

  return (
    <div className="color-input">
      <div className="color-row">
        <button className="swatch" style={{ background: cssColor(value) }} onClick={() => setOpen((v) => !v)} title="选 token" />
        <input
          className="expr-input mono"
          value={value}
          placeholder="token / #RRGGBB / @NN"
          onChange={(e) => onChange(e.target.value)}
        />
        <input
          type="color"
          className="native-color"
          value={c ? toHex6(c.r, c.g, c.b) : '#000000'}
          onChange={(e) => setBase(e.target.value)}
        />
      </div>
      {hasAlpha || pct !== 100 ? (
        <div className="alpha-row">
          <span>不透明</span>
          <input type="range" min={0} max={100} value={pct} onChange={(e) => setPct(Number(e.target.value))} />
          <span className="mono">{pct}%</span>
        </div>
      ) : null}
      {open ? (
        <div className="token-pop">
          {TOKENS.map((t) => (
            <button key={t.name} className="token" onClick={() => { setBase(t.name); setOpen(false) }}>
              <span className="swatch sm" style={{ background: t.hex }} />
              <span className="mono">{t.name}</span>
              <span className="note">{t.note}</span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}

function toHex6(r: number, g: number, b: number): string {
  const h = (x: number): string => Math.round(x).toString(16).padStart(2, '0')
  return '#' + h(r) + h(g) + h(b)
}
