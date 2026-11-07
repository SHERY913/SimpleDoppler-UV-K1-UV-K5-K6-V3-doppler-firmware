/**
 * 属性面板：按类型出字段 + 几何 + 锚点 + 未列出的原始字段。
 * 所有输入都直接写 store（每次修改 = 一步撤销）。
 */
import { useMemo, useState } from 'react'
import { store, useEditor } from '../state/store'
import { fieldsOf, TYPE_LABEL, type FieldDef } from '../data/propschema'
import { ICON_NAMES, glyphOf } from '../data/icons'
import { ExprInput } from './ExprInput'
import { ColorInput } from './ColorInput'
import { ActionInput } from './ActionInput'
import { previewScope } from '../data/fixtures'
import { asBool, asNum, asStr, evalExpr, template } from '../core/expr'
import { anchorFlags } from '../core/model'
import type { JsonValue } from '../core/jsonc'

const PATH_SUGGEST = ['passes', 'cart.items', 'list.rows', 'sat.tx', 'tl.rows', 'settings.rows', 'settings.aboutRows']

export function Inspector(): JSX.Element {
  const st = useEditor()
  const uid = st.selection[0] ?? null
  const node = uid ? store.node(uid) : null
  const scope = useMemo(() => previewScope(), [])
  const [newKey, setNewKey] = useState('')

  if (!node) {
    return (
      <div className="inspector">
        <div className="empty-hint">
          <p>没选中节点。</p>
          <p className="dim">点画布或图层树选一个；Shift 加选；拖空白处框选。</p>
        </div>
      </div>
    )
  }

  const fields = fieldsOf(node.type)
  const known = new Set(fields.map((f) => f.key))
  const extras = Object.keys(node.props).filter((k) => !known.has(k))
  const a = anchorFlags(node.anchor)

  const setAnchor = (key: 'l' | 'r' | 't' | 'b'): void => {
    const next = { ...a, [key]: !a[key] }
    const s = (next.l ? 'l' : '') + (next.r ? 'r' : '') + (next.t ? 't' : '') + (next.b ? 'b' : '')
    store.updateNode(node.uid, { anchor: s === '' ? 'lt' : s })
  }

  const num = (v: string): number | null => {
    if (v.trim() === '') return null
    const n = Number(v)
    return Number.isFinite(n) ? n : null
  }

  const renderField = (f: FieldDef): JSX.Element => {
    const raw = node.props[f.key]
    const val = raw === undefined ? undefined : raw
    const onChange = (v: JsonValue | undefined): void => store.updateProp(node.uid, f.key, v)

    let control: JSX.Element
    switch (f.kind) {
      case 'num': {
        const cur = typeof val === 'number' ? val : (f.def as number | undefined) ?? 0
        control = (
          <input
            type="number"
            className="num-input mono"
            value={val === undefined ? '' : cur}
            placeholder={f.def === undefined ? '' : String(f.def)}
            min={f.min}
            max={f.max}
            step={f.step ?? 1}
            onChange={(e) => {
              const n = num(e.target.value)
              onChange(n === null ? undefined : n)
            }}
          />
        )
        break
      }
      case 'color':
        control = <ColorInput value={typeof val === 'string' ? val : ''} onChange={(v) => onChange(v)} />
        break
      case 'enum':
        control = (
          <select
            className="sel"
            value={typeof val === 'string' ? val : String(f.def ?? '')}
            onChange={(e) => onChange(e.target.value)}
          >
            {val === undefined ? <option value="">（默认）</option> : null}
            {(f.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        )
        break
      case 'bool':
        control = (
          <label className="check">
            <input
              type="checkbox"
              checked={typeof val === 'boolean' ? val : (f.def as boolean | undefined) ?? false}
              onChange={(e) => onChange(e.target.checked)}
            />
            <span>{typeof val === 'boolean' ? String(val) : '默认 ' + String(f.def ?? false)}</span>
          </label>
        )
        break
      case 'icon':
        control = (
          <div className="icon-input">
            <select className="sel" value={typeof val === 'string' ? val : ''} onChange={(e) => onChange(e.target.value)}>
              <option value="">（无）</option>
              {ICON_NAMES.map((n) => <option key={n} value={n}>{glyphOf(n)} {n}</option>)}
            </select>
          </div>
        )
        break
      case 'action':
        control = <ActionInput value={typeof val === 'string' ? val : ''} onChange={(v) => onChange(v)} />
        break
      case 'path':
        control = (
          <div className="expr-wrap">
            <input
              className="expr-input mono"
              list="path-suggest"
              value={typeof val === 'string' ? val : String(f.def ?? '')}
              placeholder={String(f.def ?? '')}
              onChange={(e) => onChange(e.target.value)}
            />
            <datalist id="path-suggest">
              {PATH_SUGGEST.map((p) => <option key={p} value={p} />)}
            </datalist>
          </div>
        )
        break
      case 'options':
        control = (
          <input
            className="expr-input mono"
            value={typeof val === 'string' ? val : ''}
            placeholder="A|B|C"
            onChange={(e) => onChange(e.target.value)}
          />
        )
        break
      case 'template':
      case 'text':
      case 'multiline':
      case 'expr': {
        const isTpl = f.kind === 'template' || f.kind === 'text' || f.kind === 'multiline'
        const v = typeof val === 'string' ? val : ''
        let preview = ''
        if (v !== '') {
          try {
            preview = isTpl ? template(v, scope) : asStr(evalExpr(v, scope))
          } catch {
            preview = '（表达式出错）'
          }
        }
        control = (
          <div className="expr-block">
            <ExprInput
              value={v}
              onChange={(x) => onChange(x)}
              mode={isTpl ? 'template' : 'expr'}
              multiline={f.kind === 'multiline'}
              placeholder={typeof f.def === 'string' ? f.def : ''}
            />
            {v !== '' ? <div className="expr-preview" title="用预览假数据算出来的结果">→ {preview}</div> : null}
          </div>
        )
        break
      }
      default:
        control = <span className="dim">?</span>
    }

    return (
      <div className={'field' + (f.kind === 'multiline' || f.kind === 'text' || f.kind === 'template' ? ' wide' : '')} key={f.key}>
        <label title={f.help ?? ''}>
          {f.label}
          {val === undefined ? <em className="unset">默认</em> : null}
        </label>
        <div className="field-ctl">{control}</div>
        {f.help ? <div className="field-help">{f.help}</div> : null}
      </div>
    )
  }

  const rotate = typeof node.props['rotate'] === 'number' ? node.props['rotate'] : 0

  return (
    <div className="inspector">
      <div className="insp-head">
        <span className="badge">{TYPE_LABEL[node.type] ?? node.type}</span>
        <input
          className="name-input"
          value={node.name ?? ''}
          placeholder={node.type}
          onChange={(e) => store.updateNode(node.uid, { name: e.target.value })}
        />
        <label className="check" title="锁定后画布上不能拖动/缩放">
          <input type="checkbox" checked={node.locked} onChange={(e) => store.updateNode(node.uid, { locked: e.target.checked })} />
          <span>锁</span>
        </label>
      </div>

      <section>
        <h4>几何</h4>
        <div className="geo-grid">
          {(['x', 'y', 'w', 'h'] as const).map((k) => (
            <label key={k} className="geo-cell">
              <span>{k.toUpperCase()}</span>
              <input
                type="number"
                className="num-input mono"
                value={Math.round(node[k] * 100) / 100}
                step={1}
                onChange={(e) => {
                  const n = Number(e.target.value)
                  if (Number.isFinite(n)) store.updateNode(node.uid, { [k]: n })
                }}
              />
            </label>
          ))}
        </div>
        <div className="anchor-row">
          <span className="lbl">锚点</span>
          <button className={a.l ? 'on' : ''} onClick={() => setAnchor('l')} title="左">L</button>
          <button className={a.r ? 'on' : ''} onClick={() => setAnchor('r')} title="右">R</button>
          <button className={a.t ? 'on' : ''} onClick={() => setAnchor('t')} title="上">T</button>
          <button className={a.b ? 'on' : ''} onClick={() => setAnchor('b')} title="下">B</button>
          <span className="mono dim">{node.anchor || 'lt'}</span>
        </div>
        <div className="field">
          <label>旋转</label>
          <div className="field-ctl">
            <input
              type="number"
              className="num-input mono"
              step={15}
              value={rotate}
              onChange={(e) => {
                const n = Number(e.target.value)
                store.updateProp(node.uid, 'rotate', Number.isFinite(n) ? n : undefined)
              }}
            />
          </div>
          <div className="field-help">只存 props.rotate，渲染器目前不读它；画布上也不转。</div>
        </div>
        <div className="field">
          <label>可见 visible</label>
          <div className="field-ctl">
            <ExprInput
              value={node.visible ?? ''}
              onChange={(v) => store.updateNode(node.uid, { visible: v === '' ? undefined : v })}
              mode="expr"
              placeholder="留空 = 一直可见"
            />
          </div>
          <div className="field-help">裸表达式，不带花括号。写 {'`'}false{'`'} 就整块不渲染。</div>
        </div>
        <div className="field">
          <label>可用 enabled</label>
          <div className="field-ctl">
            <ExprInput
              value={node.enabled ?? ''}
              onChange={(v) => store.updateNode(node.uid, { enabled: v === '' ? undefined : v })}
              mode="expr"
              placeholder="留空 = 一直可用"
            />
          </div>
        </div>
        <div className="field">
          <label>ID</label>
          <div className="field-ctl">
            <input
              className="expr-input mono"
              value={node.id ?? ''}
              onChange={(e) => store.updateNode(node.uid, { id: e.target.value === '' ? undefined : e.target.value })}
            />
          </div>
        </div>
      </section>

      <section>
        <h4>{TYPE_LABEL[node.type] ?? node.type} 字段<em className="dim"> · Render.kt</em></h4>
        {fields.length === 0 ? <div className="dim pad">这个类型没有可渲染的 props</div> : fields.map(renderField)}
      </section>

      {extras.length > 0 ? (
        <section>
          <h4>其它字段<em className="dim"> · 原样保留</em></h4>
          {extras.map((k) => (
            <div className="extra-row" key={k}>
              <span className="mono k">{k}</span>
              <input
                className="expr-input mono"
                value={jsonText(node.props[k])}
                onChange={(e) => store.updateProp(node.uid, k, parseLoose(e.target.value))}
              />
              <button className="btn tiny danger" title="删除该字段" onClick={() => store.updateProp(node.uid, k, undefined)}>✕</button>
            </div>
          ))}
        </section>
      ) : null}

      <section>
        <h4>加字段</h4>
        <div className="extra-row">
          <input
            className="expr-input mono"
            placeholder="key"
            value={newKey}
            onChange={(e) => setNewKey(e.target.value)}
          />
          <button
            className="btn tiny"
            onClick={() => {
              const k = newKey.trim()
              if (!k) return
              store.updateProp(node.uid, k, '')
              setNewKey('')
            }}
          >
            加
          </button>
        </div>
      </section>
    </div>
  )
}

function jsonText(v: JsonValue | undefined): string {
  if (v === undefined) return ''
  if (typeof v === 'string') return v
  return JSON.stringify(v)
}

function parseLoose(s: string): JsonValue {
  const t = s.trim()
  if (t === '') return ''
  if (t === 'true') return true
  if (t === 'false') return false
  if (t === 'null') return null
  if (/^-?\d+(\.\d+)?$/.test(t)) return Number(t)
  if ((t.startsWith('{') && t.endsWith('}')) || (t.startsWith('[') && t.endsWith(']'))) {
    try {
      return JSON.parse(t) as JsonValue
    } catch {
      return s
    }
  }
  return s
}

/** 供别处用的取值助手，避免各组件各写一份。 */
export function propStr(node: { props: Record<string, JsonValue> }, key: string, def = ''): string {
  const v = node.props[key]
  return typeof v === 'string' ? v : def
}

export function propNum(node: { props: Record<string, JsonValue> }, key: string, def: number): number {
  const v = node.props[key]
  return typeof v === 'number' && Number.isFinite(v) ? v : def
}

export function propBool(node: { props: Record<string, JsonValue> }, key: string, def: boolean): boolean {
  const v = node.props[key]
  if (typeof v === 'boolean') return v
  if (typeof v === 'string') return asBool(evalExpr(v, {}))
  return def
}

export { asNum }
