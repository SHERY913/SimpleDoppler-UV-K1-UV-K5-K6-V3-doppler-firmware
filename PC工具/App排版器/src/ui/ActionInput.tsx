/**
 * 动作输入：白名单下拉 + 手写兜底。选中时按参数表拼出 `name(arg)` 让作者改。
 */
import { actGroups } from '../data/acts'

export interface ActionInputProps {
  value: string
  onChange: (v: string) => void
}

export function ActionInput({ value, onChange }: ActionInputProps): JSX.Element {
  const groups = actGroups()
  return (
    <div className="action-input">
      <select
        className="sel"
        value=""
        onChange={(e) => {
          const v = e.target.value
          if (!v) return
          const def = v.includes('(') ? v : `${v}()`
          onChange(def)
        }}
      >
        <option value="">— 选动作 —</option>
        {groups.map((g) => (
          <optgroup key={g.group} label={g.group}>
            {g.items.map((a) => (
              <option key={a.name} value={a.args.length ? `${a.name}(${a.args.map(() => '').join(',')})` : a.name}>
                {a.name} · {a.label}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      <input
        className="expr-input mono"
        value={value}
        placeholder="nav.detail(norad)"
        onChange={(e) => onChange(e.target.value)}
        spellCheck={false}
      />
    </div>
  )
}
