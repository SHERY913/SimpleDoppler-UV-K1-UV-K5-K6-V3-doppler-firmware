/**
 * 带 `{}` 按钮的文本/表达式输入。插入永远走"拼接字符串"，不 eval。
 */
import { useRef, useState } from 'react'
import { VarPicker } from './VarPicker'

export interface ExprInputProps {
  value: string
  onChange: (v: string) => void
  mode: 'template' | 'expr' | 'plain'
  placeholder?: string
  multiline?: boolean
  mono?: boolean
}

export function ExprInput({ value, onChange, mode, placeholder, multiline, mono = true }: ExprInputProps): JSX.Element {
  const ref = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null)
  const [open, setOpen] = useState(false)

  const insert = (text: string): void => {
    const el = ref.current
    if (!el) {
      onChange(value + text)
      setOpen(false)
      return
    }
    const s = el.selectionStart ?? value.length
    const e = el.selectionEnd ?? value.length
    const next = value.slice(0, s) + text + value.slice(e)
    onChange(next)
    setOpen(false)
    requestAnimationFrame(() => {
      el.focus()
      const pos = s + text.length
      el.setSelectionRange(pos, pos)
    })
  }

  const cls = 'expr-input' + (mono ? ' mono' : '')

  return (
    <div className="expr-wrap">
      <div className="expr-row">
        {multiline ? (
          <textarea
            ref={(el) => { ref.current = el }}
            className={cls}
            rows={3}
            value={value}
            placeholder={placeholder}
            onChange={(e) => onChange(e.target.value)}
          />
        ) : (
          <input
            ref={(el) => { ref.current = el }}
            className={cls}
            value={value}
            placeholder={placeholder}
            onChange={(e) => onChange(e.target.value)}
            spellCheck={false}
          />
        )}
        {mode !== 'plain' ? (
          <button
            className={'btn tiny' + (open ? ' on' : '')}
            title="插入变量"
            onClick={() => setOpen((v) => !v)}
          >
            {'{}'}
          </button>
        ) : null}
      </div>
      {open ? <VarPicker mode={mode === 'expr' ? 'expr' : 'template'} onPick={insert} onClose={() => setOpen(false)} /> : null}
    </div>
  )
}
