import { useEffect, useRef, useState } from 'react'
import { ComoFunciona } from './ComoFunciona'
import { prefersReducedMotion } from './reveal'
import { Resumen } from './Resumen'

type View = 'resumen' | 'como-funciona'

function fromHash(): View {
  return window.location.hash === '#como-funciona' ? 'como-funciona' : 'resumen'
}

function BackToTop() {
  const [show, setShow] = useState(false)
  useEffect(() => {
    const onScroll = () => setShow(window.scrollY > 1200)
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])
  return (
    <button
      type="button"
      aria-label="Volver arriba"
      onClick={() => window.scrollTo({ top: 0, behavior: prefersReducedMotion() ? 'auto' : 'smooth' })}
      className={`fixed bottom-4 right-4 z-20 flex h-11 w-11 items-center justify-center rounded-full bg-navy text-white shadow-lift transition duration-200 hover:bg-navy-2 ${
        show ? 'opacity-100' : 'pointer-events-none translate-y-2 opacity-0'
      }`}
    >
      <svg viewBox="0 0 20 20" className="h-5 w-5" fill="none" aria-hidden>
        <path d="M10 15V5m0 0l-4 4m4-4l4 4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </button>
  )
}

export default function App() {
  const [view, setView] = useState<View>(fromHash)
  const header = useRef<HTMLElement>(null)

  // The sticky section index sits right under the header, so it needs the header height.
  useEffect(() => {
    const el = header.current
    if (!el) return
    const set = () => document.documentElement.style.setProperty('--header-h', `${el.offsetHeight}px`)
    set()
    const ro = new ResizeObserver(set)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    const onHash = () => {
      setView(fromHash())
      window.scrollTo(0, 0)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const tab = (v: View, label: string) => (
    <a
      href={v === 'resumen' ? '#' : '#como-funciona'}
      onClick={(e) => {
        if (v === 'resumen') {
          e.preventDefault()
          history.pushState(null, '', window.location.pathname)
          setView('resumen')
          window.scrollTo(0, 0)
        }
      }}
      aria-current={view === v ? 'page' : undefined}
      className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
        view === v ? 'bg-card text-ink shadow-card' : 'text-ink-2 hover:text-ink'
      }`}
    >
      {label}
    </a>
  )

  return (
    <div className="min-h-screen">
      <header ref={header} className="z-10 border-b border-line bg-card/85 backdrop-blur-md sm:sticky sm:top-0">
        <div className="mx-auto flex max-w-4xl flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
          <div className="flex min-w-0 items-center gap-2.5">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-navy text-white" aria-hidden>
              <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4">
                <path d="M5 10.5l3.2 3.2L15 7" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </span>
            <div className="min-w-0">
              <h1 className="text-base font-semibold tracking-tight sm:text-lg">Cierre trimestral wFIAT</h1>
              <p className="text-xs text-ink-3">Demo independiente para Ripio, por Máximo Sckell</p>
            </div>
          </div>
          <nav className="flex shrink-0 gap-1 rounded-xl bg-paper p-1">
            {tab('resumen', 'Resumen')}
            {tab('como-funciona', 'Cómo funciona')}
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-4xl px-4 pb-16">
        {view === 'resumen' ? <Resumen /> : <ComoFunciona />}
      </main>

      <BackToTop />

      <footer className="border-t border-line bg-card">
        <div className="mx-auto flex max-w-4xl flex-wrap gap-x-4 gap-y-1 px-4 py-6 text-sm text-ink-3">
          <span className="text-ink-2">Máximo Sckell</span>
          <a className="hover:text-ink" href="https://linkedin.com/in/msckell" target="_blank" rel="noreferrer">
            linkedin.com/in/msckell
          </a>
          <a className="hover:text-ink" href="https://github.com/msckell" target="_blank" rel="noreferrer">
            github.com/msckell
          </a>
        </div>
      </footer>
    </div>
  )
}
