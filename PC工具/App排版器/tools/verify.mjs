// tools/verify.ts
import fs from "node:fs";
import path from "node:path";

// src/core/jsonc.ts
var JsonSyntaxError = class extends Error {
  line;
  col;
  constructor(message, line, col) {
    super(`JSON ${message} @${line}:${col}`);
    this.line = line;
    this.col = col;
  }
};
function isJsonObject(v) {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
function parseJsonc(text) {
  const p = new Parser(text.replace(/^\uFEFF/, ""));
  p.skip();
  const v = p.value();
  p.skip();
  if (!p.eof()) p.fail("\u591A\u4F59\u5185\u5BB9");
  return v;
}
var Parser = class {
  i = 0;
  line = 1;
  col = 1;
  s;
  constructor(s) {
    this.s = s;
  }
  eof() {
    return this.i >= this.s.length;
  }
  fail(msg) {
    throw new JsonSyntaxError(msg, this.line, this.col);
  }
  advance() {
    const c = this.s[this.i];
    this.i++;
    if (c === "\n") {
      this.line++;
      this.col = 1;
    } else {
      this.col++;
    }
    return c;
  }
  skip() {
    for (; ; ) {
      if (this.eof()) return;
      const c = this.s[this.i];
      if (c === " " || c === "	" || c === "\n" || c === "\r") {
        this.advance();
        continue;
      }
      if (c === "/" && this.s[this.i + 1] === "/") {
        while (!this.eof() && this.s[this.i] !== "\n") this.advance();
        continue;
      }
      if (c === "/" && this.s[this.i + 1] === "*") {
        this.advance();
        this.advance();
        while (!this.eof() && !(this.s[this.i] === "*" && this.s[this.i + 1] === "/")) this.advance();
        if (this.eof()) this.fail("\u6CE8\u91CA\u6CA1\u6709\u7ED3\u675F");
        this.advance();
        this.advance();
        continue;
      }
      return;
    }
  }
  value() {
    this.skip();
    if (this.eof()) this.fail("\u5185\u5BB9\u4E0D\u5B8C\u6574");
    const c = this.s[this.i];
    if (c === "{") return this.object();
    if (c === "[") return this.array();
    if (c === '"') return this.string();
    if (c === "-" || c >= "0" && c <= "9") return this.number();
    if (this.s.startsWith("true", this.i)) {
      this.take(4);
      return true;
    }
    if (this.s.startsWith("false", this.i)) {
      this.take(5);
      return false;
    }
    if (this.s.startsWith("null", this.i)) {
      this.take(4);
      return null;
    }
    this.fail(`\u610F\u5916\u7684\u5B57\u7B26 '${c}'`);
  }
  take(n) {
    for (let k = 0; k < n; k++) this.advance();
  }
  object() {
    this.advance();
    const out = {};
    this.skip();
    if (this.s[this.i] === "}") {
      this.advance();
      return out;
    }
    for (; ; ) {
      this.skip();
      if (this.s[this.i] !== '"') this.fail("\u952E\u540D\u5FC5\u987B\u662F\u5B57\u7B26\u4E32");
      const k = this.string();
      this.skip();
      if (this.s[this.i] !== ":") this.fail("\u7F3A ':'");
      this.advance();
      out[k] = this.value();
      this.skip();
      const c = this.s[this.i];
      if (c === ",") {
        this.advance();
        continue;
      }
      if (c === "}") {
        this.advance();
        return out;
      }
      this.fail("\u5BF9\u8C61\u91CC\u7F3A ',' \u6216 '}'");
    }
  }
  array() {
    this.advance();
    const out = [];
    this.skip();
    if (this.s[this.i] === "]") {
      this.advance();
      return out;
    }
    for (; ; ) {
      out.push(this.value());
      this.skip();
      const c = this.s[this.i];
      if (c === ",") {
        this.advance();
        continue;
      }
      if (c === "]") {
        this.advance();
        return out;
      }
      this.fail("\u6570\u7EC4\u91CC\u7F3A ',' \u6216 ']'");
    }
  }
  string() {
    this.advance();
    let out = "";
    for (; ; ) {
      if (this.eof()) this.fail("\u5B57\u7B26\u4E32\u6CA1\u6709\u7ED3\u675F");
      const c = this.advance();
      if (c === '"') return out;
      if (c === "\\") {
        const e = this.advance();
        if (e === "n") out += "\n";
        else if (e === "t") out += "	";
        else if (e === "r") out += "\r";
        else if (e === "b") out += "\b";
        else if (e === "f") out += "\f";
        else if (e === "u") {
          const hex = this.s.slice(this.i, this.i + 4);
          if (!/^[0-9a-fA-F]{4}$/.test(hex)) this.fail("\u574F\u7684 \\u \u8F6C\u4E49");
          out += String.fromCharCode(parseInt(hex, 16));
          this.take(4);
        } else out += e;
      } else out += c;
    }
  }
  number() {
    const st = this.i;
    if (this.s[this.i] === "-") this.advance();
    while (!this.eof() && this.s[this.i] >= "0" && this.s[this.i] <= "9") this.advance();
    if (this.s[this.i] === ".") {
      this.advance();
      while (!this.eof() && this.s[this.i] >= "0" && this.s[this.i] <= "9") this.advance();
    }
    if (this.s[this.i] === "e" || this.s[this.i] === "E") {
      this.advance();
      if (this.s[this.i] === "+" || this.s[this.i] === "-") this.advance();
      while (!this.eof() && this.s[this.i] >= "0" && this.s[this.i] <= "9") this.advance();
    }
    const raw = this.s.slice(st, this.i);
    const n = Number(raw);
    if (!Number.isFinite(n)) this.fail("\u574F\u7684\u6570\u5B57");
    return n;
  }
};

// src/core/model.ts
var DESIGN_W = 360;
var DESIGN_H = 780;
var TYPES = [
  "group",
  "box",
  "text",
  "button",
  "icon",
  "image",
  "custom",
  "list",
  "slider",
  "toggle",
  "select",
  "input",
  "progress",
  "spacer"
];
var RESERVED = [
  "type",
  "id",
  "name",
  "x",
  "y",
  "w",
  "h",
  "anchor",
  "visible",
  "enabled",
  "locked",
  "children",
  "props"
];
var CONTAINER_TYPES = ["group", "box", "list", "button"];
var uidSeq = 0;
function nextUid() {
  uidSeq += 1;
  return "n" + uidSeq.toString(36) + Math.random().toString(36).slice(2, 6);
}
function num(o, k, def) {
  const v = o[k];
  return typeof v === "number" && Number.isFinite(v) ? v : def;
}
function docFromJson(root, sourceName) {
  const extras = {};
  if (!isJsonObject(root)) {
    return { version: 0, name: sourceName, screens: [], extras };
  }
  for (const [k, v] of Object.entries(root)) {
    if (k !== "version" && k !== "name" && k !== "screens" && k !== "design") extras[k] = v;
  }
  const screens = [];
  const sv = root["screens"];
  if (isJsonObject(sv)) {
    for (const [sid, raw] of Object.entries(sv)) {
      const nodes = [];
      if (isJsonObject(raw) && Array.isArray(raw["nodes"])) {
        for (const nv of raw["nodes"]) {
          const n = nodeFromJson(nv);
          if (n) nodes.push(n);
        }
      }
      screens.push({ id: sid, nodes });
    }
  }
  let design;
  const dv = root["design"];
  if (isJsonObject(dv)) design = { w: num(dv, "w", DESIGN_W), h: num(dv, "h", DESIGN_H) };
  const nameV = root["name"];
  return {
    version: num(root, "version", 0),
    name: typeof nameV === "string" ? nameV : void 0,
    screens,
    design,
    extras
  };
}
function nodeFromJson(v) {
  if (!isJsonObject(v)) return null;
  const props = {};
  const pv = v["props"];
  if (isJsonObject(pv)) for (const [k, x] of Object.entries(pv)) props[k] = x;
  for (const [k, x] of Object.entries(v)) {
    if (!RESERVED.includes(k)) props[k] = x;
  }
  const children = [];
  const cv = v["children"];
  if (Array.isArray(cv)) {
    for (const c of cv) {
      const n = nodeFromJson(c);
      if (n) children.push(n);
    }
  }
  const typeV = v["type"];
  const idV = v["id"];
  const nameV = v["name"];
  const ancV = v["anchor"];
  const visV = v["visible"];
  const enV = v["enabled"];
  return {
    uid: nextUid(),
    type: typeof typeV === "string" && typeV !== "" ? typeV : "",
    id: typeof idV === "string" && idV !== "" ? idV : void 0,
    name: typeof nameV === "string" && nameV !== "" ? nameV : void 0,
    x: num(v, "x", 0),
    y: num(v, "y", 0),
    w: num(v, "w", 0),
    h: num(v, "h", 0),
    anchor: typeof ancV === "string" && ancV !== "" ? ancV : "lt",
    anchorExplicit: typeof ancV === "string" && ancV !== "",
    nested: isJsonObject(pv) ? Object.keys(pv) : void 0,
    visible: typeof visV === "string" && visV !== "" ? visV : void 0,
    enabled: typeof enV === "string" && enV !== "" ? enV : void 0,
    locked: v["locked"] === true,
    props,
    children
  };
}
function round(nv) {
  return Math.round(nv * 100) / 100;
}
function nodeToJson(n) {
  const o = { type: n.type };
  if (n.id) o["id"] = n.id;
  if (n.name) o["name"] = n.name;
  o["x"] = round(n.x);
  o["y"] = round(n.y);
  o["w"] = round(n.w);
  o["h"] = round(n.h);
  if (n.anchorExplicit || n.anchor !== "lt") o["anchor"] = n.anchor;
  if (n.visible !== void 0) o["visible"] = n.visible;
  if (n.enabled !== void 0) o["enabled"] = n.enabled;
  if (n.locked) o["locked"] = true;
  const nested = {};
  const nestedKeys = new Set(n.nested ?? []);
  for (const [k, v] of Object.entries(n.props)) {
    if (v === void 0) continue;
    if (nestedKeys.has(k)) {
      nested[k] = v;
      continue;
    }
    if (RESERVED.includes(k)) continue;
    o[k] = v;
  }
  if (n.nested !== void 0) o["props"] = nested;
  if (n.children.length > 0) o["children"] = n.children.map(nodeToJson);
  return o;
}
function docToJson(doc) {
  const screens = {};
  for (const s of doc.screens) {
    screens[s.id] = { nodes: s.nodes.map(nodeToJson) };
  }
  const o = { version: doc.version };
  if (doc.name !== void 0) o["name"] = doc.name;
  if (doc.design) o["design"] = { w: doc.design.w, h: doc.design.h };
  for (const [k, v] of Object.entries(doc.extras)) o[k] = v;
  o["screens"] = screens;
  return o;
}
function anchorFlags(a) {
  const s = a.toLowerCase();
  return { l: s.includes("l"), r: s.includes("r"), t: s.includes("t"), b: s.includes("b") };
}

// src/core/expr.ts
var VNULL = { k: "z" };
var vNum = (v) => ({ k: "n", v });
var vStr = (v) => ({ k: "s", v });
var vBool = (v) => ({ k: "b", v });
var ExprError = class extends Error {
};
function asStr(v) {
  switch (v.k) {
    case "z":
      return "";
    case "b":
      return v.v ? "true" : "false";
    case "n":
      return fmtNum(v.v);
    case "s":
      return v.v;
  }
}
function asNum(v) {
  switch (v.k) {
    case "n":
      return v.v;
    case "b":
      return v.v ? 1 : 0;
    case "s": {
      const n = Number(v.v);
      return v.v.trim() === "" || !Number.isFinite(n) ? 0 : n;
    }
    case "z":
      return 0;
  }
}
function asBool(v) {
  switch (v.k) {
    case "b":
      return v.v;
    case "n":
      return v.v !== 0;
    case "s":
      return v.v.length > 0;
    case "z":
      return false;
  }
}
function fmtNum(v, digits) {
  if (Number.isNaN(v)) return "\u2014";
  if (!Number.isFinite(v)) return v > 0 ? "\u221E" : "\u2212\u221E";
  if (digits !== void 0) {
    const d = Math.max(0, Math.min(9, Math.trunc(digits)));
    return v.toFixed(d);
  }
  if (v === Math.trunc(v) && Math.abs(v) < 1e15) return String(Math.trunc(v));
  return v.toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
}
function pad2(n) {
  return String(n).padStart(2, "0");
}
var Times = {
  hhmm(ms) {
    if (ms <= 0) return "\u2014";
    const d = new Date(ms);
    return pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  },
  hhmmss(ms) {
    if (ms <= 0) return "\u2014";
    const d = new Date(ms);
    return pad2(d.getHours()) + ":" + pad2(d.getMinutes()) + ":" + pad2(d.getSeconds());
  },
  mmdd(ms) {
    if (ms <= 0) return "\u2014";
    const d = new Date(ms);
    return pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  },
  dur(sec) {
    if (sec <= 0) return "0 \u79D2";
    const h = Math.trunc(sec / 3600);
    const m = Math.trunc(sec % 3600 / 60);
    const s = Math.trunc(sec % 60);
    if (h > 0) return `${h} \u65F6 ${pad2(m)} \u5206`;
    if (m > 0) return `${m} \u5206 ${pad2(s)} \u79D2`;
    return `${s} \u79D2`;
  },
  countdown(ms) {
    if (ms <= 0) return "\u5DF2\u5F00\u59CB";
    const sec = Math.trunc(ms / 1e3);
    if (sec < 60) return `\u8FD8\u6709 ${sec} \u79D2`;
    return "\u8FD8\u6709 " + Times.dur(sec);
  }
};
function cmpVals(x, y) {
  if (x.k === "s" || y.k === "s") {
    const a2 = asStr(x);
    const b2 = asStr(y);
    return a2 < b2 ? -1 : a2 > b2 ? 1 : 0;
  }
  const a = asNum(x);
  const b = asNum(y);
  return a < b ? -1 : a > b ? 1 : 0;
}
function callFn(name, a) {
  const n = (i, d = 0) => a[i] === void 0 ? d : asNum(a[i]);
  const s = (i, d = "") => a[i] === void 0 ? d : asStr(a[i]);
  switch (name) {
    case "if":
      return asBool(a[0] ?? VNULL) ? a[1] ?? VNULL : a[2] ?? VNULL;
    case "fmt.freq": {
      const hz = n(0);
      const digits = Math.max(0, Math.min(9, Math.trunc(n(1, 5))));
      return hz <= 0 ? vStr("\u2014") : vStr(fmtNum(hz / 1e6, digits));
    }
    case "fmt.khz": {
      const hz = n(0);
      return hz === 0 ? vStr("\u2014") : vStr(fmtNum(hz / 1e3, Math.trunc(n(1, 4))));
    }
    case "fmt.num": {
      const d = Math.trunc(n(1, -1));
      return vStr(d < 0 ? fmtNum(n(0)) : fmtNum(n(0), d));
    }
    case "fmt.int":
      return vStr(String(Math.trunc(n(0))));
    case "fmt.time":
      return vStr(Times.hhmm(Math.trunc(n(0))));
    case "fmt.timeS":
      return vStr(Times.hhmmss(Math.trunc(n(0))));
    case "fmt.date":
      return vStr(Times.mmdd(Math.trunc(n(0))));
    case "fmt.dur":
      return vStr(Times.dur(Math.trunc(n(0))));
    case "fmt.countdown":
      return vStr(Times.countdown(Math.trunc(n(0))));
    case "fmt.az":
    case "fmt.el":
      return vStr(fmtNum(n(0), 0) + "\xB0");
    case "fmt.tone": {
      const hz = n(0);
      return hz <= 0 ? vStr("\u65E0\u4E9A\u97F3") : vStr(fmtNum(hz, 1) + " Hz");
    }
    case "fmt.pct":
      return vStr(fmtNum(n(0) * 100, Math.trunc(n(1, 0))) + "%");
    case "abs":
      return vNum(Math.abs(n(0)));
    case "min":
      return vNum(Math.min(n(0), n(1)));
    case "max":
      return vNum(Math.max(n(0), n(1)));
    case "round":
      return vNum(Math.round(n(0)));
    case "floor":
      return vNum(Math.floor(n(0)));
    case "ceil":
      return vNum(Math.ceil(n(0)));
    case "clamp": {
      const lo = Math.min(n(1), n(2));
      const hi = Math.max(n(1), n(2));
      return vNum(Math.min(hi, Math.max(lo, n(0))));
    }
    case "sign":
      return vNum(Math.sign(n(0)));
    case "default": {
      const v = a[0];
      if (v === void 0 || v.k === "z" || asStr(v).length === 0) return a[1] ?? VNULL;
      return v;
    }
    case "concat":
      return vStr(a.map(asStr).join(""));
    case "upper":
      return vStr(s(0).toUpperCase());
    case "lower":
      return vStr(s(0).toLowerCase());
    case "len":
      return vNum(s(0).length);
    case "sub": {
      const str = s(0);
      const from = Math.max(0, Math.min(str.length, Math.trunc(n(1))));
      const to = Math.max(from, Math.min(str.length, Math.trunc(n(2, str.length))));
      return vStr(str.slice(from, to));
    }
    case "trim":
      return vStr(s(0).trim());
    case "ellipsis": {
      const str = s(0);
      const max = Math.max(1, Math.trunc(n(1, 12)));
      return str.length <= max ? vStr(str) : vStr(str.slice(0, max - 1) + "\u2026");
    }
    case "not":
      return vBool(!asBool(a[0] ?? VNULL));
    case "and":
      return vBool(a.every(asBool));
    case "or":
      return vBool(a.some(asBool));
    case "|signed": {
      const v = n(0);
      const d = a.length > 1 ? Math.trunc(n(1, 1)) : 1;
      const str = fmtNum(Math.abs(v), d);
      return vStr(v > 0 ? "+" + str : v < 0 ? "\u2212" + str : str);
    }
    case "|fixed":
      return vStr(fmtNum(n(0), a.length > 1 ? Math.trunc(n(1, 2)) : 2));
    case "|int":
      return vStr(String(Math.trunc(n(0))));
    case "|pad2":
      return vStr(String(Math.max(0, Math.trunc(n(0)))).padStart(2, "0"));
    case "|upper":
      return vStr(s(0).toUpperCase());
    case "|lower":
      return vStr(s(0).toLowerCase());
    case "|trim":
      return vStr(s(0).trim());
    case "|ellipsis":
      return callFn("ellipsis", a);
    case "|pct":
      return vStr(fmtNum(n(0) * 100, a.length > 1 ? Math.trunc(n(1, 0)) : 0) + "%");
    case "|deg":
      return vStr(fmtNum(n(0), 0) + "\xB0");
    case "|default":
      return callFn("default", a);
    default:
      return VNULL;
  }
}
function isDigit(c) {
  return c >= "0" && c <= "9";
}
function isLetter(c) {
  return /[A-Za-z_\u00C0-\uFFFF]/.test(c);
}
function lex(s) {
  const out = [];
  let i = 0;
  while (i < s.length) {
    const c = s[i];
    if (/\s/.test(c)) {
      i++;
      continue;
    }
    if (isDigit(c) || c === "." && i + 1 < s.length && isDigit(s[i + 1])) {
      const st = i;
      while (i < s.length && (isDigit(s[i]) || s[i] === ".")) i++;
      const text = s.slice(st, i);
      const num2 = Number(text);
      out.push({ t: "num", text, num: Number.isFinite(num2) ? num2 : 0 });
      continue;
    }
    if (c === '"' || c === "'") {
      const q = c;
      i++;
      let sb = "";
      while (i < s.length && s[i] !== q) {
        if (s[i] === "\\" && i + 1 < s.length) {
          i++;
          sb += s[i];
        } else sb += s[i];
        i++;
      }
      if (i < s.length) i++;
      out.push({ t: "str", text: sb, num: 0 });
      continue;
    }
    if (isLetter(c)) {
      const st = i;
      while (i < s.length && (isLetter(s[i]) || isDigit(s[i]) || s[i] === "_" || s[i] === ".")) i++;
      out.push({ t: "ident", text: s.slice(st, i), num: 0 });
      continue;
    }
    const two = s.slice(i, i + 2);
    if (["==", "!=", "<=", ">=", "&&", "||"].includes(two)) {
      out.push({ t: "op", text: two, num: 0 });
      i += 2;
      continue;
    }
    out.push({ t: "op", text: c, num: 0 });
    i++;
  }
  out.push({ t: "end", text: "", num: 0 });
  return out;
}
function pathOf(obj, path2) {
  let cur = obj;
  for (const part of path2.split(".")) {
    if (cur === null || cur === void 0) return VNULL;
    if (Array.isArray(cur)) {
      const idx = Number(part);
      cur = Number.isFinite(idx) ? cur[idx] : void 0;
    } else if (typeof cur === "object") {
      cur = cur[part];
    } else return VNULL;
    if (cur === void 0) return VNULL;
  }
  if (cur === null || cur === void 0) return VNULL;
  if (typeof cur === "boolean") return vBool(cur);
  if (typeof cur === "number") return vNum(cur);
  if (typeof cur === "string") return vStr(cur);
  return VNULL;
}
var Parser2 = class {
  toks;
  p = 0;
  constructor(toks) {
    this.toks = toks;
  }
  peek() {
    return this.toks[this.p] ?? { t: "end", text: "", num: 0 };
  }
  next() {
    const t = this.peek();
    this.p++;
    return t;
  }
  isOp(s) {
    const t = this.peek();
    return t.t === "op" && t.text === s;
  }
  atEnd() {
    return this.peek().t === "end";
  }
  peekText() {
    return this.peek().text;
  }
  expr() {
    return this.ternary();
  }
  ternary() {
    const c = this.or();
    if (this.isOp("?")) {
      this.next();
      const t = this.ternary();
      if (!this.isOp(":")) throw new ExprError("\u4E09\u5143\u8868\u8FBE\u5F0F\u7F3A ':'");
      this.next();
      const f = this.ternary();
      return (s) => asBool(c(s)) ? t(s) : f(s);
    }
    return c;
  }
  or() {
    let l = this.and();
    while (this.isOp("||")) {
      this.next();
      const r = this.and();
      const prev = l;
      l = (s) => vBool(asBool(prev(s)) || asBool(r(s)));
    }
    return l;
  }
  and() {
    let l = this.eq();
    while (this.isOp("&&")) {
      this.next();
      const r = this.eq();
      const prev = l;
      l = (s) => vBool(asBool(prev(s)) && asBool(r(s)));
    }
    return l;
  }
  eq() {
    let l = this.rel();
    while (this.isOp("==") || this.isOp("!=")) {
      const op = this.next().text;
      const r = this.rel();
      const prev = l;
      l = (s) => vBool(op === "==" ? cmpVals(prev(s), r(s)) === 0 : cmpVals(prev(s), r(s)) !== 0);
    }
    return l;
  }
  rel() {
    let l = this.add();
    while (this.isOp("<") || this.isOp("<=") || this.isOp(">") || this.isOp(">=")) {
      const op = this.next().text;
      const r = this.add();
      const prev = l;
      l = (s) => {
        const x = asNum(prev(s));
        const y = asNum(r(s));
        switch (op) {
          case "<":
            return vBool(x < y);
          case "<=":
            return vBool(x <= y);
          case ">":
            return vBool(x > y);
          default:
            return vBool(x >= y);
        }
      };
    }
    return l;
  }
  add() {
    let l = this.mul();
    for (; ; ) {
      if (this.isOp("+")) {
        this.next();
        const r = this.mul();
        const prev = l;
        l = (s) => {
          const x = prev(s);
          const y = r(s);
          return x.k === "s" || y.k === "s" ? vStr(asStr(x) + asStr(y)) : vNum(asNum(x) + asNum(y));
        };
        continue;
      }
      if (this.isOp("-")) {
        this.next();
        const r = this.mul();
        const prev = l;
        l = (s) => vNum(asNum(prev(s)) - asNum(r(s)));
        continue;
      }
      return l;
    }
  }
  mul() {
    let l = this.unary();
    for (; ; ) {
      const op = this.peek().text;
      if (this.peek().t !== "op" || !["*", "/", "%"].includes(op)) return l;
      this.next();
      const r = this.unary();
      const prev = l;
      l = (s) => {
        const x = asNum(prev(s));
        const y = asNum(r(s));
        if (op === "*") return vNum(x * y);
        if (y === 0) return vNum(0);
        return vNum(op === "/" ? x / y : x % y);
      };
    }
  }
  unary() {
    const t = this.peek();
    if (t.t === "op" && (t.text === "!" || t.text === "-" || t.text === "+")) {
      this.next();
      const a = this.unary();
      if (t.text === "!") return (s) => vBool(!asBool(a(s)));
      if (t.text === "-") return (s) => vNum(-asNum(a(s)));
      return a;
    }
    return this.postfix();
  }
  postfix() {
    let n = this.primary();
    while (this.isOp("|")) {
      this.next();
      const tok = this.next();
      if (tok.t !== "ident") throw new ExprError("\u7BA1\u9053\u540E\u9762\u8981\u5199\u8FC7\u6EE4\u5668\u540D");
      const name = tok.text;
      const args = this.isOp("(") ? this.argsList() : [];
      const base = n;
      n = (s) => callFn("|" + name, [base(s), ...args.map((f) => f(s))]);
    }
    return n;
  }
  argsList() {
    this.next();
    const out = [];
    if (this.isOp(")")) {
      this.next();
      return out;
    }
    for (; ; ) {
      out.push(this.expr());
      if (this.isOp(",")) {
        this.next();
        continue;
      }
      if (this.isOp(")")) {
        this.next();
        return out;
      }
      throw new ExprError("\u51FD\u6570\u53C2\u6570\u7F3A ',' \u6216 ')'");
    }
  }
  primary() {
    const tok = this.next();
    switch (tok.t) {
      case "num": {
        const v = tok.num;
        return () => vNum(v);
      }
      case "str": {
        const v = tok.text;
        return () => vStr(v);
      }
      case "ident": {
        if (this.isOp("(")) {
          const name = tok.text;
          const args = this.argsList();
          return (s) => callFn(name, args.map((f) => f(s)));
        }
        if (tok.text === "true") return () => vBool(true);
        if (tok.text === "false") return () => vBool(false);
        if (tok.text === "null") return () => VNULL;
        const path2 = tok.text;
        return (s) => pathOf(s, path2);
      }
      case "op": {
        if (tok.text === "(") {
          const e = this.expr();
          if (!this.isOp(")")) throw new ExprError("\u7F3A ')'");
          this.next();
          return e;
        }
        throw new ExprError(`\u610F\u5916\u7684\u8BB0\u53F7 '${tok.text}'`);
      }
      default:
        throw new ExprError("\u8868\u8FBE\u5F0F\u4E0D\u5B8C\u6574");
    }
  }
};
var cache = /* @__PURE__ */ new Map();
function compile(src) {
  const hit = cache.get(src);
  if (hit instanceof ExprError) throw hit;
  if (hit) return hit;
  try {
    const p = new Parser2(lex(src));
    const fn = p.expr();
    if (!p.atEnd()) throw new ExprError(`\u591A\u4F59\u7684\u8BB0\u53F7\uFF1A${p.peekText()}`);
    cache.set(src, fn);
    return fn;
  } catch (e) {
    const err = e instanceof ExprError ? e : new ExprError(String(e));
    cache.set(src, err);
    throw err;
  }
}
function evalExpr(src, scope) {
  try {
    return compile(src)(scope);
  } catch {
    return VNULL;
  }
}
function checkExpr(src) {
  try {
    compile(src);
    return null;
  } catch (e) {
    return e instanceof Error ? e.message : String(e);
  }
}
function matchBrace(text, from) {
  let i = from;
  let quote = "";
  let depth = 0;
  while (i < text.length) {
    const c = text[i];
    if (quote !== "") {
      if (c === "\\") i++;
      else if (c === quote) quote = "";
    } else if (c === "'" || c === '"') quote = c;
    else if (c === "(") depth++;
    else if (c === ")") depth--;
    else if (c === "{") depth++;
    else if (c === "}") {
      if (depth === 0) return i;
      depth--;
    }
    i++;
  }
  return -1;
}
function template(text, scope) {
  if (text.indexOf("{") < 0) return text;
  let out = "";
  let i = 0;
  while (i < text.length) {
    const c = text[i];
    if (c === "{" && text[i + 1] === "{") {
      out += "{";
      i += 2;
    } else if (c === "}" && text[i + 1] === "}") {
      out += "}";
      i += 2;
    } else if (c === "{") {
      const end = matchBrace(text, i + 1);
      if (end < 0) {
        out += text.slice(i);
        i = text.length;
      } else {
        out += asStr(evalExpr(text.slice(i + 1, end).trim(), scope));
        i = end + 1;
      }
    } else {
      out += c;
      i++;
    }
  }
  return out;
}
function checkTemplate(text) {
  let i = 0;
  while (i < text.length) {
    const c = text[i];
    if (c === "{" && text[i + 1] === "{") {
      i += 2;
      continue;
    }
    if (c === "{") {
      const end = matchBrace(text, i + 1);
      if (end < 0) return "\u82B1\u62EC\u53F7\u6CA1\u6709\u95ED\u5408";
      const err = checkExpr(text.slice(i + 1, end).trim());
      if (err) return err;
      i = end + 1;
      continue;
    }
    i++;
  }
  return null;
}

// src/core/colors.ts
var TOKENS = [
  { name: "bg", hex: "#080A0E", note: "\u9875\u9762\u5E95\u8272" },
  { name: "card", hex: "#12161D", note: "\u5361\u7247 / \u5E95\u680F" },
  { name: "line", hex: "#1E252F", note: "\u63CF\u8FB9 / \u5206\u9694" },
  { name: "grid", hex: "#232B36", note: "\u7F51\u683C / \u6781\u5F31\u7684\u5757" },
  { name: "txt", hex: "#E6EAF0", note: "\u6B63\u6587" },
  { name: "dim", hex: "#8A94A3", note: "\u6B21\u8981\u6587\u5B57" },
  { name: "acc", hex: "#35C7E8", note: "\u5F3A\u8C03\u8272" },
  { name: "ok", hex: "#43D17C", note: "\u6B63\u5E38 / \u6709\u4FE1\u53F7" },
  { name: "warn", hex: "#E8B33C", note: "\u8B66\u544A" },
  { name: "err", hex: "#E8574A", note: "\u9519\u8BEF" },
  { name: "sel", hex: "#1B2A36", note: "\u9009\u4E2D\u6001\u5E95" },
  { name: "faint", hex: "#151B24", note: "\u7981\u7528 / \u6781\u5F31\u7684\u5E95" },
  { name: "none", hex: "#00000000", note: "\u900F\u660E" }
];
var TOKEN_MAP = new Map(TOKENS.map((t) => [t.name, t]));
function parseColor(spec) {
  if (!spec) return null;
  const raw = spec.trim();
  if (raw === "") return null;
  const at = raw.lastIndexOf("@");
  const base = at >= 0 ? raw.slice(0, at).trim() : raw;
  const pct = at >= 0 ? Number(raw.slice(at + 1)) : 100;
  const mult = Number.isFinite(pct) ? Math.max(0, Math.min(100, pct)) / 100 : 1;
  let c = null;
  if (base.startsWith("#")) {
    const h = base.slice(1);
    if (/^[0-9a-fA-F]{8}$/.test(h)) {
      c = {
        a: parseInt(h.slice(0, 2), 16) / 255,
        r: parseInt(h.slice(2, 4), 16),
        g: parseInt(h.slice(4, 6), 16),
        b: parseInt(h.slice(6, 8), 16)
      };
    } else if (/^[0-9a-fA-F]{6}$/.test(h)) {
      c = { a: 1, r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16), b: parseInt(h.slice(4, 6), 16) };
    } else if (/^[0-9a-fA-F]{3}$/.test(h)) {
      const r = h[0] + h[0];
      const g = h[1] + h[1];
      const b = h[2] + h[2];
      c = { a: 1, r: parseInt(r, 16), g: parseInt(g, 16), b: parseInt(b, 16) };
    }
  } else {
    const t = TOKEN_MAP.get(base);
    if (t) c = parseColor(t.hex);
  }
  if (!c) return null;
  return { ...c, a: c.a * mult };
}
function isKnownColorSpec(spec) {
  return spec === "" || parseColor(spec) !== null;
}

// src/data/acts.ts
var A = (name, args, label, group) => ({ name, args, label, group });
var ACTS = [
  // 导航
  A("nav.back", [], "\u8FD4\u56DE", "\u5BFC\u822A"),
  A("nav.detail", ["norad"], "\u6253\u5F00\u536B\u661F\u8BE6\u60C5", "\u5BFC\u822A"),
  A("nav.track", [], "\u6253\u5F00\u8DDF\u8E2A\u9875", "\u5BFC\u822A"),
  A("nav.station", [], "\u6253\u5F00\u7535\u53F0\u9875", "\u5BFC\u822A"),
  A("nav.about", [], "\u6253\u5F00\u5173\u4E8E", "\u5BFC\u822A"),
  A("nav.selfcheck", [], "\u81EA\u68C0", "\u5BFC\u822A"),
  A("tab.set", ["x"], "\u5207\u6362\u4E3B\u6807\u7B7E", "\u5BFC\u822A"),
  // 详情页 / 收藏 / 清单
  A("fav.toggle", ["norad"], "\u6536\u85CF\u5F00\u5173", "\u8BE6\u60C5"),
  A("cart.toggle", ["norad", "aos"], "\u52A0\u5165/\u79FB\u51FA\u6E05\u5355", "\u8BE6\u60C5"),
  A("cart.clear", [], "\u6E05\u7A7A\u6E05\u5355", "\u8BE6\u60C5"),
  A("cart.write", [], "\u5199\u5165\u7535\u53F0", "\u8BE6\u60C5"),
  A("playmode.set", ["mode"], "\u64AD\u653E\u6A21\u5F0F", "\u8BE6\u60C5"),
  A("sheet.close", [], "\u5173\u9762\u677F", "\u8BE6\u60C5"),
  A("sheet.filter", [], "\u7B5B\u9009\u9762\u677F", "\u8BE6\u60C5"),
  A("sheet.sort", [], "\u6392\u5E8F\u9762\u677F", "\u8BE6\u60C5"),
  A("sheet.intro", [], "\u4ECB\u7ECD\u9762\u677F", "\u8BE6\u60C5"),
  // 列表页
  A("ui.filter", ["x"], "\u7B5B\u9009\uFF1A\u6CE2\u6BB5/\u6A21\u5F0F", "\u5217\u8868"),
  A("ui.sort", ["x"], "\u6392\u5E8F", "\u5217\u8868"),
  A("ui.span", ["x"], "\u65F6\u95F4\u8DE8\u5EA6", "\u5217\u8868"),
  A("ui.minEl", ["x"], "\u6700\u5C0F\u4EF0\u89D2", "\u5217\u8868"),
  A("ui.view", ["x"], "\u89C6\u56FE\u5207\u6362", "\u5217\u8868"),
  A("ui.sheet", ["x"], "\u6253\u5F00\u9762\u677F", "\u5217\u8868"),
  A("ui.pin", ["x"], "\u7F6E\u9876", "\u5217\u8868"),
  A("ui.search", [], "\u641C\u7D22", "\u5217\u8868"),
  A("table.clear", [], "\u6E05\u7A7A\u8868\u683C\u9009\u62E9", "\u5217\u8868"),
  A("tle.refresh", [], "\u5237\u65B0 TLE", "\u5217\u8868"),
  A("tle.revert", [], "\u8FD8\u539F TLE", "\u5217\u8868"),
  A("notify.replan", [], "\u91CD\u6392\u63D0\u9192", "\u5217\u8868"),
  // 天球页
  A("sky.now", [], "\u8DF3\u5230\u5F53\u524D", "\u5929\u7403"),
  A("sky.offset", ["n"], "\u65F6\u95F4\u504F\u79FB\uFF08\u5C0F\u65F6\uFF09", "\u5929\u7403"),
  A("sky.pick", ["norad"], "\u9009\u661F", "\u5929\u7403"),
  // 跟踪页
  A("track.trim", ["n"], "\u591A\u666E\u52D2\u5FAE\u8C03", "\u8DDF\u8E2A"),
  A("track.takeover", [], "\u63A5\u7BA1\u9891\u7387", "\u8DDF\u8E2A"),
  A("track.simPass", [], "\u6A21\u62DF\u8FC7\u5883", "\u8DDF\u8E2A"),
  A("track.log", [], "\u5199\u65E5\u5FD7", "\u8DDF\u8E2A"),
  // 电台
  A("radio.connect", [], "\u8FDE\u63A5\u7535\u53F0", "\u7535\u53F0"),
  A("radio.disconnect", [], "\u65AD\u5F00", "\u7535\u53F0"),
  A("radio.probe", [], "\u63A2\u6D4B", "\u7535\u53F0"),
  A("radio.release", [], "\u677E\u624B", "\u7535\u53F0"),
  A("radio.tcp", [], "TCP \u6A21\u5F0F", "\u7535\u53F0"),
  A("radio.usb", [], "USB \u6A21\u5F0F", "\u7535\u53F0"),
  A("station.clear", [], "\u6E05\u7A7A\u53F0\u7AD9", "\u7535\u53F0"),
  A("station.locate", [], "\u5B9A\u4F4D\u53F0\u7AD9", "\u7535\u53F0"),
  A("station.save", [], "\u4FDD\u5B58\u53F0\u7AD9", "\u7535\u53F0"),
  A("diag.export", [], "\u5BFC\u51FA\u8BCA\u65AD", "\u7535\u53F0"),
  A("diag.reset", [], "\u590D\u4F4D\u8BCA\u65AD", "\u7535\u53F0"),
  // 设置
  A("settings.toggle", ["key"], "\u8BBE\u7F6E\u5F00\u5173", "\u8BBE\u7F6E"),
  A("prefs.toggle", ["key"], "\u504F\u597D\u5F00\u5173", "\u8BBE\u7F6E"),
  A("settings.act", ["key"], "\u8BBE\u7F6E\u52A8\u4F5C", "\u8BBE\u7F6E"),
  A("settings.pick", ["key"], "\u8BBE\u7F6E\u9009\u62E9", "\u8BBE\u7F6E"),
  A("settings.text", ["key"], "\u8BBE\u7F6E\u8F93\u5165", "\u8BBE\u7F6E"),
  A("settings.up", [], "\u4E0A\u79FB\u4E00\u6761", "\u8BBE\u7F6E"),
  A("settings.dn", [], "\u4E0B\u79FB\u4E00\u6761", "\u8BBE\u7F6E"),
  A("theme.open", [], "\u4E3B\u9898\u9875", "\u8BBE\u7F6E"),
  A("theme.preset", ["n"], "\u4E3B\u9898\u9884\u8BBE", "\u8BBE\u7F6E"),
  A("theme.hue", [], "\u8272\u76F8", "\u8BBE\u7F6E"),
  A("theme.sat", [], "\u9971\u548C", "\u8BBE\u7F6E"),
  A("theme.val", [], "\u660E\u5EA6", "\u8BBE\u7F6E"),
  A("theme.seed", [], "\u79CD\u5B50\u8272", "\u8BBE\u7F6E")
];
function parseAct(src) {
  const m = /^\s*([A-Za-z_][\w.]*)\s*(?:\((.*)\))?\s*$/.exec(src);
  if (!m) return null;
  const name = m[1];
  const inner = m[2];
  const args = inner === void 0 || inner.trim() === "" ? [] : inner.split(",").map((x) => x.trim());
  return { name, args };
}
function actKnown(src) {
  const p = parseAct(src);
  return p !== null && ACTS.some((a) => a.name === p.name);
}

// src/core/validate.ts
var KNOWN = new Set(TYPES);
var PATH_RE = /^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$/;
var VARISH = /^(now|prefs|sat|next|cart|list|sky|track|settings|ui|item|sel|tl|pass|passes)\.[A-Za-z_]/;
function validateDoc(doc, rootError) {
  const out = [];
  const push = (i) => {
    out.push(i);
  };
  if (rootError) {
    push({ level: "E", screen: "-", path: "(\u6587\u4EF6)", uid: null, field: "root", message: rootError });
  }
  if (doc.version !== 1) {
    push({
      level: "E",
      screen: "-",
      path: "(\u6587\u4EF6)",
      uid: null,
      field: "version",
      message: `version = ${doc.version}\uFF0CApp \u7AEF\u53EA\u8BA4 1\uFF0C\u4F1A\u6574\u4EFD\u62D2\u8F7D`
    });
  }
  if (doc.design) {
    if (doc.design.w !== 360 || doc.design.h !== 780) {
      push({
        level: "W",
        screen: "-",
        path: "(\u6587\u4EF6)",
        uid: null,
        field: "design",
        message: `design = ${doc.design.w}\xD7${doc.design.h}\uFF0C\u6E32\u67D3\u5668\u6309 360\xD7780 \u7B97 scale\uFF0C\u5199\u4E86\u4E5F\u4E0D\u5F71\u54CD`
      });
    }
  }
  if (doc.screens.length === 0) {
    push({ level: "E", screen: "-", path: "(\u6587\u4EF6)", uid: null, field: "screens", message: "\u6CA1\u6709\u4EFB\u4F55\u5C4F" });
  }
  for (const screen of doc.screens) {
    const walk = (nodes, prefix, parentType) => {
      nodes.forEach((n, i) => {
        const path2 = `${screen.id}.${prefix}[${i}]`;
        const label = n.name ?? n.id ?? n.type ?? "?";
        const at = (field, message, level = "E") => {
          push({ level, screen: screen.id, path: `${path2} (${label})`, uid: n.uid, field, message });
        };
        if (!n.type) at("type", "\u7F3A type");
        else if (!KNOWN.has(n.type)) at("type", `\u7C7B\u578B '${n.type}' \u4E0D\u8BA4\u8BC6`);
        if (n.w <= 0 || n.h <= 0) at("w/h", `\u5BBD\u9AD8\u5FC5\u987B > 0\uFF08\u73B0\u5728 ${n.w}\xD7${n.h}\uFF09`);
        if (n.x < 0 || n.y < 0) at("x/y", `\u5750\u6807\u662F\u8D1F\u7684\uFF08${n.x},${n.y}\uFF09\uFF0C\u951A\u70B9\u89E3\u7B97\u4F1A\u628A\u5B83\u9876\u51FA\u53BB`, "W");
        if (!/^[lrtb]*$/.test(n.anchor)) at("anchor", `\u951A\u70B9 '${n.anchor}' \u53EA\u80FD\u7531 l r t b \u7EC4\u6210`);
        if (parentType && !CONTAINER_TYPES.includes(parentType)) {
          at("children", `\u7236\u8282\u70B9\u662F ${parentType}\uFF0C\u53EA\u6709 group/box/list/button \u80FD\u88C5\u5B50\u8282\u70B9`);
        }
        for (const key of ["visible", "enabled"]) {
          const v = n[key];
          if (v === void 0) continue;
          if (v.includes("{") || v.includes("}")) {
            at(key, `${key} \u5FC5\u987B\u662F\u88F8\u8868\u8FBE\u5F0F\uFF0C\u4E0D\u80FD\u5E26\u82B1\u62EC\u53F7\uFF08\u5199\u6210 ${v.replace(/[{}]/g, "")}\uFF09`);
          } else {
            const err = checkExpr(v);
            if (err) at(key, `${key} \u8868\u8FBE\u5F0F\u6709\u9519\uFF1A${err}`);
          }
        }
        const clickable = n.props["onClick"] !== void 0 || ["button", "toggle", "select", "slider", "input"].includes(n.type);
        if (clickable && (n.w < 48 || n.h < 48)) {
          at("w/h", `\u70B9\u51FB\u533A ${Math.round(n.w)}\xD7${Math.round(n.h)} < 48dp\uFF0C\u771F\u673A\u4E0A\u5F88\u96BE\u70B9`, "W");
        }
        const act = n.props["onClick"];
        if (typeof act === "string" && act.trim() !== "") {
          const p = parseAct(act);
          if (!p) at("onClick", `\u52A8\u4F5C '${act}' \u4E0D\u662F name(args) \u7684\u5F62\u5F0F`);
          else if (!actKnown(act)) at("onClick", `\u52A8\u4F5C '${p.name}' \u4E0D\u5728 Acts \u767D\u540D\u5355\u91CC`, "W");
        }
        for (const key of ["text", "empty", "hint"]) {
          const v = n.props[key];
          if (typeof v !== "string" || v === "") continue;
          if (v.includes("{")) {
            const err = checkTemplate(v);
            if (err) at(key, `\u6A21\u677F\u6709\u9519\uFF1A${err}`);
          } else if (VARISH.test(v)) {
            at(key, `'${v}' \u6CA1\u6709\u82B1\u62EC\u53F7\uFF0C\u4F1A\u6309\u5B57\u9762\u663E\u793A\uFF08\u8981\u5199\u6210 {${v}}\uFF09`, "W");
          }
        }
        const items = n.props["items"];
        if (n.type === "list" && typeof items === "string") {
          if (items.includes("{")) at("items", "items \u662F\u88F8\u8DEF\u5F84\uFF0C\u4E0D\u662F\u6A21\u677F");
          else if (!PATH_RE.test(items)) at("items", `items '${items}' \u4E0D\u662F\u5408\u6CD5\u8DEF\u5F84`);
        }
        for (const key of ["fill", "fill2", "border", "fillPressed", "fillDisabled", "color", "statusColor"]) {
          const v = n.props[key];
          if (typeof v === "string" && v !== "" && !isKnownColorSpec(v)) {
            at(key, `\u989C\u8272 '${v}' \u4E0D\u8BA4\u8BC6\uFF08\u4F1A\u6309\u900F\u660E\u5904\u7406\uFF09`, "W");
          }
        }
        for (const k of Object.keys(n.props)) {
          if (["uid", "props"].includes(k)) at(k, `\u4FDD\u7559\u5B57 '${k}' \u4E0D\u8BE5\u51FA\u73B0\u5728 props \u91CC`);
        }
        if (n.children.length > 0) walk(n.children, `${prefix}[${i}].children`, n.type);
      });
    };
    walk(screen.nodes, "nodes", null);
  }
  return out;
}

// src/data/fixtures.ts
var now = () => {
  const d = /* @__PURE__ */ new Date();
  const pad = (x) => String(x).padStart(2, "0");
  return {
    ms: Date.now(),
    hhmm: pad(d.getHours()) + ":" + pad(d.getMinutes()),
    hhmmss: pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds()),
    utc: d.toISOString().slice(11, 19) + "Z",
    date: pad(d.getMonth() + 1) + "-" + pad(d.getDate())
  };
};
var PASSES = [
  {
    i: 1,
    aos: Date.now() + 74e4,
    los: 0,
    aosText: "20:41",
    losText: "20:49",
    date: "11-07",
    durS: 486,
    durText: "8 \u5206 06 \u79D2",
    maxEl: 62,
    maxElText: "62",
    maxElAt: 0,
    azAos: 212,
    azLos: 34,
    azText: "212\xB0\u219234\xB0",
    countdown: "\u8FD8\u6709 12 \u5206 20 \u79D2",
    ms: 74e4,
    live: false,
    norad: 25544,
    name: "ISS (ZARYA)",
    inCart: true,
    sub: "NORAD 25544 \xB7 LEO",
    time: "20:41",
    elText: "62\xB0 \xB7 8 \u5206",
    has: true,
    elGood: true,
    fav: true,
    tleText: "TLE 2 \u5929\u524D",
    showTle: true,
    tleBad: false
  },
  {
    i: 2,
    aos: 0,
    los: 0,
    aosText: "21:03",
    losText: "21:09",
    date: "11-07",
    durS: 342,
    durText: "5 \u5206 42 \u79D2",
    maxEl: 28,
    maxElText: "28",
    maxElAt: 0,
    azAos: 18,
    azLos: 158,
    azText: "18\xB0\u2192158\xB0",
    countdown: "\u8FD8\u6709 34 \u5206 41 \u79D2",
    ms: 2081e3,
    live: false,
    norad: 43017,
    name: "CAS-4B",
    inCart: false,
    sub: "NORAD 43017 \xB7 LEO",
    time: "21:03",
    elText: "28\xB0 \xB7 5 \u5206",
    has: true,
    elGood: true,
    fav: false,
    tleText: "TLE 4 \u5929\u524D",
    showTle: true,
    tleBad: true
  },
  {
    i: 3,
    aos: 0,
    los: 0,
    aosText: "21:22",
    losText: "21:31",
    date: "11-07",
    durS: 528,
    durText: "8 \u5206 48 \u79D2",
    maxEl: 74,
    maxElText: "74",
    maxElAt: 0,
    azAos: 350,
    azLos: 96,
    azText: "350\xB0\u219296\xB0",
    countdown: "\u8FD8\u6709 53 \u5206 02 \u79D2",
    ms: 3182e3,
    live: false,
    norad: 7530,
    name: "FOX-1B (AO-91)",
    inCart: true,
    sub: "NORAD 7530 \xB7 LEO",
    time: "21:22",
    elText: "74\xB0 \xB7 8 \u5206",
    has: true,
    elGood: true,
    fav: true,
    tleText: "TLE 1 \u5929\u524D",
    showTle: true,
    tleBad: false
  },
  {
    i: 4,
    aos: 0,
    los: 0,
    aosText: "22:14",
    losText: "22:19",
    date: "11-07",
    durS: 296,
    durText: "4 \u5206 56 \u79D2",
    maxEl: 12,
    maxElText: "12",
    maxElAt: 0,
    azAos: 96,
    azLos: 244,
    azText: "96\xB0\u2192244\xB0",
    countdown: "\u8FD8\u6709 1 \u65F6 45 \u5206",
    ms: 63e5,
    live: false,
    norad: 40967,
    name: "AO-73 (FUNcube-1)",
    inCart: false,
    sub: "NORAD 40967 \xB7 LEO",
    time: "22:14",
    elText: "12\xB0 \xB7 4 \u5206",
    has: true,
    elGood: false,
    fav: false,
    tleText: "TLE 9 \u5929\u524D",
    showTle: true,
    tleBad: true
  },
  {
    i: 5,
    aos: 0,
    los: 0,
    aosText: "23:07",
    losText: "23:13",
    date: "11-07",
    durS: 371,
    durText: "6 \u5206 11 \u79D2",
    maxEl: 41,
    maxElText: "41",
    maxElAt: 0,
    azAos: 302,
    azLos: 71,
    azText: "302\xB0\u219271\xB0",
    countdown: "\u8FD8\u6709 2 \u65F6 38 \u5206",
    ms: 948e4,
    live: false,
    norad: 43678,
    name: "CAS-4A",
    inCart: false,
    sub: "NORAD 43678 \xB7 LEO",
    time: "23:07",
    elText: "41\xB0 \xB7 6 \u5206",
    has: true,
    elGood: true,
    fav: false,
    tleText: "TLE 3 \u5929\u524D",
    showTle: true,
    tleBad: false
  }
];
var TX = [
  { downMhz: 145.987654, upMhz: 435.123456, mode: "FM", type: "\u4E3B", desc: "\u8BED\u97F3 / APRS" },
  { downMhz: 145.825, upMhz: 145.825, mode: "FM", type: "\u5907", desc: "APRS \u4E2D\u7EE7" },
  { downMhz: 437.8, upMhz: 145.9, mode: "SSB", type: "\u6D4B\u63A7", desc: "\u9065\u6D4B\u4FE1\u6807" }
];
var SETTINGS_ROWS = [
  { kind: "section", key: "", key2: "", title: "\u8DDF\u8E2A", sub: "", value: "", b2: "", on: false, warn: false, hasSub: false, line: false, isSection: true, isToggle: false, isSelect: false, isStep: false, isInput: false, isBig: false, isNote: false, isBtn1: false, isBtn2: false, isLink: false, isHold: false },
  { kind: "toggle", key: "notify", key2: "", title: "\u8FC7\u5883\u63D0\u9192", sub: "AOS \u524D\u63D0\u9192", value: "", b2: "", on: true, warn: false, hasSub: true, line: true, isSection: false, isToggle: true, isSelect: false, isStep: false, isInput: false, isBig: false, isNote: false, isBtn1: false, isBtn2: false, isLink: false, isHold: false },
  { kind: "step", key: "minEl", key2: "", title: "\u6700\u5C0F\u4EF0\u89D2", sub: "", value: "10\xB0", b2: "", on: false, warn: false, hasSub: false, line: true, isSection: false, isToggle: false, isSelect: false, isStep: true, isInput: false, isBig: false, isNote: false, isBtn1: false, isBtn2: false, isLink: false, isHold: false },
  { kind: "select", key: "band", key2: "", title: "\u6CE2\u6BB5", sub: "", value: "2m / 70cm", b2: "", on: false, warn: false, hasSub: false, line: true, isSection: false, isToggle: false, isSelect: true, isStep: false, isInput: false, isBig: false, isNote: false, isBtn1: false, isBtn2: false, isLink: false, isHold: false }
];
function previewScope() {
  const nextMs = 74e4;
  return {
    now: now(),
    prefs: {
      freqDigits: 5,
      showTleAge: true,
      minEl: 10,
      searchHours: 24,
      timelineHours: 6,
      rxOnly: false,
      listMode: "table",
      notifyOn: true,
      notifyLeadS: 300,
      callsign: "BG0FPJ",
      night: true,
      accent: "acc",
      sortMode: "aos",
      sortLabel: "\u6309 AOS",
      favPin: true,
      radioName: "UV-K1",
      demo: true,
      keepAwake: true,
      releaseOnLeave: true,
      autoReconnect: true,
      compassGain: 1,
      simPass: false,
      playMode: "voice",
      playVoice: true,
      playSstv: false
    },
    sat: {
      name: "ISS (ZARYA)",
      norad: 25544,
      downMhz: 145.987654,
      upMhz: 145.99,
      toneHz: 67,
      mode: "FM",
      band: "2m",
      orbit: "LEO",
      inclDeg: 51.64,
      source: "TLE",
      rxOk: true,
      hasFreq: true,
      blurb: "\u56FD\u9645\u7A7A\u95F4\u7AD9\u4E1A\u4F59\u65E0\u7EBF\u7535\u4E2D\u7EE7\u3002\u8DE8\u533A\u8FC7\u5883\u65F6 SSTV \u548C\u8BED\u97F3\u90FD\u5728\u8FD9\u9897\u661F\u4E0A\uFF0CAOS \u524D\u7684\u591A\u666E\u52D2\u659C\u7387\u53D8\u5316\u6700\u5FEB\uFF0C\u8DDF\u8E2A\u9875\u7684\u8BFB\u6570\u4E5F\u6700\u96BE\u770B\u3002",
      tleAgeDays: 2,
      fav: true,
      curDownMhz: 145.987654,
      curUpMhz: 145.99,
      curToneHz: 67,
      curMode: "FM",
      txLabel: "FM \xB7 67.0 Hz",
      tx: TX
    },
    next: {
      has: true,
      ms: nextMs,
      countdown: "\u8FD8\u6709 12 \u5206 20 \u79D2",
      aos: Date.now() + nextMs,
      aosText: "20:41",
      losText: "20:49",
      maxEl: 62,
      maxElText: "62",
      maxElAt: Date.now() + nextMs + 24e4,
      durS: 486,
      durText: "8 \u5206 06 \u79D2",
      azAos: 212,
      azLos: 34,
      live: false,
      norad: 25544,
      satName: "ISS (ZARYA)"
    },
    passes: PASSES,
    cart: {
      count: 2,
      satCount: 2,
      bytes: 4096,
      fill: 0.0625,
      tooMany: false,
      max: 32,
      items: [
        { norad: 25544, name: "ISS (ZARYA)", aos: Date.now() + nextMs, aosText: "20:41", durS: 486, maxEl: 62 },
        { norad: 7530, name: "FOX-1B (AO-91)", aos: 0, aosText: "21:22", durS: 528, maxEl: 74 }
      ]
    },
    track: {
      doppHz: -12700,
      azDeg: 212,
      elDeg: 41,
      rangeKm: 1842.6,
      rateKmS: -5.42,
      remainS: 312,
      progress: 0.36,
      inPass: true,
      aosMs: 0,
      losMs: 0,
      trimHz: -300,
      errDeg: 2.4,
      aligned: true,
      sim: false,
      marks: 3,
      live: true,
      stationOk: true
    },
    list: {
      rows: PASSES.map((p) => ({ ...p })),
      tl: {
        rows: [
          { norad: 25544, name: "ISS (ZARYA)", sub: "20:41 \xB7 62\xB0" },
          { norad: 43017, name: "CAS-4B", sub: "21:03 \xB7 28\xB0" },
          { norad: 7530, name: "FOX-1B (AO-91)", sub: "21:22 \xB7 74\xB0" },
          { norad: 40967, name: "AO-73 (FUNcube-1)", sub: "22:14 \xB7 12\xB0" },
          { norad: 43678, name: "CAS-4A", sub: "23:07 \xB7 41\xB0" }
        ],
        t0: Date.now(),
        spanMs: 216e5,
        stepMs: 18e5
      }
    },
    sky: {
      count: 27,
      rx: 3,
      busy: false,
      off: 0,
      selNorad: 25544,
      sel: { has: true, name: "ISS (ZARYA)", az: 212, el: 41, mode: "FM", rxOk: true, los: false },
      next: { has: true, name: "CAS-4B", countdown: "\u8FD8\u6709 34 \u5206 41 \u79D2" },
      dots: [
        { norad: 25544, az: 212, el: 41, name: "ISS (ZARYA)" },
        { norad: 43017, az: 18, el: 12, name: "CAS-4B" },
        { norad: 7530, az: 350, el: 8, name: "FOX-1B (AO-91)" }
      ]
    },
    settings: { rows: SETTINGS_ROWS, aboutRows: 4, head: "\u8BBE\u7F6E" },
    ui: {
      // 详情页
      loading: false,
      empty: false,
      stationOk: true,
      sortEl: 10,
      minEl: 10,
      count: 5,
      inCart: 2,
      // 列表页
      sub: "24 \u5C0F\u65F6\u5185 \xB7 5 \u6B21",
      hits: 5,
      filtered: true,
      filterSummary: "2m \xB7 FM \xB7 \u226510\xB0",
      noStation: false,
      computing: false,
      progress: 0.62,
      scanText: "\u626B\u63CF 627 \u9897",
      query: "",
      isTable: true,
      isTimeline: false,
      sheetFilter: false,
      sheetSort: false,
      sortLabel: "\u6309 AOS",
      sortPopular: false,
      sortAos: true,
      sortName: false,
      sortNorad: false,
      pin: 0,
      span3: false,
      span6: true,
      span12: false,
      span24: false,
      spanHours: 6,
      tleHint: "TLE 2 \u5929\u524D",
      tleBad: false,
      b2m: true,
      b70: true,
      b23cm: false,
      b6m: false,
      bOther: false,
      mFM: true,
      mSSB: false,
      mCW: false,
      favOnly: false,
      up: "\u2191 145.990",
      soon: "12 \u5206",
      // 跟踪页
      connected: true,
      notConnected: false,
      connHint: "",
      status: "\u9501\u5B9A",
      statusColor: "ok",
      statusFill: "ok@14",
      linkText: "USB \xB7 57600",
      rxText: "\u63A5\u6536\u4E2D",
      rxColor: "ok",
      txLabel: "\u53D1\u5C04",
      txText: "145.990",
      txColor: "acc",
      doppText: "-12.7 kHz",
      doppColor: "warn",
      txShiftText: "\u2193 12.7",
      rangeText: "1842.6 km",
      rateText: "-5.42 km/s",
      azElText: "212\xB0 / 41\xB0",
      azElColor: "txt",
      remainText: "5 \u5206 12 \u79D2",
      remainColor: "txt",
      errLabel: "\u7F57\u76D8",
      errText: "2.4\xB0",
      errColor: "dim",
      scaleText: "\xB150 kHz",
      hint: "\u6309\u4F4F M \u9884\u542C",
      hintColor: "dim",
      trimText: "-300 Hz",
      trimColor: "acc",
      progressPct: "36%",
      progressText2: "36%",
      windowText: "20:41 \u2192 20:49",
      note: "",
      framesText: "48 \u5E27",
      canMark: true,
      simOn: false,
      compassAz: 212,
      compassEl: 41,
      lineAz: 212,
      lineEl: 41,
      lineDashed: true,
      lineLabel: "ISS",
      axis: "AZ",
      gain: 1,
      // 设置页
      banner: "\u6F14\u793A\u6570\u636E\u5F00\u7740\uFF0C\u8FC7\u5883\u662F\u5047\u7684",
      hasBanner: true,
      isErr: false,
      night: true,
      seedHex: "#35C7E8",
      preset: "\u6D77\u84DD",
      themeLine: "H 196\xB0 S 76% V 91%",
      hueText: "196\xB0",
      satText: "76%",
      valText: "91%",
      verLine: "2.0.0-m5 \xB7 build 117",
      aboutFoot: "BG0FPJ \xB7 F4HWN"
    },
    item: PASSES[0],
    i: 1,
    sel: { has: true, name: "ISS (ZARYA)", az: 212, el: 41, mode: "FM", rxOk: true, los: false },
    tl: { rows: 12, t0: Date.now(), spanMs: 216e5, stepMs: 18e5 }
  };
}
var FIXTURE_NAMES = ["ISS (ZARYA)", "CAS-4B", "FOX-1B (AO-91)"];
var FIXTURE_READINGS = ["145.987654 MHz", "-12.7 kHz"];
function rowsAt(scope, path2) {
  let cur = scope;
  for (const part of path2.split(".")) {
    if (cur === null || cur === void 0 || typeof cur !== "object") return null;
    if (Array.isArray(cur)) {
      const idx = Number(part);
      cur = Number.isFinite(idx) ? cur[idx] : void 0;
    } else {
      cur = cur[part];
    }
  }
  return Array.isArray(cur) ? cur : null;
}

// src/core/resolve.ts
function resolveRect(n, designPW, designPH, actualPW, actualPH) {
  const f = anchorFlags(n.anchor);
  const leftInset = n.x;
  const rightInset = designPW - n.x - n.w;
  const topInset = n.y;
  const bottomInset = designPH - n.y - n.h;
  let rx = leftInset;
  let rw = n.w;
  if (f.l && f.r) {
    rx = leftInset;
    rw = Math.max(actualPW - leftInset - rightInset, 0);
  } else if (f.r) {
    rx = actualPW - rightInset - n.w;
  }
  let ry = topInset;
  let rh = n.h;
  if (f.t && f.b) {
    ry = topInset;
    rh = Math.max(actualPH - topInset - bottomInset, 0);
  } else if (f.b) {
    ry = actualPH - bottomInset - n.h;
  }
  return { x: rx, y: ry, w: rw, h: rh };
}

// tools/verify.ts
var layoutDir = process.argv[2] ?? "";
if (!layoutDir) {
  console.error("\u7528\u6CD5: node tools/verify.mjs <layout \u76EE\u5F55>");
  process.exit(2);
}
var failures = 0;
function ok(cond, msg) {
  if (cond) {
    console.log("  \u2713 " + msg);
  } else {
    failures += 1;
    console.log("  \u2717 " + msg);
  }
}
function readJson(file) {
  return parseJsonc(fs.readFileSync(file, "utf8"));
}
function diff(a, b, at = "", out = []) {
  if (a === b) return out;
  if (a === void 0) {
    out.push(at + " (\u4EC5\u65B0\u6587\u4EF6\u6709)");
    return out;
  }
  if (b === void 0) {
    out.push(at + " (\u4EC5\u539F\u6587\u4EF6\u6709)");
    return out;
  }
  const ta = typeof a;
  const tb = typeof b;
  if (ta !== tb || a === null || b === null) {
    out.push(`${at}: ${JSON.stringify(a)} \u2192 ${JSON.stringify(b)}`);
    return out;
  }
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b)) {
      out.push(`${at}: \u6570\u7EC4/\u975E\u6570\u7EC4`);
      return out;
    }
    if (a.length !== b.length) out.push(`${at}: \u957F\u5EA6 ${a.length} \u2192 ${b.length}`);
    const n = Math.min(a.length, b.length);
    for (let i = 0; i < n; i += 1) diff(a[i], b[i], `${at}[${i}]`, out);
    return out;
  }
  if (ta === "object") {
    const oa = a;
    const ob = b;
    const keys = /* @__PURE__ */ new Set([...Object.keys(oa), ...Object.keys(ob)]);
    for (const k of [...keys].sort()) diff(oa[k], ob[k], at ? `${at}.${k}` : k, out);
    return out;
  }
  out.push(`${at}: ${JSON.stringify(a)} \u2192 ${JSON.stringify(b)}`);
  return out;
}
function walkNodes(doc, fn) {
  const rec = (n, screen, trail) => {
    fn(n, screen, trail);
    n.children.forEach((c, i) => rec(c, screen, `${trail}.children[${i}]`));
  };
  for (const s of doc.screens) s.nodes.forEach((n, i) => rec(n, s.id, `screens.${s.id}.nodes[${i}]`));
}
console.log("\n[1] \u4E94\u4EFD\u771F\u6E90\u5F80\u8FD4\uFF08\u89E3\u6790 \u2192 \u6A21\u578B \u2192 \u5BFC\u51FA\uFF09");
var files = ["detail.json", "list.json", "sky.json", "track.json", "settings.json"];
var docs = /* @__PURE__ */ new Map();
for (const f of files) {
  const p = path.join(layoutDir, f);
  const src = readJson(p);
  const doc = docFromJson(src, f);
  docs.set(f, doc);
  const back = docToJson(doc);
  const d = diff(src, back);
  console.log(`  ${f}: ${doc.screens.length} \u5C4F / ${doc.screens.reduce((a, s) => a + s.nodes.length, 0)} \u9876\u5C42\u8282\u70B9`);
  ok(d.length === 0, `${f} \u5BFC\u51FA\u4E0E\u539F\u6587\u4EF6\u8BED\u4E49\u4E00\u81F4${d.length ? "\uFF1A" + d.slice(0, 6).join(" | ") : ""}`);
}
console.log("\n[2] \u62D6\u52A8 detail \u7684\u300C\u8DDF\u8E2A\u300D\u6309\u94AE \u2192 \u5BFC\u51FA diff");
{
  const file = "detail.json";
  const src = readJson(path.join(layoutDir, file));
  const doc = docFromJson(src, file);
  let target = null;
  let trail = "";
  walkNodes(doc, (n, _s, t) => {
    if (!target && n.props["text"] === "\u8DDF\u8E2A") {
      target = n;
      trail = t;
    }
  });
  ok(!!target, "\u627E\u5230 text=\u8DDF\u8E2A \u7684\u8282\u70B9" + (target ? `\uFF08${trail}\uFF09` : ""));
  if (target) {
    const before = docToJson(doc);
    const t = target;
    const oldX = t.x;
    t.x = oldX + 12;
    t.y = t.y + 4;
    const after = docToJson(doc);
    const d = diff(before, after);
    console.log("  diff: " + (d.length ? d.join(" | ") : "(\u7A7A)"));
    ok(d.length === 2, "\u53EA\u6709\u4E24\u6761\u5DEE\u5F02\uFF08x \u4E0E y\uFF09");
    ok(d.every((p) => p.startsWith(trail + ".")), "\u5DEE\u5F02\u90FD\u843D\u5728\u76EE\u6807\u8282\u70B9\u8DEF\u5F84\u4E0A");
    ok(trail === "screens.detail.nodes[3]", `\u76EE\u6807\u8DEF\u5F84\u662F ${trail}`);
    const beforeNodes = JSON.stringify(before).length;
    ok(beforeNodes > 1e3, "\u5BFC\u51FA\u4F53\u91CF\u6B63\u5E38");
  }
}
console.log("\n[3] \u6821\u9A8C\u6545\u610F\u5199\u574F\u7684\u6587\u4EF6");
{
  const bad = parseJsonc(`{
    "version": 99,
    "screens": {
      "bad": { "nodes": [
        { "type": "blink", "x": 10, "y": 10, "w": 50, "h": 20, "text": "\u8C01" },
        { "type": "text", "x": 10, "y": 40, "w": 120, "h": 20,
          "text": "\u597D", "visible": "{sat.rxOk}", "size": 12 },
        { "type": "button", "x": 10, "y": 80, "w": 30, "h": 20, "text": "\u5C0F", "onClick": "nav.back" }
      ]}
    }
  }`);
  const doc = docFromJson(bad, "bad.json");
  const issues = validateDoc(doc, null);
  for (const i of issues) console.log(`  ${i.level} ${i.screen} ${i.path} ${i.field} \u2014 ${i.message}`);
  const has = (pred) => issues.some((i) => i.level === "E" && pred(i.message, i.field, i.path));
  ok(has((m) => m.includes("\u7248\u672C") || m.includes("version")), "\u62A5\u51FA\u7248\u672C\u4E0D\u5BF9\uFF08version 99\uFF09");
  ok(has((m) => m.includes("\u4E0D\u8BA4\u8BC6") || m.includes("\u672A\u77E5")), "\u62A5\u51FA\u672A\u77E5 type");
  ok(has((m, f) => f === "visible" && (m.includes("\u82B1\u62EC\u53F7") || m.includes("\u88F8\u8868\u8FBE\u5F0F"))), "\u62A5\u51FA visible \u5E26\u82B1\u62EC\u53F7");
  ok(issues.every((i) => !i.path.includes("nodes[") || i.uid !== void 0), "\u8282\u70B9\u7EA7\u9519\u8BEF\u90FD\u5E26 uid\uFF08\u80FD\u8DF3\u8F6C\uFF09");
  ok(issues.every((i) => i.screen !== "" && i.path !== ""), "\u6BCF\u6761\u9519\u8BEF\u90FD\u6709\u5C4F\u540D\u4E0E\u4F4D\u7F6E\u8DEF\u5F84");
}
console.log("\n[4] \u9884\u89C8\u5047\u6570\u636E + \u6A21\u677F\u6E32\u67D3");
{
  const scope = previewScope();
  const doc = docs.get("list.json");
  ok(!!doc, "list.json \u5DF2\u8F7D\u5165");
  const rendered = [];
  const bad = [];
  const renderOne = (n, sc) => {
    for (const key of ["text", "empty", "hint"]) {
      const v = n.props[key];
      if (typeof v !== "string" || v.trim() === "") continue;
      try {
        rendered.push(template(v, sc));
      } catch (e) {
        bad.push(`${key}: ${v} \u2192 ${String(e)}`);
      }
    }
  };
  if (doc) {
    const rec = (n, sc) => {
      renderOne(n, sc);
      if (n.type === "list") {
        const itemsPath = typeof n.props["items"] === "string" ? n.props["items"] : "";
        const rows = rowsAt(scope, itemsPath) ?? [];
        rows.forEach((item, i) => {
          const rowScope = { ...scope, item, i };
          n.children.forEach((c) => rec(c, rowScope));
        });
        return;
      }
      n.children.forEach((c) => rec(c, sc));
    };
    doc.screens.forEach((s) => s.nodes.forEach((n) => rec(n, scope)));
  }
  console.log(`  list.json \u6E32\u67D3\u51FA ${rendered.length} \u6BB5\u6587\u672C`);
  const detail = docs.get("detail.json");
  if (detail) {
    const rec2 = (n) => {
      for (const key of ["text", "empty"]) {
        const v = n.props[key];
        if (typeof v !== "string" || v.trim() === "") continue;
        try {
          rendered.push(template(v, scope));
        } catch (e) {
          bad.push(`detail ${key}: ${v} \u2192 ${String(e)}`);
        }
      }
      n.children.forEach(rec2);
    };
    detail.screens.forEach((s) => s.nodes.forEach(rec2));
  }
  ok(bad.length === 0, `list.json \u5168\u90E8\u6A21\u677F\u80FD\u6C42\u503C${bad.length ? "\uFF1A" + bad[0] : ""}`);
  const all = rendered.join("\n");
  ok(all.includes("ISS (ZARYA)"), "\u5047\u6570\u636E\u542B\u6700\u957F\u540D ISS (ZARYA)");
  ok(all.includes("CAS-4B") && all.includes("FOX-1B (AO-91)"), "\u5047\u6570\u636E\u542B CAS-4B / FOX-1B (AO-91)");
  console.log("  \u542B 145 \u7684\u6BB5\u843D: " + rendered.filter((x) => x.includes("145")).slice(0, 8).join(" | "));
  console.log("  \u542B MHz \u7684\u6BB5\u843D: " + rendered.filter((x) => x.includes("MHz")).slice(0, 8).join(" | "));
  ok(all.includes("145.98"), "\u8BFB\u6570\u542B 145.987654 MHz \u7684\u8D85\u957F\u5C0F\u6570");
  ok(FIXTURE_NAMES.length >= 3 && FIXTURE_READINGS.length >= 2, "fixtures \u66B4\u9732\u540D\u5B57/\u8BFB\u6570\u8868");
  const dt = asStr(evalExpr("fmt.freq(sat.downMhz*1000000, 3)", scope));
  console.log("  fmt.freq(145.987654e6, 3) = " + dt);
  ok(dt === "145.988", "fmt.freq \u53E3\u5F84\u4E0E Expr.kt \u4E00\u81F4\uFF083 \u4F4D\u5C0F\u6570\uFF09");
  const kh = asStr(evalExpr("fmt.khz(track.doppHz, 1)", scope));
  console.log("  fmt.khz(-12700, 1) = " + kh);
  ok(kh === "-12.7", "\u591A\u666E\u52D2\u8BFB\u6570 -12.7 kHz");
  const tr = docs.get("track.json");
  ok(!!tr, "track.json \u5DF2\u8F7D\u5165");
  if (tr) {
    const texts = [];
    walkNodes(tr, (n) => {
      for (const key of ["text", "empty"]) {
        const v = n.props[key];
        if (typeof v !== "string" || v.trim() === "") continue;
        try {
          texts.push(template(v, scope));
        } catch (e) {
          bad.push(`track ${key}: ${v} \u2192 ${String(e)}`);
        }
      }
    });
    const t = texts.join("\n");
    console.log("  track \u6E32\u67D3\u6BB5\u843D\u793A\u4F8B: " + texts.slice(0, 6).join(" / "));
    ok(bad.length === 0, "track.json \u5168\u90E8\u6A21\u677F\u80FD\u6C42\u503C");
    ok(t.includes("-12.7") || t.includes("\u221212.7"), "track \u9875\u51FA\u73B0 -12.7 kHz \u91CF\u7EA7\u8BFB\u6570");
  }
}
console.log("\n[5] \u951A\u70B9\u89E3\u7B97\uFF08\u5BF9\u7167 LayoutDoc.resolve\uFF09");
{
  const base = { x: 20, y: 30, w: 100, h: 40 };
  const lrtb = resolveRect({ ...base, anchor: "lrtb" }, 360, 780, 720, 1560);
  console.log("  lrtb \u2192", JSON.stringify(lrtb));
  ok(lrtb.x === 20 && lrtb.w === 720 - 20 - (360 - 20 - 100), "lrtb\uFF1Ax \u53D6\u5DE6\u5185\u8FB9\u8DDD\uFF0Cw \u5403\u6389\u5DE6\u53F3\u5185\u8FB9\u8DDD");
  const rt = resolveRect({ x: 200, y: 30, w: 100, h: 40, anchor: "rt" }, 360, 780, 720, 1560);
  console.log("  rt \u2192", JSON.stringify(rt));
  ok(rt.x === 720 - (360 - 200 - 100) - 100 && rt.w === 100, "rt\uFF1Ax \u4ECE\u53F3\u8FB9\u53CD\u63A8\uFF0Cw \u4E0D\u53D8");
  const lt = resolveRect({ ...base, anchor: "lt" }, 360, 780, 720, 1560);
  ok(lt.x === 20 && lt.w === 100 && lt.y === 30 && lt.h === 40, "lt\uFF1A\u539F\u6837");
}
console.log("\n" + (failures === 0 ? "\u5168\u90E8\u901A\u8FC7 \u2713" : `${failures} \u9879\u5931\u8D25 \u2717`));
process.exit(failures === 0 ? 0 : 1);
