/**
 * 快捷键面板。
 */
import { store } from '../state/store'

const GROUPS: { title: string; rows: [string, string][] }[] = [
  {
    title: '文件',
    rows: [
      ['Ctrl + O', '打开 JSON'],
      ['Ctrl + S', '导出下载'],
      ['Ctrl + Alt + V', '校验'],
    ],
  },
  {
    title: '编辑',
    rows: [
      ['Ctrl + Z', '撤销'],
      ['Ctrl + Y / Ctrl + Shift + Z', '重做'],
      ['Ctrl + C / Ctrl + V', '复制 / 粘贴'],
      ['Ctrl + Shift + C / Ctrl + Shift + V', '复制样式 / 粘贴样式'],
      ['Ctrl + D', '原地副本'],
      ['Delete / Backspace', '删除选中'],
      ['Ctrl + G / Ctrl + Shift + G', '分组 / 解组'],
      ['Esc', '取消选择 / 关面板'],
    ],
  },
  {
    title: '画布',
    rows: [
      ['方向键', '移动 1dp'],
      ['Shift + 方向键', '移动 10dp'],
      ['滚轮', '以光标为中心缩放'],
      ['空格 + 拖拽', '平移画布'],
      ['Ctrl + 0', '缩放到 100%'],
      ['Shift + 点击', '加选'],
      ['拖空白处', '框选'],
      ['Alt + 拖拽', '忽略吸附（临时）'],
    ],
  },
  {
    title: '视图',
    rows: [
      ['P', '编辑 / 预览 切换'],
      ['G', '网格 关 / 1 / 4 / 8'],
      ['L', '参考线开关'],
    ],
  },
]

export function Shortcuts(): JSX.Element | null {
  return (
    <div className="modal" onMouseDown={() => store.setDialog(null)}>
      <div className="modal-card" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>快捷键</h3>
          <button className="x" onClick={() => store.setDialog(null)}>✕</button>
        </div>
        <div className="sc-grid">
          {GROUPS.map((g) => (
            <div key={g.title} className="sc-group">
              <h4>{g.title}</h4>
              {g.rows.map(([k, v]) => (
                <div className="sc-row" key={k}>
                  <kbd>{k}</kbd>
                  <span>{v}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
