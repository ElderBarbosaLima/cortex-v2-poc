// v2 style guardrail (RAY-82420), run by `npm run lint`. Fails on what the v2
// rebrand removed so it cannot creep back in from new code or a `main` merge:
//   - v1 colour names (ocean, ink, cream, amber, mist...) in app/ classes or var(--color-*) reads
//   - hard-coded hex colours in .vue files
//   - radii outside the measured set (rounded-md/lg/xl/full/none only)
// Fix v1 names with `node scripts/brand-v2-codemod.mjs <files>`; roles are in brand-visual-v2.
import { readFileSync, readdirSync } from 'node:fs'
import { join, relative, dirname } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')

// Bare `slate`/`amber`/`teal` are v1; `slate-500`, `amber-600`, `teal-500` are Tailwind's own palette.
const V1 = 'ocean(?:-deep|-\\d{2,3})?|ink(?:-\\d{2,3})?|cream|sky|slate|pale|amber(?:-deep)?|teal|sage|mist|steel|rule(?:-strong)?|critical'
const PREFIX = 'bg|text|border(?:-[trblxyse])?|ring(?:-offset)?|outline|divide|fill|stroke|from|via|to|decoration|placeholder|caret|accent|shadow'
const RULES = [
  { name: 'v1 colour name', files: /\.(vue|ts|css)$/, re: new RegExp(`(?<![\\w-])(?:[\\w-]+(?:\\[[^\\]\\s]*\\])?:)*!?(?:${PREFIX})-(?:${V1})(?:\\/\\d+)?(?![\\w-])|var\\(--color-(?:${V1})\\)`, 'g') },
  { name: 'hard-coded hex colour', files: /\.vue$/, re: /(?<![\w&-])#[0-9a-fA-F]{3,8}(?![\w-])/g },
  { name: 'off-scale radius', files: /\.(vue|ts)$/, re: /(?<![\w-])rounded(?:-(?:t|r|b|l|s|e|tl|tr|bl|br|ss|se|es|ee))?(?:-(?:xs|sm|2xl|3xl|\[[^\]]*\]))?(?![\w-])/g }
]

// Third-party brand marks are hard-coded on purpose: each integration's logo
// colour is the vendor's own. The integrations page is outside the v2 restyle
// (plan R36), so its hex colours are exempt from the hex rule only.
const HEX_EXEMPT = ['app/pages/integrations.vue']

/**
 * Indexes of lines that sit wholly inside a comment: `//`, `/* ... *\/` blocks
 * (including their ` * ` body lines) and `<!-- ... -->` in templates. Prose in
 * a comment is not a class or a colour, so it is never a violation. A comment
 * that shares a line with code is still scanned.
 */
function commentLineIndexes(lines) {
  const out = new Set()
  let inBlock = false
  let inHtml = false
  lines.forEach((line, i) => {
    const t = line.trim()
    if (inBlock) {
      out.add(i)
      if (t.includes('*/')) inBlock = false
    } else if (inHtml) {
      out.add(i)
      if (t.includes('-->')) inHtml = false
    } else if (t.startsWith('//')) {
      out.add(i)
    } else if (t.startsWith('/*')) {
      out.add(i)
      if (!t.includes('*/')) inBlock = true
    } else if (t.startsWith('<!--')) {
      out.add(i)
      if (!t.includes('-->')) inHtml = true
    }
  })
  return out
}

/** Violations in one file's source: [{ line, rule, match }]. */
export function check(source, path) {
  const out = []
  const lines = source.split('\n')
  const comments = commentLineIndexes(lines)
  lines.forEach((text, i) => {
    if (comments.has(i)) return
    for (const { name, files, re } of RULES) {
      if (!files.test(path)) continue
      if (name === 'hard-coded hex colour' && HEX_EXEMPT.some(f => path.endsWith(f))) continue
      for (const [match] of text.matchAll(re)) out.push({ line: i + 1, rule: name, match })
    }
  })
  return out
}

function walk(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name)
    if (e.isDirectory()) return e.name === '__tests__' ? [] : walk(p)
    return [p]
  })
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const problems = walk(join(ROOT, 'app')).flatMap((f) => {
    const path = relative(ROOT, f)
    return check(readFileSync(f, 'utf8'), path).map(v => `${path}:${v.line}  ${v.rule}: ${v.match}`)
  })
  if (problems.length) {
    console.error(problems.join('\n'))
    console.error(`\ncheck-v2-styles: ${problems.length} problem(s). Use role names and the 6/8/12/pill radii (brand-visual-v2).`)
    process.exit(1)
  }
  console.log('check-v2-styles: clean')
}
