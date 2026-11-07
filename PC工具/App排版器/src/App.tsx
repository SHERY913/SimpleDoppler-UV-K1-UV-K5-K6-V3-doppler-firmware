import { useEffect, useRef } from 'react'
import { store, useEditor } from './state/store'
import { TopBar } from './ui/TopBar'
import { LayerTree } from './ui/LayerTree'
import { Canvas } from './ui/Canvas'
import { Inspector } from './ui/Inspector'
import { Preview } from './ui/Preview'
import { ValidateDialog } from './ui/ValidateDialog'
import { Shortcuts } from './ui/Shortcuts'

const GRID_CYCLE = [0, 1, 4, 8]

export default function App(): JSX.Element {
  const st = useEditor()
  const openRef = useRef<HTMLInputElement | null>(null)

  // `?open=sample/detail.json`：开发期直读 doppler-ng 的真源，省一次选文件。
  // `?mode=preview`：截图/自检时直接落进预览模式。
  useEffect(() => {
    const q = new URLSearchParams(window.location.search)
    const wantPreview = q.get('mode') === 'preview'
    if (wantPreview) store.setMode('preview')
    const file = q.get('open')
    if (!file) return
    void fetch(file)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((t) => {
        store.openText(t, file.split('/').pop() ?? 'layout.json')
        // openText 会把模式拨回编辑，截图/自检时要再拨一次
        if (wantPreview) store.setMode('preview')
      })
      .catch(() => store.toast('打不开 ' + file + '（dev 服务器 /sample 中间件只在 npm run dev 下有效）', 'err'))
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      const t = e.target as HTMLElement | null
      const typing = !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)
      const meta = e.ctrlKey || e.metaKey

      if (meta && e.key.toLowerCase() === 'o') {
        e.preventDefault()
        openRef.current?.click()
        return
      }
      if (typing) return
      if (meta && !e.shiftKey && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        store.undo()
        return
      }
      if (meta && (e.key.toLowerCase() === 'y' || (e.shiftKey && e.key.toLowerCase() === 'z'))) {
        e.preventDefault()
        store.redo()
        return
      }
      if (meta && e.key.toLowerCase() === 's') {
        e.preventDefault()
        store.exportDownload()
        return
      }
      if (meta && e.altKey && e.key.toLowerCase() === 'v') {
        e.preventDefault()
        store.runValidate()
        return
      }
      if (meta && e.shiftKey && e.key.toLowerCase() === 'c') {
        e.preventDefault()
        store.copyStyle()
        return
      }
      if (meta && e.shiftKey && e.key.toLowerCase() === 'v') {
        e.preventDefault()
        store.pasteStyle()
        return
      }
      if (meta && e.key.toLowerCase() === 'c') {
        e.preventDefault()
        store.copy()
        return
      }
      if (meta && e.key.toLowerCase() === 'v') {
        e.preventDefault()
        store.paste()
        return
      }
      if (meta && e.key.toLowerCase() === 'd') {
        e.preventDefault()
        store.duplicateSelected()
        return
      }
      if (meta && e.key.toLowerCase() === 'g') {
        e.preventDefault()
        if (e.shiftKey) {
          const uid = store.state.selection[0]
          if (uid) store.ungroup(uid)
        } else store.groupSelected()
        return
      }
      if (meta && e.key === '0') {
        e.preventDefault()
        store.setZoom(1)
        return
      }
      if (e.key === 'Delete' || e.key === 'Backspace') {
        if (store.state.selection.length === 0) return
        e.preventDefault()
        store.deleteSelected()
        return
      }
      if (e.key.startsWith('Arrow')) {
        if (store.state.selection.length === 0) return
        e.preventDefault()
        const step = e.shiftKey ? 10 : 1
        const dx = e.key === 'ArrowLeft' ? -step : e.key === 'ArrowRight' ? step : 0
        const dy = e.key === 'ArrowUp' ? -step : e.key === 'ArrowDown' ? step : 0
        store.moveBy(store.topSelection(), dx, dy)
        return
      }
      if (e.key === 'Escape') {
        if (store.state.dialog) store.setDialog(null)
        else store.clearSelection()
        return
      }
      if (e.key.toLowerCase() === 'p') {
        store.setMode(store.state.mode === 'edit' ? 'preview' : 'edit')
        return
      }
      if (e.key.toLowerCase() === 'g') {
        const i = GRID_CYCLE.indexOf(store.state.grid)
        store.setGrid(GRID_CYCLE[(i + 1) % GRID_CYCLE.length])
        return
      }
      if (e.key.toLowerCase() === 'l') {
        store.toggleGuides()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div className="app">
      <input
        ref={openRef}
        type="file"
        accept=".json,application/json"
        style={{ display: 'none' }}
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (!f) return
          void f.text().then((t) => store.openText(t, f.name))
          e.target.value = ''
        }}
      />
      <TopBar />
      <div className="main">
        <aside className="left">
          <LayerTree />
        </aside>
        <main className="center">
          {!st.doc ? (
            <div className="blank-state">
              <div className="bs-card">
                <h2>打开一份 layout.json</h2>
                <p className="dim">
                  导入 <span className="mono">doppler-ng/app/src/main/assets/layout/*.json</span>，
                  编辑后导出同名文件，再 adb push 回手机。
                </p>
                <button className="btn primary" onClick={() => openRef.current?.click()}>选文件</button>
              </div>
            </div>
          ) : st.mode === 'edit' ? (
            <Canvas />
          ) : (
            <Preview />
          )}
        </main>
        <aside className="right">
          <Inspector />
        </aside>
      </div>
      <ValidateDialog />
      {st.dialog === 'shortcuts' ? <Shortcuts /> : null}
      {st.toast ? (
        <div className={'toast ' + st.toast.kind}>{st.toast.msg}</div>
      ) : null}
    </div>
  )
}
