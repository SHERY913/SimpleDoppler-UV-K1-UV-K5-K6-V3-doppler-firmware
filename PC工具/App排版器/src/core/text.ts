/**
 * 文字度量 —— 复刻 Render.kt 的 AutoText。
 *
 * em = Σ(码点 > 0x2E80 ? 1.0 : 0.56)，即中文 1em、ASCII 0.56em；
 * 然后 while (sp0 > size*0.6 && em*sp0 > maxWidth*maxLines) sp0 *= 0.94。
 *
 * 这是估算不是测量（Compose 用的是真字形宽度），排版器也照抄，
 * 目的只是让预览和真机"看起来一样紧"，而不是算出精确像素。
 */
export function emWidth(text: string): number {
  let em = 0
  for (const ch of text) {
    const cp = ch.codePointAt(0) ?? 0
    em += cp > 0x2e80 ? 1.0 : 0.56
  }
  return em
}

export function shrinkFontSize(
  size0: number,
  text: string,
  maxWidthDp: number,
  maxLines: number,
): number {
  let sp = size0
  const em = emWidth(text)
  const lines = Math.max(1, maxLines)
  while (sp > size0 * 0.6 && em * sp > maxWidthDp * lines) sp *= 0.94
  return sp
}

export function lineCount(text: string, sp: number, maxWidth: number): number {
  const em = emWidth(text)
  if (em <= 0) return 1
  return Math.max(1, Math.ceil((em * sp) / Math.max(1, maxWidth)))
}

export const WEIGHTS: Record<string, number> = {
  light: 300,
  normal: 400,
  medium: 500,
  semibold: 600,
  bold: 700,
}

export const FONTS: Record<string, string> = {
  sans: '"Noto Sans SC", "Segoe UI", system-ui, sans-serif',
  mono: '"JetBrains Mono", "Cascadia Mono", Consolas, monospace',
  serif: '"Noto Serif SC", Georgia, serif',
}
