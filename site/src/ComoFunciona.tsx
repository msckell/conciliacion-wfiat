import type { ReactNode } from 'react'
import { site } from './data'
import { Card, Check, Ext, Pill, Section } from './ui'

const LABEL_TONE: Record<string, 'ok' | 'accent' | 'warn'> = {
  documented: 'ok',
  inferred: 'accent',
  hypothesis: 'warn',
}

function Diagram() {
  const stages: { title: string; items: string }[] = [
    { title: 'Fuentes públicas', items: 'Nodos de cada red, exploradores y los certificados publicados' },
    { title: 'Motor determinístico', items: 'Bloque del corte, cantidad de tokens, emisiones y quemas, conciliación exacta' },
    { title: 'Agentes', items: 'Agente de excepciones y redactor del resumen, con Claude' },
    { title: 'Verificador', items: 'Rechaza cifras escritas por la IA, datos ajenos y afirmaciones prohibidas' },
    { title: 'Salidas', items: 'Slack, tareas, esta página y el Excel' },
  ]
  return (
    <ol className="flex flex-col gap-2 sm:flex-row sm:items-stretch">
      {stages.map((s, i) => (
        <li key={s.title} className="flex flex-col items-center gap-2 sm:flex-1 sm:flex-row">
          <div className="w-full flex-1 self-stretch rounded-xl border border-line bg-card p-3">
            <p className="text-sm font-semibold text-ink">{s.title}</p>
            <p className="mt-1 text-xs leading-snug text-ink-2">{s.items}</p>
          </div>
          {i < stages.length - 1 && (
            <span className="text-ink-3" aria-hidden>
              <span className="sm:hidden">↓</span>
              <span className="hidden sm:inline">→</span>
            </span>
          )}
        </li>
      ))}
    </ol>
  )
}

function Table({ head, children }: { head: string[]; children: ReactNode }) {
  return (
    <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
      <table className="w-full min-w-[640px] border-collapse text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs font-medium uppercase tracking-wide text-ink-3">
            {head.map((h, i) => (
              <th key={h} className={`px-2 py-2 font-medium ${i > 1 ? 'text-right' : ''}`}>
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
      title="Verificación contra el contador"
      lead={`Regla única para todas las filas: ${v.rule} Coincide en ${v.matched} de ${v.total}.`}
    >
      <Card className="p-0 sm:p-0">
        <div className="p-4 sm:p-5">
          <Table head={['Moneda', 'Corte', 'Suma de contratos', 'Ajustes', 'Calculado', 'Certificado', 'Diferencia', 'Estado']}>
            {v.rows.map((r) => (
              <tr key={r.token + r.cutoff} className="border-b border-line last:border-0">
                <td className="px-2 py-2 font-medium">{r.token}</td>
                <td className="num px-2 py-2">{r.cutoff}</td>
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
        La cifra certificada lleva al certificado publicado. La suma de contratos se muestra con dos decimales y
        se compara en tokens enteros.
      </p>
    </Section>
  )
}

function Methodology() {
  return (
    <Section
      title="Metodología"
      lead="Cada decisión lleva su etiqueta. Documentado: tiene una fuente. Inferido: su única evidencia es que reproduce las cifras certificadas. Hipótesis: falta evidencia."
    >
      <ul className="space-y-2">
        {site.methodology.map((m) => (
          <li key={m.decision}>
            <Card className="p-3 sm:p-4">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:gap-3">
                <span className="shrink-0">
                  <Pill tone={LABEL_TONE[m.label_key]}>{m.label}</Pill>
                </span>
                <div>
                  <p className="text-ink">{m.decision}</p>
                  <p className="mt-0.5 text-xs text-ink-3">{m.source}</p>
                </div>
              </div>
            </Card>
          </li>
        ))}
      </ul>
    </Section>
  )
}

function Exceptions() {
  const e = site.exceptions
  if (!e) return null
  return (
    <Section
      title="El agente de excepciones"
      lead={`Toma cada movimiento que el motor no pudo clasificar. Investigó ${e.investigated}, probó ${e.resolved} con evidencia que el código comprobó y dejó ${e.tasks} como tarea para una persona.`}
    >
      <ul className="space-y-3">
        {e.items.map((it, i) => (
          <li key={i}>
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
                <span className="text-sm font-medium">
                  {it.chain}, {it.movement}
                </span>
              </div>
              <p className="mt-2 text-sm text-ink-3">Por qué quedó para revisar: {it.reason}</p>
              <p className="mt-2 break-words text-sm text-ink">{it.summary}</p>
              {it.steps.length > 0 && (
                <ol className="mt-3 flex flex-wrap gap-1.5">
                  {it.steps.map((s, j) => (
                    <li key={j} className="rounded-md bg-paper px-2 py-0.5 text-xs text-ink-2">
                      {j + 1}. {s}
                    </li>
                  ))}
                </ol>
              )}
              {it.checks.length > 0 && it.outcome === 'resolved' && (
                <p className="mt-2 break-words text-xs text-ok">Comprobado por el código: {it.checks.join(' · ')}</p>
              )}
              <p className="mt-2 text-sm">
                <Ext href={it.explorer_url}>Ver transacción</Ext>
              </p>
            </Card>
          </li>
        ))}
      </ul>
    </Section>
  )
}

function Verifier() {
  const v = site.verifier
  const ev = site.extraction_eval
  return (
    <Section
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

function Production() {
  const runs = site.runs
  return (
    <Section title="En producción">
      <div className="grid gap-3 sm:grid-cols-2">
        <Card>
          <p className="font-medium">Últimas corridas</p>
          {runs.length === 0 ? (
            <p className="mt-2 text-sm text-ink-3">Todavía no hay corridas registradas.</p>
          ) : (
            <ul className="mt-2 divide-y divide-line text-sm">
              {runs.map((r, i) => (
                <li key={i} className="flex items-center justify-between gap-2 py-1.5">
                  <span>
                    {r.kind === 'close' ? 'Cierre' : 'Monitor'} <span className="num text-ink-3">{r.started_at}</span>
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
        <Card>
          <p className="font-medium">Integración continua y monitor diario</p>
          <p className="mt-2 text-sm text-ink-2">
            Pendiente. La próxima etapa suma los tests en cada cambio, el monitor diario de redes nuevas a las
            09:00 de Buenos Aires y las alertas por Slack cuando una corrida falla.
          </p>
          <div className="mt-3">
            <Pill tone="neutral">Pendiente</Pill>
          </div>
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
    <Section title="Decisiones de diseño">
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

export function ComoFunciona() {
  return (
    <>
      <Section title="Cómo funciona" lead="De las fuentes públicas a lo que recibe Finanzas.">
        <Diagram />
      </Section>
      <Verification />
      <Methodology />
      <Exceptions />
      <Verifier />
      <Production />
      <Decisions />
    </>
  )
}
