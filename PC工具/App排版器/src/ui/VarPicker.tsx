/**
 * 变量选择器。只往外抛字符串（变量路径或函数名），排版器不执行任何代码。
 */
import { useMemo, useState } from 'react'
import { VAR_TREE, flatVars } from '../data/scope'
import { FN_NAMES, PIPE_NAMES } from '../core/expr'

export interface VarPickerProps {
  mode: 'template' | 'expr'
  onPick: (text: string) => void
  onClose: () => void
}

export function VarPicker({ mode, onPick, onClose }: VarPickerProps): JSX.Element {
  const [q, setQ] = useState('')
  const [tab, setTab] = useState<'var' | 'fn' | 'pipe'>('var')
  const all = useMemo(() => flatVars(), [])
  const hits = useMemo(() => {
    const s = q.trim().toLowerCase()
    if (!s) return null
    return all.filter((v) => v.path.toLowerCase().includes(s) || v.label.includes(s))
  }, [q, all])

  return (
    <div className="picker" onMouseDown={(e) => e.stopPropagation()}>
      <div className="picker-head">
        <div className="tabs">
          <button className={tab === 'var' ? 'on' : ''} onClick={() => setTab('var')}>变量</button>
          <button className={tab === 'fn' ? 'on' : ''} onClick={() => setTab('fn')}>函数</button>
          <button className={tab === 'pipe' ? 'on' : ''} onClick={() => setTab('pipe')}>管道</button>
        </div>
        <button className="x" onClick={onClose}>✕</button>
      </div>
      {tab === 'var' ? (
        <>
          <input
            className="picker-search"
            autoFocus
            placeholder="搜变量：name / el / countdown…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <div className="picker-body">
            {hits
              ? hits.map((v) => (
                <button key={v.path + v.group} className="vrow" onClick={() => onPick(mode === 'template' ? `{${v.path}}` : v.path)}>
                  <span className="vp">{v.path}</span>
                  <span className="vl">{v.label}</span>
                </button>
              ))
              : VAR_TREE.map((g) => (
                <div key={g.id} className="vgroup">
                  <div className="vgroup-t">{g.label}{g.note ? <em> · {g.note}</em> : null}</div>
                  {g.leaves.map((v) => (
                    <button key={v.path} className="vrow" onClick={() => onPick(mode === 'template' ? `{${v.path}}` : v.path)}>
                      <span className="vp">{v.path}</span>
                      <span className="vl">{v.label}</span>
                    </button>
                  ))}
                  {(g.children ?? []).map((c) => (
                    <div key={c.id} className="vsub">
                      <div className="vgroup-t">{c.label}</div>
                      {c.leaves.map((v) => (
                        <button key={v.path} className="vrow" onClick={() => onPick(mode === 'template' ? `{${v.path}}` : v.path)}>
                          <span className="vp">{v.path}</span>
                          <span className="vl">{v.label}</span>
                        </button>
                      ))}
                    </div>
                  ))}
                </div>
              ))}
          </div>
        </>
      ) : (
        <div className="picker-body">
          {(tab === 'fn' ? FN_NAMES : PIPE_NAMES).map((f) => (
            <button
              key={f}
              className="vrow"
              onClick={() => onPick(tab === 'fn' ? `${f}(` : `|${f}`)}
            >
              <span className="vp">{tab === 'fn' ? `${f}(…)` : `|${f}`}</span>
            </button>
          ))}
        </div>
      )}
      <div className="picker-foot">
        {mode === 'template' ? '点变量插入 {路径}，外层再拼中文' : '表达式里直接写路径，不加花括号'}
      </div>
    </div>
  )
}
