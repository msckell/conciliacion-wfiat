// Everything the page shows comes from this file, written by the pipeline
// (`cierre site`). The page formats nothing and computes no figure.
import raw from '../../data/site/site.json'

export const site = raw

export type Site = typeof raw
export type Token = Site['close']['tokens'][number]

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

export const excelHref = `/data/${site.close.excel_file}`
