/**
 * 校验结果表。点一行跳到该节点（换屏 + 展开祖先 + 选中）。
 */
import { store, useEditor } from '../state/store'
import { countIssues } from '../core/validate'

export function ValidateDialog(): JSX.Element | null {
  const st = useEditor()
  if (st.dialog !== 'validate') return null
  const issues = st.issues ?? []
  const c = countIssues(issues)

  return (
    <div className="modal" onMouseDown={() => store.setDialog(null)}>
      <div className="modal-card wide" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>导出前校验 <span className="dim">· {issues.length} 条</span></h3>
          <span className={c.e > 0 ? 'pill err' : 'pill ok'}>{c.e} 错误</span>
          <span className={c.w > 0 ? 'pill warn' : 'pill ok'}>{c.w} 警告</span>
          <button className="x" onClick={() => store.setDialog(null)}>✕</button>
        </div>
        {issues.length === 0 ? (
          <div className="pad ok-text">没有发现问题，可以导出。</div>
        ) : (
          <div className="issue-table">
            <div className="issue-head">
              <span>级</span><span>屏</span><span>位置</span><span>字段</span><span>说明</span>
            </div>
            {issues.map((i, idx) => (
              <button
                key={idx}
                className={'issue-row ' + (i.level === 'E' ? 'lv-e' : 'lv-w') + (i.uid ? '' : ' no-jump')}
                onClick={() => i.uid && store.jumpTo(i.uid)}
                title={i.uid ? '跳到该节点' : '文件级问题，没有节点可跳'}
              >
                <span className="lv">{i.level}</span>
                <span className="mono">{i.screen}</span>
                <span className="mono path">{i.path}</span>
                <span className="mono">{i.field}</span>
                <span className="msg">{i.message}</span>
              </button>
            ))}
          </div>
        )}
        <div className="modal-foot">
          <span className="dim">规则和 App 端 LayoutParse 一致：版本、screens 形状、type、宽高、锚点、visible/enabled 裸表达式、模板语法、动作白名单、48dp 点击区。</span>
          <button className="btn" onClick={() => store.setDialog(null)}>关闭</button>
        </div>
      </div>
    </div>
  )
}
