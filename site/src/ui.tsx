import type { ReactNode } from 'react'
import { useReveal } from './reveal'

export function Section({
  id,
  title,
  lead,
  children,
}: {
  id?: string
  title: string
  lead?: ReactNode
  children: ReactNode
}) {
  const [ref, shown] = useReveal<HTMLElement>()
  return (
    <section ref={ref} id={id} className={`reveal mt-12 scroll-mt-20 ${shown ? 'is-visible' : ''}`}>
      <h2 className="text-xl font-semibold tracking-tight text-ink sm:text-[1.65rem]">{title}</h2>
      {lead && <p className="mt-2 max-w-prose text-ink-2">{lead}</p>}
      <div className="mt-5">{children}</div>
    </section>
  )
}

export function Card({
  children,
  className = '',
  flush = false,
}: {
  children: ReactNode
  className?: string
  flush?: boolean // no inner padding, for lists and tables that bring their own
}) {
  return (
    <div className={`rounded-2xl border border-line bg-card shadow-card ${flush ? '' : 'p-4 sm:p-5'} ${className}`}>
      {children}
    </div>
  )
}

// Same deep navy look as the hero, for the few blocks that deserve the eye.
export function NavyCard({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`hero-bg overflow-hidden rounded-2xl p-4 text-white shadow-lift sm:p-6 ${className}`}>{children}</div>
  )
}

export const REPO_URL = 'https://github.com/msckell/conciliacion-wfiat'

export function Ext({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-accent underline decoration-accent/30 underline-offset-2 hover:decoration-accent"
    >
      {children}
    </a>
  )
}

const TONES = {
  ok: 'bg-ok-soft text-ok',
  warn: 'bg-warn-soft text-warn',
  neutral: 'bg-paper text-ink-2 border border-line',
  accent: 'bg-accent-soft text-accent',
}

export function Pill({ tone, children }: { tone: keyof typeof TONES; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ${TONES[tone]}`}
    >
      {children}
    </span>
  )
}

export function Check({ className = 'h-4 w-4' }: { className?: string }) {
  return (
    <svg viewBox="0 0 20 20" fill="none" className={className} aria-hidden>
      <path
        d="M5 10.5l3.2 3.2L15 7"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export function Dot({ className = 'h-4 w-4' }: { className?: string }) {
  return (
    <svg viewBox="0 0 20 20" className={className} aria-hidden>
      <circle cx="10" cy="10" r="4" fill="currentColor" />
    </svg>
  )
}

export function Alert({ className = 'h-4 w-4' }: { className?: string }) {
  return (
    <svg viewBox="0 0 20 20" fill="none" className={className} aria-hidden>
      <path d="M10 6v5" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
      <circle cx="10" cy="14.2" r="1.3" fill="currentColor" />
    </svg>
  )
}

// Token icons live in src/assets/tokens/<symbol>.svg|png and are picked up at build time.
const TOKEN_ICONS: Record<string, string | undefined> = Object.fromEntries(
  Object.entries(
    import.meta.glob<string>('./assets/tokens/*.{svg,png,webp}', { eager: true, query: '?url', import: 'default' }),
  ).map(([path, url]) => [path.split('/').pop()!.split('.')[0], url]),
)

export function TokenIcon({ symbol, size = 'md' }: { symbol: string; size?: 'sm' | 'md' }) {
  const box = size === 'sm' ? 'h-5 w-5 text-[7px]' : 'h-9 w-9 text-[10px]'
  const url = TOKEN_ICONS[symbol.toLowerCase()]
  if (url) return <img src={url} alt="" className={`${box} shrink-0 rounded-full object-contain ring-1 ring-line`} />
  // Monogram until the icon file exists: the currency code without the leading "w".
  return (
    <span
      aria-hidden
      className={`${box} inline-flex shrink-0 items-center justify-center rounded-full bg-accent-soft font-bold tracking-tight text-accent`}
    >
      {size === 'sm' ? symbol.slice(1, 2) : symbol.slice(1)}
    </span>
  )
}
