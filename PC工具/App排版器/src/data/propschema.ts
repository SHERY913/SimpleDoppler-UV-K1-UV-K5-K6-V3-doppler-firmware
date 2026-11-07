/**
 * 属性面板的字段表 —— 每个类型认哪些 props，完全照 Render.kt。
 * 没列进来的字段仍然能编辑（面板底部有"其它字段"原始表）。
 */
import type { JsonValue } from '../core/jsonc'
import { TOKENS } from '../core/colors'

export type FieldKind =
  | 'text' | 'multiline' | 'num' | 'color' | 'enum' | 'bool'
  | 'expr' | 'path' | 'action' | 'icon' | 'options' | 'template'

export interface FieldDef {
  key: string
  label: string
  kind: FieldKind
  options?: string[]
  def?: JsonValue
  help?: string
  min?: number
  max?: number
  step?: number
}

const colors = TOKENS.map((t) => t.name)

const fill: FieldDef = { key: 'fill', label: '填充', kind: 'color', def: '', help: 'token / #RRGGBB / #AARRGGBB / @NN' }
const radius: FieldDef = { key: 'radius', label: '圆角', kind: 'num', def: 0, min: 0, max: 96 }
const border: FieldDef = { key: 'border', label: '描边色', kind: 'color', def: '' }
const borderW: FieldDef = { key: 'borderW', label: '描边宽', kind: 'num', def: 1, min: 0, max: 24, step: 0.5 }
const alpha: FieldDef = { key: 'alpha', label: '不透明度', kind: 'num', def: 1, min: 0, max: 1, step: 0.05 }

const textCommon: FieldDef[] = [
  { key: 'text', label: '文字', kind: 'template', def: '', help: '带 {} 才是模板' },
  { key: 'size', label: '字号', kind: 'num', def: 14, min: 4, max: 96 },
  { key: 'color', label: '颜色', kind: 'color', def: 'txt' },
  { key: 'weight', label: '字重', kind: 'enum', options: ['light', 'normal', 'medium', 'semibold', 'bold'], def: 'normal' },
  { key: 'font', label: '字体', kind: 'enum', options: ['sans', 'mono', 'serif'], def: 'sans' },
  { key: 'align', label: '对齐', kind: 'enum', options: ['left', 'center', 'right'], def: 'left' },
  { key: 'maxLines', label: '最大行数', kind: 'num', def: 1, min: 1, max: 12 },
  { key: 'lineHeight', label: '行高', kind: 'num', def: 0, min: 0, max: 96, help: '0 = 字号×1.25' },
  { key: 'letterSpacing', label: '字距', kind: 'num', def: 0, min: -4, max: 12, step: 0.1 },
  { key: 'shrink', label: '自动缩排', kind: 'bool', def: true },
]

export const FIELDS: Record<string, FieldDef[]> = {
  group: [fill, radius, border, borderW, alpha],
  box: [
    fill,
    { key: 'fill2', label: '渐变底', kind: 'color', def: '', help: '有值则上下渐变' },
    radius, border, borderW, alpha,
  ],
  text: textCommon,
  button: [
    { key: 'text', label: '文字', kind: 'template', def: '按钮' },
    { key: 'size', label: '字号', kind: 'num', def: 13, min: 6, max: 48 },
    { key: 'color', label: '文字色', kind: 'color', def: 'txt' },
    { key: 'fill', label: '底色', kind: 'color', def: 'acc@14' },
    { key: 'fillPressed', label: '按下底色', kind: 'color', def: 'acc@30' },
    { key: 'fillDisabled', label: '禁用底色', kind: 'color', def: '' },
    { key: 'border', label: '描边色', kind: 'color', def: '' },
    { key: 'radius', label: '圆角', kind: 'num', def: 8, min: 0, max: 96 },
    { key: 'align', label: '对齐', kind: 'enum', options: ['left', 'center', 'right'], def: 'center' },
    { key: 'weight', label: '字重', kind: 'enum', options: ['light', 'normal', 'medium', 'semibold', 'bold'], def: 'medium' },
    { key: 'icon', label: '图标', kind: 'icon', def: '' },
    { key: 'onClick', label: '点击动作', kind: 'action', def: '' },
    { key: 'onLongClick', label: '长按动作', kind: 'action', def: '', help: '留空则只响应点击' },
  ],
  icon: [
    { key: 'name', label: '图标名', kind: 'icon', def: 'star' },
    { key: 'color', label: '颜色', kind: 'color', def: 'dim' },
    { key: 'size', label: '尺寸', kind: 'num', def: 18, min: 6, max: 96 },
  ],
  image: [
    { key: 'name', label: '图片名', kind: 'text', def: '' },
    fill,
    radius,
  ],
  custom: [
    { key: 'name', label: '自绘名', kind: 'text', def: '', help: 'CustomRegistry 里没注册就画占位' },
  ],
  list: [
    { key: 'items', label: '数据源', kind: 'path', def: 'passes', help: '裸路径，不是表达式' },
    { key: 'rowH', label: '行高', kind: 'num', def: 56, min: 8, max: 240 },
    { key: 'gap', label: '行距', kind: 'num', def: 0, min: 0, max: 48 },
    { key: 'empty', label: '空态文字', kind: 'template', def: '' },
  ],
  slider: [
    { key: 'min', label: '最小', kind: 'num', def: 0 },
    { key: 'max', label: '最大', kind: 'num', def: 100 },
    { key: 'value', label: '值', kind: 'expr', def: '0' },
    { key: 'onChange', label: '变更动作', kind: 'action', def: '' },
    fill, radius,
  ],
  toggle: [
    { key: 'text', label: '文字', kind: 'template', def: '开关' },
    { key: 'size', label: '字号', kind: 'num', def: 14, min: 6, max: 48 },
    { key: 'color', label: '颜色', kind: 'color', def: 'txt' },
    { key: 'value', label: '值', kind: 'expr', def: 'false' },
    { key: 'onChange', label: '变更动作', kind: 'action', def: '' },
  ],
  select: [
    { key: 'options', label: '选项', kind: 'options', def: 'A|B|C', help: '数组、| 分隔，或含 {} 的模板' },
    { key: 'value', label: '值', kind: 'expr', def: '' },
    { key: 'onChange', label: '变更动作', kind: 'action', def: '' },
  ],
  input: [
    { key: 'value', label: '值', kind: 'expr', def: '' },
    { key: 'hint', label: '占位', kind: 'template', def: '输入…' },
    fill, border, radius,
    { key: 'singleLine', label: '单行', kind: 'bool', def: true },
  ],
  progress: [
    { key: 'value', label: '进度 0..1', kind: 'expr', def: '0' },
    { key: 'height', label: '条高', kind: 'num', def: 4, min: 1, max: 32 },
    { key: 'color', label: '颜色', kind: 'color', def: 'acc' },
  ],
  spacer: [],
}

export const TYPE_LABEL: Record<string, string> = {
  group: '分组', box: '矩形', text: '文字', button: '按钮', icon: '图标',
  image: '图片', custom: '自绘', list: '列表', slider: '滑条', toggle: '开关',
  select: '选择', input: '输入', progress: '进度', spacer: '占位',
}

export const COLOR_TOKENS = colors

export function fieldsOf(type: string): FieldDef[] {
  return FIELDS[type] ?? []
}
