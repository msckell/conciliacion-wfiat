import { useEffect, useRef, useState, type ReactNode } from 'react'
import { production, site } from './data'
import { Card, Check, Ext, Pill, Section, TokenIcon } from './ui'

const LABEL_TONE: Record<string, 'ok' | 'accent' | 'warn'> = {
  documented: 'ok',
  inferred: 'accent',
  hypothesis: 'warn',
}

type StageKind = 'source' | 'code' | 'ai' | 'output'

const STAGE_STYLE: Record<StageKind, string> = {
  source: 'border-white/15 bg-white/5',
  code: 'border-white/30 bg-white/10',
  ai: 'border-[#9aa3ff]/60 bg-accent/35',
  output: 'border-white/15 bg-white/5',
}

function KindChip({ kind }: { kind: StageKind }) {
  if (kind === 'code')
    return <span className="rounded-md bg-white px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-navy">Código</span>
  if (kind === 'ai')
    return <span className="rounded-md bg-accent px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white ring-1 ring-white/30">IA</span>
  return null
}

function Diagram() {
  const stages: { title: string; items: string; kind: StageKind }[] = [
    { title: 'Fuentes públicas', items: 'Nodos de cada red, exploradores y los certificados publicados', kind: 'source' },
    { title: 'Motor determinístico', items: 'Bloque del corte, cantidad de tokens, emisiones y quemas, conciliación exacta', kind: 'code' },
    { title: 'Agentes', items: 'Agente de excepciones y redactor del resumen, con Claude', kind: 'ai' },
    { title: 'Verificador', items: 'Rechaza cifras escritas por la IA, datos ajenos y afirmaciones prohibidas', kind: 'code' },
    { title: 'Salidas', items: 'Slack, tareas, esta página y el Excel', kind: 'output' },
  ]
  return (
    <>
      <ol className="flex flex-col gap-2 sm:flex-row sm:items-stretch">
        {stages.map((s, i) => (
          <li key={s.title} className="flex flex-col items-center gap-2 sm:flex-1 sm:flex-row">
            <div className={`w-full flex-1 self-stretch rounded-xl border p-3 ${STAGE_STYLE[s.kind]}`}>
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-semibold text-white">{s.title}</p>
                <KindChip kind={s.kind} />
              </div>
              <p className="mt-1 text-xs leading-snug text-on-navy">{s.items}</p>
            </div>
            {i < stages.length - 1 && (
              <span className="text-on-navy" aria-hidden>
                <span className="sm:hidden">↓</span>
                <span className="hidden sm:inline">→</span>
              </span>
            )}
          </li>
        ))}
      </ol>
      <div className="mt-5 grid gap-2 border-t border-white/15 pt-4 text-sm text-on-navy sm:grid-cols-2">
        <p className="flex items-start gap-2">
          <KindChip kind="code" />
          <span>Con los mismos datos, siempre da el mismo resultado. Calcula y escribe cada cifra.</span>
        </p>
        <p className="flex items-start gap-2">
          <KindChip kind="ai" />
          <span>Investiga y redacta explicaciones. Nunca escribe una cifra.</span>
        </p>
      </div>
    </>
  )
}

function Table({ head, children }: { head: string[]; children: ReactNode }) {
  return (
    <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
      <table className="w-full min-w-[640px] border-collapse text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs font-medium uppercase tracking-wide text-ink-3">
            {head.map((h, i) => (
              <th key={h} className={`px-2 py-2 font-medium ${i > 0 ? 'text-right' : ''}`}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  )
}

function Verification() {
  const v = site.verification
  return (
    <Section
      id="verificacion"
      title="Verificación contra el contador"
      lead={`Regla única para todas las filas: ${v.rule} Coincide en ${v.matched} de ${v.total}.`}
    >
      <p className="mb-2 flex items-center gap-1.5 text-xs text-ink-3 sm:hidden">
        <span className="flex h-4 w-4 items-center justify-center rounded-full bg-ok-soft text-ok" aria-hidden>
          <Check className="h-3 w-3" />
        </span>
        coincide con la cifra certificada. Tocá una fila para ver el detalle.
      </p>
      <Card flush className="divide-y divide-line sm:hidden">
        {v.rows.map((r) => (
          <details key={r.token + r.cutoff} className="group px-3 py-2.5">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-2">
              <span className="flex items-center gap-2">
                <TokenIcon symbol={r.token} size="sm" />
                <span className="leading-tight">
                  <span className="block font-medium">{r.token}</span>
                  <span className="num block text-xs text-ink-3">{r.cutoff}</span>
                </span>
              </span>
              <span className="flex min-w-0 items-center gap-2">
                <span className="num truncate text-sm">{r.certified}</span>
                {r.match ? (
                  <span
                    className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-ok-soft text-ok"
                    title="Coincide"
                  >
                    <Check className="h-3.5 w-3.5" />
                    <span className="sr-only">Coincide</span>
                  </span>
                ) : (
                  <Pill tone="warn">No coincide</Pill>
                )}
                <svg viewBox="0 0 20 20" className="h-4 w-4 text-ink-3 transition group-open:rotate-180" aria-hidden>
                  <path d="M6 8l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </span>
            </summary>
            <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 pb-1 text-sm">
              <dt className="text-ink-3">Suma de contratos</dt>
              <dd className="num text-right">{r.raw_sum}</dd>
              <dt className="text-ink-3">Ajustes</dt>
              <dd className="text-right">{r.adjustments}</dd>
              <dt className="text-ink-3">Calculado</dt>
              <dd className="num text-right">{r.computed}</dd>
              <dt className="text-ink-3">Certificado</dt>
              <dd className="num text-right">
                <Ext href={r.document_url}>{r.certified}</Ext>
              </dd>
              <dt className="text-ink-3">Diferencia</dt>
              <dd className="num text-right">{r.difference}</dd>
            </dl>
          </details>
        ))}
      </Card>
      <Card flush className="hidden sm:block">
        <div className="px-3 py-2">
          <Table head={['Moneda y corte', 'Suma de contratos', 'Ajustes', 'Calculado', 'Certificado', 'Diferencia', 'Estado']}>
            {v.rows.map((r) => (
              <tr key={r.token + r.cutoff} className="border-b border-line last:border-0">
                <td className="px-2 py-2">
                  <span className="flex items-center gap-2">
                    <TokenIcon symbol={r.token} size="sm" />
                    <span className="leading-tight">
                      <span className="block font-medium">{r.token}</span>
                      <span className="num block text-xs text-ink-3">{r.cutoff}</span>
                    </span>
                  </span>
                </td>
                <td className="num px-2 py-2 text-right text-ink-2">{r.raw_sum}</td>
                <td className="px-2 py-2 text-right text-ink-2">{r.adjustments}</td>
                <td className="num px-2 py-2 text-right">{r.computed}</td>
                <td className="num px-2 py-2 text-right">
                  <Ext href={r.document_url}>{r.certified}</Ext>
                </td>
                <td className="num px-2 py-2 text-right">{r.difference}</td>
                <td className="px-2 py-2 text-right">
                  {r.match ? (
                    <Pill tone="ok">
                      <Check className="h-3 w-3" />
                      Coincide
                    </Pill>
                  ) : (
                    <Pill tone="warn">No coincide</Pill>
                  )}
                </td>
              </tr>
            ))}
          </Table>
        </div>
      </Card>
      <p className="mt-2 text-xs text-ink-3">
        La cifra certificada lleva al certificado publicado. La suma de contratos se muestra con hasta dos decimales y
        se compara en tokens enteros.
      </p>
    </Section>
  )
}

const LABEL_GROUPS: { key: string; hint: string }[] = [
  { key: 'documented', hint: 'Tiene una fuente.' },
  { key: 'inferred', hint: 'Su única evidencia es que reproduce las cifras certificadas.' },
  { key: 'hypothesis', hint: 'Falta evidencia.' },
]

function Methodology() {
  return (
    <Section
      id="metodologia"
      title="Metodología"
      lead="Cada decisión lleva su etiqueta. Documentado: tiene una fuente. Inferido: su única evidencia es que reproduce las cifras certificadas. Hipótesis: falta evidencia."
    >
      <div className="space-y-3">
        {LABEL_GROUPS.map((g) => {
          const items = site.methodology.filter((m) => m.label_key === g.key)
          if (items.length === 0) return null
          return (
            <Card key={g.key} flush>
              <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3 sm:px-5">
                <Pill tone={LABEL_TONE[g.key]}>{items[0].label}</Pill>
                <span className="text-xs text-ink-3">{g.hint}</span>
              </div>
              <ul className="divide-y divide-line">
                {items.map((m) => (
                  <li key={m.decision} className="px-4 py-3 sm:px-5">
                    <p className="text-ink">{m.decision}</p>
                    <p className="mt-0.5 text-xs text-ink-3">{m.source}</p>
                  </li>
                ))}
              </ul>
            </Card>
          )
        })}
      </div>
    </Section>
  )
}

// Addresses and hashes read better shortened. The full value stays in the tooltip and in
// the explorer link of each card.
const HEX = /(0x[0-9a-fA-F]{40,})/g

function ShortHashes({ text }: { text: string }) {
  return (
    <>
      {text.split(HEX).map((part, i) =>
        /^0x[0-9a-fA-F]{40,}$/.test(part) ? (
          <code key={i} title={part} className="rounded bg-paper px-1 font-mono text-[0.85em] text-ink-2">
            {part.slice(0, 6)}…{part.slice(-4)}
          </code>
        ) : (
          part
        ),
      )}
    </>
  )
}

// Consecutive repeats of the same step show once, with how many times it ran.
function collapseSteps(steps: string[]): { text: string; times: number }[] {
  const out: { text: string; times: number }[] = []
  for (const s of steps) {
    const last = out[out.length - 1]
    if (last && last.text === s) last.times += 1
    else out.push({ text: s, times: 1 })
  }
  return out
}

type ExceptionItem = NonNullable<typeof site.exceptions>['items'][number]

function ExceptionCard({ it }: { it: ExceptionItem }) {
  const symbol = it.movement.match(/\bw[A-Z]{3}\b/)?.[0]
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        {it.outcome === 'resolved' ? (
          <Pill tone="ok">
            <Check className="h-3 w-3" />
            Resuelto con evidencia
          </Pill>
        ) : (
          <Pill tone="warn">Tarea para una persona</Pill>
        )}
        <span className="flex items-center gap-1.5 text-sm font-medium">
          {symbol && <TokenIcon symbol={symbol} size="sm" />}
          {it.chain}, {it.movement}
        </span>
      </div>
      <p className="mt-2 text-sm text-ink-3">
        Por qué quedó para revisar: <ShortHashes text={it.reason} />
      </p>
      <p className="mt-2 break-words text-sm text-ink">
        <ShortHashes text={it.summary} />
      </p>
      {it.steps.length > 0 && (
        <ol className="mt-3 flex flex-wrap gap-1.5">
          {collapseSteps(it.steps).map((s, j) => (
            <li key={j} className="rounded-md bg-paper px-2 py-0.5 text-xs text-ink-2">
              {j + 1}. {s.text}
              {s.times > 1 && <span className="text-ink-3"> ({s.times} veces)</span>}
            </li>
          ))}
        </ol>
      )}
      {it.checks.length > 0 && it.outcome === 'resolved' && (
        <p className="mt-2 break-words text-xs text-ok">
          Comprobado por el código: <ShortHashes text={it.checks.join(' · ')} />
        </p>
      )}
      <p className="mt-2 text-sm">
        <Ext href={it.explorer_url}>Ver transacción</Ext>
      </p>
    </Card>
  )
}

function Exceptions() {
  const e = site.exceptions
  if (!e) return null
  const tasks = e.items.filter((it) => it.outcome !== 'resolved')
  const resolved = e.items.filter((it) => it.outcome === 'resolved')
  return (
    <Section
      id="excepciones"
      title="El agente de excepciones"
      lead={`Toma cada movimiento que el motor no pudo clasificar. Investigó ${e.investigated}, probó ${e.resolved} con evidencia que el código comprobó y dejó ${e.tasks} como tarea para una persona.`}
    >
      {tasks.length > 0 && (
        <>
          <h3 className="mb-2 text-sm font-semibold text-ink-2">Tareas para una persona ({e.tasks})</h3>
          <ul className="space-y-3">
            {tasks.map((it, i) => (
              <li key={i}>
                <ExceptionCard it={it} />
              </li>
            ))}
          </ul>
        </>
      )}
      {resolved.length > 0 && (
        <details className="group mt-5">
          <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-semibold text-accent">
            <svg viewBox="0 0 20 20" className="h-4 w-4 transition group-open:rotate-90" aria-hidden>
              <path d="M8 6l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Resueltos con evidencia ({e.resolved})
          </summary>
          <ul className="mt-3 space-y-3">
            {resolved.map((it, i) => (
              <li key={i}>
                <ExceptionCard it={it} />
              </li>
            ))}
          </ul>
        </details>
      )}
    </Section>
  )
}

function Verifier() {
  const v = site.verifier
  const ev = site.extraction_eval
  return (
    <Section
      id="verificador"
      title="El verificador de IA"
      lead="La IA nunca escribe una cifra. Escribe la explicación citando datos por su identificador y el código pone el número. Cada texto pasa por el verificador antes de publicarse."
    >
      <div className="grid gap-3 lg:grid-cols-2">
        <Card>
          <p className="font-medium">Intentos de este cierre</p>
          <ul className="mt-2 divide-y divide-line text-sm">
            {v.attempts.map((a, i) => (
              <li key={i} className="flex items-center justify-between gap-2 py-1.5">
                <span>
                  {a.token} <span className="text-ink-3">intento {a.attempt}</span>
                </span>
                {a.accepted ? (
                  <Pill tone="ok">Aceptado</Pill>
                ) : (
                  <Pill tone="warn">Rechazado: {a.problems.join(', ')}</Pill>
                )}
              </li>
            ))}
          </ul>
          {v.model && <p className="mt-2 text-xs text-ink-3">Modelo: {v.model}</p>}
        </Card>
        <Card>
          <p className="font-medium">Lo que rechaza (casos de los tests)</p>
          <ul className="mt-2 divide-y divide-line text-sm">
            {v.cases.map((c) => (
              <li key={c.text} className="py-1.5">
                <p className="font-medium text-ink">{c.problem}</p>
                <p className="break-words font-mono text-xs text-ink-3">{c.text}</p>
              </li>
            ))}
          </ul>
        </Card>
      </div>
      {ev && (
        <p className="mt-3 text-sm text-ink-2">
          Además, la IA lee cada certificado y su lectura se puntúa contra la tabla confirmada a mano:{' '}
          {ev.all_fields_ok} de {ev.documents} certificados con todos los campos correctos.
        </p>
      )}
    </Section>
  )
}

// Time since an instant in the data. The only thing the page computes: it depends on
// when the page is read, not on the data.
function since(iso: string): { text: string; hours: number } {
  const hours = (Date.now() - Date.parse(iso)) / 3_600_000
  const n = (v: number, one: string, many: string) => `hace ${v} ${v === 1 ? one : many}`
  if (hours < 1) return { text: n(Math.max(1, Math.round(hours * 60)), 'minuto', 'minutos'), hours }
  if (hours < 48) return { text: n(Math.round(hours), 'hora', 'horas'), hours }
  return { text: n(Math.round(hours / 24), 'día', 'días'), hours }
}

const STALE_HOURS = 36

function Production() {
  const runs = site.runs
  const { schedule, ci, monitor, slack_live } = production
  const last = monitor ? since(monitor.last_run_at_iso) : null
  const lastOk = monitor?.last_ok_at_iso ? since(monitor.last_ok_at_iso) : null
  const stale = !lastOk || lastOk.hours > STALE_HOURS
  return (
    <Section id="produccion" title="En producción">
      <div className="grid gap-3 sm:grid-cols-2">
        <Card>
          <p className="font-medium">Monitor diario</p>
          {schedule.monitor && <p className="mt-1 text-sm text-ink-2">{schedule.monitor}.</p>}
          {monitor && last ? (
            <>
              <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
                <span>Última corrida {last.text}</span>
                {monitor.last_run_status === 'ok' ? <Pill tone="ok">OK</Pill> : <Pill tone="warn">Falló</Pill>}
                {stale && <Pill tone="warn">Atrasado: sin corrida exitosa en {STALE_HOURS} horas</Pill>}
              </div>
              <p className="mt-2 text-sm text-ink-2">
                Revisa {monitor.networks_checked} redes de la lista por contratos nuevos, lee la cantidad de tokens
                de cada red y{' '}
                {monitor.golden_live_all_match
                  ? `recalcula desde la red las ${monitor.golden_live_total} certificaciones publicadas: coinciden todas.`
                  : 'recalcula desde la red las certificaciones publicadas: chequeo en revisión.'}
              </p>
              {monitor.problems.length > 0 && (
                <ul className="mt-2 space-y-1 text-sm text-warn">
                  {monitor.problems.map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
              )}
              {monitor.changes.length > 0 && (
                <ul className="mt-2 space-y-1 text-sm text-ink">
                  {monitor.changes.map((c) => (
                    <li key={c}>{c}</li>
                  ))}
                </ul>
              )}
            </>
          ) : (
            <p className="mt-2 text-sm text-ink-3">Todavía no corrió.</p>
          )}
        </Card>
        <Card>
          <p className="font-medium">Integración continua</p>
          <p className="mt-1 text-sm text-ink-2">
            En cada cambio de código: lint, tests sin conexión (incluye las certificaciones publicadas) y el build
            de esta página.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
            {ci ? (
              <>
                {ci.conclusion === 'success' ? (
                  <Pill tone="ok">
                    <Check className="h-3 w-3" /> En verde
                  </Pill>
                ) : (
                  <Pill tone="warn">Falló</Pill>
                )}
                <span className="num text-ink-3">
                  {ci.at} · {ci.sha}
                </span>
              </>
            ) : (
              <span className="text-ink-3">Sin datos todavía: lo lee el monitor diario.</span>
            )}
          </div>
        </Card>
        <Card>
          <p className="font-medium">Cierre y alertas</p>
          {schedule.close && <p className="mt-1 text-sm text-ink-2">{schedule.close}. También se corre a mano.</p>}
          <p className="mt-2 text-sm text-ink-2">
            Avisa por Slack solo con el Excel ya publicado. Si un paso falla, manda una alerta sin link. Cada caso
            que el agente no puede probar se abre como tarea.
          </p>
          <div className="mt-3">
            {slack_live ? <Pill tone="ok">Slack conectado</Pill> : <Pill tone="neutral">Slack en modo de prueba</Pill>}
          </div>
        </Card>
        <Card>
          <p className="font-medium">Últimas corridas</p>
          {runs.length === 0 ? (
            <p className="mt-2 text-sm text-ink-3">Todavía no hay corridas registradas.</p>
          ) : (
            <ul className="mt-2 divide-y divide-line text-sm">
              {runs.map((r, i) => (
                <li key={i} className="flex items-center justify-between gap-2 py-1.5">
                  <span>
                    {r.kind === 'close' ? 'Cierre' : 'Monitor'} <span className="text-ink-3">{r.trigger}</span>{' '}
                    <span className="num text-ink-3">{r.started_at}</span>
                  </span>
                  <span className="flex items-center gap-2">
                    <span className="text-xs text-ink-3">{r.duration}</span>
                    {r.status === 'ok' ? <Pill tone="ok">OK</Pill> : <Pill tone="warn">Falló</Pill>}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </Section>
  )
}

function Decisions() {
  const items = [
    'Las cifras salen solo del motor determinístico, en unidades enteras y sin decimales flotantes. La IA explica, no calcula.',
    'Cada red se calcula por dos caminos independientes y el cierre se detiene si no coinciden.',
    'El método se prueba contra las certificaciones publicadas con una sola regla para todas.',
    'La máquina propone y la persona decide: lo que el agente no puede probar queda como tarea.',
    'Página estática sin servidor ni base de datos. Los datos los escribe la corrida y quedan versionados.',
  ]
  return (
    <Section id="decisiones" title="Decisiones de diseño">
      <ul className="space-y-2">
        {items.map((t) => (
          <li key={t} className="flex gap-2 text-ink">
            <span className="text-accent">•</span>
            {t}
          </li>
        ))}
      </ul>
    </Section>
  )
}

const INDEX: { id: string; label: string }[] = [
  { id: 'verificacion', label: 'Verificación' },
  { id: 'metodologia', label: 'Metodología' },
  ...(site.exceptions ? [{ id: 'excepciones', label: 'Excepciones' }] : []),
  { id: 'verificador', label: 'Verificador de IA' },
  { id: 'produccion', label: 'En producción' },
  { id: 'decisiones', label: 'Decisiones' },
]

function SectionIndex() {
  const nav = useRef<HTMLElement>(null)
  const [active, setActive] = useState<string | null>(null)

  useEffect(() => {
    const els = INDEX.map((s) => document.getElementById(s.id)).filter((el): el is HTMLElement => el !== null)
    const io = new IntersectionObserver(
      (entries) => {
        const hit = entries.find((e) => e.isIntersecting)
        if (hit) setActive(hit.target.id)
      },
      { rootMargin: '-35% 0px -60% 0px' },
    )
    els.forEach((el) => io.observe(el))
    return () => io.disconnect()
  }, [])

  // Keep the active chip visible on narrow screens, scrolling only the index.
  useEffect(() => {
    const chip = nav.current?.querySelector<HTMLElement>(`[data-id="${active}"]`)
    if (nav.current && chip) nav.current.scrollTo({ left: chip.offsetLeft - 16, behavior: 'smooth' })
  }, [active])

  const go = (id: string) => {
    const el = document.getElementById(id)
    if (!el || !nav.current) return
    const header = window.matchMedia('(min-width: 640px)').matches
      ? parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--header-h')) || 0
      : 0
    const offset = header + nav.current.offsetHeight + 16
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - offset, behavior: reduce ? 'auto' : 'smooth' })
  }

  return (
    <nav
      ref={nav}
      aria-label="Secciones"
      className="sticky top-0 z-[5] -mx-4 mt-6 flex gap-1.5 overflow-x-auto border-b border-line bg-paper/90 px-4 py-2 backdrop-blur-md [scrollbar-width:none] sm:top-[var(--header-h,0px)]"
    >
      {INDEX.map((s) => (
        <a
          key={s.id}
          data-id={s.id}
          href={`#${s.id}`}
          onClick={(e) => {
            e.preventDefault()
            go(s.id)
          }}
          aria-current={active === s.id ? 'true' : undefined}
          className={`shrink-0 rounded-full px-3 py-1 text-sm font-medium transition-colors ${
            active === s.id ? 'bg-navy text-white' : 'bg-card text-ink-2 ring-1 ring-line hover:text-ink'
          }`}
        >
          {s.label}
        </a>
      ))}
    </nav>
  )
}

export function ComoFunciona() {
  return (
    <>
      <section className="fade-up pt-5 sm:pt-10">
        <div className="hero-bg overflow-hidden rounded-3xl px-5 pb-6 pt-6 text-white shadow-lift sm:px-8 sm:pb-8 sm:pt-8">
          <h2 className="text-xl font-semibold tracking-tight sm:text-[1.65rem]">Cómo funciona</h2>
          <p className="mt-2 max-w-prose text-on-navy">De las fuentes públicas a lo que recibe Finanzas.</p>
          <div className="mt-5">
            <Diagram />
          </div>
        </div>
      </section>
      <SectionIndex />
      <Verification />
      <Methodology />
      <Exceptions />
      <Verifier />
      <Production />
      <Decisions />
    </>
  )
}
