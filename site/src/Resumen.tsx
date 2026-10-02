import { useState } from 'react'
import { excelHref, isOtherNetwork, networkColor, site, type Token } from './data'
import { SlackMessage } from './Slack'
import { Alert, Card, Check, Dot, Ext, Pill, Section, TokenIcon } from './ui'

const close = site.close

function DownloadIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" aria-hidden>
      <path d="M10 3v10m0 0l-4-4m4 4l4-4M4 16h12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function HeroStat({ value, label }: { value: string; label: string }) {
  return (
    <div className="bg-navy/55 px-4 py-3.5 sm:px-5 sm:py-4">
      <dt className="sr-only">{label}</dt>
      <dd className="num text-xl font-semibold tracking-tight text-white sm:text-2xl">{value}</dd>
      <dd className="mt-0.5 text-xs leading-snug text-on-navy sm:text-[13px]">{label}</dd>
    </div>
  )
}

// A step detail that is a bare URL reads better as a link named after its file.
function StepDetail({ text }: { text: string }) {
  if (/^https?:\/\/\S+$/.test(text)) {
    return <Ext href={text}>{text.split('/').pop()}</Ext>
  }
  return <>{text}</>
}

function Timeline() {
  const t = site.timeline
  const [open, setOpen] = useState(false)
  if (!t) {
    return <Card>Todavía no hay una corrida registrada del cierre.</Card>
  }
  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-ink-3">
          Corrida del {t.started_at} (hora de Buenos Aires), duró {t.duration}.
        </p>
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          className="text-sm font-medium text-accent sm:hidden"
        >
          {open ? 'Ocultar el detalle' : 'Ver el detalle de cada paso'}
        </button>
      </div>
      <ol className="mt-4 space-y-0">
        {t.steps.map((s, i) => {
          const done = s.status === 'ok'
          const last = i === t.steps.length - 1
          return (
            <li
              key={s.key}
              className={`reveal-step relative flex gap-3 last:pb-0 ${open ? 'pb-5' : 'pb-3.5 sm:pb-5'}`}
              style={{ transitionDelay: `${150 + i * 110}ms` }}
            >
              {!last && (
                <span className="absolute left-[11px] top-7 bottom-0 w-px bg-line" aria-hidden />
              )}
              <span
                className={`relative mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${
                  done ? 'bg-ok-soft text-ok' : 'bg-warn-soft text-warn'
                }`}
              >
                {done ? <Check /> : <Dot className="h-3 w-3" />}
              </span>
              <div className="min-w-0">
                <p className="font-medium leading-snug text-ink">{s.title}</p>
                <p className={`mt-0.5 break-words text-sm text-ink-2 ${open ? '' : 'hidden sm:block'}`}>
                  <StepDetail text={s.detail} />
                </p>
                <p className="mt-0.5 text-xs text-ink-3">
                  {s.at} · {s.duration}
                </p>
              </div>
            </li>
          )
        })}
      </ol>
    </Card>
  )
}

function NetworkBar({ token }: { token: Token }) {
  const ordered = [
    ...token.by_network.filter((n) => !isOtherNetwork(n.chain)),
    ...token.by_network.filter((n) => isOtherNetwork(n.chain)),
  ]
  return (
    <div>
      <div
        className="grow flex h-3 w-full overflow-hidden rounded-full bg-paper"
        role="img"
        aria-label={`Reparto de ${token.symbol} por red`}
      >
        {ordered.map((n) => (
          <div
            key={n.chain}
            title={`${n.name}: ${n.amount} (${n.share_pct}%)`}
            className="h-full border-r-2 border-card last:border-r-0"
            style={{ width: `${n.share_pct}%`, background: networkColor(n.chain), minWidth: 3 }}
          />
        ))}
      </div>
      <details className="group mt-2">
        <summary className="cursor-pointer list-none text-xs font-medium text-accent">
          <span className="group-open:hidden">Ver por red</span>
          <span className="hidden group-open:inline">Ocultar</span>
        </summary>
        <table className="mt-2 w-full text-sm">
          <tbody>
            {token.by_network.map((n) => (
              <tr key={n.chain} className="border-t border-line">
                <td className="py-1 pr-2">
                  <span
                    className="mr-2 inline-block h-2.5 w-2.5 rounded-sm align-middle"
                    style={{ background: networkColor(n.chain) }}
                  />
                  {n.name}
                </td>
                <td className="num py-1 text-right">{n.amount}</td>
                <td className="num w-14 py-1 text-right text-ink-3">{n.share_pct}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  )
}

function Legend() {
  const seen = new Map<string, string>()
  for (const t of close.tokens) for (const n of t.by_network) seen.set(n.chain, n.name)
  const named = [...seen].filter(([chain]) => !isOtherNetwork(chain))
  const others = [...seen].filter(([chain]) => isOtherNetwork(chain)).map(([, name]) => name)
  const swatch = (color: string) => (
    <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: color }} />
  )
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2">
      {named.map(([chain, name]) => (
        <span key={chain} className="inline-flex items-center gap-1.5">
          {swatch(networkColor(chain))}
          {name}
        </span>
      ))}
      {others.length > 0 && (
        <span className="inline-flex items-center gap-1.5">
          {swatch('var(--color-net-other)')}
          Otras redes ({others.join(', ')})
        </span>
      )}
    </div>
  )
}

function NetworksChecked() {
  const withContracts = new Set(close.networks.map((n) => n.name))
  const without = close.networks_checked.filter((name) => !withContracts.has(name))
  return (
    <Card className="mt-4">
      <p className="font-medium">Redes revisadas ({close.networks_checked.length})</p>
      <p className="mt-0.5 text-sm text-ink-2">
        Con contratos en {close.networks.length} de ellas. Cada una lleva el bloque usado en el corte como prueba.
      </p>
      <ul className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
        {close.networks.map((n) => (
          <li key={n.name} className="rounded-xl border border-line px-3 py-2">
            <p className="flex items-center gap-1.5 text-sm font-medium">
              {n.name}
              {n.is_new && <Pill tone="warn">Nueva</Pill>}
            </p>
            <p className="text-xs text-ink-3">
              Bloque{' '}
              <Ext href={n.cutoff_block_url}>
                <span className="num">{n.cutoff_block}</span>
              </Ext>
            </p>
          </li>
        ))}
      </ul>
      {without.length > 0 && (
        <>
          <p className="mt-4 text-xs font-medium text-ink-3">Sin contratos de las monedas</p>
          <ul className="mt-1.5 flex flex-wrap gap-1.5">
            {without.map((name) => (
              <li key={name} className="rounded-full bg-paper px-2.5 py-0.5 text-xs text-ink-2">
                {name}
              </li>
            ))}
          </ul>
        </>
      )}
      <p className="mt-3 text-xs text-ink-3">Solo se revisan redes EVM de una lista escrita.</p>
    </Card>
  )
}

function TokenCard({ token }: { token: Token }) {
  return (
    <Card className="transition duration-200 hover:-translate-y-0.5 hover:shadow-lift">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2.5">
          <TokenIcon symbol={token.symbol} />
          <div className="min-w-0 leading-tight">
            <p className="font-semibold">{token.symbol}</p>
            <p className="truncate text-xs text-ink-3">{token.name}</p>
          </div>
        </div>
        <span className="shrink-0 rounded-full bg-paper px-2 py-0.5 text-xs text-ink-2">
          {token.networks_with_balance} redes con saldo
        </span>
      </div>
      <p className="num mt-3 text-2xl font-semibold tracking-tight sm:text-[1.7rem]">{token.closing}</p>
      <p className="num text-sm text-ink-2">
        {token.change} desde el {close.previous_cutoff}
      </p>
      <div className="mt-4">
        <NetworkBar token={token} />
      </div>
    </Card>
  )
}

export function Resumen() {
  const v = site.verification
  const vs = site.agent_vs_manual
  const exc = site.exceptions
  return (
    <>
      <section className="pt-5 sm:pt-10">
        <div className="fade-up hero-bg overflow-hidden rounded-3xl px-5 pb-5 pt-6 text-white shadow-lift sm:px-10 sm:pb-8 sm:pt-10">
          <span className="inline-flex items-center gap-2 rounded-full bg-white/10 px-3 py-1 text-xs font-medium text-on-navy ring-1 ring-white/15">
            <span className="h-1.5 w-1.5 rounded-full bg-[#6ee7a8]" aria-hidden />
            Cierre al {close.cutoff}, listo para revisión
          </span>
          <p className="mt-4 max-w-2xl text-lg leading-relaxed text-white sm:text-[1.4rem] sm:leading-relaxed">
            Cada trimestre, un contador certifica que cada wARS, wBRL y demás stablecoins de Ripio están
            respaldadas. Para eso, alguien de Finanzas junta los datos de todas las redes. Este agente
            arma esa parte solo, al día siguiente del cierre.
          </p>
          <div className="mt-6 flex flex-col gap-2 sm:flex-row sm:gap-3">
            <a
              href={excelHref}
              download
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-white px-4 py-2.5 font-semibold text-navy transition hover:bg-white/90"
            >
              <DownloadIcon />
              Descargar el Excel
            </a>
            <a
              href="#cierre"
              onClick={(e) => {
                e.preventDefault()
                document.getElementById('cierre')?.scrollIntoView({ behavior: 'smooth' })
              }}
              className="inline-flex items-center justify-center rounded-xl px-4 py-2.5 font-semibold text-white ring-1 ring-white/25 transition hover:bg-white/10"
            >
              Ver el cierre
            </a>
          </div>
          <dl className="mt-8 grid grid-cols-2 gap-px overflow-hidden rounded-2xl bg-white/10 ring-1 ring-white/10 lg:grid-cols-4">
            <HeroStat value={`${v.matched} de ${v.total}`} label="certificaciones publicadas coinciden con el contador" />
            <HeroStat
              value={`${close.reconciled} de ${close.reconciliations}`}
              label="conciliaciones por moneda y red con diferencia cero"
            />
            {vs && (
              <>
                <HeroStat value={String(vs.transactions)} label={`transacciones revisadas en ${vs.networks} redes`} />
                <HeroStat value={vs.duration} label="duró la última corrida del agente" />
              </>
            )}
          </dl>
        </div>
      </section>

      <Section title="Lo que hizo el agente" lead="Cada paso de la última corrida del cierre, tal como quedó registrado.">
        <Timeline />
      </Section>

      <div className="mt-8 flex items-start gap-3 rounded-xl border border-ok/25 bg-ok-soft p-4 text-ok">
        <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-ok text-white">
          <Check />
        </span>
        <p className="font-medium leading-snug">
          Coincide con la cantidad de tokens certificada por el contador en {v.matched} de las {v.total}{' '}
          certificaciones publicadas.
        </p>
      </div>

      <Section title="Cómo lo hace">
        <ol className="grid gap-3 sm:grid-cols-3">
          {[
            'Cuenta los tokens de cada red a la fecha y hora del corte, con el bloque usado en cada red como prueba.',
            'Lista cada emisión y cada quema con su comprobante.',
            'Avisa a Finanzas por Slack qué cambió y qué le toca revisar.',
          ].map((text, i) => (
            <li key={i}>
              <Card className="h-full">
                <span className="flex h-7 w-7 items-center justify-center rounded-full bg-accent-soft text-sm font-semibold text-accent">
                  {i + 1}
                </span>
                <p className="mt-3 text-ink">{text}</p>
              </Card>
            </li>
          ))}
        </ol>
      </Section>

      <Section
        id="cierre"
        title={`Cierre al ${close.cutoff}, listo para revisión`}
        lead={`Foto fija al ${close.cutoff_instant}. Generado el ${close.generated_at}.`}
      >
        {close.new_networks.map((n) => (
          <div key={n.name} className="mb-4 flex items-start gap-3 rounded-xl border border-warn/25 bg-warn-soft p-4 text-warn">
            <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-warn text-white">
              <Alert />
            </span>
            <div className="text-ink">
              <p className="font-medium">Red nueva desde el último cierre: {n.name}</p>
              <p className="mt-0.5 text-sm text-ink-2">
                Los contratos se crearon desde el bloque{' '}
                <Ext href={n.first_creation_block_url}>
                  <span className="num">{n.first_creation_block}</span>
                </Ext>
                , después del cierre anterior. Por eso su saldo de apertura es cero.
              </p>
            </div>
          </div>
        ))}

        <div className="mb-3">
          <Legend />
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          {close.tokens.map((t) => (
            <TokenCard key={t.symbol} token={t} />
          ))}
        </div>

        <Card className="mt-4">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="font-medium">
                {close.reconciled} de {close.reconciliations} conciliaciones por moneda y red con diferencia
                cero
              </p>
              <p className="mt-0.5 text-sm text-ink-2">
                Saldo de apertura más emisiones menos quemas igual a saldo de cierre, exacto.
              </p>
            </div>
            <a
              href={excelHref}
              download
              className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-accent px-4 py-2.5 font-semibold text-white transition hover:bg-accent/90"
            >
              <DownloadIcon />
              Descargar el Excel
            </a>
          </div>
        </Card>

        <NetworksChecked />
      </Section>

      <Section
        title="El mensaje que recibe Finanzas"
        lead={
          site.slack.dry_run
            ? 'Así queda el mensaje en Slack. Por ahora corre en modo de prueba: se genera igual, pero todavía no se envía.'
            : 'El mensaje que llegó a Slack.'
        }
      >
        <SlackMessage excelHref={excelHref} />
        {exc && (
          <p className="mt-2 text-sm text-ink-3">
            Las tareas salen del agente de excepciones: investigó {exc.investigated} movimientos, resolvió{' '}
            {exc.resolved} con evidencia y dejó {exc.tasks} para una persona.
          </p>
        )}
      </Section>

      <Section title="A mano y con el agente">
        <div className="grid gap-3 sm:grid-cols-2">
          <Card>
            <p className="text-sm font-semibold text-ink-3">Hecho a mano</p>
            <p className="mt-1">
              Buscar en cada explorador, una planilla por red y el riesgo de olvidarse una red nueva.
            </p>
          </Card>
          <Card className="border-accent/30">
            <p className="text-sm font-semibold text-accent">Con el agente</p>
            <p className="mt-1">
              {vs
                ? `Este cierre revisó ${vs.transactions} transacciones en ${vs.networks} redes para ${vs.tokens} monedas, en ${vs.duration}, con el link de cada transacción.`
                : 'Todavía no hay una corrida registrada.'}
            </p>
            {vs && (
              <dl className="mt-4 grid grid-cols-3 gap-2 border-t border-line pt-4">
                {[
                  [String(vs.transactions), 'transacciones'],
                  [String(vs.networks), 'redes'],
                  [vs.duration, 'de corrida'],
                ].map(([value, label]) => (
                  <div key={label}>
                    <dd className="num text-lg font-semibold tracking-tight text-ink sm:text-xl">{value}</dd>
                    <dt className="text-xs text-ink-3">{label}</dt>
                  </div>
                ))}
              </dl>
            )}
          </Card>
        </div>
      </Section>

      <Section title="El mismo método sirve en otras áreas">
        <ul className="space-y-2 text-ink">
          <li className="flex gap-2">
            <span className="text-accent">•</span>En People, para armar el legajo de cada ingreso.
          </li>
          <li className="flex gap-2">
            <span className="text-accent">•</span>En Legales, para seguir las normas nuevas de cada país donde
            opera Ripio.
          </li>
        </ul>
      </Section>

      <div className="mt-12 rounded-2xl border border-line bg-card/60 p-4 sm:p-5">
        <Pill tone="neutral">Aviso</Pill>
        <p className="mt-2 max-w-prose text-sm text-ink-2">
          No es una herramienta oficial de Ripio. Usa solo datos públicos: las blockchains y las
          certificaciones que Ripio publica. No calcula el respaldo bancario de cierres sin certificar.
        </p>
      </div>
    </>
  )
}
