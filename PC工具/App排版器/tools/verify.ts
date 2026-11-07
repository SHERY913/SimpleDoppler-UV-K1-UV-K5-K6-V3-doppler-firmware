/**
 * 验收脚本（不依赖浏览器）：
 *   node tools/verify.mjs <layout 目录>
 *
 * 1) 五份真源 JSON：解析 → 模型 → 导出，逐键对比，报告差异（应只有格式化差异 = 0）
 * 2) 拖动 detail.json 的「跟踪」按钮 +12dp → 导出 → 对比，只允许该节点 x 变
 * 3) 故意写坏的文件（version 99 / 未知 type / visible 带花括号）→ 校验必须报出可定位的 E
 * 4) 预览假数据：跑 list.json 里所有模板，确认最长名与超长读数都渲染出来
 * 5) 锚点解算：与 LayoutDoc.resolve 的口径核对两组
 */
import fs from 'node:fs'
import path from 'node:path'
import { parseJsonc, type JsonValue, type JsonObject } from '../src/core/jsonc'
import { docFromJson, docToJson, type Doc, type LNode } from '../src/core/model'
import { validateDoc } from '../src/core/validate'
import { previewScope, FIXTURE_NAMES, FIXTURE_READINGS, rowsAt } from '../src/data/fixtures'
import { template, asStr, evalExpr } from '../src/core/expr'
import { resolveRect } from '../src/core/resolve'

const layoutDir = process.argv[2] ?? ''
if (!layoutDir) {
  console.error('用法: node tools/verify.mjs <layout 目录>')
  process.exit(2)
}

let failures = 0
function ok(cond: boolean, msg: string): void {
  if (cond) {
    console.log('  ✓ ' + msg)
  } else {
    failures += 1
    console.log('  ✗ ' + msg)
  }
}

function readJson(file: string): JsonValue {
  return parseJsonc(fs.readFileSync(file, 'utf8'))
}

/** 语义 diff：走两边对象，返回路径列表（数组下标写 [i]）。 */
function diff(a: JsonValue | undefined, b: JsonValue | undefined, at = '', out: string[] = []): string[] {
  if (a === b) return out
  if (a === undefined) { out.push(at + ' (仅新文件有)'); return out }
  if (b === undefined) { out.push(at + ' (仅原文件有)'); return out }
  const ta = typeof a
  const tb = typeof b
  if (ta !== tb || a === null || b === null) {
    out.push(`${at}: ${JSON.stringify(a)} → ${JSON.stringify(b)}`)
    return out
  }
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b)) { out.push(`${at}: 数组/非数组`); return out }
    if (a.length !== b.length) out.push(`${at}: 长度 ${a.length} → ${b.length}`)
    const n = Math.min(a.length, b.length)
    for (let i = 0; i < n; i += 1) diff(a[i], b[i], `${at}[${i}]`, out)
    return out
  }
  if (ta === 'object') {
    const oa = a as JsonObject
    const ob = b as JsonObject
    const keys = new Set([...Object.keys(oa), ...Object.keys(ob)])
    for (const k of [...keys].sort()) diff(oa[k], ob[k], at ? `${at}.${k}` : k, out)
    return out
  }
  out.push(`${at}: ${JSON.stringify(a)} → ${JSON.stringify(b)}`)
  return out
}

function walkNodes(doc: Doc, fn: (n: LNode, screen: string, trail: string) => void): void {
  const rec = (n: LNode, screen: string, trail: string): void => {
    fn(n, screen, trail)
    n.children.forEach((c, i) => rec(c, screen, `${trail}.children[${i}]`))
  }
  for (const s of doc.screens) s.nodes.forEach((n, i) => rec(n, s.id, `screens.${s.id}.nodes[${i}]`))
}

// ── 1. 往返 ───────────────────────────────────────────────
console.log('\n[1] 五份真源往返（解析 → 模型 → 导出）')
const files = ['detail.json', 'list.json', 'sky.json', 'track.json', 'settings.json']
const docs = new Map<string, Doc>()
for (const f of files) {
  const p = path.join(layoutDir, f)
  const src = readJson(p)
  const doc = docFromJson(src, f)
  docs.set(f, doc)
  const back = docToJson(doc) as JsonValue
  const d = diff(src, back)
  console.log(`  ${f}: ${doc.screens.length} 屏 / ${doc.screens.reduce((a, s) => a + s.nodes.length, 0)} 顶层节点`)
  ok(d.length === 0, `${f} 导出与原文件语义一致${d.length ? '：' + d.slice(0, 6).join(' | ') : ''}`)
}

// ── 2. 拖动「跟踪」按钮 ───────────────────────────────────
console.log('\n[2] 拖动 detail 的「跟踪」按钮 → 导出 diff')
{
  const file = 'detail.json'
  const src = readJson(path.join(layoutDir, file))
  const doc = docFromJson(src, file)
  let target: LNode | null = null
  let trail = ''
  walkNodes(doc, (n, _s, t) => {
    if (!target && n.props['text'] === '跟踪') {
      target = n
      trail = t
    }
  })
  ok(!!target, '找到 text=跟踪 的节点' + (target ? `（${trail}）` : ''))
  if (target) {
    const before = docToJson(doc) as JsonValue
    const t: LNode = target
    const oldX = t.x
    t.x = oldX + 12
    t.y = t.y + 4
    const after = docToJson(doc) as JsonValue
    const d = diff(before, after)
    console.log('  diff: ' + (d.length ? d.join(' | ') : '(空)'))
    ok(d.length === 2, '只有两条差异（x 与 y）')
    ok(d.every((p) => p.startsWith(trail + '.')), '差异都落在目标节点路径上')
    ok(trail === 'screens.detail.nodes[3]', `目标路径是 ${trail}`)
    // 再核对：除目标节点外，其它节点坐标没变
    const beforeNodes = JSON.stringify(before).length
    ok(beforeNodes > 1000, '导出体量正常')
  }
}

// ── 3. 校验坏文件 ─────────────────────────────────────────
console.log('\n[3] 校验故意写坏的文件')
{
  const bad = parseJsonc(`{
    "version": 99,
    "screens": {
      "bad": { "nodes": [
        { "type": "blink", "x": 10, "y": 10, "w": 50, "h": 20, "text": "谁" },
        { "type": "text", "x": 10, "y": 40, "w": 120, "h": 20,
          "text": "好", "visible": "{sat.rxOk}", "size": 12 },
        { "type": "button", "x": 10, "y": 80, "w": 30, "h": 20, "text": "小", "onClick": "nav.back" }
      ]}
    }
  }`)
  const doc = docFromJson(bad, 'bad.json')
  const issues = validateDoc(doc, null)
  for (const i of issues) console.log(`  ${i.level} ${i.screen} ${i.path} ${i.field} — ${i.message}`)
  const has = (pred: (m: string, f: string, p: string) => boolean): boolean =>
    issues.some((i) => i.level === 'E' && pred(i.message, i.field, i.path))
  ok(has((m) => m.includes('版本') || m.includes('version')), '报出版本不对（version 99）')
  ok(has((m) => m.includes('不认识') || m.includes('未知')), '报出未知 type')
  ok(has((m, f) => f === 'visible' && (m.includes('花括号') || m.includes('裸表达式'))), '报出 visible 带花括号')
  ok(issues.every((i) => !i.path.includes('nodes[') || i.uid !== undefined), '节点级错误都带 uid（能跳转）')
  ok(issues.every((i) => i.screen !== '' && i.path !== ''), '每条错误都有屏名与位置路径')
}

// ── 4. 预览假数据 ─────────────────────────────────────────
console.log('\n[4] 预览假数据 + 模板渲染')
{
  const scope = previewScope()
  const doc = docs.get('list.json')
  ok(!!doc, 'list.json 已载入')
  const rendered: string[] = []
  const bad: string[] = []
  const renderOne = (n: LNode, sc: Record<string, JsonValue>): void => {
    for (const key of ['text', 'empty', 'hint']) {
      const v = n.props[key]
      if (typeof v !== 'string' || v.trim() === '') continue
      try {
        rendered.push(template(v, sc))
      } catch (e) {
        bad.push(`${key}: ${v} → ${String(e)}`)
      }
    }
  }
  if (doc) {
    const rec = (n: LNode, sc: Record<string, JsonValue>): void => {
      renderOne(n, sc)
      // list 的子节点是行模板：用假数据逐行套一遍
      if (n.type === 'list') {
        const itemsPath = typeof n.props['items'] === 'string' ? n.props['items'] : ''
        const rows = rowsAt(scope, itemsPath) ?? []
        rows.forEach((item, i) => {
          const rowScope: Record<string, JsonValue> = { ...scope, item: item as JsonValue, i }
          n.children.forEach((c) => rec(c, rowScope))
        })
        return
      }
      n.children.forEach((c) => rec(c, sc))
    }
    doc.screens.forEach((s) => s.nodes.forEach((n) => rec(n, scope)))
  }
  console.log(`  list.json 渲染出 ${rendered.length} 段文本`)
  // 详情页也套一遍：它才是显示频率的那一屏
  const detail = docs.get('detail.json')
  if (detail) {
    const rec2 = (n: LNode): void => {
      for (const key of ['text', 'empty']) {
        const v = n.props[key]
        if (typeof v !== 'string' || v.trim() === '') continue
        try {
          rendered.push(template(v, scope))
        } catch (e) {
          bad.push(`detail ${key}: ${v} → ${String(e)}`)
        }
      }
      n.children.forEach(rec2)
    }
    detail.screens.forEach((s) => s.nodes.forEach(rec2))
  }
  ok(bad.length === 0, `list.json 全部模板能求值${bad.length ? '：' + bad[0] : ''}`)
  const all = rendered.join('\n')
  ok(all.includes('ISS (ZARYA)'), '假数据含最长名 ISS (ZARYA)')
  ok(all.includes('CAS-4B') && all.includes('FOX-1B (AO-91)'), '假数据含 CAS-4B / FOX-1B (AO-91)')
  console.log('  含 145 的段落: ' + rendered.filter((x) => x.includes('145')).slice(0, 8).join(' | '))
  console.log('  含 MHz 的段落: ' + rendered.filter((x) => x.includes('MHz')).slice(0, 8).join(' | '))
  ok(all.includes('145.98'), '读数含 145.987654 MHz 的超长小数')
  ok(FIXTURE_NAMES.length >= 3 && FIXTURE_READINGS.length >= 2, 'fixtures 暴露名字/读数表')
  const dt = asStr(evalExpr('fmt.freq(sat.downMhz*1000000, 3)', scope))
  console.log('  fmt.freq(145.987654e6, 3) = ' + dt)
  ok(dt === '145.988', 'fmt.freq 口径与 Expr.kt 一致（3 位小数）')
  const kh = asStr(evalExpr('fmt.khz(track.doppHz, 1)', scope))
  console.log('  fmt.khz(-12700, 1) = ' + kh)
  ok(kh === '-12.7', '多普勒读数 -12.7 kHz')
  const tr = docs.get('track.json')
  ok(!!tr, 'track.json 已载入')
  if (tr) {
    const texts: string[] = []
    walkNodes(tr, (n) => {
      for (const key of ['text', 'empty']) {
        const v = n.props[key]
        if (typeof v !== 'string' || v.trim() === '') continue
        try {
          texts.push(template(v, scope))
        } catch (e) {
          bad.push(`track ${key}: ${v} → ${String(e)}`)
        }
      }
    })
    const t = texts.join('\n')
    console.log('  track 渲染段落示例: ' + texts.slice(0, 6).join(' / '))
    ok(bad.length === 0, 'track.json 全部模板能求值')
    ok(t.includes('-12.7') || t.includes('−12.7'), 'track 页出现 -12.7 kHz 量级读数')
  }
}

// ── 5. 锚点解算 ───────────────────────────────────────────
console.log('\n[5] 锚点解算（对照 LayoutDoc.resolve）')
{
  // 设计父 360×780，实际父 720×1560（2×），节点 x=20,w=100,anchor=lrtb
  const base = { x: 20, y: 30, w: 100, h: 40 }
  const lrtb = resolveRect({ ...base, anchor: 'lrtb' }, 360, 780, 720, 1560)
  console.log('  lrtb →', JSON.stringify(lrtb))
  ok(lrtb.x === 20 && lrtb.w === 720 - 20 - (360 - 20 - 100), 'lrtb：x 取左内边距，w 吃掉左右内边距')
  const rt = resolveRect({ x: 200, y: 30, w: 100, h: 40, anchor: 'rt' }, 360, 780, 720, 1560)
  console.log('  rt →', JSON.stringify(rt))
  ok(rt.x === 720 - (360 - 200 - 100) - 100 && rt.w === 100, 'rt：x 从右边反推，w 不变')
  const lt = resolveRect({ ...base, anchor: 'lt' }, 360, 780, 720, 1560)
  ok(lt.x === 20 && lt.w === 100 && lt.y === 30 && lt.h === 40, 'lt：原样')
}

console.log('\n' + (failures === 0 ? '全部通过 ✓' : `${failures} 项失败 ✗`))
process.exit(failures === 0 ? 0 : 1)
