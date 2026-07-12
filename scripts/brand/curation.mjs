import fs from 'node:fs'
import path from 'node:path'

function excludeSet(d) {
  return new Set((d.curation?.skills?.exclude) || [])
}

export function stageSkills(d, { srcSkillsDir, destSkillsDir }) {
  const exclude = excludeSet(d)
  const staged = [], excluded = []
  function walk(rel) {
    const abs = path.join(srcSkillsDir, rel)
    const relNorm = rel.split(path.sep).join('/')
    if (relNorm && exclude.has(relNorm)) { excluded.push(relNorm); return }
    const st = fs.statSync(abs)
    if (st.isDirectory()) {
      for (const name of fs.readdirSync(abs)) walk(rel ? path.join(rel, name) : name)
    } else {
      const destPath = path.join(destSkillsDir, rel)
      fs.mkdirSync(path.dirname(destPath), { recursive: true })
      fs.copyFileSync(abs, destPath)
      staged.push(relNorm)
    }
  }
  walk('')
  return { staged, excluded: [...new Set(excluded)] }
}

export function excludedToolsets(d) {
  return (d.curation?.tools?.excludeToolsets) || []
}
