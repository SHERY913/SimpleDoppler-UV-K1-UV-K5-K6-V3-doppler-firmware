/**
 * Expr.kt 的逐条移植。行数不多，但必须一比一：预览里的每个读数都是这里算出来的，
 * 差一个 toFixed 就会让作者以为版面错了。
 *
 * 不执行任意代码：只有路径取数 + 白名单函数 + 算术比较，与 App 端同构。
 */
import type { JsonValue } from './jsonc'

export type Val =
  | { k: 'z' }
  | { k: 'b'; v: boolean }
  | { k: 'n'; v: number }
  | { k: 's'; v: string }

export const VNULL: Val = { k: 'z' }
export const vNum = (v: number): Val => ({ k: 'n', v })
export const vStr = (v: string): Val => ({ k: 's', v })
export const vBool = (v: boolean): Val => ({ k: 'b', v })

export class ExprError extends Error {}

export function asStr(v: Val): string {
  switch (v.k) {
    case 'z':
      return ''
    case 'b':
      return v.v ? 'true' : 'false'
    case 'n':
      return fmtNum(v.v)
    case 's':
      return v.v
  }
}

export function asNum(v: Val): number {
  switch (v.k) {
    case 'n':
      return v.v
    case 'b':
      return v.v ? 1 : 0
    case 's': {
      const n = Number(v.v)
      return v.v.trim() === '' || !Number.isFinite(n) ? 0 : n
    }
    case 'z':
      return 0
  }
}

export function asBool(v: Val): boolean {
  switch (v.k) {
    case 'b':
      return v.v
    case 'n':
      return v.v !== 0
    case 's':
      return v.v.length > 0
    case 'z':
      return false
  }
}

export function fmtNum(v: number, digits?: number): string {
  if (Number.isNaN(v)) return '—'
  if (!Number.isFinite(v)) return v > 0 ? '∞' : '−∞'
  if (digits !== undefined) {
    const d = Math.max(0, Math.min(9, Math.trunc(digits)))
    return v.toFixed(d)
  }
  if (v === Math.trunc(v) && Math.abs(v) < 1e15) return String(Math.trunc(v))
  return v.toFixed(6).replace(/0+$/, '').replace(/\.$/, '')
}

/** 与 Kotlin SimpleDateFormat("HH:mm") 等价，走本机时区。 */
function pad2(n: number): string {
  return String(n).padStart(2, '0')
}
const Times = {
  hhmm(ms: number): string {
    if (ms <= 0) return '—'
    const d = new Date(ms)
    return pad2(d.getHours()) + ':' + pad2(d.getMinutes())
  },
  hhmmss(ms: number): string {
    if (ms <= 0) return '—'
    const d = new Date(ms)
    return pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds())
  },
  mmdd(ms: number): string {
    if (ms <= 0) return '—'
    const d = new Date(ms)
    return pad2(d.getMonth() + 1) + '-' + pad2(d.getDate())
  },
  dur(sec: number): string {
    if (sec <= 0) return '0 秒'
    const h = Math.trunc(sec / 3600)
    const m = Math.trunc((sec % 3600) / 60)
    const s = Math.trunc(sec % 60)
    if (h > 0) return `${h} 时 ${pad2(m)} 分`
    if (m > 0) return `${m} 分 ${pad2(s)} 秒`
    return `${s} 秒`
  },
  countdown(ms: number): string {
    if (ms <= 0) return '已开始'
    const sec = Math.trunc(ms / 1000)
    if (sec < 60) return `还有 ${sec} 秒`
    return '还有 ' + Times.dur(sec)
  },
}

function cmpVals(x: Val, y: Val): number {
  if (x.k === 's' || y.k === 's') {
    const a = asStr(x)
    const b = asStr(y)
    return a < b ? -1 : a > b ? 1 : 0
  }
  const a = asNum(x)
  const b = asNum(y)
  return a < b ? -1 : a > b ? 1 : 0
}

function callFn(name: string, a: Val[]): Val {
  const n = (i: number, d = 0): number => (a[i] === undefined ? d : asNum(a[i]))
  const s = (i: number, d = ''): string => (a[i] === undefined ? d : asStr(a[i]))
  switch (name) {
    case 'if':
      return asBool(a[0] ?? VNULL) ? a[1] ?? VNULL : a[2] ?? VNULL
    case 'fmt.freq': {
      const hz = n(0)
      const digits = Math.max(0, Math.min(9, Math.trunc(n(1, 5))))
      return hz <= 0 ? vStr('—') : vStr(fmtNum(hz / 1e6, digits))
    }
    case 'fmt.khz': {
      const hz = n(0)
      return hz === 0 ? vStr('—') : vStr(fmtNum(hz / 1e3, Math.trunc(n(1, 4))))
    }
    case 'fmt.num': {
      const d = Math.trunc(n(1, -1))
      return vStr(d < 0 ? fmtNum(n(0)) : fmtNum(n(0), d))
    }
    case 'fmt.int':
      return vStr(String(Math.trunc(n(0))))
    case 'fmt.time':
      return vStr(Times.hhmm(Math.trunc(n(0))))
    case 'fmt.timeS':
      return vStr(Times.hhmmss(Math.trunc(n(0))))
    case 'fmt.date':
      return vStr(Times.mmdd(Math.trunc(n(0))))
    case 'fmt.dur':
      return vStr(Times.dur(Math.trunc(n(0))))
    case 'fmt.countdown':
      return vStr(Times.countdown(Math.trunc(n(0))))
    case 'fmt.az':
    case 'fmt.el':
      return vStr(fmtNum(n(0), 0) + '°')
    case 'fmt.tone': {
      const hz = n(0)
      return hz <= 0 ? vStr('无亚音') : vStr(fmtNum(hz, 1) + ' Hz')
    }
    case 'fmt.pct':
      return vStr(fmtNum(n(0) * 100, Math.trunc(n(1, 0))) + '%')
    case 'abs':
      return vNum(Math.abs(n(0)))
    case 'min':
      return vNum(Math.min(n(0), n(1)))
    case 'max':
      return vNum(Math.max(n(0), n(1)))
    case 'round':
      return vNum(Math.round(n(0)))
    case 'floor':
      return vNum(Math.floor(n(0)))
    case 'ceil':
      return vNum(Math.ceil(n(0)))
    case 'clamp': {
      const lo = Math.min(n(1), n(2))
      const hi = Math.max(n(1), n(2))
      return vNum(Math.min(hi, Math.max(lo, n(0))))
    }
    case 'sign':
      return vNum(Math.sign(n(0)))
    case 'default': {
      const v = a[0]
      if (v === undefined || v.k === 'z' || asStr(v).length === 0) return a[1] ?? VNULL
      return v
    }
    case 'concat':
      return vStr(a.map(asStr).join(''))
    case 'upper':
      return vStr(s(0).toUpperCase())
    case 'lower':
      return vStr(s(0).toLowerCase())
    case 'len':
      return vNum(s(0).length)
    case 'sub': {
      const str = s(0)
      const from = Math.max(0, Math.min(str.length, Math.trunc(n(1))))
      const to = Math.max(from, Math.min(str.length, Math.trunc(n(2, str.length))))
      return vStr(str.slice(from, to))
    }
    case 'trim':
      return vStr(s(0).trim())
    case 'ellipsis': {
      const str = s(0)
      const max = Math.max(1, Math.trunc(n(1, 12)))
      return str.length <= max ? vStr(str) : vStr(str.slice(0, max - 1) + '…')
    }
    case 'not':
      return vBool(!asBool(a[0] ?? VNULL))
    case 'and':
      return vBool(a.every(asBool))
    case 'or':
      return vBool(a.some(asBool))
    case '|signed': {
      const v = n(0)
      const d = a.length > 1 ? Math.trunc(n(1, 1)) : 1
      const str = fmtNum(Math.abs(v), d)
      return vStr(v > 0 ? '+' + str : v < 0 ? '−' + str : str)
    }
    case '|fixed':
      return vStr(fmtNum(n(0), a.length > 1 ? Math.trunc(n(1, 2)) : 2))
    case '|int':
      return vStr(String(Math.trunc(n(0))))
    case '|pad2':
      return vStr(String(Math.max(0, Math.trunc(n(0)))).padStart(2, '0'))
    case '|upper':
      return vStr(s(0).toUpperCase())
    case '|lower':
      return vStr(s(0).toLowerCase())
    case '|trim':
      return vStr(s(0).trim())
    case '|ellipsis':
      return callFn('ellipsis', a)
    case '|pct':
      return vStr(fmtNum(n(0) * 100, a.length > 1 ? Math.trunc(n(1, 0)) : 0) + '%')
    case '|deg':
      return vStr(fmtNum(n(0), 0) + '°')
    case '|default':
      return callFn('default', a)
    default:
      return VNULL
  }
}

// ── 词法 ────────────────────────────────────────────────────
type TokKind = 'num' | 'str' | 'ident' | 'op' | 'end'
interface Tok {
  t: TokKind
  text: string
  num: number
}

function isDigit(c: string): boolean {
  return c >= '0' && c <= '9'
}
function isLetter(c: string): boolean {
  return /[A-Za-z_\u00C0-\uFFFF]/.test(c)
}

function lex(s: string): Tok[] {
  const out: Tok[] = []
  let i = 0
  while (i < s.length) {
    const c = s[i]
    if (/\s/.test(c)) {
      i++
      continue
    }
    if (isDigit(c) || (c === '.' && i + 1 < s.length && isDigit(s[i + 1]))) {
      const st = i
      while (i < s.length && (isDigit(s[i]) || s[i] === '.')) i++
      const text = s.slice(st, i)
      const num = Number(text)
      out.push({ t: 'num', text, num: Number.isFinite(num) ? num : 0 })
      continue
    }
    if (c === '"' || c === "'") {
      const q = c
      i++
      let sb = ''
      while (i < s.length && s[i] !== q) {
        if (s[i] === '\\' && i + 1 < s.length) {
          i++
          sb += s[i]
        } else sb += s[i]
        i++
      }
      if (i < s.length) i++
      out.push({ t: 'str', text: sb, num: 0 })
      continue
    }
    if (isLetter(c)) {
      const st = i
      while (i < s.length && (isLetter(s[i]) || isDigit(s[i]) || s[i] === '_' || s[i] === '.')) i++
      out.push({ t: 'ident', text: s.slice(st, i), num: 0 })
      continue
    }
    const two = s.slice(i, i + 2)
    if (['==', '!=', '<=', '>=', '&&', '||'].includes(two)) {
      out.push({ t: 'op', text: two, num: 0 })
      i += 2
      continue
    }
    out.push({ t: 'op', text: c, num: 0 })
    i++
  }
  out.push({ t: 'end', text: '', num: 0 })
  return out
}

export type Scope = JsonValue | Record<string, JsonValue> | null

type Fn = (scope: Scope) => Val

function pathOf(obj: Scope, path: string): Val {
  let cur: unknown = obj
  for (const part of path.split('.')) {
    if (cur === null || cur === undefined) return VNULL
    if (Array.isArray(cur)) {
      const idx = Number(part)
      cur = Number.isFinite(idx) ? cur[idx] : undefined
    } else if (typeof cur === 'object') {
      cur = (cur as Record<string, unknown>)[part]
    } else return VNULL
    if (cur === undefined) return VNULL
  }
  if (cur === null || cur === undefined) return VNULL
  if (typeof cur === 'boolean') return vBool(cur)
  if (typeof cur === 'number') return vNum(cur)
  if (typeof cur === 'string') return vStr(cur)
  return VNULL
}

class Parser {
  private toks: Tok[]
  private p = 0
  constructor(toks: Tok[]) {
    this.toks = toks
  }
  private peek(): Tok {
    return this.toks[this.p] ?? { t: 'end', text: '', num: 0 }
  }
  private next(): Tok {
    const t = this.peek()
    this.p++
    return t
  }
  private isOp(s: string): boolean {
    const t = this.peek()
    return t.t === 'op' && t.text === s
  }
  atEnd(): boolean {
    return this.peek().t === 'end'
  }
  peekText(): string {
    return this.peek().text
  }

  expr(): Fn {
    return this.ternary()
  }
  private ternary(): Fn {
    const c = this.or()
    if (this.isOp('?')) {
      this.next()
      const t = this.ternary()
      if (!this.isOp(':')) throw new ExprError("三元表达式缺 ':'")
      this.next()
      const f = this.ternary()
      return (s) => (asBool(c(s)) ? t(s) : f(s))
    }
    return c
  }
  private or(): Fn {
    let l = this.and()
    while (this.isOp('||')) {
      this.next()
      const r = this.and()
      const prev = l
      l = (s) => vBool(asBool(prev(s)) || asBool(r(s)))
    }
    return l
  }
  private and(): Fn {
    let l = this.eq()
    while (this.isOp('&&')) {
      this.next()
      const r = this.eq()
      const prev = l
      l = (s) => vBool(asBool(prev(s)) && asBool(r(s)))
    }
    return l
  }
  private eq(): Fn {
    let l = this.rel()
    while (this.isOp('==') || this.isOp('!=')) {
      const op = this.next().text
      const r = this.rel()
      const prev = l
      l = (s) => vBool(op === '==' ? cmpVals(prev(s), r(s)) === 0 : cmpVals(prev(s), r(s)) !== 0)
    }
    return l
  }
  private rel(): Fn {
    let l = this.add()
    while (this.isOp('<') || this.isOp('<=') || this.isOp('>') || this.isOp('>=')) {
      const op = this.next().text
      const r = this.add()
      const prev = l
      l = (s) => {
        const x = asNum(prev(s))
        const y = asNum(r(s))
        switch (op) {
          case '<':
            return vBool(x < y)
          case '<=':
            return vBool(x <= y)
          case '>':
            return vBool(x > y)
          default:
            return vBool(x >= y)
        }
      }
    }
    return l
  }
  private add(): Fn {
    let l = this.mul()
    for (;;) {
      if (this.isOp('+')) {
        this.next()
        const r = this.mul()
        const prev = l
        l = (s) => {
          const x = prev(s)
          const y = r(s)
          return x.k === 's' || y.k === 's' ? vStr(asStr(x) + asStr(y)) : vNum(asNum(x) + asNum(y))
        }
        continue
      }
      if (this.isOp('-')) {
        this.next()
        const r = this.mul()
        const prev = l
        l = (s) => vNum(asNum(prev(s)) - asNum(r(s)))
        continue
      }
      return l
    }
  }
  private mul(): Fn {
    let l = this.unary()
    for (;;) {
      const op = this.peek().text
      if (this.peek().t !== 'op' || !['*', '/', '%'].includes(op)) return l
      this.next()
      const r = this.unary()
      const prev = l
      l = (s) => {
        const x = asNum(prev(s))
        const y = asNum(r(s))
        if (op === '*') return vNum(x * y)
        if (y === 0) return vNum(0)
        return vNum(op === '/' ? x / y : x % y)
      }
    }
  }
  private unary(): Fn {
    const t = this.peek()
    if (t.t === 'op' && (t.text === '!' || t.text === '-' || t.text === '+')) {
      this.next()
      const a = this.unary()
      if (t.text === '!') return (s) => vBool(!asBool(a(s)))
      if (t.text === '-') return (s) => vNum(-asNum(a(s)))
      return a
    }
    return this.postfix()
  }
  private postfix(): Fn {
    let n = this.primary()
    while (this.isOp('|')) {
      this.next()
      const tok = this.next()
      if (tok.t !== 'ident') throw new ExprError('管道后面要写过滤器名')
      const name = tok.text
      const args = this.isOp('(') ? this.argsList() : []
      const base = n
      n = (s) => callFn('|' + name, [base(s), ...args.map((f) => f(s))])
    }
    return n
  }
  private argsList(): Fn[] {
    this.next() // (
    const out: Fn[] = []
    if (this.isOp(')')) {
      this.next()
      return out
    }
    for (;;) {
      out.push(this.expr())
      if (this.isOp(',')) {
        this.next()
        continue
      }
      if (this.isOp(')')) {
        this.next()
        return out
      }
      throw new ExprError("函数参数缺 ',' 或 ')'")
    }
  }
  private primary(): Fn {
    const tok = this.next()
    switch (tok.t) {
      case 'num': {
        const v = tok.num
        return () => vNum(v)
      }
      case 'str': {
        const v = tok.text
        return () => vStr(v)
      }
      case 'ident': {
        if (this.isOp('(')) {
          const name = tok.text
          const args = this.argsList()
          return (s) => callFn(name, args.map((f) => f(s)))
        }
        if (tok.text === 'true') return () => vBool(true)
        if (tok.text === 'false') return () => vBool(false)
        if (tok.text === 'null') return () => VNULL
        const path = tok.text
        return (s) => pathOf(s, path)
      }
      case 'op': {
        if (tok.text === '(') {
          const e = this.expr()
          if (!this.isOp(')')) throw new ExprError("缺 ')'")
          this.next()
          return e
        }
        throw new ExprError(`意外的记号 '${tok.text}'`)
      }
      default:
        throw new ExprError('表达式不完整')
    }
  }
}

const cache = new Map<string, Fn | ExprError>()

function compile(src: string): Fn {
  const hit = cache.get(src)
  if (hit instanceof ExprError) throw hit
  if (hit) return hit
  try {
    const p = new Parser(lex(src))
    const fn = p.expr()
    if (!p.atEnd()) throw new ExprError(`多余的记号：${p.peekText()}`)
    cache.set(src, fn)
    return fn
  } catch (e) {
    const err = e instanceof ExprError ? e : new ExprError(String(e))
    cache.set(src, err)
    throw err
  }
}

/** 与 App 端同语义：任何错误都退化成空值，界面不炸。 */
export function evalExpr(src: string, scope: Scope): Val {
  try {
    return compile(src)(scope)
  } catch {
    return VNULL
  }
}

/** 校验/预检用：把错误交出去。 */
export function checkExpr(src: string): string | null {
  try {
    compile(src)
    return null
  } catch (e) {
    return e instanceof Error ? e.message : String(e)
  }
}

export function evalExprStrict(src: string, scope: Scope): Val {
  return compile(src)(scope)
}

function matchBrace(text: string, from: number): number {
  let i = from
  let quote = ''
  let depth = 0
  while (i < text.length) {
    const c = text[i]
    if (quote !== '') {
      if (c === '\\') i++
      else if (c === quote) quote = ''
    } else if (c === "'" || c === '"') quote = c
    else if (c === '(') depth++
    else if (c === ')') depth--
    else if (c === '{') depth++
    else if (c === '}') {
      if (depth === 0) return i
      depth--
    }
    i++
  }
  return -1
}

/** 模板渲染，规则与 Expr.template 完全一致。 */
export function template(text: string, scope: Scope): string {
  if (text.indexOf('{') < 0) return text
  let out = ''
  let i = 0
  while (i < text.length) {
    const c = text[i]
    if (c === '{' && text[i + 1] === '{') {
      out += '{'
      i += 2
    } else if (c === '}' && text[i + 1] === '}') {
      out += '}'
      i += 2
    } else if (c === '{') {
      const end = matchBrace(text, i + 1)
      if (end < 0) {
        out += text.slice(i)
        i = text.length
      } else {
        out += asStr(evalExpr(text.slice(i + 1, end).trim(), scope))
        i = end + 1
      }
    } else {
      out += c
      i++
    }
  }
  return out
}

/** 校验模板里每一段 `{...}` 的语法（App 端出错只是显示空，但作者明显不希望这样）。 */
export function checkTemplate(text: string): string | null {
  let i = 0
  while (i < text.length) {
    const c = text[i]
    if (c === '{' && text[i + 1] === '{') {
      i += 2
      continue
    }
    if (c === '{') {
      const end = matchBrace(text, i + 1)
      if (end < 0) return '花括号没有闭合'
      const err = checkExpr(text.slice(i + 1, end).trim())
      if (err) return err
      i = end + 1
      continue
    }
    i++
  }
  return null
}

export const FN_NAMES = [
  'if', 'fmt.freq', 'fmt.khz', 'fmt.num', 'fmt.int', 'fmt.time', 'fmt.timeS',
  'fmt.date', 'fmt.dur', 'fmt.countdown', 'fmt.az', 'fmt.el', 'fmt.tone', 'fmt.pct',
  'abs', 'min', 'max', 'round', 'floor', 'ceil', 'clamp', 'sign', 'default',
  'concat', 'upper', 'lower', 'len', 'sub', 'trim', 'ellipsis', 'not', 'and', 'or',
] as const

export const PIPE_NAMES = [
  'signed', 'fixed', 'int', 'pad2', 'upper', 'lower', 'trim', 'ellipsis', 'pct', 'deg', 'default',
] as const

export const FN_DOC: Record<string, string> = {
  'if': 'if(条件, 真值, 假值)',
  'fmt.freq': 'fmt.freq(Hz, 位数=5) → MHz',
  'fmt.khz': 'fmt.khz(Hz, 位数=4) → kHz',
  'fmt.num': 'fmt.num(值, 位数=-1)',
  'fmt.int': 'fmt.int(值)',
  'fmt.time': 'fmt.time(毫秒) → HH:mm',
  'fmt.timeS': 'fmt.timeS(毫秒) → HH:mm:ss',
  'fmt.date': 'fmt.date(毫秒) → MM-dd',
  'fmt.dur': 'fmt.dur(秒)',
  'fmt.countdown': 'fmt.countdown(毫秒)',
  'fmt.az': 'fmt.az(度) → 123°',
  'fmt.el': 'fmt.el(度) → 41°',
  'fmt.tone': 'fmt.tone(Hz) → 无亚音/67.0 Hz',
  'fmt.pct': 'fmt.pct(0..1, 位数=0)',
  abs: 'abs(x)',
  min: 'min(a,b)',
  max: 'max(a,b)',
  round: 'round(x)',
  floor: 'floor(x)',
  ceil: 'ceil(x)',
  clamp: 'clamp(x,lo,hi)',
  sign: 'sign(x)',
  default: 'default(x, 兜底)',
  concat: 'concat(a,b,…)',
  upper: 'upper(s)',
  lower: 'lower(s)',
  len: 'len(s)',
  sub: 'sub(s,from,to)',
  trim: 'trim(s)',
  ellipsis: 'ellipsis(s, 最大长度=12)',
  not: 'not(x)',
  and: 'and(a,b,…)',
  or: 'or(a,b,…)',
}
