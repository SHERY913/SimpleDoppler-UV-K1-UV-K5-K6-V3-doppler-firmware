/**
 * 导出前预检 —— 判据和 App 端 LayoutParse 保持一致，外加几条"作者会踩"的警告。
 *
 * 分级：
 *   E —— App 端会整份拒载或该节点被丢掉
 *   W —— 能载入，但多半不是作者想要的（点击区太小、颜色名不认识、动作不在白名单）
 */
import { checkExpr, checkTemplate } from './expr'
import { isKnownColorSpec } from './colors'
import { TYPES, CONTAINER_TYPES, type Doc, type LNode } from './model'
import { actKnown, parseAct } from '../data/acts'

export type Level = 'E' | 'W'

export interface Issue {
  level: Level
  screen: string
  path: string
  uid: string | null
  field: string
  message: string
}

const KNOWN = new Set<string>(TYPES)
const PATH_RE = /^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$/
const VARISH = /^(now|prefs|sat|next|cart|list|sky|track|settings|ui|item|sel|tl|pass|passes)\.[A-Za-z_]/

export function validateDoc(doc: Doc, rootError: string | null): Issue[] {
  const out: Issue[] = []
  const push = (i: Issue): void => {
    out.push(i)
  }

  if (rootError) {
    push({ level: 'E', screen: '-', path: '(文件)', uid: null, field: 'root', message: rootError })
  }

  if (doc.version !== 1) {
    push({
      level: 'E', screen: '-', path: '(文件)', uid: null, field: 'version',
      message: `version = ${doc.version}，App 端只认 1，会整份拒载`,
    })
  }
  if (doc.design) {
    if (doc.design.w !== 360 || doc.design.h !== 780) {
      push({
        level: 'W', screen: '-', path: '(文件)', uid: null, field: 'design',
        message: `design = ${doc.design.w}×${doc.design.h}，渲染器按 360×780 算 scale，写了也不影响`,
      })
    }
  }
  if (doc.screens.length === 0) {
    push({ level: 'E', screen: '-', path: '(文件)', uid: null, field: 'screens', message: '没有任何屏' })
  }

  for (const screen of doc.screens) {
    const walk = (nodes: LNode[], prefix: string, parentType: string | null): void => {
      nodes.forEach((n, i) => {
        const path = `${screen.id}.${prefix}[${i}]`
        const label = n.name ?? n.id ?? n.type ?? '?'
        const at = (field: string, message: string, level: Level = 'E'): void => {
          push({ level, screen: screen.id, path: `${path} (${label})`, uid: n.uid, field, message })
        }

        if (!n.type) at('type', '缺 type')
        else if (!KNOWN.has(n.type)) at('type', `类型 '${n.type}' 不认识`)
        if (n.w <= 0 || n.h <= 0) at('w/h', `宽高必须 > 0（现在 ${n.w}×${n.h}）`)
        if (n.x < 0 || n.y < 0) at('x/y', `坐标是负的（${n.x},${n.y}），锚点解算会把它顶出去`, 'W')
        if (!/^[lrtb]*$/.test(n.anchor)) at('anchor', `锚点 '${n.anchor}' 只能由 l r t b 组成`)
        if (parentType && !CONTAINER_TYPES.includes(parentType)) {
          at('children', `父节点是 ${parentType}，只有 group/box/list/button 能装子节点`)
        }

        for (const key of ['visible', 'enabled'] as const) {
          const v = n[key]
          if (v === undefined) continue
          if (v.includes('{') || v.includes('}')) {
            at(key, `${key} 必须是裸表达式，不能带花括号（写成 ${v.replace(/[{}]/g, '')}）`)
          } else {
            const err = checkExpr(v)
            if (err) at(key, `${key} 表达式有错：${err}`)
          }
        }

        // 点击区：带 onClick 或者是天生可交互的类型
        const clickable = n.props['onClick'] !== undefined ||
          ['button', 'toggle', 'select', 'slider', 'input'].includes(n.type)
        if (clickable && (n.w < 48 || n.h < 48)) {
          at('w/h', `点击区 ${Math.round(n.w)}×${Math.round(n.h)} < 48dp，真机上很难点`, 'W')
        }

        const act = n.props['onClick']
        if (typeof act === 'string' && act.trim() !== '') {
          const p = parseAct(act)
          if (!p) at('onClick', `动作 '${act}' 不是 name(args) 的形式`)
          else if (!actKnown(act)) at('onClick', `动作 '${p.name}' 不在 Acts 白名单里`, 'W')
        }

        for (const key of ['text', 'empty', 'hint'] as const) {
          const v = n.props[key]
          if (typeof v !== 'string' || v === '') continue
          if (v.includes('{')) {
            const err = checkTemplate(v)
            if (err) at(key, `模板有错：${err}`)
          } else if (VARISH.test(v)) {
            at(key, `'${v}' 没有花括号，会按字面显示（要写成 {${v}}）`, 'W')
          }
        }

        const items = n.props['items']
        if (n.type === 'list' && typeof items === 'string') {
          if (items.includes('{')) at('items', 'items 是裸路径，不是模板')
          else if (!PATH_RE.test(items)) at('items', `items '${items}' 不是合法路径`)
        }

        for (const key of ['fill', 'fill2', 'border', 'fillPressed', 'fillDisabled', 'color', 'statusColor']) {
          const v = n.props[key]
          if (typeof v === 'string' && v !== '' && !isKnownColorSpec(v)) {
            at(key, `颜色 '${v}' 不认识（会按透明处理）`, 'W')
          }
        }

        // props 里残留的保留字会被 App 端当成顶层字段读走，容易踩
        for (const k of Object.keys(n.props)) {
          if (['uid', 'props'].includes(k)) at(k, `保留字 '${k}' 不该出现在 props 里`)
        }

        if (n.children.length > 0) walk(n.children, `${prefix}[${i}].children`, n.type)
      })
    }
    walk(screen.nodes, 'nodes', null)
  }
  return out
}

export function countIssues(list: Issue[]): { e: number; w: number } {
  let e = 0
  let w = 0
  for (const i of list) {
    if (i.level === 'E') e++
    else w++
  }
  return { e, w }
}
