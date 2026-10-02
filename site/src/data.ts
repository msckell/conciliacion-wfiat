// Everything the page shows comes from this file, written by the pipeline
// (`cierre site`). The page formats nothing and computes no figure.
import site from '../../data/site/site.json'

export { site }

export type Token = (typeof site)['close']['tokens'][number]

// Fixed color per network, so a network keeps its color across every token.
// Seven categorical slots, validated order. Networks past seven share a gray.
const NETWORK_SLOT: Record<string, string> = {
  ethereum: 'var(--color-net-1)',
  base: 'var(--color-net-2)',
  worldchain: 'var(--color-net-3)',
  hyperevm: 'var(--color-net-4)',
  celo: 'var(--color-net-5)',
  bsc: 'var(--color-net-6)',
  arc: 'var(--color-net-7)',
}

export function networkColor(chain: string): string {
  return NETWORK_SLOT[chain] ?? 'var(--color-net-other)'
}

// Networks past the seven slots fold into one gray "Otras redes" group.
export function isOtherNetwork(chain: string): boolean {
  return !(chain in NETWORK_SLOT)
}

// Files the pipeline publishes next to the page (see scripts/copy-data.mjs).
export const dataHref = (file: string) => `/data/${file}`

export const excelHref = dataHref(site.close.excel_file)

// Typed by hand: the JSON holds null until the first CI read or monitor run.
type Production = {
  schedule: { monitor: string | null; close: string | null }
  ci: { conclusion: string | null; at: string; sha: string } | null
  monitor: {
    last_run_at_iso: string
    last_run_status: string
    last_ok_at_iso: string | null
    networks_checked: number
    golden_live_all_match: boolean
    golden_live_total: number | null
    changes: string[]
    problems: string[]
  } | null
  slack_live: boolean
}

export const production = site.production as unknown as Production
