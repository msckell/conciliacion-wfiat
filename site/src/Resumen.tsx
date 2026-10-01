import { excelHref, networkColor, site, type Token } from './data'
import { SlackMessage } from './Slack'
import { Alert, Card, Check, Dot, Ext, Pill, Section } from './ui'

const close = site.close

function Timeline() {
  const t = site.timeline
  if (!t) {
    return <Card>Todavía no hay una corrida registrada del cierre.</Card>
  }
  return (
    <Card>
      <p className="text-sm text-ink-3">
        Corrida del {t.started_at} (hora de Buenos Aires), duró {t.duration}.
      </p>
      <ol className="mt-4 space-y-0">
        {t.steps.map((s, i) => {
          const done = s.status === 'ok'
          const last = i === t.steps.length - 1
          return (
            <li key={s.key} className="relative flex gap-3 pb-5 last:pb-0">
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
                <p className="mt-0.5 text-sm text-ink-2">{s.detail}</p>
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
  return (
    <div>
      <div
        className="flex h-3 w-full overflow-hidden rounded-full bg-paper"
        role="img"
        aria-label={`Reparto de ${token.symbol} por red`}
      >
        {token.by_network.map((n) => (
          <div
            key={n.chain}
            title={`${n.name}: ${n.amount} (${n.share_pct}%)`}
            className="h-full border-r-2 border-card last:border-r-0"
            style={{ width: `${n.share_pct}%`, background: networkColor(n.chain), minWidth: 3 }}
          />
        ))}
      </div>
      <details className="group mt-2">
        <summary className="cursor-pointer list-none text-xs text-ink-3 hover:text-ink-2">
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
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2">
      {[...seen].map(([chain, name]) => (
        <span key={chain} className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: networkColor(chain) }} />
          {name}
        </span>
      ))}
    </div>
  )
}

function TokenCard({ token }: { token: Token }) {
  return (
    <Card>
      <div className="flex items-baseline justify-between gap-2">
        <div>
          <span className="text-lg font-semibold">{token.symbol}</span>
          <span className="ml-2 text-sm text-ink-3">{token.name}</span>
        </div>
        <span className="text-xs text-ink-3">{token.networks_with_balance} redes con saldo</span>
      </div>
      <p className="num mt-2 text-2xl font-semibold tracking-tight sm:text-[1.7rem]">{token.closing}</p>
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
      <section className="pt-8 sm:pt-12">
        <p className="max-w-prose text-lg leading-relaxed text-ink sm:text-xl">
          Cada trimestre, un contador certifica que cada wARS, wBRL y demás stablecoins de Ripio están
          respaldadas. Para eso, alguien de Finanzas junta los datos de todas las redes. Este agente
          arma esa parte solo, al día siguiente del cierre.
        </p>
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
                <span className="text-sm font-semibold text-accent">Paso {i + 1}</span>
                <p className="mt-1 text-ink">{text}</p>
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
              className="inline-flex shrink-0 items-center justify-center gap-2 rounded-lg bg-accent px-4 py-2.5 font-medium text-white hover:bg-accent/90"
            >
              <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" aria-hidden>
                <path d="M10 3v10m0 0l-4-4m4 4l4-4M4 16h12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              Descargar el Excel
            </a>
          </div>
        </Card>

        <div className="mt-4">
          <p className="text-sm font-medium text-ink-2">Redes revisadas ({close.networks_checked.length})</p>
          <p className="mt-1 text-sm leading-relaxed text-ink-3">{close.networks_checked.join(' · ')}</p>
          <p className="mt-1 text-xs text-ink-3">
            Los contratos existen en {close.networks.length} de ellas. Solo se revisan redes EVM de una lista
            escrita.
          </p>
        </div>
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

      <div className="mt-12">
        <Pill tone="neutral">Aviso</Pill>
        <p className="mt-2 max-w-prose text-sm text-ink-2">
          No es una herramienta oficial de Ripio. Usa solo datos públicos: las blockchains y las
          certificaciones que Ripio publica. No calcula el respaldo bancario de cierres sin certificar.
        </p>
      </div>
    </>
  )
}
