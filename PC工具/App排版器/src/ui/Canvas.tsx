/**
 * 画布。设计坐标 360×780，整体用 CSS transform 等比缩放。
 *
 * 拖拽一律走 rAF 批处理：pointermove 只写 ref，帧回调里直接改 DOM 的
 * style/transform，React 一次都不重渲染，松手才 commit 一次（所以 100 个节点也顺）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import { DESIGN_H, DESIGN_W, type LNode } from '../core/model'
import { store, useEditor } from '../state/store'
import { NodeView } from './NodeView'
import { previewScope } from '../data/fixtures'

type DragMode = 'move' | 'resize' | 'rotate' | 'marquee' | 'pan'

interface Rect {
  x: number
  y: number
  w: number
  h: number
}

interface DragState {
  mode: DragMode
  startDesign: { x: number; y: number }
  startClient: { x: number; y: number }
  origin: { x: number; y: number }
  scale: number
  rects: Map<string, Rect>
  primary: string
  handle: string
  bbox: Rect
  applied: { dx: number; dy: number }
  moved: boolean
  angle: number
  additive: boolean
}

interface Pending {
  x: number
  y: number
  shift: boolean
}

const HANDLES: { id: string; x: number; y: number; cursor: string }[] = [
  { id: 'nw', x: 0, y: 0, cursor: 'nwse-resize' },
  { id: 'n', x: 0.5, y: 0, cursor: 'ns-resize' },
  { id: 'ne', x: 1, y: 0, cursor: 'nesw-resize' },
  { id: 'e', x: 1, y: 0.5, cursor: 'ew-resize' },
  { id: 'se', x: 1, y: 1, cursor: 'nwse-resize' },
  { id: 's', x: 0.5, y: 1, cursor: 'ns-resize' },
  { id: 'sw', x: 0, y: 1, cursor: 'nesw-resize' },
  { id: 'w', x: 0, y: 0.5, cursor: 'ew-resize' },
]

function absOf(uid: string): Rect | null {
  const e = store.entry(uid)
  if (!e) return null
  let x = e.node.x
  let y = e.node.y
  let p = e.parent
  while (p) {
    x += p.x
    y += p.y
    p = store.entry(p.uid)?.parent ?? null
  }
  return { x, y, w: e.node.w, h: e.node.h }
}

function bboxOf(uids: string[]): Rect | null {
  let out: Rect | null = null
  for (const u of uids) {
    const r = absOf(u)
    if (!r) continue
    if (!out) out = { ...r }
    else {
      const x2 = Math.max(out.x + out.w, r.x + r.w)
      const y2 = Math.max(out.y + out.h, r.y + r.h)
      out.x = Math.min(out.x, r.x)
      out.y = Math.min(out.y, r.y)
      out.w = x2 - out.x
      out.h = y2 - out.y
    }
  }
  return out
}

export function Canvas(): JSX.Element {
  const st = useEditor()
  const screen = store.screen()
  const hostRef = useRef<HTMLDivElement | null>(null)
  const stageRef = useRef<HTMLDivElement | null>(null)
  const selBoxRef = useRef<HTMLDivElement | null>(null)
  const marqueeRef = useRef<HTMLDivElement | null>(null)
  const angleRef = useRef<HTMLDivElement | null>(null)
  const nodeEls = useRef(new Map<string, HTMLDivElement>())
  const dragRef = useRef<DragState | null>(null)
  const pendingRef = useRef<Pending | null>(null)
  const rafRef = useRef(0)
  const [host, setHost] = useState({ w: 900, h: 820 })
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [spaceHeld, setSpaceHeld] = useState(false)
  const originRef = useRef({ x: 0, y: 0 })
  const scaleRef = useRef(1)

  const fit = Math.min((host.w - 48) / DESIGN_W, (host.h - 48) / DESIGN_H)
  const fitScale = Math.max(0.2, fit)
  const scale = fitScale * st.zoom
  const origin = {
    x: (host.w - DESIGN_W * scale) / 2 + pan.x,
    y: (host.h - DESIGN_H * scale) / 2 + pan.y,
  }
  originRef.current = origin
  scaleRef.current = scale

  useEffect(() => {
    const el = hostRef.current
    if (!el) return
    const ro = new ResizeObserver(() => {
      setHost({ w: el.clientWidth, h: el.clientHeight })
    })
    ro.observe(el)
    setHost({ w: el.clientWidth, h: el.clientHeight })
    return () => ro.disconnect()
  }, [])

  // 空格平移
  useEffect(() => {
    const down = (e: KeyboardEvent): void => {
      if (e.code === 'Space' && !(e.target instanceof HTMLInputElement) && !(e.target instanceof HTMLTextAreaElement)) {
        setSpaceHeld(true)
        e.preventDefault()
      }
    }
    const up = (e: KeyboardEvent): void => {
      if (e.code === 'Space') setSpaceHeld(false)
    }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
    }
  }, [])

  const toDesign = useCallback((clientX: number, clientY: number): { x: number; y: number } => {
    const el = hostRef.current
    if (!el) return { x: 0, y: 0 }
    const r = el.getBoundingClientRect()
    const o = originRef.current
    const s = scaleRef.current
    return { x: (clientX - r.left - o.x) / s, y: (clientY - r.top - o.y) / s }
  }, [])

  const siblingsOf = useCallback((uid: string): Rect[] => {
    const e = store.entry(uid)
    if (!e) return []
    const arr = e.parent ? e.parent.children : e.screen.nodes
    return arr
      .filter((n) => n.uid !== uid && !st.selection.includes(n.uid))
      .map((n) => ({ x: n.x, y: n.y, w: n.w, h: n.h }))
  }, [st.selection])

  const snapMove = useCallback((uid: string, r: Rect, dx: number, dy: number): { dx: number; dy: number } => {
    if (!st.snap) return { dx, dy }
    const sib = siblingsOf(uid)
    const candX: number[] = []
    const candY: number[] = []
    for (const s of sib) {
      candX.push(s.x, s.x + s.w / 2, s.x + s.w)
      candY.push(s.y, s.y + s.h / 2, s.y + s.h)
    }
    const e = store.entry(uid)
    const parentW = e?.parent ? e.parent.w : DESIGN_W
    const parentH = e?.parent ? e.parent.h : DESIGN_H
    candX.push(0, parentW / 2, parentW)
    candY.push(0, parentH / 2, parentH)

    const mine = [r.x + dx, r.x + r.w / 2 + dx, r.x + r.w + dx]
    const mineY = [r.y + dy, r.y + r.h / 2 + dy, r.y + r.h + dy]
    const TH = 4
    let bestX: { d: number; v: number } | null = null
    for (const m of mine) {
      for (const c of candX) {
        const d = c - m
        if (Math.abs(d) <= TH && (!bestX || Math.abs(d) < Math.abs(bestX.d))) bestX = { d, v: c }
      }
    }
    let bestY: { d: number; v: number } | null = null
    for (const m of mineY) {
      for (const c of candY) {
        const d = c - m
        if (Math.abs(d) <= TH && (!bestY || Math.abs(d) < Math.abs(bestY.d))) bestY = { d, v: c }
      }
    }
    let ndx = dx + (bestX ? bestX.d : 0)
    let ndy = dy + (bestY ? bestY.d : 0)
    if (st.grid > 0) {
      if (!bestX) ndx = Math.round((r.x + dx) / st.grid) * st.grid - r.x
      if (!bestY) ndy = Math.round((r.y + dy) / st.grid) * st.grid - r.y
    }
    setGuides(bestX ? bestX.v : null, bestY ? bestY.v : null)
    return { dx: ndx, dy: ndy }
  }, [st.snap, st.grid, siblingsOf])

  const setGuides = useCallback((vx: number | null, hy: number | null): void => {
    const gl = guideRefs.current
    const o = originRef.current
    const s = scaleRef.current
    if (vx === null) gl[0].style.display = 'none'
    else {
      gl[0].style.display = 'block'
      gl[0].style.left = `${o.x + vx * s}px`
    }
    if (hy === null) gl[1].style.display = 'none'
    else {
      gl[1].style.display = 'block'
      gl[1].style.top = `${o.y + hy * s}px`
    }
  }, [])

  const guideRefs = useRef<HTMLDivElement[]>([])

  const paintSelBox = useCallback((r: Rect): void => {
    const box = selBoxRef.current
    if (!box) return
    const o = originRef.current
    const s = scaleRef.current
    box.style.display = 'block'
    box.style.left = `${o.x + r.x * s}px`
    box.style.top = `${o.y + r.y * s}px`
    box.style.width = `${r.w * s}px`
    box.style.height = `${r.h * s}px`
  }, [])

  const tick = useCallback((): void => {
    rafRef.current = 0
    const d = dragRef.current
    const p = pendingRef.current
    if (!d || !p) return
    const s = d.scale
    const dx0 = (p.x - d.startClient.x) / s
    const dy0 = (p.y - d.startClient.y) / s
    if (Math.abs(dx0) > 0.3 || Math.abs(dy0) > 0.3) d.moved = true

    if (d.mode === 'move') {
      const prim = d.rects.get(d.primary)
      if (!prim) return
      const { dx, dy } = snapMove(d.primary, prim, dx0, dy0)
      d.applied = { dx, dy }
      for (const [uid] of d.rects) {
        const el = nodeEls.current.get(uid)
        if (el) el.style.transform = `translate(${dx}px, ${dy}px)`
      }
      paintSelBox({
        x: d.bbox.x + dx, y: d.bbox.y + dy, w: d.bbox.w, h: d.bbox.h,
      })
      return
    }

    if (d.mode === 'resize') {
      const r0 = d.rects.get(d.primary)
      if (!r0) return
      const h = d.handle
      let { x, y, w, hh } = { x: r0.x, y: r0.y, w: r0.w, hh: r0.h }
      let dx = dx0
      let dy = dy0
      if (st.grid > 0) {
        const g = st.grid
        const rx = Math.round((r0.x + dx) / g) * g
        const ry = Math.round((r0.y + dy) / g) * g
        const rw = Math.round((r0.w + (h.includes('w') ? -dx : h.includes('e') ? dx : 0)) / g) * g
        const rh = Math.round((r0.h + (h.includes('n') ? -dy : h.includes('s') ? dy : 0)) / g) * g
        dx = rx - r0.x
        dy = ry - r0.y
        if (h.includes('w')) w = Math.max(1, rw)
        if (h.includes('e')) w = Math.max(1, rw)
        if (h.includes('n')) hh = Math.max(1, rh)
        if (h.includes('s')) hh = Math.max(1, rh)
      } else {
        if (h.includes('w')) w = Math.max(1, r0.w - dx)
        if (h.includes('e')) w = Math.max(1, r0.w + dx)
        if (h.includes('n')) hh = Math.max(1, r0.h - dy)
        if (h.includes('s')) hh = Math.max(1, r0.h + dy)
      }
      if (h.includes('w')) x = r0.x + r0.w - w
      if (h.includes('n')) y = r0.y + r0.h - hh
      const el = nodeEls.current.get(d.primary)
      if (el) {
        el.style.left = `${x}px`
        el.style.top = `${y}px`
        el.style.width = `${w}px`
        el.style.height = `${hh}px`
      }
      const ae = absOf(d.primary)
      const parentOffset = ae ? { x: ae.x - (store.entry(d.primary)?.node.x ?? 0), y: ae.y - (store.entry(d.primary)?.node.y ?? 0) } : { x: 0, y: 0 }
      paintSelBox({ x: x + parentOffset.x, y: y + parentOffset.y, w, h: hh })
      d.applied = { dx, dy }
      d.rects.set(d.primary, { x, y, w, h: hh })
      return
    }

    if (d.mode === 'rotate') {
      const c = d.bbox
      const cx = d.origin.x + (c.x + c.w / 2) * s
      const cy = d.origin.y + (c.y + c.h / 2) * s
      const ang = (Math.atan2(p.y - cy, p.x - cx) * 180) / Math.PI + 90
      const snapped = Math.round(ang / 15) * 15
      d.angle = snapped
      if (angleRef.current) {
        angleRef.current.style.display = 'block'
        angleRef.current.textContent = `${snapped}°`
        angleRef.current.style.left = `${p.x}px`
        angleRef.current.style.top = `${p.y - 28}px`
      }
      return
    }

    if (d.mode === 'marquee') {
      const x1 = Math.min(d.startClient.x, p.x)
      const y1 = Math.min(d.startClient.y, p.y)
      const w = Math.abs(p.x - d.startClient.x)
      const h = Math.abs(p.y - d.startClient.y)
      const m = marqueeRef.current
      if (m) {
        m.style.display = 'block'
        m.style.left = `${x1}px`
        m.style.top = `${y1}px`
        m.style.width = `${w}px`
        m.style.height = `${h}px`
      }
      return
    }

    if (d.mode === 'pan') {
      setPan({ x: d.applied.dx + dx0, y: d.applied.dy + dy0 })
    }
  }, [paintSelBox, snapMove, st.grid])

  const schedule = useCallback((): void => {
    if (!rafRef.current) rafRef.current = requestAnimationFrame(tick)
  }, [tick])

  useEffect(() => {
    const move = (e: PointerEvent): void => {
      if (!dragRef.current) return
      pendingRef.current = { x: e.clientX, y: e.clientY, shift: e.shiftKey }
      schedule()
    }
    const up = (e: PointerEvent): void => {
      const d = dragRef.current
      if (!d) return
      dragRef.current = null
      pendingRef.current = null
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current)
        rafRef.current = 0
      }
      setGuides(null, null)
      if (marqueeRef.current) marqueeRef.current.style.display = 'none'
      if (angleRef.current) angleRef.current.style.display = 'none'

      if (d.mode === 'move' && d.moved) {
        for (const [uid] of d.rects) {
          const el = nodeEls.current.get(uid)
          if (el) el.style.transform = ''
        }
        store.moveBy([...d.rects.keys()], Math.round(d.applied.dx * 100) / 100, Math.round(d.applied.dy * 100) / 100)
      } else if (d.mode === 'move') {
        for (const [uid] of d.rects) {
          const el = nodeEls.current.get(uid)
          if (el) el.style.transform = ''
        }
      } else if (d.mode === 'resize' && d.moved) {
        const r = d.rects.get(d.primary)
        if (r) store.setRect(d.primary, r)
      } else if (d.mode === 'rotate' && d.moved) {
        store.updateProp(d.primary, 'rotate', d.angle)
        store.toast(`已写入 rotate=${d.angle}`)
      } else if (d.mode === 'marquee') {
        const el = hostRef.current
        if (el) {
          const r = el.getBoundingClientRect()
          const a = { x: Math.min(d.startClient.x, e.clientX) - r.left, y: Math.min(d.startClient.y, e.clientY) - r.top }
          const b = { x: Math.max(d.startClient.x, e.clientX) - r.left, y: Math.max(d.startClient.y, e.clientY) - r.top }
          const p1 = { x: (a.x - d.origin.x) / d.scale, y: (a.y - d.origin.y) / d.scale }
          const p2 = { x: (b.x - d.origin.x) / d.scale, y: (b.y - d.origin.y) / d.scale }
          const hits: string[] = []
          for (const [uid, entry] of store.index()) {
            if (entry.screen.id !== store.state.screenId) continue
            const ar = absOf(uid)
            if (!ar) continue
            if (ar.x < p2.x && ar.x + ar.w > p1.x && ar.y < p2.y && ar.y + ar.h > p1.y) hits.push(uid)
          }
          if (hits.length > 0) store.selectMany(hits, d.additive)
          else if (!d.additive) store.clearSelection()
        }
      }
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
  }, [schedule, setGuides])

  const startDrag = (mode: DragMode, e: ReactPointerEvent, primary: string, handle = ''): void => {
    const rects = new Map<string, Rect>()
    const uids = mode === 'resize' || mode === 'rotate' ? [primary] : store.state.selection
    for (const uid of uids) {
      const r = absOf(uid)
      if (r) rects.set(uid, { ...r, x: store.entry(uid)?.node.x ?? 0, y: store.entry(uid)?.node.y ?? 0 })
    }
    const bb = bboxOf(uids) ?? { x: 0, y: 0, w: 10, h: 10 }
    dragRef.current = {
      mode,
      startDesign: toDesign(e.clientX, e.clientY),
      startClient: { x: e.clientX, y: e.clientY },
      origin: { ...originRef.current },
      scale: scaleRef.current,
      rects,
      primary,
      handle,
      bbox: bb,
      applied: { dx: 0, dy: 0 },
      moved: false,
      angle: 0,
      additive: e.shiftKey,
    }
    ;(e.target as Element).setPointerCapture?.(e.pointerId)
  }

  const onNodePointerDown = (e: ReactPointerEvent<HTMLDivElement>, n: LNode): void => {
    if (e.button !== 0) return
    e.stopPropagation()
    if (n.locked) {
      store.select(n.uid, e.shiftKey)
      return
    }
    if (e.shiftKey) store.select(n.uid, true)
    else if (!store.state.selection.includes(n.uid)) store.select(n.uid, false)
    const entry = store.entry(n.uid)
    const screenEntryUid = entry?.screen.id
    if (screenEntryUid !== store.state.screenId) return
    startDrag('move', e, n.uid)
  }

  const onHostPointerDown = (e: ReactPointerEvent<HTMLDivElement>): void => {
    if (e.button === 1 || spaceHeld) {
      dragRef.current = {
        mode: 'pan',
        startDesign: { x: 0, y: 0 },
        startClient: { x: e.clientX, y: e.clientY },
        origin: { ...originRef.current },
        scale: scaleRef.current,
        rects: new Map(),
        primary: '',
        handle: '',
        bbox: { x: 0, y: 0, w: 0, h: 0 },
        applied: { dx: pan.x, dy: pan.y },
        moved: false,
        angle: 0,
        additive: false,
      }
      e.preventDefault()
      return
    }
    if (e.button !== 0) return
    if ((e.target as HTMLElement).closest('[data-node]')) return
    dragRef.current = {
      mode: 'marquee',
      startDesign: toDesign(e.clientX, e.clientY),
      startClient: { x: e.clientX, y: e.clientY },
      origin: { ...originRef.current },
      scale: scaleRef.current,
      rects: new Map(),
      primary: '',
      handle: '',
      bbox: { x: 0, y: 0, w: 0, h: 0 },
      applied: { dx: 0, dy: 0 },
      moved: false,
      angle: 0,
      additive: e.shiftKey,
    }
    if (!e.shiftKey) store.clearSelection()
  }

  const onWheel = (e: React.WheelEvent<HTMLDivElement>): void => {
    e.preventDefault()
    const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1
    const next = Math.max(0.25, Math.min(4, st.zoom * factor))
    const el = hostRef.current
    if (el) {
      const r = el.getBoundingClientRect()
      const px = e.clientX - r.left
      const py = e.clientY - r.top
      const before = { x: (px - origin.x) / scale, y: (py - origin.y) / scale }
      const nScale = fitScale * next
      const nOriginX = (host.w - DESIGN_W * nScale) / 2 + pan.x
      const nOriginY = (host.h - DESIGN_H * nScale) / 2 + pan.y
      const after = { x: (px - nOriginX) / nScale, y: (py - nOriginY) / nScale }
      setPan({
        x: pan.x + (after.x - before.x) * nScale,
        y: pan.y + (after.y - before.y) * nScale,
      })
    }
    store.setZoom(next)
  }

  const selRect = useMemo(() => bboxOf(st.selection), [st.doc, st.selection, st.screenId])

  const gridStyle: React.CSSProperties = st.grid > 0
    ? {
      backgroundImage:
        `linear-gradient(to right, rgba(35,43,54,0.55) 1px, transparent 1px),` +
        `linear-gradient(to bottom, rgba(35,43,54,0.55) 1px, transparent 1px)`,
      backgroundSize: `${st.grid}px ${st.grid}px`,
    }
    : {}

  const scope = useMemo(() => previewScope(), [])
  const selBox = selRect
    ? {
      left: origin.x + selRect.x * scale,
      top: origin.y + selRect.y * scale,
      width: selRect.w * scale,
      height: selRect.h * scale,
    }
    : null

  return (
    <div
      className={'canvas-host' + (spaceHeld ? ' panning' : '')}
      ref={hostRef}
      onPointerDown={onHostPointerDown}
      onWheel={onWheel}
      onContextMenu={(e) => e.preventDefault()}
    >
      <div
        className="stage"
        ref={stageRef}
        style={{
          width: DESIGN_W,
          height: DESIGN_H,
          transform: `translate(${origin.x}px, ${origin.y}px) scale(${scale})`,
          transformOrigin: '0 0',
        }}
      >
        <div className="stage-bg" style={{ background: '#080A0E', ...gridStyle }} />
        {st.showGuides ? (
          <>
            <div className="ref-line" style={{ top: 24 }}><span>状态栏 24</span></div>
            <div className="ref-line" style={{ top: 48 }}><span>标题栏 48</span></div>
            <div className="ref-line" style={{ top: DESIGN_H - 56 }}><span>底栏 {DESIGN_H - 56}</span></div>
            <div className="safe-area" />
          </>
        ) : null}
        {screen
          ? screen.nodes.map((n) => (
            <NodeView
              key={n.uid}
              node={n}
              scope={scope}
              editing
              selected={st.selection.includes(n.uid)}
              elRef={(uid, el) => {
                if (el) nodeEls.current.set(uid, el)
                else nodeEls.current.delete(uid)
              }}
              onPointerDown={onNodePointerDown}
            />
          ))
          : null}
      </div>

      {/* 吸附参考线（屏幕坐标） */}
      <div className="guides-layer">
        <div ref={(el) => { if (el) guideRefs.current[0] = el }} className="guide v" style={{ display: 'none' }} />
        <div ref={(el) => { if (el) guideRefs.current[1] = el }} className="guide h" style={{ display: 'none' }} />
      </div>

      {selBox ? (
        <div className="selbox" ref={selBoxRef} style={selBox}>
          {HANDLES.map((h) => (
            <div
              key={h.id}
              className="handle"
              style={{ left: `${h.x * 100}%`, top: `${h.y * 100}%`, cursor: h.cursor }}
              onPointerDown={(e) => {
                e.stopPropagation()
                const uid = st.selection[0]
                if (uid) startDrag('resize', e, uid, h.id)
              }}
            />
          ))}
          <div
            className="rot-handle"
            title="旋转（15° 吸附）。写进 props.rotate，App 端按此角度绘制，点击命中仍按未旋转的矩形"
            onPointerDown={(e) => {
              e.stopPropagation()
              const uid = st.selection[0]
              if (uid) startDrag('rotate', e, uid)
            }}
          />
          <div className="selinfo">
            {Math.round(selRect?.w ?? 0)}×{Math.round(selRect?.h ?? 0)}
            {st.selection.length > 1 ? ` · ${st.selection.length} 个` : ''}
          </div>
        </div>
      ) : null}

      <div ref={marqueeRef} className="marquee" style={{ display: 'none' }} />
      <div ref={angleRef} className="angle-badge" style={{ display: 'none' }} />

      <div className="canvas-hud">
        <span>{Math.round(scale * 100)}%</span>
        <span className="sep">|</span>
        <span>滚轮缩放 · 空格拖拽平移</span>
        <span className="sep">|</span>
        <span>{screen ? `${screen.nodes.length} 顶层节点` : '无屏'}</span>
      </div>
    </div>
  )
}
