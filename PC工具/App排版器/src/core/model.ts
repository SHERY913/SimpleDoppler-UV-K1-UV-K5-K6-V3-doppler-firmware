/**
 * 文档模型。与 LayoutDoc.kt 一一对应，但多存一个 `uid`：画布与图层树要一个
 * 稳定 key，而 schema 里的 `id` 是可选的、作者还会随手改。`uid` 永不导出。
 */
import { isJsonObject, type JsonObject, type JsonValue } from './jsonc'

export const DESIGN_W = 360
export const DESIGN_H = 780
export const SCHEMA_VERSION = 1

export const TYPES = [
  'group', 'box', 'text', 'button', 'icon', 'image', 'custom',
  'list', 'slider', 'toggle', 'select', 'input', 'progress', 'spacer',
] as const
export type NodeType = (typeof TYPES)[number]

export const RESERVED = [
  'type', 'id', 'name', 'x', 'y', 'w', 'h', 'anchor', 'visible', 'enabled',
  'locked', 'children', 'props',
] as const

export const CONTAINER_TYPES: readonly string[] = ['group', 'box', 'list', 'button']

export interface LNode {
  uid: string
  type: string
  id?: string
  name?: string
  x: number
  y: number
  w: number
  h: number
  anchor: string
  /** 原文件里显式写了 anchor 吗。导出口径：没写过就不补，免得 diff 里全是噪音。 */
  anchorExplicit?: boolean
  /** 原文件把哪些 prop 写进了嵌套 `props` 对象。导出按原样还回去（undefined = 从没有 props 块）。 */
  nested?: string[]
  visible?: string
  enabled?: string
  locked: boolean
  props: JsonObject
  children: LNode[]
}

export interface LScreen {
  id: string
  nodes: LNode[]
}

export interface Doc {
  version: number
  name?: string
  screens: LScreen[]
  /** 顶层有没有 `design` 块、以及值是多少（校验时要用）。 */
  design?: { w: number; h: number }
  /** 顶层多余的键，导出时原样带回去。 */
  extras: JsonObject
}

let uidSeq = 0
export function nextUid(): string {
  uidSeq += 1
  return 'n' + uidSeq.toString(36) + Math.random().toString(36).slice(2, 6)
}

const NUM_KEYS = new Set(['x', 'y', 'w', 'h'])

function num(o: JsonObject, k: string, def: number): number {
  const v = o[k]
  return typeof v === 'number' && Number.isFinite(v) ? v : def
}

/** 从一份已解析的 JSON 建文档。不认识的类型照样留下（排版器要能就地修）。 */
export function docFromJson(root: JsonValue, sourceName: string): Doc {
  const extras: JsonObject = {}
  if (!isJsonObject(root)) {
    return { version: 0, name: sourceName, screens: [], extras }
  }
  for (const [k, v] of Object.entries(root)) {
    if (k !== 'version' && k !== 'name' && k !== 'screens' && k !== 'design') extras[k] = v
  }
  const screens: LScreen[] = []
  const sv = root['screens']
  if (isJsonObject(sv)) {
    for (const [sid, raw] of Object.entries(sv)) {
      const nodes: LNode[] = []
      if (isJsonObject(raw) && Array.isArray(raw['nodes'])) {
        for (const nv of raw['nodes']) {
          const n = nodeFromJson(nv)
          if (n) nodes.push(n)
        }
      }
      screens.push({ id: sid, nodes })
    }
  }
  let design: Doc['design']
  const dv = root['design']
  if (isJsonObject(dv)) design = { w: num(dv, 'w', DESIGN_W), h: num(dv, 'h', DESIGN_H) }
  const nameV = root['name']
  return {
    version: num(root, 'version', 0),
    name: typeof nameV === 'string' ? nameV : undefined,
    screens,
    design,
    extras,
  }
}

function nodeFromJson(v: JsonValue): LNode | null {
  if (!isJsonObject(v)) return null
  const props: JsonObject = {}
  const pv = v['props']
  if (isJsonObject(pv)) for (const [k, x] of Object.entries(pv)) props[k] = x
  for (const [k, x] of Object.entries(v)) {
    if (!(RESERVED as readonly string[]).includes(k)) props[k] = x
  }
  const children: LNode[] = []
  const cv = v['children']
  if (Array.isArray(cv)) {
    for (const c of cv) {
      const n = nodeFromJson(c)
      if (n) children.push(n)
    }
  }
  const typeV = v['type']
  const idV = v['id']
  const nameV = v['name']
  const ancV = v['anchor']
  const visV = v['visible']
  const enV = v['enabled']
  return {
    uid: nextUid(),
    type: typeof typeV === 'string' && typeV !== '' ? typeV : '',
    id: typeof idV === 'string' && idV !== '' ? idV : undefined,
    name: typeof nameV === 'string' && nameV !== '' ? nameV : undefined,
    x: num(v, 'x', 0),
    y: num(v, 'y', 0),
    w: num(v, 'w', 0),
    h: num(v, 'h', 0),
    anchor: typeof ancV === 'string' && ancV !== '' ? ancV : 'lt',
    anchorExplicit: typeof ancV === 'string' && ancV !== '',
    nested: isJsonObject(pv) ? Object.keys(pv) : undefined,
    visible: typeof visV === 'string' && visV !== '' ? visV : undefined,
    enabled: typeof enV === 'string' && enV !== '' ? enV : undefined,
    locked: v['locked'] === true,
    props,
    children,
  }
}

function round(nv: number): number {
  return Math.round(nv * 100) / 100
}

function nodeToJson(n: LNode): JsonObject {
  const o: JsonObject = { type: n.type }
  if (n.id) o['id'] = n.id
  if (n.name) o['name'] = n.name
  o['x'] = round(n.x)
  o['y'] = round(n.y)
  o['w'] = round(n.w)
  o['h'] = round(n.h)
  // 默认锚点 lt 是解析器兜底值，原文件没写就不写回去。
  if (n.anchorExplicit || n.anchor !== 'lt') o['anchor'] = n.anchor
  if (n.visible !== undefined) o['visible'] = n.visible
  if (n.enabled !== undefined) o['enabled'] = n.enabled
  if (n.locked) o['locked'] = true
  const nested: JsonObject = {}
  const nestedKeys = new Set(n.nested ?? [])
  for (const [k, v] of Object.entries(n.props)) {
    if (v === undefined) continue
    if (nestedKeys.has(k)) {
      nested[k] = v
      continue
    }
    if ((RESERVED as readonly string[]).includes(k)) continue
    o[k] = v
  }
  if (n.nested !== undefined) o['props'] = nested
  if (n.children.length > 0) o['children'] = n.children.map(nodeToJson)
  return o
}

export function docToJson(doc: Doc): JsonObject {
  const screens: JsonObject = {}
  for (const s of doc.screens) {
    screens[s.id] = { nodes: s.nodes.map(nodeToJson) } as JsonObject
  }
  const o: JsonObject = { version: doc.version }
  if (doc.name !== undefined) o['name'] = doc.name
  if (doc.design) o['design'] = { w: doc.design.w, h: doc.design.h } as JsonObject
  for (const [k, v] of Object.entries(doc.extras)) o[k] = v
  o['screens'] = screens
  return o
}

export function cloneDoc(doc: Doc): Doc {
  return {
    version: doc.version,
    name: doc.name,
    design: doc.design ? { ...doc.design } : undefined,
    extras: JSON.parse(JSON.stringify(doc.extras)) as JsonObject,
    screens: doc.screens.map((s) => ({ id: s.id, nodes: s.nodes.map(cloneNode) })),
  }
}

export function cloneNode(n: LNode): LNode {
  return {
    ...n,
    uid: n.uid,
    nested: n.nested ? [...n.nested] : undefined,
    props: JSON.parse(JSON.stringify(n.props)) as JsonObject,
    children: n.children.map(cloneNode),
  }
}

export function blankNode(type: NodeType, x = 20, y = 20): LNode {
  const base: LNode = {
    uid: nextUid(), type, x, y, w: 100, h: 40, anchor: 'lt',
    locked: false, props: {}, children: [],
  }
  switch (type) {
    case 'text':
      base.w = 140
      base.h = 20
      base.props = { text: '新文字', size: 13, color: 'txt', maxLines: 1 }
      break
    case 'button':
      base.w = 100
      base.h = 44
      base.props = { text: '按钮', size: 13, align: 'center', radius: 9, fill: 'card', color: 'txt', onClick: '' }
      break
    case 'box':
      base.w = 120
      base.h = 60
      base.props = { fill: 'card', radius: 8 }
      break
    case 'group':
      base.w = 200
      base.h = 120
      base.children = []
      break
    case 'icon':
      base.w = 20
      base.h = 20
      base.props = { name: 'star', color: 'dim', size: 18 }
      break
    case 'image':
      base.w = 64
      base.h = 64
      base.props = { name: 'logo', fill: 'card' }
      break
    case 'custom':
      base.w = 160
      base.h = 160
      base.props = { name: 'dome' }
      break
    case 'list':
      base.w = 360
      base.h = 300
      base.anchor = 'lrtb'
      base.props = { items: 'passes', rowH: 54, gap: 0, empty: '' }
      base.children = []
      break
    case 'slider':
      base.w = 200
      base.h = 30
      base.props = { min: 0, max: 100, value: '0', onChange: '' }
      break
    case 'toggle':
      base.w = 200
      base.h = 32
      base.props = { text: '开关', size: 14, color: 'txt', value: 'false', onChange: '' }
      break
    case 'select':
      base.w = 140
      base.h = 36
      base.props = { options: 'A|B|C', value: '', onChange: '' }
      break
    case 'input':
      base.w = 200
      base.h = 40
      base.props = { value: '', hint: '输入…', fill: 'card', border: 'line', radius: 10 }
      break
    case 'progress':
      base.w = 200
      base.h = 8
      base.props = { value: '0', height: 4, color: 'acc' }
      break
    case 'spacer':
      base.w = 20
      base.h = 20
      break
  }
  return base
}

export function anchorFlags(a: string): { l: boolean; r: boolean; t: boolean; b: boolean } {
  const s = a.toLowerCase()
  return { l: s.includes('l'), r: s.includes('r'), t: s.includes('t'), b: s.includes('b') }
}

export function anchorFromFlags(f: { l: boolean; r: boolean; t: boolean; b: boolean }): string {
  let s = ''
  if (f.l) s += 'l'
  if (f.r) s += 'r'
  if (f.t) s += 't'
  if (f.b) s += 'b'
  return s
}

export interface FlatEntry {
  node: LNode
  parent: LNode | null
  screen: LScreen
  depth: number
  index: number
}

export type NodeIndex = Map<string, FlatEntry>
