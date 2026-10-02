// Copies the files the page links to (the Excel package and the Slack screenshot) from data/
// into public/data/.
// The page data itself is imported at build time from data/site/site.json.
import { copyFileSync, mkdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const site = JSON.parse(readFileSync(join(root, 'data', 'site', 'site.json'), 'utf8'))
const [d, m, y] = site.close.cutoff.split('/')
const name = site.close.excel_file
const out = join(root, 'site', 'public', 'data')
mkdirSync(out, { recursive: true })
const closeDir = join(root, 'data', 'closes', `${y}-${m}-${d}`)
for (const file of [name, site.slack.screenshot].filter(Boolean)) {
  copyFileSync(join(closeDir, file), join(out, file))
  console.log(`copied ${file}`)
}
