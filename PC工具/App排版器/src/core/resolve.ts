/**
 * 锚点解算 —— LNode.resolve 的逐行移植。
 * 编辑画布在设计尺寸下解算时恒等于 x/y/w/h；预览只有一处非设计尺寸
 * （手机屏不是 360×780 时），所以两处共用这一个函数。
 */
import { anchorFlags } from './model'

export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

export interface NodeGeom {
  x: number
  y: number
  w: number
  h: number
  anchor: string
}

export function resolveRect(
  n: NodeGeom,
  designPW: number,
  designPH: number,
  actualPW: number,
  actualPH: number,
): Rect {
  const f = anchorFlags(n.anchor)
  const leftInset = n.x
  const rightInset = designPW - n.x - n.w
  const topInset = n.y
  const bottomInset = designPH - n.y - n.h

  let rx = leftInset
  let rw = n.w
  if (f.l && f.r) {
    rx = leftInset
    rw = Math.max(actualPW - leftInset - rightInset, 0)
  } else if (f.r) {
    rx = actualPW - rightInset - n.w
  }

  let ry = topInset
  let rh = n.h
  if (f.t && f.b) {
    ry = topInset
    rh = Math.max(actualPH - topInset - bottomInset, 0)
  } else if (f.b) {
    ry = actualPH - bottomInset - n.h
  }
  return { x: rx, y: ry, w: rw, h: rh }
}

/** 设计坐标下（编辑画布）某节点的绝对矩形：把各级 offset 加起来即可。 */
export function absRect(path: { x: number; y: number; w: number; h: number }[]): Rect {
  let x = 0
  let y = 0
  for (const p of path) {
    x += p.x
    y += p.y
  }
  const last = path[path.length - 1]
  return { x, y, w: last ? last.w : 0, h: last ? last.h : 0 }
}
