/**
 * 一个节点的画面。编辑画布和预览共用同一份渲染 —— 预览里看到的就是这些规则，
 * 规则全部照 Render.kt：绝对定位 + 锚点（设计尺寸下恒等）+ 自动缩排 + 颜色 token。
 */
import type { CSSProperties, PointerEvent as ReactPointerEvent, ReactNode } from 'react'
import type { LNode } from '../core/model'
import { asBool, asNum, evalExpr, template, type Scope } from '../core/expr'
import { cssColor } from '../core/colors'
import { FONTS, WEIGHTS, shrinkFontSize } from '../core/text'
import { glyphOf } from '../data/icons'
import { rowsAt } from '../data/fixtures'
import type { JsonValue } from '../core/jsonc'

export interface NodeViewProps {
  node: LNode
  scope: Scope
  editing: boolean
  selected?: boolean
  hideLocked?: boolean
  elRef?: (uid: string, el: HTMLDivElement | null) => void
  onPointerDown?: (e: ReactPointerEvent<HTMLDivElement>, node: LNode) => void
  onClickNode?: (node: LNode) => void
}

function strProp(n: LNode, key: string, def = ''): string {
  const v = n.props[key]
  return typeof v === 'string' ? v : def
}

/** Render.kt 的 `.dyn(scope)`：字符串里有 `{}` 就按模板求值，否则原样返回。 */
function dyn(n: LNode, key: string, scope: Scope, def = ''): string {
  const raw = strProp(n, key, def)
  return raw.includes('{') ? template(raw, scope) : raw
}

function numProp(n: LNode, key: string, def: number): number {
  const v = n.props[key]
  return typeof v === 'number' && Number.isFinite(v) ? v : def
}

function boolProp(n: LNode, key: string, def: boolean): boolean {
  const v = n.props[key]
  if (typeof v === 'boolean') return v
  if (typeof v === 'string') return v === 'true'
  return def
}

export function NodeView(props: NodeViewProps): JSX.Element | null {
  const { node: n, scope, editing, selected, elRef, onPointerDown, onClickNode } = props

  const visible = n.visible ? asBool(evalExpr(n.visible, scope)) : true
  const enabled = n.enabled ? asBool(evalExpr(n.enabled, scope)) : true
  if (!visible && !editing) return null

  const w = Math.max(0, n.w)
  const h = Math.max(0, n.h)
  const base: CSSProperties = {
    position: 'absolute',
    left: n.x,
    top: n.y,
    width: w,
    height: h,
    boxSizing: 'border-box',
  }
  if (!visible) base.opacity = 0.28
  // 旋转：与 Render.kt 的 graphicsLayer { rotationZ } 同义 —— 只转画，
  // 命中区仍是未旋转的矩形（编辑时选框也跟着转，所见即所得）。
  const rot = numProp(n, 'rotate', 0)
  if (rot !== 0) {
    base.transform = `rotate(${rot}deg)`
    base.transformOrigin = 'center center'
  }

  const common: CSSProperties = {
    ...base,
    outline: editing ? (selected ? '1px solid #35C7E8' : '1px dashed rgba(53,199,232,0.18)') : 'none',
    outlineOffset: editing ? 0 : undefined,
    cursor: editing ? (n.locked ? 'not-allowed' : 'move') : 'default',
    overflow: 'visible',
  }

  const handlePointer = (e: ReactPointerEvent<HTMLDivElement>): void => {
    if (editing && onPointerDown) onPointerDown(e, n)
  }
  const handleClick = (): void => {
    if (onClickNode) onClickNode(n)
  }

  const inner = (style: CSSProperties, children?: ReactNode): JSX.Element => (
    <div
      ref={(el) => elRef?.(n.uid, el)}
      style={{ ...common, ...style }}
      data-uid={n.uid}
      data-type={n.type}
      onPointerDown={handlePointer}
      onClick={handleClick}
      title={editing ? `${n.type} ${n.id ?? ''}` : undefined}
    >
      {children}
    </div>
  )

  const kids = n.children.map((c) => (
    <NodeView
      key={c.uid}
      node={c}
      scope={scope}
      editing={editing}
      elRef={elRef}
      onPointerDown={onPointerDown}
      onClickNode={onClickNode}
    />
  ))

  switch (n.type) {
    case 'group':
      return inner({ background: 'transparent' }, kids)

    case 'box': {
      const fill = dyn(n, 'fill', scope)
      const fill2 = dyn(n, 'fill2', scope)
      const radius = numProp(n, 'radius', 0)
      const bw = numProp(n, 'borderW', 1)
      const border = dyn(n, 'border', scope)
      const alpha = numProp(n, 'alpha', 1)
      return inner({
        background: fill2
          ? `linear-gradient(180deg, ${cssColor(fill)}, ${cssColor(fill2)})`
          : cssColor(fill),
        borderRadius: radius,
        border: border ? `${bw}px solid ${cssColor(border)}` : undefined,
        opacity: (visible ? 1 : 0.28) * alpha,
      }, kids)
    }

    case 'text': {
      const raw = strProp(n, 'text', '')
      const txt = template(raw, scope)
      const size0 = numProp(n, 'size', 14)
      const maxLines = Math.max(1, Math.round(numProp(n, 'maxLines', 3)))
      const shrink = boolProp(n, 'shrink', true)
      const sp = shrink ? shrinkFontSize(size0, txt, w, maxLines) : size0
      const lh = numProp(n, 'lineHeight', 0)
      const align = dyn(n, 'align', scope, 'left')
      const weight = dyn(n, 'weight', scope, 'normal')
      const font = dyn(n, 'font', scope, 'sans')
      const color = dyn(n, 'color', scope, 'txt')
      return inner({
        fontFamily: FONTS[font] ?? FONTS.sans,
        fontSize: sp,
        lineHeight: lh > 0 ? `${lh}px` : `${sp * 1.25}px`,
        fontWeight: WEIGHTS[weight] ?? 400,
        color: cssColor(color),
        textAlign: align === 'center' ? 'center' : align === 'right' ? 'right' : 'left',
        letterSpacing: numProp(n, 'letterSpacing', 0),
        overflow: 'hidden',
        display: '-webkit-box',
        WebkitLineClamp: maxLines,
        WebkitBoxOrient: 'vertical',
        whiteSpace: 'normal',
        wordBreak: 'break-word',
      } as CSSProperties, txt)
    }

    case 'button': {
      const raw = strProp(n, 'text', '')
      const txt = template(raw, scope)
      const size = numProp(n, 'size', 13)
      const align = dyn(n, 'align', scope, 'center')
      const icon = dyn(n, 'icon', scope)
      const color = dyn(n, 'color', scope, enabled ? 'txt' : 'faint')
      const fill = dyn(n, 'fill', scope, enabled ? 'acc@14' : 'card')
      const fillDis = dyn(n, 'fillDisabled', scope)
      const radius = numProp(n, 'radius', 8)
      const bw = numProp(n, 'borderW', 1)
      const border = dyn(n, 'border', scope)
      return inner({
        background: cssColor(enabled ? fill : fillDis || 'card'),
        borderRadius: radius,
        border: border ? `${bw}px solid ${cssColor(border)}` : undefined,
        color: cssColor(color),
        display: 'flex',
        alignItems: 'center',
        justifyContent: align === 'left' ? 'flex-start' : align === 'right' ? 'flex-end' : 'center',
        gap: 6,
        padding: 0,
        fontFamily: FONTS.sans,
        fontSize: size,
        fontWeight: 500,
        opacity: enabled ? 1 : 0.6,
        userSelect: 'none',
      }, <>
        {icon ? <span style={{ fontSize: size + 4, lineHeight: 1 }}>{glyphOf(icon)}</span> : null}
        <span style={{ overflow: 'hidden', whiteSpace: 'nowrap' }}>{txt}</span>
        {kids.length ? kids : null}
      </>)
    }

    case 'icon': {
      const size = numProp(n, 'size', 18)
      return inner({
        color: cssColor(dyn(n, 'color', scope, 'dim')),
        fontSize: size,
        lineHeight: `${size}px`,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }, glyphOf(dyn(n, 'name', scope)))
    }

    case 'image':
      return inner({
        background: cssColor(dyn(n, 'fill', scope, 'card')),
        borderRadius: numProp(n, 'radius', 0),
        border: '1px solid #1E252F',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: '#556',
        fontSize: 10,
      }, dyn(n, 'name', scope))

    case 'custom':
      return inner({
        border: '1px dashed #2A3542',
        borderRadius: 6,
        color: '#5A6B7C',
        fontSize: 10,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        textAlign: 'center',
        padding: 4,
      }, `自绘：${dyn(n, 'name', scope)}`)

    case 'list': {
      const items = strProp(n, 'items', '')
      const rowH = numProp(n, 'rowH', 56)
      const gap = numProp(n, 'gap', 0)
      const rows = rowsAt(scope, items) ?? []
      const emptyRaw = strProp(n, 'empty', '')
      if (rows.length === 0) {
        const emptyTxt = template(emptyRaw, scope)
        return inner({
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#8A94A3',
          fontSize: 12,
          border: editing ? '1px dashed #24303C' : 'none',
        }, emptyTxt || (editing ? `空列表 ${items}` : ''))
      }
      const maxRows = Math.min(40, Math.max(1, Math.ceil((h + gap) / (rowH + gap))))
      const rowEls = rows.slice(0, maxRows).map((row, i) => {
        const rowScope: Record<string, JsonValue> = {
          ...(scope as Record<string, JsonValue>),
          item: row as JsonValue,
          i,
        }
        return (
          <div
            key={i}
            style={{ position: 'absolute', left: 0, top: i * (rowH + gap), width: '100%', height: rowH }}
          >
            {n.children.map((c) => (
              <NodeView
                key={c.uid}
                node={c}
                scope={rowScope}
                editing={editing}
                elRef={elRef}
                onPointerDown={onPointerDown}
                onClickNode={onClickNode}
              />
            ))}
          </div>
        )
      })
      return inner({
        overflow: 'visible',
        border: editing ? '1px dashed #24303C' : 'none',
      }, <>{rowEls}{kids}</>)
    }

    case 'slider': {
      const min = numProp(n, 'min', 0)
      const max = numProp(n, 'max', 100)
      const v = asNum(evalExpr(strProp(n, 'value', '0'), scope))
      const t = max === min ? 0 : Math.max(0, Math.min(1, (v - min) / (max - min)))
      return inner({ display: 'flex', alignItems: 'center' }, <>
        <div style={{ position: 'absolute', left: 0, right: 0, height: 4, borderRadius: 2, background: '#1E252F' }} />
        <div style={{ position: 'absolute', left: 0, width: `${t * 100}%`, height: 4, borderRadius: 2, background: cssColor('acc') }} />
        <div style={{
          position: 'absolute', left: `calc(${t * 100}% - 7px)`, width: 14, height: 14,
          borderRadius: 7, background: cssColor('acc'), top: 'calc(50% - 7px)',
        }} />
      </>)
    }

    case 'toggle': {
      const on = asBool(evalExpr(strProp(n, 'value', 'false'), scope))
      const size = numProp(n, 'size', 14)
      return inner({ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }, <>
        <span style={{ fontSize: size, color: cssColor(dyn(n, 'color', scope, 'txt')), overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis' }}>
          {template(strProp(n, 'text', ''), scope)}
        </span>
        <span style={{
          flex: '0 0 auto', width: 34, height: 20, borderRadius: 10,
          background: on ? cssColor('acc@70') : cssColor('line'), position: 'relative',
        }}>
          <span style={{
            position: 'absolute', top: 2, left: on ? 16 : 2, width: 16, height: 16,
            borderRadius: 8, background: cssColor(on ? 'txt' : 'dim'),
          }} />
        </span>
      </>)
    }

    case 'select': {
      const raw = n.props['options']
      const opts = Array.isArray(raw)
        ? raw.map((x) => String(x))
        : typeof raw === 'string' ? raw.split('|') : []
      const val = asStrSafe(evalExpr(strProp(n, 'value', ''), scope))
      const label = val || opts[0] || '选择'
      return inner({
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        background: cssColor('card'), border: `1px solid ${cssColor('line')}`, borderRadius: 8,
        padding: '0 8px', fontSize: 12, color: cssColor('txt'),
      }, <><span>{label}</span><span style={{ color: cssColor('dim') }}>▾</span></>)
    }

    case 'input': {
      const val = asStrSafe(evalExpr(strProp(n, 'value', ''), scope))
      return inner({
        display: 'flex', alignItems: 'center', padding: '0 10px', fontSize: 12,
        background: cssColor(dyn(n, 'fill', scope, 'card')),
        border: `1px solid ${cssColor(dyn(n, 'border', scope, 'line'))}`,
        borderRadius: numProp(n, 'radius', 10),
        color: cssColor(val ? 'txt' : 'dim'),
        overflow: 'hidden', whiteSpace: 'nowrap',
      }, val || template(strProp(n, 'hint', ''), scope))
    }

    case 'progress': {
      const v = Math.max(0, Math.min(1, asNum(evalExpr(strProp(n, 'value', '0'), scope))))
      const barH = numProp(n, 'height', 4)
      return inner({ display: 'flex', alignItems: 'center' }, <>
        <div style={{ position: 'absolute', left: 0, right: 0, height: barH, borderRadius: barH / 2, background: cssColor('line') }} />
        <div style={{ position: 'absolute', left: 0, width: `${v * 100}%`, height: barH, borderRadius: barH / 2, background: cssColor(dyn(n, 'color', scope, 'acc')) }} />
      </>)
    }

    case 'spacer':
      return inner({ border: editing ? '1px dashed #1E252F' : 'none' }, kids)

    default:
      return inner({
        border: '1px dashed #E8574A',
        color: '#E8574A',
        fontSize: 10,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        overflow: 'hidden',
      }, `未知类型：${n.type || '(空)'}`)
  }
}

function asStrSafe(v: { k: string; v?: number | string | boolean }): string {
  if (v.k === 's') return String(v.v)
  if (v.k === 'n') return String(v.v)
  if (v.k === 'b') return v.v ? 'true' : 'false'
  return ''
}
