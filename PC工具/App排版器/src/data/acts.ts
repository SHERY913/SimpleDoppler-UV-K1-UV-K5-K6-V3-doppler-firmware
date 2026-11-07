/**
 * Acts 白名单 —— 从 5 个 Layout*Screen.kt 的 when 分支里抄出的全部动作名。
 * 排版器只拼字符串，参数写错 App 端也只是没反应，所以这里给足模板。
 */
export interface ActDef {
  name: string
  args: string[]
  label: string
  group: string
}

const A = (name: string, args: string[], label: string, group: string): ActDef => ({ name, args, label, group })

export const ACTS: ActDef[] = [
  // 导航
  A('nav.back', [], '返回', '导航'),
  A('nav.detail', ['norad'], '打开卫星详情', '导航'),
  A('nav.track', [], '打开跟踪页', '导航'),
  A('nav.station', [], '打开电台页', '导航'),
  A('nav.about', [], '打开关于', '导航'),
  A('nav.selfcheck', [], '自检', '导航'),
  A('tab.set', ['x'], '切换主标签', '导航'),
  // 详情页 / 收藏 / 清单
  A('fav.toggle', ['norad'], '收藏开关', '详情'),
  A('cart.toggle', ['norad', 'aos'], '加入/移出清单', '详情'),
  A('cart.clear', [], '清空清单', '详情'),
  A('cart.write', [], '写入电台', '详情'),
  A('playmode.set', ['mode'], '播放模式', '详情'),
  A('sheet.close', [], '关面板', '详情'),
  A('sheet.filter', [], '筛选面板', '详情'),
  A('sheet.sort', [], '排序面板', '详情'),
  A('sheet.intro', [], '介绍面板', '详情'),
  // 列表页
  A('ui.filter', ['x'], '筛选：波段/模式', '列表'),
  A('ui.sort', ['x'], '排序', '列表'),
  A('ui.span', ['x'], '时间跨度', '列表'),
  A('ui.minEl', ['x'], '最小仰角', '列表'),
  A('ui.view', ['x'], '视图切换', '列表'),
  A('ui.sheet', ['x'], '打开面板', '列表'),
  A('ui.pin', ['x'], '置顶', '列表'),
  A('ui.search', [], '搜索', '列表'),
  A('table.clear', [], '清空表格选择', '列表'),
  A('tle.refresh', [], '刷新 TLE', '列表'),
  A('tle.revert', [], '还原 TLE', '列表'),
  A('notify.replan', [], '重排提醒', '列表'),
  // 天球页
  A('sky.now', [], '跳到当前', '天球'),
  A('sky.offset', ['n'], '时间偏移（小时）', '天球'),
  A('sky.pick', ['norad'], '选星', '天球'),
  // 跟踪页
  A('track.trim', ['n'], '多普勒微调', '跟踪'),
  A('track.takeover', [], '接管频率', '跟踪'),
  A('track.simPass', [], '模拟过境', '跟踪'),
  A('track.log', [], '写日志', '跟踪'),
  // 电台
  A('radio.connect', [], '连接电台', '电台'),
  A('radio.disconnect', [], '断开', '电台'),
  A('radio.probe', [], '探测', '电台'),
  A('radio.release', [], '松手', '电台'),
  A('radio.tcp', [], 'TCP 模式', '电台'),
  A('radio.usb', [], 'USB 模式', '电台'),
  A('station.clear', [], '清空台站', '电台'),
  A('station.locate', [], '定位台站', '电台'),
  A('station.save', [], '保存台站', '电台'),
  A('diag.export', [], '导出诊断', '电台'),
  A('diag.reset', [], '复位诊断', '电台'),
  // 设置
  A('settings.toggle', ['key'], '设置开关', '设置'),
  A('prefs.toggle', ['key'], '偏好开关', '设置'),
  A('settings.act', ['key'], '设置动作', '设置'),
  A('settings.pick', ['key'], '设置选择', '设置'),
  A('settings.text', ['key'], '设置输入', '设置'),
  A('settings.up', [], '上移一条', '设置'),
  A('settings.dn', [], '下移一条', '设置'),
  A('theme.open', [], '主题页', '设置'),
  A('theme.preset', ['n'], '主题预设', '设置'),
  A('theme.hue', [], '色相', '设置'),
  A('theme.sat', [], '饱和', '设置'),
  A('theme.val', [], '明度', '设置'),
  A('theme.seed', [], '种子色', '设置'),
]

export function actGroups(): { group: string; items: ActDef[] }[] {
  const order: string[] = []
  const map = new Map<string, ActDef[]>()
  for (const a of ACTS) {
    if (!map.has(a.group)) {
      map.set(a.group, [])
      order.push(a.group)
    }
    map.get(a.group)?.push(a)
  }
  return order.map((g) => ({ group: g, items: map.get(g) ?? [] }))
}

export function parseAct(src: string): { name: string; args: string[] } | null {
  const m = /^\s*([A-Za-z_][\w.]*)\s*(?:\((.*)\))?\s*$/.exec(src)
  if (!m) return null
  const name = m[1]
  const inner = m[2]
  const args = inner === undefined || inner.trim() === '' ? [] : inner.split(',').map((x) => x.trim())
  return { name, args }
}

export function actKnown(src: string): boolean {
  const p = parseAct(src)
  return p !== null && ACTS.some((a) => a.name === p.name)
}
