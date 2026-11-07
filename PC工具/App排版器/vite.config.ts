import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * 开发期直读手机上那份真源：`?open=sample/detail.json` 直接打开 doppler-ng 里的文件。
 * 只读，不落盘；路径相对本文件，绝对路径一个都不写。
 * 生产构建里这个插件不生效（apply: 'serve'），所以 dist 不依赖它。
 */
function sampleServer(): Plugin {
  const LAYOUT = fileURLToPath(new URL('../../../doppler-ng/app/src/main/assets/layout', import.meta.url))
  // 自检用的假文件（故意写坏的那份在这里，不碰 doppler-ng）
  const FIXTURES = fileURLToPath(new URL('./tools/fixtures', import.meta.url))
  const serveDir = (dir: string) => (req: { url?: string }, res: {
    statusCode: number
    setHeader(k: string, v: string): void
    end(b?: string): void
  }, next: () => void): void => {
    const name = (req.url ?? '').replace(/^\//, '').split('?')[0]
    if (!/^[a-z0-9._-]+$/i.test(name)) {
      next()
      return
    }
    const p = path.join(dir, name)
    if (!fs.existsSync(p)) {
      res.statusCode = 404
      res.end('no such layout: ' + name)
      return
    }
    res.setHeader('Content-Type', 'application/json; charset=utf-8')
    res.end(fs.readFileSync(p, 'utf8'))
  }
  return {
    name: 'doppler-layout-samples',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use('/sample', serveDir(LAYOUT))
      server.middlewares.use('/fixture', serveDir(FIXTURES))
    },
  }
}

// 不写死任何绝对路径：dist 是相对产物，目录名带中文也不影响构建。
export default defineConfig({
  plugins: [react(), sampleServer()],
  base: './',
  server: {
    host: '127.0.0.1',
    port: 5273,
    watch: {
      // Windows 上 npm 写 package.json 会先落在 .package.json.<pid>.*.tmpdir 里，
      // vite 的 watcher 撞上这种瞬时目录会 EBUSY 直接把 dev server 打死。
      ignored: ['**/.*.tmpdir/**', '**/node_modules/**', '**/dist/**', '**/shots/**'],
    },
  },
  build: { outDir: 'dist', target: 'es2022', sourcemap: false },
})
