// Copies the files people download (the Excel package) from data/ into public/data/.
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
copyFileSync(join(root, 'data', 'closes', `${y}-${m}-${d}`, name), join(out, name))
console.log(`copied ${name}`)
