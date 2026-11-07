/**
 * 顶栏：文件、屏、视图开关、校验。
 */
import { useRef } from 'react'
import { store, useEditor } from '../state/store'

export function TopBar(): JSX.Element {
  const st = useEditor()
  const fileRef = useRef<HTMLInputElement | null>(null)
  const doc = st.doc

  return (
    <header className="topbar">
      <div className="brand">
        <span className="dot" />
        <strong>App 排版器</strong>
        <span className="dim mono">360×780</span>
      </div>

      <div className="group">
        <button className="btn" onClick={() => fileRef.current?.click()}>打开</button>
        <input
          ref={fileRef}
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
        <button
          className="btn"
          disabled={!doc}
          title="Ctrl+S"
          onClick={() => store.exportDownload()}
        >
          导出
        </button>
        <button className="btn" disabled={!doc} onClick={() => store.runValidate()}>
          校验
        </button>
        {st.rootError ? <span className="pill err" title={st.rootError}>screens 形状不对</span> : null}
      </div>

      <div className="group">
        <button className="btn icon" disabled={st.past.length === 0} onClick={() => store.undo()} title="撤销 Ctrl+Z">↶</button>
        <button className="btn icon" disabled={st.future.length === 0} onClick={() => store.redo()} title="重做 Ctrl+Y">↷</button>
        <span className="dim mono small">{st.past.length} 步</span>
      </div>

      <div className="group screens">
        {(doc?.screens ?? []).map((s) => (
          <button
            key={s.id}
            className={'tab' + (s.id === st.screenId ? ' on' : '')}
            onClick={() => store.setScreen(s.id)}
            title={`${s.nodes.length} 个顶层节点`}
          >
            {s.id}
          </button>
        ))}
      </div>

      <div className="group">
        <div className="seg">
          <button className={st.mode === 'edit' ? 'on' : ''} onClick={() => store.setMode('edit')}>编辑</button>
          <button className={st.mode === 'preview' ? 'on' : ''} onClick={() => store.setMode('preview')}>预览</button>
        </div>
        <select
          className="sel small"
          value={st.grid}
          onChange={(e) => store.setGrid(Number(e.target.value))}
          title="网格（dp）"
        >
          <option value={0}>网格 关</option>
          <option value={1}>网格 1</option>
          <option value={4}>网格 4</option>
          <option value={8}>网格 8</option>
        </select>
        <button
          className={'btn small' + (st.snap ? ' on' : '')}
          onClick={() => store.toggleSnap()}
          title="对齐吸附 + 网格吸附"
        >吸附</button>
        <button
          className={'btn small' + (st.showGuides ? ' on' : '')}
          onClick={() => store.toggleGuides()}
          title="状态栏 / 底栏 参考线"
        >参考线</button>
        <button className="btn small" onClick={() => store.setDialog('shortcuts')} title="快捷键">⌘ 快捷键</button>
      </div>

      <div className="spacer" />
      <div className="fileinfo mono">
        {doc ? <>{st.fileName}{st.dirty ? <span className="dirty">*</span> : null}</> : '未打开文件'}
        {st.lastSaved ? <span className="dim"> · 已保存 {st.lastSaved}</span> : null}
      </div>
    </header>
  )
}
