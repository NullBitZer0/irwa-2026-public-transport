/**
 * Render smoke test.
 *
 * A successful `vite build` does not mean the app renders. A missing prop is
 * not a build error — it is a TypeError the first time the component renders,
 * which shows the user a blank page while every check in CI stays green.
 *
 * That happened here: App rendered <Sidebar> without `pendingHolds` or
 * `onResumePayment`, Sidebar read `pendingHolds.length`, and the whole UI died on
 * mount. The build passed, the backend tests passed, and the demo was a white
 * screen.
 *
 * So: check the props statically (cheap, and names the exact missing prop), then
 * render the real app in jsdom to prove it mounts.
 *
 * Run: npm test
 */

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const failures = []

async function check(name, fn) {
  try {
    await fn()
    console.log(`  ok   ${name}`)
  } catch (error) {
    failures.push(name)
    console.log(`  FAIL ${name}\n         ${error.message}`)
  }
}

/** Prop names a component destructures, read from its default export only. */
function destructuredProps(componentFile) {
  const source = readFileSync(resolve(here, componentFile), 'utf8')
  const signature = /export\s+default\s+function\s+\w+\s*\(\{([\s\S]*?)\}\s*\)/.exec(
    source,
  )
  if (!signature) return new Set()

  const names = new Set()
  for (const part of signature[1].split(',')) {
    const name = part.split(':')[0].trim().replace(/^\.\.\./, '')
    if (name && /^[A-Za-z_$][\w$]*$/.test(name)) names.add(name)
  }
  return names
}

/** Prop names passed at every `<Tag ... />` call site in a file. */
function passedProps(callerFile, tag) {
  const source = readFileSync(resolve(here, callerFile), 'utf8')
  const passed = new Set()
  const usage = new RegExp(`<${tag}\\b([\\s\\S]*?)/>`, 'g')
  let site
  while ((site = usage.exec(source)) !== null) {
    for (const part of site[1].split(/\s+/)) {
      const name = part.split('=')[0].trim()
      if (name && /^[A-Za-z_$][\w$]*$/.test(name)) passed.add(name)
    }
  }
  return passed
}

console.log('Checking component props are passed at their call sites...')
for (const [file, tag] of [
  ['src/components/Sidebar.jsx', 'Sidebar'],
  ['src/components/ChatMessage.jsx', 'ChatMessage'],
  ['src/components/PaymentPortal.jsx', 'PaymentPortal'],
  ['src/components/ConversationHistory.jsx', 'ConversationHistory'],
  ['src/components/ConversationViewer.jsx', 'ConversationViewer'],
]) {
  await check(`<${tag}> receives every prop it uses`, () => {
    const needed = destructuredProps(file)
    const passed = passedProps('src/App.jsx', tag)
    const missing = [...needed].filter((prop) => !passed.has(prop))
    if (missing.length) {
      throw new Error(
        `<${tag}> is missing ${missing.join(', ')} — a missing prop is a ` +
          `render-time TypeError, not a build error`,
      )
    }
  })
}

console.log('Rendering <App/> in jsdom...')
await check('App mounts and renders content', async () => {
  const { JSDOM } = await import('jsdom')
  const dom = new JSDOM('<!doctype html><div id="root"></div>', {
    url: 'http://localhost/',
    pretendToBeVisual: true,
  })

  // Node 22 exposes some of these as getter-only globals, so assign defensively.
  const expose = (key, value) => {
    try {
      globalThis[key] = value
    } catch {
      Object.defineProperty(globalThis, key, { value, configurable: true })
    }
  }

  expose('window', dom.window)
  expose('document', dom.window.document)
  expose('navigator', dom.window.navigator)
  expose('HTMLElement', dom.window.HTMLElement)
  expose('Element', dom.window.Element)
  expose('Node', dom.window.Node)
  expose('Event', dom.window.Event)
  expose('requestAnimationFrame', (cb) => setTimeout(cb, 0))
  expose('cancelAnimationFrame', (id) => clearTimeout(id))

  // jsdom implements neither scrollIntoView nor smooth scrolling; the app calls
  // it on every render. Stubbed so the check tests our code, not jsdom's gaps.
  dom.window.Element.prototype.scrollIntoView = () => {}
  dom.window.Element.prototype.scrollTo = () => {}

  // The app calls /api on mount. Stub it so the check is offline and quiet.
  const stubFetch = () =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ status: 'ok', agent: 'orchestrator', version: '1.0.0' }),
    })
  expose('fetch', stubFetch)
  dom.window.fetch = stubFetch

  const [{ createElement }, { createRoot }] = await Promise.all([
    import('react'),
    import('react-dom/client'),
  ])

  // Load through Vite so JSX is transformed.
  const vite = await import('vite')
  const server = await vite.createServer({
    root: here,
    server: { middlewareMode: true },
    appType: 'custom',
    logLevel: 'error',
  })

  try {
    const module = await server.ssrLoadModule('/src/App.jsx')
    const container = dom.window.document.getElementById('root')
    createRoot(container).render(createElement(module.default))

    // Give effects (the health check and purchase fetch) a turn to run.
    await new Promise((r) => setTimeout(r, 400))

    const html = container.innerHTML
    if (!html.trim()) {
      throw new Error('App rendered nothing — the page would be blank')
    }
    if (!html.includes('LankaJourney')) {
      throw new Error('App rendered, but not the app we recognise')
    }
  } finally {
    await server.close()
  }
})

if (failures.length) {
  console.error(`\n${failures.length} check(s) failed`)
  process.exit(1)
}
console.log('\nAll render checks passed')
