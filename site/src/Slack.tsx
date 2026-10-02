import { Fragment, useState, type ReactNode } from 'react'
import { site } from './data'

// Faithful render of the Block Kit payload the pipeline wrote (slack_payload.json).
// Only the subset of mrkdwn the payload uses: *bold*, <url|text>, :emoji: and lines.

const EMOJI: Record<string, string> = {
  ':warning:': '⚠️',
  ':rotating_light:': '🚨',
  ':mag:': '🔍',
  ':white_check_mark:': '✅',
  ':new:': '🆕',
}

function inline(text: string): ReactNode[] {
  const out: ReactNode[] = []
  const re = /<([^|>]+)\|([^>]+)>|\*([^*]+)\*|(:[a-z_]+:)/g
  let last = 0
  let m: RegExpExecArray | null
  let k = 0
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index))
    if (m[1]) {
      out.push(
        <a key={k++} href={m[1]} target="_blank" rel="noreferrer" className="text-[#1264a3] underline decoration-[#1264a3]/40 underline-offset-2 hover:decoration-[#1264a3]">
          {m[2]}
        </a>,
      )
    } else if (m[3]) {
      out.push(
        <strong key={k++} className="font-bold">
          {m[3]}
        </strong>,
      )
    } else if (m[4]) {
      out.push(EMOJI[m[4]] ?? m[4])
    }
    last = re.lastIndex
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

function Mrkdwn({ text }: { text: string }) {
  return (
    <>
      {text.split('\n').map((line, i) => (
        <Fragment key={i}>
          {i > 0 && <br />}
          {inline(line)}
        </Fragment>
      ))}
    </>
  )
}

type Block = {
  type: string
  text?: { type: string; text: string }
  elements?: { text: { text: string }; url: string }[]
}

export function SlackMessage({ excelHref }: { excelHref: string }) {
  const payload = site.slack.payload as { blocks: Block[] } | null
  if (!payload) return null
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-white shadow-sm">
      <div className="flex items-center gap-2 border-b border-line bg-[#2b2d31] px-4 py-2 text-sm text-white">
        <span className="font-semibold"># finanzas-cierre</span>
      </div>
      <div className="flex gap-3 p-4 text-[15px] leading-relaxed text-[#1d1c1d]">
        <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-accent text-sm font-bold text-white">
          CW
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="font-bold">Cierre wFIAT</span>
            <span className="rounded bg-[#e8e8e8] px-1 text-[10px] font-semibold uppercase text-[#616061]">
              App
            </span>
          </div>
          <div className="space-y-2 break-words">
            {payload.blocks.map((b, i) => {
              if (b.type === 'header')
                return (
                  <div key={i} className="pt-1 text-lg font-bold">
                    {b.text?.text}
                  </div>
                )
              if (b.type === 'section' && b.text)
                return (
                  <div key={i}>
                    <Mrkdwn text={b.text.text} />
                  </div>
                )
              if (b.type === 'actions')
                return (
                  <div key={i} className="pt-1">
                    {b.elements?.map((e, j) => (
                      <a
                        key={j}
                        // The dry run payload has no published link yet: the button opens
                        // the Excel served by this page.
                        href={e.url.startsWith('https://LINK') ? excelHref : e.url}
                        className="inline-block rounded border border-[#007a5a] bg-[#007a5a] px-3 py-1 text-sm font-bold text-white hover:bg-[#148567]"
                      >
                        {e.text.text}
                      </a>
                    ))}
                  </div>
                )
              return null
            })}
          </div>
        </div>
      </div>
    </div>
  )
}

// The message as text (working links) and, after a live send, the screenshot from the channel.
export function SlackView({ excelHref }: { excelHref: string }) {
  const shot = site.slack.screenshot ? `/data/${site.slack.screenshot}` : null
  const [tab, setTab] = useState<'message' | 'screenshot'>('message')
  if (!shot) return <SlackMessage excelHref={excelHref} />
  const button = (value: typeof tab, label: string) => (
    <button
      type="button"
      onClick={() => setTab(value)}
      aria-pressed={tab === value}
      className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
        tab === value ? 'bg-card text-ink shadow-card' : 'text-ink-2 hover:text-ink'
      }`}
    >
      {label}
    </button>
  )
  return (
    <div>
      <div className="mb-3 inline-flex gap-1 rounded-xl bg-line/60 p-1">
        {button('message', 'Mensaje')}
        {button('screenshot', 'Captura en Slack')}
      </div>
      {tab === 'message' ? (
        <SlackMessage excelHref={excelHref} />
      ) : (
        <figure>
          <a href={shot} target="_blank" rel="noreferrer" className="block">
            <img
              src={shot}
              alt="Captura del mensaje del cierre en el canal #finanzas-cierre de Slack"
              className="w-full rounded-xl border border-line bg-white shadow-sm"
              loading="lazy"
            />
          </a>
          <figcaption className="mt-2 text-xs text-ink-3">
            Captura del canal #finanzas-cierre. Tocá la imagen para verla en tamaño completo.
          </figcaption>
        </figure>
      )}
    </div>
  )
}
