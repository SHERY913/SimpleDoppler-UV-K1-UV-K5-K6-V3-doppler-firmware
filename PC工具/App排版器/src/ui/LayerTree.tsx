/**
 * 图层树。拖拽改序（前半→放到前面、后半→放到后面、中间→放进容器），
 * 双击改名，眼睛/锁，Ctrl 点击加选。
 */
import { useState } from 'react'
import { store, useEditor } from '../state/store'
import type { LNode } from '../core/model'
import { CONTAINER_TYPES } from '../core/model'
import { TYPE_LABEL } from '../data/propschema'

interface DropHint {
  uid: string
  pos: 'before' | 'after' | 'inside'
}

export function LayerTree(): JSX.Element {
  const st = useEditor()
  const screen = store.screen()
  const [dragUid, setDragUid] = useState<string | null>(null)
  const [hint, setHint] = useState<DropHint | null>(null)
  const [editing, setEditing] = useState<string | null>(null)

  if (!screen) return <div className="tree"><div className="empty-hint">没有屏。先打开一份 layout.json。</div></div>

  const drop = (e: React.DragEvent, node: LNode, canInside: boolean): void => {
    e.preventDefault()
    e.stopPropagation()
    const src = dragUid ?? e.dataTransfer.getData('text/plain')
    if (!src || src === node.uid) return
    const el = e.currentTarget as HTMLElement
    const r = el.getBoundingClientRect()
    const y = e.clientY - r.top
    let pos: DropHint['pos'] = 'inside'
    if (!canInside) pos = y < r.height / 2 ? 'before' : 'after'
    else if (y < r.height * 0.28) pos = 'before'
    else if (y > r.height * 0.72) pos = 'after'
    setHint({ uid: node.uid, pos })
  }

  const commitDrop = (): void => {
    const h = hint
    const src = dragUid
    setHint(null)
    setDragUid(null)
    if (!h || !src) return
    const e = store.entry(h.uid)
    if (!e) return
    if (h.pos === 'inside') {
      store.reorder(src, e.node.uid, e.node.children.length)
      store.setExpanded(e.node.uid, true)
      return
    }
    const parentUid = e.parent ? e.parent.uid : null
    const idx = e.index + (h.pos === 'after' ? 1 : 0)
    store.reorder(src, parentUid, idx)
  }

  const rows: JSX.Element[] = []
  const walk = (nodes: LNode[], depth: number): void => {
    for (const n of nodes) {
      const isSel = st.selection.includes(n.uid)
      const open = store.isExpanded(n.uid)
      const hasKids = n.children.length > 0
      const canInside = CONTAINER_TYPES.includes(n.type)
      rows.push(
        <div
          key={n.uid}
          className={
            'tree-row' + (isSel ? ' sel' : '') +
            (hint?.uid === n.uid ? ` drop-${hint.pos}` : '') +
            (n.locked ? ' locked' : '')
          }
          style={{ paddingLeft: 6 + depth * 12 }}
          draggable={editing !== n.uid}
          onDragStart={(e) => {
            setDragUid(n.uid)
            e.dataTransfer.setData('text/plain', n.uid)
            e.dataTransfer.effectAllowed = 'move'
          }}
          onDragOver={(e) => drop(e, n, canInside)}
          onDragLeave={() => setHint(null)}
          onDrop={(e) => {
            e.preventDefault()
            commitDrop()
          }}
          onDragEnd={() => {
            setHint(null)
            setDragUid(null)
          }}
          onClick={(e) => store.select(n.uid, e.shiftKey || e.ctrlKey || e.metaKey)}
          onDoubleClick={() => setEditing(n.uid)}
        >
          <button
            className={'caret' + (hasKids ? '' : ' hidden')}
            onClick={(e) => {
              e.stopPropagation()
              store.toggleExpanded(n.uid)
            }}
          >
            {open ? '▾' : '▸'}
          </button>
          <span className={'type-dot t-' + n.type} title={TYPE_LABEL[n.type] ?? n.type} />
          {editing === n.uid ? (
            <input
              className="rename"
              autoFocus
              defaultValue={n.name ?? ''}
              onBlur={(e) => {
                store.updateNode(n.uid, { name: e.target.value || undefined })
                setEditing(null)
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
                if (e.key === 'Escape') setEditing(null)
              }}
              onClick={(e) => e.stopPropagation()}
            />
          ) : (
            <span className="tree-name" title={`${n.type}${n.id ? ' · ' + n.id : ''}`}>
              {n.name ?? n.id ?? TYPE_LABEL[n.type] ?? n.type}
              {n.visible ? <em className="vtag" title={`visible: ${n.visible}`}>if</em> : null}
            </span>
          )}
          <span className="tree-ops">
            <button
              className={'eye' + (n.visible ? ' cond' : '')}
              title={n.visible ? `条件显示：${n.visible}` : '隐藏（临时）'}
              onClick={(e) => {
                e.stopPropagation()
                store.updateNode(n.uid, { visible: n.visible ? undefined : 'false' })
              }}
            >
              {n.visible ? '◐' : '●'}
            </button>
            <button
              className={'lock' + (n.locked ? ' on' : '')}
              title="锁定"
              onClick={(e) => {
                e.stopPropagation()
                store.updateNode(n.uid, { locked: !n.locked })
              }}
            >
              {n.locked ? '🔒' : '🔓'}
            </button>
          </span>
        </div>,
      )
      if (hasKids && open) walk(n.children, depth + 1)
    }
  }
  walk(screen.nodes, 0)

  return (
    <div className="tree">
      <div className="tree-head">
        <span>{screen.id}</span>
        <span className="dim">{screen.nodes.length} 顶层 · {store.index().size} 总</span>
      </div>
      <div className="tree-body" onDragOver={(e) => e.preventDefault()} onDrop={() => { setHint(null); setDragUid(null) }}>
        {rows.length === 0 ? <div className="empty-hint">这屏是空的。用下面的 + 加节点。</div> : rows}
      </div>
      <div className="tree-foot">
        {['box', 'text', 'button', 'icon', 'group', 'list', 'spacer'].map((t) => (
          <button
            key={t}
            className="btn tiny"
            title={`新建${TYPE_LABEL[t]}`}
            onClick={() => {
              const sel = st.selection[0]
              const e = sel ? store.entry(sel) : null
              const parent = e && CONTAINER_TYPES.includes(e.node.type) ? e.node.uid : null
              store.addNode(t as never, parent)
            }}
          >
            +{TYPE_LABEL[t]}
          </button>
        ))}
      </div>
    </div>
  )
}
