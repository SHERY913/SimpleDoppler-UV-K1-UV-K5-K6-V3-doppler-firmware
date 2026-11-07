/**
 * 文档状态。自写 store + useSyncExternalStore —— 一个文档对象就是一次快照，
 * 撤销栈里存的是整份 Doc 的克隆（70 KB 一份，100 步也就 7 MB，完全够用）。
 */
import { useSyncExternalStore } from 'react'
import {
  cloneNode, docFromJson, docToJson, nextUid, type Doc, type LNode, type LScreen,
  type NodeIndex, CONTAINER_TYPES, blankNode, type NodeType,
} from '../core/model'
import { isJsonObject, parseJsonc, stringifyJson, JsonSyntaxError } from '../core/jsonc'
import { validateDoc, type Issue } from '../core/validate'
import type { FlatEntry } from '../core/model'

export type Mode = 'edit' | 'preview'
export type DialogKind = 'validate' | 'shortcuts' | 'binding' | null

export interface BindingTarget {
  uid: string
  key: string
  mode: 'template' | 'expr'
}

export interface Toast {
  id: number
  msg: string
  kind: 'ok' | 'err'
}

export interface EditorState {
  doc: Doc | null
  fileName: string
  rootError: string | null
  screenId: string | null
  selection: string[]
  mode: Mode
  grid: number
  zoom: number
  showGuides: boolean
  snap: boolean
  expanded: Record<string, boolean>
  past: Doc[]
  future: Doc[]
  dirty: boolean
  issues: Issue[] | null
  dialog: DialogKind
  binding: BindingTarget | null
  previewLog: { t: number; msg: string }[]
  toast: Toast | null
  lastSaved: string | null
}

const UNDO_LIMIT = 120

function buildIndex(doc: Doc): NodeIndex {
  const m: NodeIndex = new Map()
  const walk = (nodes: LNode[], parent: LNode | null, screen: LScreen, depth: number): void => {
    nodes.forEach((n, i) => {
      m.set(n.uid, { node: n, parent, screen, depth, index: i })
      walk(n.children, n, screen, depth + 1)
    })
  }
  for (const s of doc.screens) walk(s.nodes, null, s, 0)
  return m
}

class Store {
  /** 只读快照。写入一律走 set()/mutate()，这样撤销栈才不会漏步。 */
  state: EditorState = {
    doc: null,
    fileName: 'layout.json',
    rootError: null,
    screenId: null,
    selection: [],
    mode: 'edit',
    grid: 8,
    zoom: 1,
    showGuides: true,
    snap: true,
    expanded: {},
    past: [],
    future: [],
    dirty: false,
    issues: null,
    dialog: null,
    binding: null,
    previewLog: [],
    toast: null,
    lastSaved: null,
  }

  private listeners = new Set<() => void>()
  private indexDoc: Doc | null = null
  private indexCache: NodeIndex = new Map()
  private clipboard: LNode[] = []
  private styleClip: Record<string, unknown> | null = null
  private toastSeq = 0

  getState = (): EditorState => this.state

  subscribe = (l: () => void): (() => void) => {
    this.listeners.add(l)
    return () => {
      this.listeners.delete(l)
    }
  }

  private set(patch: Partial<EditorState>): void {
    this.state = { ...this.state, ...patch }
    for (const l of this.listeners) l()
  }

  index(): NodeIndex {
    const doc = this.state.doc
    if (!doc) return new Map()
    if (this.indexDoc !== doc) {
      this.indexCache = buildIndex(doc)
      this.indexDoc = doc
    }
    return this.indexCache
  }

  entry(uid: string): FlatEntry | null {
    return this.index().get(uid) ?? null
  }

  node(uid: string): LNode | null {
    return this.entry(uid)?.node ?? null
  }

  screen(): LScreen | null {
    const doc = this.state.doc
    if (!doc) return null
    return doc.screens.find((s) => s.id === this.state.screenId) ?? doc.screens[0] ?? null
  }

  toast(msg: string, kind: 'ok' | 'err' = 'ok'): void {
    this.toastSeq += 1
    const t: Toast = { id: this.toastSeq, msg, kind }
    this.set({ toast: t })
    window.setTimeout(() => {
      if (this.state.toast?.id === t.id) this.set({ toast: null })
    }, 2600)
  }

  // ── 打开 / 导出 ────────────────────────────────────────────

  openText(text: string, fileName: string): boolean {
    let root: unknown
    try {
      root = parseJsonc(text)
    } catch (e) {
      const msg = e instanceof JsonSyntaxError ? e.message : String(e)
      this.toast(`解析失败：${msg}`, 'err')
      return false
    }
    let rootError: string | null = null
    if (!isJsonObject(root)) rootError = '根节点必须是对象'
    else if (!isJsonObject(root['screens'])) rootError = 'screens 必须是对象（不是数组），App 端会拒载'
    const doc = docFromJson(root as never, fileName)
    const firstScreen = doc.screens[0]?.id ?? null
    this.set({
      doc,
      fileName,
      rootError,
      screenId: firstScreen,
      selection: [],
      past: [],
      future: [],
      dirty: false,
      issues: null,
      mode: 'edit',
      zoom: 1,
      lastSaved: null,
      previewLog: [],
    })
    this.toast(`已载入 ${fileName}：${doc.screens.length} 屏 / ${this.index().size} 个节点`)
    return true
  }

  exportText(): string | null {
    const doc = this.state.doc
    if (!doc) return null
    return stringifyJson(docToJson(doc))
  }

  exportDownload(): void {
    const text = this.exportText()
    if (text === null) return
    const blob = new Blob([text], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = this.state.fileName || 'layout.json'
    a.click()
    URL.revokeObjectURL(a.href)
    this.set({ dirty: false, lastSaved: new Date().toLocaleTimeString() })
    this.toast(`已下载 ${this.state.fileName}`)
  }

  // ── 可变操作 ───────────────────────────────────────────────

  private mutate(fn: (doc: Doc) => void): void {
    const cur = this.state.doc
    if (!cur) return
    const next = clone2(cur)
    fn(next)
    const past = [...this.state.past, cur]
    if (past.length > UNDO_LIMIT) past.splice(0, past.length - UNDO_LIMIT)
    this.set({ doc: next, past, future: [], dirty: true })
  }

  updateNode(uid: string, patch: Partial<Pick<LNode, 'type' | 'id' | 'name' | 'x' | 'y' | 'w' | 'h' | 'anchor' | 'visible' | 'enabled' | 'locked'>>): void {
    this.mutate((doc) => {
      const n = find(doc, uid)
      if (!n) return
      Object.assign(n, patch)
    })
  }

  updateProp(uid: string, key: string, value: unknown): void {
    this.mutate((doc) => {
      const n = find(doc, uid)
      if (!n) return
      if (value === undefined) delete n.props[key]
      else n.props[key] = value as never
    })
  }

  moveBy(uids: string[], dx: number, dy: number): void {
    if (uids.length === 0 || (dx === 0 && dy === 0)) return
    this.mutate((doc) => {
      for (const uid of uids) {
        const n = find(doc, uid)
        if (!n || n.locked) continue
        n.x = round2(n.x + dx)
        n.y = round2(n.y + dy)
      }
    })
  }

  setRect(uid: string, r: { x: number; y: number; w: number; h: number }): void {
    this.mutate((doc) => {
      const n = find(doc, uid)
      if (!n) return
      n.x = round2(r.x)
      n.y = round2(r.y)
      n.w = round2(Math.max(1, r.w))
      n.h = round2(Math.max(1, r.h))
    })
  }

  addNode(type: NodeType, parentUid: string | null): string {
    let uid = ''
    this.mutate((doc) => {
      const parent = parentUid ? find(doc, parentUid) : null
      const n = blankNode(type, parent ? Math.round(parent.w / 2 - 50) : 120, parent ? Math.round(parent.h / 2 - 20) : 300)
      uid = n.uid
      if (parent && CONTAINER_TYPES.includes(parent.type)) parent.children.push(n)
      else {
        const s = doc.screens.find((x) => x.id === this.state.screenId) ?? doc.screens[0]
        s?.nodes.push(n)
      }
    })
    if (uid) this.set({ selection: [uid] })
    return uid
  }

  deleteSelected(): void {
    const sel = new Set(this.state.selection)
    if (sel.size === 0) return
    this.mutate((doc) => {
      const prune = (nodes: LNode[]): LNode[] =>
        nodes.filter((n) => !sel.has(n.uid)).map((n) => {
          n.children = prune(n.children)
          return n
        })
      for (const s of doc.screens) s.nodes = prune(s.nodes)
    })
    this.set({ selection: [] })
  }

  duplicateSelected(): void {
    const roots = this.topSelection()
    if (roots.length === 0) return
    const newSel: string[] = []
    this.mutate((doc) => {
      for (const uid of roots) {
        const e = this.entry(uid)
        if (!e) continue
        const copy = cloneNode(e.node)
        reuid(copy)
        copy.x += 8
        copy.y += 8
        copy.name = (copy.name ?? copy.type) + ' 副本'
        if (e.parent) {
          const p = find(doc, e.parent.uid)
          p?.children.splice(e.index + 1, 0, copy)
        } else {
          const s = doc.screens.find((x) => x.id === e.screen.id)
          s?.nodes.splice(e.index + 1, 0, copy)
        }
        newSel.push(copy.uid)
      }
    })
    this.set({ selection: newSel })
  }

  copy(): void {
    const roots = this.topSelection()
    const nodes: LNode[] = []
    for (const uid of roots) {
      const e = this.entry(uid)
      if (e) nodes.push(cloneNode(e.node))
    }
    if (nodes.length === 0) return
    this.clipboard = nodes
    this.toast(`已复制 ${nodes.length} 个节点`)
  }

  paste(): void {
    if (this.clipboard.length === 0) return
    const target = this.state.selection[0]
    const te = target ? this.entry(target) : null
    const parentUid = te ? (CONTAINER_TYPES.includes(te.node.type) ? te.node.uid : te.parent?.uid ?? null) : null
    const newSel: string[] = []
    this.mutate((doc) => {
      for (const src of this.clipboard) {
        const c = cloneNode(src)
        reuid(c)
        c.x += 10
        c.y += 10
        const parent = parentUid ? find(doc, parentUid) : null
        if (parent && CONTAINER_TYPES.includes(parent.type)) parent.children.push(c)
        else {
          const s = doc.screens.find((x) => x.id === this.state.screenId) ?? doc.screens[0]
          s?.nodes.push(c)
        }
        newSel.push(c.uid)
      }
    })
    this.set({ selection: newSel })
    this.toast(`已粘贴 ${this.clipboard.length} 个节点`)
  }

  copyStyle(): void {
    const n = this.node(this.state.selection[0] ?? '')
    if (!n) return
    this.styleClip = JSON.parse(JSON.stringify(n.props)) as Record<string, unknown>
    this.toast('已复制样式')
  }

  pasteStyle(): void {
    if (!this.styleClip) return
    const style = this.styleClip
    this.mutate((doc) => {
      for (const uid of this.state.selection) {
        const n = find(doc, uid)
        if (!n) continue
        for (const [k, v] of Object.entries(style)) n.props[k] = v as never
      }
    })
    this.toast('已粘贴样式')
  }

  reorder(uid: string, parentUid: string | null, index: number): void {
    this.mutate((doc) => {
      const target = find(doc, uid)
      if (!target) return
      const e = this.entry(uid)
      let curParent: LNode | null = null
      let container: LNode[] | null = null
      if (e) {
        if (e.parent) {
          curParent = find(doc, e.parent.uid)
          container = curParent ? curParent.children : null
        } else {
          const s = doc.screens.find((x) => x.id === e.screen.id) ?? null
          container = s ? s.nodes : null
        }
      }
      if (!container) return
      const from = container.findIndex((n) => n.uid === uid)
      if (from >= 0) container.splice(from, 1)
      let dest = parentUid ? find(doc, parentUid) : null
      if (dest && !CONTAINER_TYPES.includes(dest.type)) dest = null
      const destArr = dest ? dest.children : (doc.screens.find((x) => x.id === this.state.screenId) ?? doc.screens[0])?.nodes
      if (!destArr) return
      let at = index
      if (destArr === container && from >= 0 && from < index) at -= 1
      at = Math.max(0, Math.min(destArr.length, at))
      destArr.splice(at, 0, target)
      if (dest && e) {
        // 换父节点时坐标要换算到新父的局部坐标
        if (curParent !== dest) {
          const dx = (e.parent?.x ?? 0) - dest.x
          const dy = (e.parent?.y ?? 0) - dest.y
          target.x = round2(target.x + dx)
          target.y = round2(target.y + dy)
        }
      }
    })
  }

  groupSelected(): void {
    const sel = this.state.selection
    if (sel.length === 0) return
    const entries = sel.map((u) => this.entry(u)).filter((e): e is FlatEntry => e !== null)
    if (entries.length === 0) return
    // 只取顶层（不要把子和父一起搬）
    const set = new Set(sel)
    const roots = entries.filter((e) => {
      let p = e.parent
      while (p) {
        if (set.has(p.uid)) return false
        p = this.entry(p.uid)?.parent ?? null
      }
      return true
    })
    const minX = Math.min(...roots.map((e) => e.node.x))
    const minY = Math.min(...roots.map((e) => e.node.y))
    const maxX = Math.max(...roots.map((e) => e.node.x + e.node.w))
    const maxY = Math.max(...roots.map((e) => e.node.y + e.node.h))
    let gid = ''
    this.mutate((doc) => {
      const screen = doc.screens.find((x) => x.id === this.state.screenId) ?? doc.screens[0]
      if (!screen) return
      const g = blankNode('group', minX, minY)
      g.w = Math.max(20, maxX - minX)
      g.h = Math.max(20, maxY - minY)
      g.name = '分组'
      gid = g.uid
      const moved: LNode[] = []
      for (const e of roots) {
        const n = find(doc, e.node.uid)
        if (!n) continue
        n.x = round2(n.x - minX)
        n.y = round2(n.y - minY)
        moved.push(n)
      }
      // 从原容器摘掉
      const strip = (nodes: LNode[]): LNode[] =>
        nodes.filter((n) => !moved.some((m) => m.uid === n.uid)).map((n) => {
          n.children = strip(n.children)
          return n
        })
      screen.nodes = strip(screen.nodes)
      g.children = moved
      screen.nodes.push(g)
    })
    if (gid) this.set({ selection: [gid] })
  }

  ungroup(uid: string): void {
    const e = this.entry(uid)
    if (!e || e.node.type !== 'group') return
    this.mutate((doc) => {
      const parent = e.parent ? find(doc, e.parent.uid) : null
      const screen = doc.screens.find((x) => x.id === e.screen.id)
      const arr = parent ? parent.children : screen?.nodes
      if (!arr) return
      const g = arr.find((n) => n.uid === uid)
      if (!g) return
      const idx = arr.indexOf(g)
      const kids = g.children
      for (const k of kids) {
        k.x = round2(k.x + g.x)
        k.y = round2(k.y + g.y)
      }
      arr.splice(idx, 1, ...kids)
    })
    this.set({ selection: [] })
  }

  // ── 选择 ──────────────────────────────────────────────────

  select(uid: string, additive = false): void {
    const cur = this.state.selection
    if (!additive) {
      this.set({ selection: [uid] })
      return
    }
    this.set({ selection: cur.includes(uid) ? cur.filter((u) => u !== uid) : [...cur, uid] })
  }

  selectMany(uids: string[], additive = false): void {
    if (!additive) {
      this.set({ selection: uids })
      return
    }
    const s = new Set(this.state.selection)
    for (const u of uids) s.add(u)
    this.set({ selection: [...s] })
  }

  clearSelection(): void {
    if (this.state.selection.length === 0) return
    this.set({ selection: [] })
  }

  topSelection(): string[] {
    const set = new Set(this.state.selection)
    const out: string[] = []
    for (const uid of this.state.selection) {
      let p = this.entry(uid)?.parent ?? null
      let nested = false
      while (p) {
        if (set.has(p.uid)) {
          nested = true
          break
        }
        p = this.entry(p.uid)?.parent ?? null
      }
      if (!nested) out.push(uid)
    }
    return out
  }

  // ── 视图 ──────────────────────────────────────────────────

  setScreen(id: string): void {
    this.set({ screenId: id, selection: [] })
  }

  setMode(mode: Mode): void {
    this.set({ mode, selection: mode === 'preview' ? [] : this.state.selection })
  }

  setGrid(g: number): void {
    this.set({ grid: g })
  }

  setZoom(z: number): void {
    this.set({ zoom: Math.max(0.25, Math.min(4, z)) })
  }

  toggleSnap(): void {
    this.set({ snap: !this.state.snap })
  }

  toggleGuides(): void {
    this.set({ showGuides: !this.state.showGuides })
  }

  setDialog(d: DialogKind): void {
    this.set({ dialog: d })
  }

  setBinding(b: BindingTarget | null): void {
    this.set({ binding: b, dialog: b ? 'binding' : null })
  }

  toggleExpanded(uid: string): void {
    const cur = this.state.expanded[uid] !== false
    this.set({ expanded: { ...this.state.expanded, [uid]: !cur } })
  }

  isExpanded(uid: string): boolean {
    return this.state.expanded[uid] !== false
  }

  setExpanded(uid: string, open: boolean): void {
    this.set({ expanded: { ...this.state.expanded, [uid]: open } })
  }

  expandTo(uid: string): void {
    const next = { ...this.state.expanded }
    let e = this.entry(uid)
    while (e && e.parent) {
      next[e.parent.uid] = true
      e = this.entry(e.parent.uid)
    }
    this.set({ expanded: next })
  }

  // ── 撤销 ──────────────────────────────────────────────────

  undo(): void {
    const past = this.state.past
    const doc = this.state.doc
    if (past.length === 0 || !doc) return
    const prev = past[past.length - 1]
    const ids = new Set<string>()
    for (const [k] of buildIndex(prev)) ids.add(k)
    this.set({
      doc: prev,
      past: past.slice(0, -1),
      future: [doc, ...this.state.future].slice(0, UNDO_LIMIT),
      selection: this.state.selection.filter((u) => ids.has(u)),
      dirty: true,
    })
  }

  redo(): void {
    const fut = this.state.future
    const doc = this.state.doc
    if (fut.length === 0 || !doc) return
    const nx = fut[0]
    this.set({
      doc: nx,
      past: [...this.state.past, doc].slice(-UNDO_LIMIT),
      future: fut.slice(1),
      dirty: true,
    })
  }

  // ── 校验 ──────────────────────────────────────────────────

  runValidate(): Issue[] {
    const doc = this.state.doc
    if (!doc) return []
    const issues = validateDoc(doc, this.state.rootError)
    this.set({ issues, dialog: 'validate' })
    return issues
  }

  jumpTo(uid: string | null): void {
    if (!uid) return
    const e = this.entry(uid)
    if (!e) return
    this.expandTo(uid)
    this.set({ screenId: e.screen.id, selection: [uid], dialog: null, mode: 'edit', zoom: 1 })
  }

  logPreview(msg: string): void {
    const log = [...this.state.previewLog, { t: Date.now(), msg }].slice(-80)
    this.set({ previewLog: log })
  }
}

function round2(v: number): number {
  return Math.round(v * 100) / 100
}

function reuid(n: LNode): void {
  n.uid = nextUid()
  for (const c of n.children) reuid(c)
}

function find(doc: Doc, uid: string): LNode | null {
  const walk = (nodes: LNode[]): LNode | null => {
    for (const n of nodes) {
      if (n.uid === uid) return n
      const hit = walk(n.children)
      if (hit) return hit
    }
    return null
  }
  for (const s of doc.screens) {
    const hit = walk(s.nodes)
    if (hit) return hit
  }
  return null
}

function clone2(doc: Doc): Doc {
  return {
    version: doc.version,
    name: doc.name,
    design: doc.design ? { ...doc.design } : undefined,
    extras: JSON.parse(JSON.stringify(doc.extras)) as never,
    screens: doc.screens.map((s) => ({ id: s.id, nodes: s.nodes.map(cloneNode) })),
  }
}

export const store = new Store()

export function useEditor(): EditorState {
  return useSyncExternalStore(store.subscribe, store.getState, store.getState)
}
