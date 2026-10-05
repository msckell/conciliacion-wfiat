import { useState } from 'react'
import { excelHref, isOtherNetwork, networkColor, site, type Token } from './data'
import { SlackView } from './Slack'
import { Alert, Card, Check, Dot, Ext, NavyCard, Pill, REPO_URL, Section, TokenIcon } from './ui'

const close = site.close
const verification = site.verification
const agent = site.agent_vs_manual

function ExcelButton({ noShrink = false }: { noShrink?: boolean }) {
  return (
    <a
      href={excelHref}
      download
      className={`inline-flex ${noShrink ? 'shrink-0 ' : ''}items-center justify-center gap-2 rounded-xl bg-white px-4 py-2.5 font-semibold text-navy transition hover:bg-white/90`}
    >
      <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" aria-hidden>
        <path d="M10 3v10m0 0l-4-4m4 4l4-4M4 16h12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      Descargar el Excel
    </a>
  )
}

function HeroStat({ value, label, warn = false }: { value: string; label: string; warn?: boolean }) {
  return (
    <div className={`px-4 py-3.5 sm:px-5 sm:py-4 ${warn ? 'bg-[#fbbf24]/15' : 'bg-navy/55'}`}>
      <dt className="sr-only">{label}</dt>
      <dd className={`num text-xl font-semibold tracking-tight sm:text-2xl ${warn ? 'text-[#fcd34d]' : 'text-white'}`}>
        {value}
      </dd>
      <dd className="mt-0.5 text-xs leading-snug text-on-navy sm:text-[13px]">{label}</dd>
    </div>
  )
}

function CodeIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" aria-hidden>
      <path d="M7 6l-4 4 4 4M13 6l4 4-4 4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function Hero() {
  const exc = site.exceptions
  return (
    <section className="pt-5 sm:pt-10">
      <div className="fade-up hero-bg overflow-hidden rounded-3xl px-5 pb-5 pt-6 text-white shadow-lift sm:px-10 sm:pb-8 sm:pt-10">
        <span className="inline-flex items-center gap-2 rounded-full bg-white/10 px-3 py-1 text-xs font-medium text-on-navy ring-1 ring-white/15">
          <span className="h-1.5 w-1.5 rounded-full bg-[#6ee7a8]" aria-hidden />
          Cierre al {close.cutoff}
        </span>
        <h2 className="mt-4 max-w-3xl text-3xl font-semibold leading-tight tracking-tight text-white sm:text-5xl sm:leading-[1.1]">
          El cierre del trimestre, armado solo y listo para revisar.
        </h2>
        <p className="mt-4 max-w-2xl text-lg leading-relaxed text-white/90 sm:text-xl">
          Junta los datos de cada red, los concilia y le entrega a Finanzas un Excel con lo que tiene que revisar.
        </p>
        <p className="mt-2 max-w-2xl text-sm text-on-navy">
          Es la parte onchain que después certifica un contador, para las stablecoins wFIAT de Ripio.
        </p>
        <div className="mt-6 flex flex-col gap-2 sm:flex-row sm:gap-3">
          <ExcelButton />
          <a
            href={REPO_URL}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 font-semibold text-white ring-1 ring-white/25 transition hover:bg-white/10"
          >
            <CodeIcon />
            Ver el código
          </a>
        </div>
        <dl className="mt-8 grid grid-cols-2 gap-px overflow-hidden rounded-2xl bg-white/10 ring-1 ring-white/10 lg:grid-cols-4">
          <HeroStat
            value={close.all_reconciled ? 'Todo cierra' : `${close.reconciled} de ${close.reconciliations}`}
            label={`${close.reconciled} de ${close.reconciliations} saldos por moneda y red, sin diferencia`}
          />
          {agent && (
            <>
              <HeroStat value={`${agent.networks} redes`} label={`y ${agent.tokens} monedas en esta corrida`} />
              <HeroStat value={agent.duration} label="duró la corrida completa" />
            </>
          )}
          {exc && <HeroStat value={`${exc.tasks} casos`} label="quedaron para que los revise una persona" warn />}
        </dl>
        <p className="mt-4 flex items-center gap-2 text-sm text-on-navy">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[#6ee7a8]/20 text-[#6ee7a8]">
            <Check className="h-3.5 w-3.5" />
          </span>
          Método validado: coincide con el contador en {verification.matched} de {verification.total} certificaciones
          publicadas.
        </p>
      </div>
    </section>
  )
}

// A step detail that is a bare URL reads better as a link named after its file.
function StepDetail({ text }: { text: string }) {
  if (/^https?:\/\/\S+$/.test(text)) {
    return <Ext href={text}>{text.split('/').pop()}</Ext>
  }
  return <>{text}</>
}

// Short names for the run steps, for the strip a reader scans at a glance.
const STEP_SHORT: Record<string, string> = {
  engine: 'Leyó cada red',
  package: 'Concilió los saldos',
  exceptions: 'Investigó los casos raros',
  publish: 'Publicó el Excel',
  notify: 'Avisó a Finanzas',
}

function StepStrip({ steps }: { steps: { key: string; status: string }[] }) {
  const shown = steps.filter((s) => s.key in STEP_SHORT)
  return (
    <ol className="grid grid-cols-1 gap-2 sm:grid-cols-5 sm:gap-0">
      {shown.map((s, i) => (
        <li key={s.key} className="relative flex items-center gap-3 sm:flex-col sm:gap-2 sm:text-center">
          {i < shown.length - 1 && (
            <span className="absolute left-1/2 top-5 hidden h-px w-full bg-line sm:block" aria-hidden />
          )}
          <span
            className={`relative flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-sm font-semibold ${
              s.status === 'ok' ? 'bg-ok-soft text-ok' : 'bg-warn-soft text-warn'
            }`}
          >
            {s.status === 'ok' ? <Check className="h-5 w-5" /> : i + 1}
          </span>
          <span className="text-sm font-medium leading-snug text-ink sm:px-2">{STEP_SHORT[s.key]}</span>
        </li>
      ))}
    </ol>
  )
}

function Timeline() {
  const t = site.timeline
  const [open, setOpen] = useState(false)
  if (!t) {
    return <Card>Todavía no hay una corrida registrada del cierre.</Card>
  }
  return (
    <Card>
      <StepStrip steps={t.steps} />
      <div className="mt-5 flex flex-wrap items-center justify-between gap-2 border-t border-line pt-4">
        <p className="text-sm text-ink-3">
          Corrida del {t.started_at} (hora de Buenos Aires), duró {t.duration}.
        </p>
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          className="text-sm font-medium text-accent"
        >
          {open ? 'Ocultar el detalle' : 'Ver cada paso en detalle'}
        </button>
      </div>
      {open && (
      <ol className="mt-4 space-y-0">
        {t.steps.map((s, i) => {
          const done = s.status === 'ok'
          const last = i === t.steps.length - 1
          return (
            <li
              key={s.key}
              className="relative flex gap-3 pb-5 last:pb-0"
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
                <p className="mt-0.5 break-words text-sm text-ink-2">
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
      )}
    </Card>
  )
}

// One case the exception agent resolved, told in four plain steps. The first resolved case
// in the data, so the card never shows a case that is not in this close.
function ResolvedCase() {
  const item = site.exceptions?.items.find((x) => x.outcome === 'resolved')
  if (!item) return null
  const primary = item.summary.includes('emisión primaria')
  const steps = [
    {
      title: 'Encontró algo que no reconocía',
      text: `Una ${item.movement} en ${item.chain} que no pasó por ningún camino conocido.`,
    },
    {
      title: 'Investigó por su cuenta',
      text: 'Leyó la transacción y consultó en la blockchain quién tenía permiso para emitir.',
    },
    {
      title: 'Propuso una respuesta',
      text: primary
        ? 'Es una emisión nueva, hecha por una cuenta autorizada del emisor.'
        : 'Propuso una clasificación con la evidencia que encontró.',
    },
    {
      title: 'El código la comprobó',
      text: 'Antes de aceptarla, el sistema confirmó el permiso en ese mismo bloque. Recién ahí la dio por resuelta.',
    },
  ]
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <Pill tone="ok">
          <Check className="h-3.5 w-3.5" />
          Resuelto por el agente
        </Pill>
        <span className="text-sm text-ink-3">
          {item.token} en {item.chain}
        </span>
      </div>
      <ol className="mt-4 grid gap-4 sm:grid-cols-4">
        {steps.map((s, i) => (
          <li key={s.title} className="flex gap-3 sm:flex-col sm:gap-2">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent-soft text-sm font-semibold text-accent">
              {i + 1}
            </span>
            <div>
              <p className="font-medium leading-snug text-ink">{s.title}</p>
              <p className="mt-1 text-sm text-ink-2">{s.text}</p>
            </div>
          </li>
        ))}
      </ol>
      <p className="mt-4 border-t border-line pt-3 text-sm">
        <Ext href={item.explorer_url}>Ver la transacción en el explorador</Ext>
      </p>
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

function NewNetworkAlert({ network }: { network: (typeof close.new_networks)[number] }) {
  return (
    <div className="mb-4 flex items-start gap-3 rounded-xl border border-warn/25 bg-warn-soft p-4 text-warn">
      <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-warn text-white">
        <Alert />
      </span>
      <div className="text-ink">
        <p className="font-medium">Red nueva desde el último cierre: {network.name}</p>
        <p className="mt-0.5 text-sm text-ink-2">
          Los contratos se crearon desde el bloque{' '}
          <Ext href={network.first_creation_block_url}>
            <span className="num">{network.first_creation_block}</span>
          </Ext>
          , después del cierre anterior. Por eso su saldo de apertura es cero.
        </p>
      </div>
    </div>
  )
}

function ReconciliationCard() {
  return (
    <NavyCard className="mt-4">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="num text-2xl font-semibold tracking-tight text-white">
            {close.reconciled} de {close.reconciliations}
          </p>
          <p className="mt-0.5 font-medium text-white">conciliaciones por moneda y red con diferencia cero</p>
          <p className="mt-1 text-sm text-on-navy">
            Saldo de apertura más emisiones menos quemas igual a saldo de cierre, exacto.
          </p>
        </div>
        <ExcelButton noShrink />
      </div>
    </NavyCard>
  )
}

const HOW_IT_WORKS = [
  'Cuenta los tokens de cada red a la fecha y hora del corte, con el bloque usado en cada red como prueba.',
  'Lista cada emisión y cada quema con su comprobante.',
  'Avisa a Finanzas por Slack qué cambió y qué le toca revisar.',
]

function HandVsAgent() {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Card>
        <p className="text-sm font-semibold text-ink-3">Hecho a mano</p>
        <p className="mt-1">
          Buscar en cada explorador, una planilla por red y el riesgo de olvidarse una red nueva.
        </p>
      </Card>
      <NavyCard>
        <p className="text-sm font-semibold text-on-navy">Con el agente</p>
        <p className="mt-1 text-white">
          {agent
            ? `Este cierre revisó ${agent.transactions} transacciones en ${agent.networks} redes para ${agent.tokens} monedas, en ${agent.duration}, con el link de cada transacción.`
            : 'Todavía no hay una corrida registrada.'}
        </p>
        {agent && (
          <dl className="mt-4 grid grid-cols-3 gap-2 border-t border-white/15 pt-4">
            {[
              [String(agent.transactions), 'transacciones'],
              [String(agent.networks), 'redes'],
              [agent.duration, 'de corrida'],
            ].map(([value, label]) => (
              <div key={label}>
                <dd className="num text-lg font-semibold tracking-tight text-white sm:text-xl">{value}</dd>
                <dt className="text-xs text-on-navy">{label}</dt>
              </div>
            ))}
          </dl>
        )}
      </NavyCard>
    </div>
  )
}

export function Resumen() {
  const exc = site.exceptions
  return (
    <>
      <Hero />

      <Section
        id="cierre"
        title={`Cierre al ${close.cutoff}, listo para revisión`}
        lead={`Foto fija al ${close.cutoff_instant}. Generado el ${close.generated_at}.`}
      >
        {close.new_networks.map((n) => (
          <NewNetworkAlert key={n.name} network={n} />
        ))}

        <div className="mb-3">
          <Legend />
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          {close.tokens.map((t) => (
            <TokenCard key={t.symbol} token={t} />
          ))}
        </div>

        <ReconciliationCard />
        <NetworksChecked />
      </Section>

      <Section title="Lo que hizo el agente" lead="La última corrida del cierre, tal como quedó registrada.">
        <Timeline />
      </Section>

      {exc && (
        <Section
          title="Un caso que resolvió el agente"
          lead={`El motor dejó ${exc.investigated} movimientos sin clasificar. El agente resolvió ${exc.resolved} con evidencia y dejó ${exc.tasks} para una persona. Este es uno de los resueltos.`}
        >
          <ResolvedCase />
        </Section>
      )}

      <Section title="Cómo lo hace">
        <ol className="grid gap-3 sm:grid-cols-3">
          {HOW_IT_WORKS.map((text, i) => (
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
        title="El mensaje que recibe Finanzas"
        lead={
          site.slack.dry_run
            ? 'Así queda el mensaje en Slack. Por ahora corre en modo de prueba: se genera igual, pero todavía no se envía.'
            : 'El mensaje que llegó a Slack.'
        }
      >
        <SlackView />
        {exc && (
          <p className="mt-2 text-sm text-ink-3">
            Las tareas salen del agente de excepciones: investigó {exc.investigated} movimientos, resolvió{' '}
            {exc.resolved} con evidencia y dejó {exc.tasks} para una persona.
          </p>
        )}
      </Section>

      <Section title="A mano y con el agente">
        <HandVsAgent />
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
