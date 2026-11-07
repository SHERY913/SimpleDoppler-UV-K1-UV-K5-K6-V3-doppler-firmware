/**
 * 预览：隐藏手柄和网格，用假数据渲染整屏。按钮可点，只往日志里写一行。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { DESIGN_H, DESIGN_W } from '../core/model'
import { store, useEditor } from '../state/store'
import { NodeView } from './NodeView'
import { previewScope } from '../data/fixtures'

export function Preview(): JSX.Element {
  const st = useEditor()
  const screen = store.screen()
  const hostRef = useRef<HTMLDivElement | null>(null)
  const [size, setSize] = useState({ w: 900, h: 800 })
  const scope = useMemo(() => previewScope(), [st.doc])

  useEffect(() => {
    const el = hostRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }))
    ro.observe(el)
    setSize({ w: el.clientWidth, h: el.clientHeight })
    return () => ro.disconnect()
  }, [])

  const scale = Math.max(0.2, Math.min((size.w - 72) / DESIGN_W, (size.h - 96) / DESIGN_H))

  return (
    <div className="preview" ref={hostRef}>
      <div className="preview-stage-wrap" style={{ width: DESIGN_W * scale, height: DESIGN_H * scale }}>
        <div
          className="stage preview-stage"
          style={{
            width: DESIGN_W,
            height: DESIGN_H,
            transform: `scale(${scale})`,
            transformOrigin: '0 0',
            background: '#080A0E',
          }}
        >
          {screen
            ? screen.nodes.map((n) => (
              <NodeView
                key={n.uid}
                node={n}
                scope={scope}
                editing={false}
                onClickNode={(node) => {
                  const act = node.props['onClick']
                  if (typeof act === 'string' && act.trim() !== '') {
                    store.logPreview(`${act}  ← ${node.name ?? node.type}`)
                  }
                }}
              />
            ))
            : <div className="dim pad">没有屏</div>}
        </div>
      </div>
      <div className="preview-side">
        <div className="ps-head">点击日志</div>
        <div className="ps-log">
          {st.previewLog.length === 0 ? <div className="dim">点预览里的按钮，动作会打到这儿（不会真执行）。</div> : null}
          {[...st.previewLog].reverse().map((l, i) => (
            <div className="log-line" key={`${l.t}-${i}`}>
              <span className="mono t">{new Date(l.t).toLocaleTimeString()}</span>
              <span className="mono">{l.msg}</span>
            </div>
          ))}
        </div>
        <div className="ps-note">
          假数据：ISS (ZARYA) · CAS-4B · FOX-1B (AO-91) · 145.987654 MHz · -12.7 kHz
        </div>
      </div>
    </div>
  )
}
