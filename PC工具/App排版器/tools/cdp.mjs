// 极简 CDP 客户端：连本机 Edge 的调试端口，跑一段 JS，把结果打出来。
// 用法：node tools/cdp.mjs "<js 表达式>" [--port 9222] [--page 0]
// 用途：量真实 DOM 几何 / 点按钮 / 切模式，比截图靠眼睛快。
const args = process.argv.slice(2)
const expr = args[0]
const portArg = args.indexOf('--port')
const port = portArg >= 0 ? Number(args[portArg + 1]) : 9222
const pageArg = args.indexOf('--page')
const pageIdx = pageArg >= 0 ? Number(args[pageArg + 1]) : 0
const shotArg = args.indexOf('--shot')
const shotFile = shotArg >= 0 ? args[shotArg + 1] : ''
// --drag x1,y1,x2,y2 —— 用真实鼠标事件拖一把（合成事件 React 的 pointer capture 不认）
const dragArg = args.indexOf('--drag')
const drag = dragArg >= 0 ? args[dragArg + 1].split(',').map(Number) : null

if (!expr) {
  console.error('用法: node tools/cdp.mjs "<js>" [--port 9222] [--page 0]')
  process.exit(2)
}

const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
const pages = list.filter((t) => t.type === 'page')
const page = pages[pageIdx]
if (!page) {
  console.error('没有可用的 page target：' + JSON.stringify(list.map((t) => t.type)))
  process.exit(3)
}

const ws = new WebSocket(page.webSocketDebuggerUrl)
let id = 0
const pending = new Map()

const send = (method, params) =>
  new Promise((resolve, reject) => {
    const msgId = ++id
    pending.set(msgId, { resolve, reject })
    ws.send(JSON.stringify({ id: msgId, method, params }))
  })

ws.addEventListener('message', (ev) => {
  const msg = JSON.parse(String(ev.data))
  if (msg.id && pending.has(msg.id)) {
    const p = pending.get(msg.id)
    pending.delete(msg.id)
    if (msg.error) p.reject(new Error(JSON.stringify(msg.error)))
    else p.resolve(msg.result)
  }
})

await new Promise((resolve, reject) => {
  ws.addEventListener('open', resolve, { once: true })
  ws.addEventListener('error', () => reject(new Error('WS 连接失败')), { once: true })
})

const res = await send('Runtime.evaluate', {
  expression: `(async () => { ${expr.includes('return ') ? expr : 'return (' + expr + ')'} })()`,
  awaitPromise: true,
  returnByValue: true,
  userGesture: true,
})

if (res.exceptionDetails) {
  console.error('页面异常:', JSON.stringify(res.exceptionDetails, null, 2))
  process.exit(1)
}
console.log(typeof res.result.value === 'string' ? res.result.value : JSON.stringify(res.result.value, null, 2))

if (shotFile) {
  const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false })
  const { writeFileSync } = await import('node:fs')
  writeFileSync(shotFile, Buffer.from(shot.data, 'base64'))
  console.log('screenshot -> ' + shotFile)
}

if (drag) {
  const [x1, y1, x2, y2] = drag
  const mouse = (type, x, y, buttons) =>
    send('Input.dispatchMouseEvent', { type, x, y, button: 'left', buttons, clickCount: 1 })
  await mouse('mouseMoved', x1, y1, 0)
  await mouse('mousePressed', x1, y1, 1)
  for (let i = 1; i <= 6; i++) {
    await mouse('mouseMoved', x1 + ((x2 - x1) * i) / 6, y1 + ((y2 - y1) * i) / 6, 1)
    await new Promise((r) => setTimeout(r, 16))
  }
  await mouse('mouseReleased', x2, y2, 0)
  console.log('dragged ' + JSON.stringify(drag))
}

ws.close()
process.exit(0)
