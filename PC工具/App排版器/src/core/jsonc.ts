/**
 * JSON 边界。App 端的 JsonParse 允许 `//` 与块注释（手改 layout 太需要了），
 * 排版器必须能吃下同样带注释的文件 —— 五份真源里就有三份带注释。
 *
 * 不认尾随逗号，和 App 端一致：导出的东西要严格。
 */
export type JsonValue = string | number | boolean | null | JsonValue[] | JsonObject
export interface JsonObject {
  [key: string]: JsonValue
}

export class JsonSyntaxError extends Error {
  line: number
  col: number
  constructor(message: string, line: number, col: number) {
    super(`JSON ${message} @${line}:${col}`)
    this.line = line
    this.col = col
  }
}

export function isJsonObject(v: unknown): v is JsonObject {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

export function parseJsonc(text: string): JsonValue {
  const p = new Parser(text.replace(/^\uFEFF/, ''))
  p.skip()
  const v = p.value()
  p.skip()
  if (!p.eof()) p.fail('多余内容')
  return v
}

class Parser {
  private i = 0
  private line = 1
  private col = 1
  private s: string
  constructor(s: string) {
    this.s = s
  }

  eof(): boolean {
    return this.i >= this.s.length
  }

  fail(msg: string): never {
    throw new JsonSyntaxError(msg, this.line, this.col)
  }

  private advance(): string {
    const c = this.s[this.i]
    this.i++
    if (c === '\n') {
      this.line++
      this.col = 1
    } else {
      this.col++
    }
    return c
  }

  skip(): void {
    for (;;) {
      if (this.eof()) return
      const c = this.s[this.i]
      if (c === ' ' || c === '\t' || c === '\n' || c === '\r') {
        this.advance()
        continue
      }
      if (c === '/' && this.s[this.i + 1] === '/') {
        while (!this.eof() && this.s[this.i] !== '\n') this.advance()
        continue
      }
      if (c === '/' && this.s[this.i + 1] === '*') {
        this.advance()
        this.advance()
        while (!this.eof() && !(this.s[this.i] === '*' && this.s[this.i + 1] === '/')) this.advance()
        if (this.eof()) this.fail('注释没有结束')
        this.advance()
        this.advance()
        continue
      }
      return
    }
  }

  value(): JsonValue {
    this.skip()
    if (this.eof()) this.fail('内容不完整')
    const c = this.s[this.i]
    if (c === '{') return this.object()
    if (c === '[') return this.array()
    if (c === '"') return this.string()
    if (c === '-' || (c >= '0' && c <= '9')) return this.number()
    if (this.s.startsWith('true', this.i)) {
      this.take(4)
      return true
    }
    if (this.s.startsWith('false', this.i)) {
      this.take(5)
      return false
    }
    if (this.s.startsWith('null', this.i)) {
      this.take(4)
      return null
    }
    this.fail(`意外的字符 '${c}'`)
  }

  private take(n: number): void {
    for (let k = 0; k < n; k++) this.advance()
  }

  private object(): JsonObject {
    this.advance() // {
    const out: JsonObject = {}
    this.skip()
    if (this.s[this.i] === '}') {
      this.advance()
      return out
    }
    for (;;) {
      this.skip()
      if (this.s[this.i] !== '"') this.fail('键名必须是字符串')
      const k = this.string()
      this.skip()
      if (this.s[this.i] !== ':') this.fail("缺 ':'")
      this.advance()
      out[k] = this.value()
      this.skip()
      const c = this.s[this.i]
      if (c === ',') {
        this.advance()
        continue
      }
      if (c === '}') {
        this.advance()
        return out
      }
      this.fail("对象里缺 ',' 或 '}'")
    }
  }

  private array(): JsonValue[] {
    this.advance() // [
    const out: JsonValue[] = []
    this.skip()
    if (this.s[this.i] === ']') {
      this.advance()
      return out
    }
    for (;;) {
      out.push(this.value())
      this.skip()
      const c = this.s[this.i]
      if (c === ',') {
        this.advance()
        continue
      }
      if (c === ']') {
        this.advance()
        return out
      }
      this.fail("数组里缺 ',' 或 ']'")
    }
  }

  private string(): string {
    this.advance() // "
    let out = ''
    for (;;) {
      if (this.eof()) this.fail('字符串没有结束')
      const c = this.advance()
      if (c === '"') return out
      if (c === '\\') {
        const e = this.advance()
        if (e === 'n') out += '\n'
        else if (e === 't') out += '\t'
        else if (e === 'r') out += '\r'
        else if (e === 'b') out += '\b'
        else if (e === 'f') out += '\f'
        else if (e === 'u') {
          const hex = this.s.slice(this.i, this.i + 4)
          if (!/^[0-9a-fA-F]{4}$/.test(hex)) this.fail('坏的 \\u 转义')
          out += String.fromCharCode(parseInt(hex, 16))
          this.take(4)
        } else out += e
      } else out += c
    }
  }

  private number(): number {
    const st = this.i
    if (this.s[this.i] === '-') this.advance()
    while (!this.eof() && this.s[this.i] >= '0' && this.s[this.i] <= '9') this.advance()
    if (this.s[this.i] === '.') {
      this.advance()
      while (!this.eof() && this.s[this.i] >= '0' && this.s[this.i] <= '9') this.advance()
    }
    if (this.s[this.i] === 'e' || this.s[this.i] === 'E') {
      this.advance()
      if (this.s[this.i] === '+' || this.s[this.i] === '-') this.advance()
      while (!this.eof() && this.s[this.i] >= '0' && this.s[this.i] <= '9') this.advance()
    }
    const raw = this.s.slice(st, this.i)
    const n = Number(raw)
    if (!Number.isFinite(n)) this.fail('坏的数字')
    return n
  }
}

/**
 * 序列化：2 空格缩进，键顺序 = 对象插入顺序（与原始文件一致，diff 才可读）。
 * 导出时把 props 摊到顶层 —— 和 App 端 `for ((k, pv) in o.fields)` 的行为对齐，
 * 也省得作者在两个地方找同一个字段。
 */
export function stringifyJson(v: JsonValue): string {
  return JSON.stringify(v, null, 2) + '\n'
}
