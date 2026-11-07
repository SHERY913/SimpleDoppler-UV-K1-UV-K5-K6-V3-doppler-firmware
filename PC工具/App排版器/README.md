# App 排版器（doppler-ng 版面编辑）

给作者自己用的网页版版面编辑器：打开 `doppler-ng/app/src/main/assets/layout/*.json`，在 360×780 的设计画布上改版面，导出回同名字文件，`adb push` 进手机。零后端、零 UI 框架，纯 Vite + React + TS。

导出的 JSON 与 App 端渲染器（`LayoutDoc.kt` / `Render.kt` / `Expr.kt`）逐键对齐：字段名、锚点解算、文字自动缩排、颜色 token 都照抄，不做「差不多」。

## 起

```bash
npm i
npm run dev      # http://127.0.0.1:5273
npm run build    # tsc --noEmit && vite build → dist/
npm run verify   # 与 doppler-ng 的五份真源做往返 diff（需要同级目录里有 doppler-ng）
```

`npm run build` 产物是相对路径（`base: './'`），`dist/` 整个丢到任意静态服务器都能开，不需要 Node。

Node 版本：本机 v24 实测通过，`vite 5` 要 Node ≥ 18。

## 打开 / 导出

- **打开**：`打开` 按钮 → 选 `*.json`（可含多屏）。解析容忍 `//` 与 `/* */` 注释（真源里就带着注释）。
- **导出**：`导出` → 下载同名文件。导出前会自动跑一遍校验，有 `E` 就不拦你，但表格会摆出来。
- **开发期直读真源**：`npm run dev` 时可用 `?open=sample/detail.json` 直接打开 `doppler-ng/.../layout/detail.json`（`sample/` 是 dev 中间件，只读；生产构建里没有这个路由）。
- **直接看某一份的写坏版**：`?open=fixture/broken.json`，`tools/fixtures/` 下放自检用的假文件。

落到手机上：

```bash
adb push detail.json /sdcard/Download/layout.json   # 调试旁路：App 会优先读这个
# 或正式覆盖（需要 adb root / run-as，视机型）：
adb push detail.json /data/data/com.f4hwn.doppler2/files/layout/detail.json
```

一屏一个文件，文件名 = `screens` 里的键名（`detail.json` / `list.json` / `sky.json` / `track.json` / `settings.json`）。

## 画布

- 360×780 设计尺寸，等比缩放居中；滚轮缩放（以光标为中心）、空格 + 拖拽平移、`Ctrl+0` 复位。
- 网格 0 / 1 / 4 / 8 dp 可切（`G` 循环），`L` 切状态栏/底栏参考线（24 / 48 / 底 56）。
- 点选、`Shift` 加选、空白处框选；拖动移动（方向键 1dp、`Shift`+方向键 10dp）。
- 八向手柄缩放；旋转手柄 15° 吸附，写进 `props.rotate`。
- 吸附：同级左/中/右/上/中/下六条线 + 父边界 + 网格，阈值 4dp；吸附时画粉线。
- 图层树：折叠、拖拽改序（拖进容器里是「放进 children」）、双击改名、锁/隐开关、`+分组` 成组（坐标自动换算成组内相对坐标）。

## 属性面板

按节点类型出字段（矩形 `fill/fill2/radius/border/borderW/alpha`；文字 `text/size/weight/font/align/maxLines/lineHeight/letterSpacing`；按钮 `fill/fillPressed/text/icon/onClick`…）。颜色输入支持 token 芯片、`#RRGGBB`、`#AARRGGBB`（ARGB，注意不是 RGBA）、`#RGB`，以及 `@NN` 不透明度后缀（`acc@14`）。

字符串字段旁的 `{}` 按钮开变量树（`Scope.kt` 抽出来的字段表，手写在 `src/data/scope.ts`），点一下插入 `{sat.name}`；函数/管道页签插 `fmt.freq(` 或 `|fixed(2)`。**只拼字符串，不执行任何代码。**

`onClick` 旁的按钮给动作白名单（`src/data/acts.ts`，从五个 `Layout*Screen.kt` 的 `when` 分支抄全）。

## 预览

`预览` 用内置假数据渲染（`src/data/fixtures.ts`：`ISS (ZARYA)` / `CAS-4B` / `FOX-1B (AO-91)`、`145.987654`、`-12.7 kHz`），走的是与画布同一份 `NodeView`：绝对定位 + 锚点 + 自动缩排 + token 颜色。预览里按钮可点，只往右侧日志栏打一行 `动作名 ← 节点名`，不执行。

## 校验

导出前 / `校验` 按钮跑 `src/core/validate.ts`，规则对着 App 端的解析器：

| 级 | 规则 |
| --- | --- |
| E | `version !== 1`（App 端整份拒载） |
| E | `screens` 不是对象 / 没有屏 |
| E | 节点缺 `type` / `type` 不在 14 种里 |
| E | `w <= 0 \|\| h <= 0` |
| E | `visible` / `enabled` 带花括号（必须裸表达式，写 `sat.rxOk` 不是 `{sat.rxOk}`） |
| E | `visible` / `enabled` / 模板里的表达式语法错 |
| E | 父节点不是 `group/box/list/button` 却挂了 children |
| W | 可点节点的点击区 < 48dp、`x/y` 为负、颜色不认识、`onClick` 不在白名单 |

表格一行一条，点一行跳到那个节点。

## 快捷键

`Ctrl+O` 开、`Ctrl+S` 导出、`Ctrl+Z` / `Ctrl+Y` / `Ctrl+Shift+Z` 撤销重做（120 步）、`Ctrl+C/V` 复制粘贴、`Ctrl+D` 原地复制、`Ctrl+Shift+C` 复制样式、`Ctrl+Shift+V` 粘贴样式、`Ctrl+G` / `Ctrl+Shift+G` 成组/解组、`Ctrl+Alt+V` 校验、`Delete` 删除、`Esc` 取消选择、`P` 预览、`G` 网格、`L` 参考线。

## schema 边界（App 端不认的东西）

排版器只往 JSON 里写 App 解析器认识的键，另外有几个「存了但不生效」的：

- `props.rotate` —— 旋转角度本工具会存、会算吸附，但 `Render.kt` 没有读它，**App 端暂不渲染旋转**，属性面板上也标了这句话。导出时留着，等 App 支持了就能直接用。
- `props.fillDisabled` —— `Render.kt` 只读 `fill` / `fillPressed`，禁用态是 `fill` 回落到 `card`。这个键存着不生效。
- `id` / `name` / `locked` —— 解析器会读，但只用于调试与图层树，不影响渲染。
- `props.rotate` 之外的未知 prop 一律原样保留（导出不会丢你没见过的字段），但 App 端同样会忽略。

导出保真细节（都验过：五份真源往返零差异）：

- 原文件里写在嵌套 `props: {...}` 里的键，导出仍回落到嵌套块；摊平写的仍在顶层。这条很关键——`sky.json` 的 `props:{name:"dome"}` 与 `props:{fill:"acc"}` 不能被摊平。
- 原文件没写 `anchor` 的节点，导出也不写（不补 `"lt"`）。
- 数字按 2 位小数取整，`0.1 + 0.2` 之类不会漏到 JSON 里。

## 目录

```
src/core/     jsonc 解析、模型与序列化、锚点解算、表达式（Expr.kt 移植）、文字缩排、颜色、校验
src/data/     scope 字段表、动作白名单、图标名、属性 schema、预览假数据
src/state/    store（useSyncExternalStore，手写 undo/redo）
src/ui/       TopBar / LayerTree / Canvas / Inspector / Preview / 校验弹窗 / 快捷键
tools/        verify.mjs（自检）、cdp.mjs（连 Edge 调试端口量 DOM / 截图）、fixtures/
```

## 自检

```bash
npm run verify   # ①五份真源往返 diff ②拖「跟踪」按钮后导出只差两条坐标 ③坏文件三条可定位错误 ④假数据渲染 ⑤锚点解算
```

`tools/cdp.mjs` 是给开发期量 DOM 用的（需要先起 Edge：`msedge --headless=new --remote-debugging-port=9222`）：

```bash
node tools/cdp.mjs "return document.querySelectorAll('.stage [data-uid]').length"
node tools/cdp.mjs "return document.body.innerText.slice(0,200)" --shot shots/now.png
```

## 已知缺口

- 旋转只存不画（App 端不支持）。
- 预览是静态假数据，不跟随时间变化；列表行按 `rowH` 铺到画布高度为止，不滚动。
- 多屏之间不能拖动节点（App 的版面也是一屏一个文件、节点不跨屏）。
- 撤销是整份文档快照，超过 120 步丢最老的；文档特别大时（几百节点）每步快照在几 MB 级。
- 未做字体度量：文字缩排用「中文 1em / ASCII 0.56em」估算，与 Android 的实际排版会有 1–2px 的差。
