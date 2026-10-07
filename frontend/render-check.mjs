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
  ['src/components/ProfilePanel.jsx', 'ProfilePanel'],
  ['src/components/Login.jsx', 'Login'],
  ['src/components/Schedules.jsx', 'Schedules'],
  ['src/components/RouteMap.jsx', 'RouteMap'],
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
  //
  // The stub has to answer per endpoint, not with one blanket body: the app is
  // authenticated now, and a stub that returns health JSON for /auth/me leaves
  // it on the login screen — which is the correct behaviour, and would fail a
  // check meant to confirm the chat renders.
  // Deliberately no full name. The sidebar used to render its sign-out button
  // only when a name was set, so a signed-in traveller without one — the demo
  // account, anyone who skipped the profile — had no way to sign out. Asserting
  // against a named user would never catch that.
  const DEMO_USER = {
    id: 'USR-RENDERTEST',
    email: 'demo@lankajourney.lk',
    full_name: '',
    contact_number: '+94771234567',
    has_card: true,
    card_brand: 'Visa',
    card_last4: '4242',
  }

  const stubFetch = (url) => {
    const path = String(url)
    const reply = (body) =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      })

    if (path.includes('/auth/me')) return reply({ status: 'OK', user: DEMO_USER })
    if (path.includes('/health')) {
      return reply({ status: 'ok', agent: 'orchestrator', version: '1.0.0' })
    }
    if (path.includes('/conversations')) return reply({ conversations: [], greeting: 'Hi' })
    if (path.includes('/purchases')) return reply({ purchases: [] })
    if (path.includes('/pending_holds')) return reply({ holds: [] })
    if (path.includes('/schedules')) {
      return reply({
        matched: 2,
        returned: 2,
        truncated: false,
        services: [
          {
            route_id: 'TRAIN-1001',
            mode: 'TRAIN',
            origin: 'Colombo Fort',
            destination: 'Kandy',
            departure_time: '06:00',
            arrival_time: '09:00',
            provider: 'SLR',
            fare_lkr: 850,
            synthetic: false,
          },
          {
            route_id: 'SLTB-1-KAND-COLO-0510',
            mode: 'BUS',
            origin: 'Kandy',
            destination: 'Colombo',
            departure_time: '05:10',
            arrival_time: '08:15',
            provider: 'SLTB',
            fare_lkr: 620,
            synthetic: true,
          },
        ],
      })
    }
    if (path.includes('/map-routes')) {
      return reply({
        outline: [
          [8.98, 79.72],
          [6.93, 79.86],
          [9.66, 80.03],
        ],
        nodes: [{ city: 'Colombo', lat: 6.9271, lng: 79.8612, service_count: 680 }],
        corridors: [
          {
            origin_city: 'Colombo',
            destination_city: 'Kandy',
            from: [6.9271, 79.8612],
            to: [7.2906, 80.6337],
            service_count: 27,
            modes: ['BUS', 'TRAIN'],
            providers: ['SLR', 'SLTB'],
            min_fare_lkr: 620,
            max_fare_lkr: 1250,
            stops: ['Colombo Fort', 'Kandy'],
            synthetic: false,
          },
        ],
        coverage: {
          services_in_scope: 812,
          drawn_as_corridors: 608,
          intra_city_not_drawn: 204,
          unmapped_places: [],
          note: 'Corridors are city-to-city.',
        },
      })
    }
    return reply({ status: 'ok' })
  }
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
    // Signed in: the initials on the profile chip are derived from the account,
    // so they prove the session reached the UI rather than the app sitting on
    // the login screen. Not the email — the chip shows the saved card instead.
    if (!html.includes('>DL<')) {
      throw new Error(
        'Signed-in traveller missing — the session did not reach the UI. ' +
          'Is the app stuck on the login screen?',
      )
    }
    // A way out, with no name on the account.
    if (!html.includes('Sign out')) {
      throw new Error(
        'No sign-out control — this user has no full name, which is exactly ' +
          'when the sidebar used to hide it.',
      )
    }
    // The three destinations the sidebar offers.
    for (const label of ['Chat', 'Timetables', 'Route map']) {
      if (!html.includes(label)) {
        throw new Error(`Sidebar is missing the ${label} view`)
      }
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
