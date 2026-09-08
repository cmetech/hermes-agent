import path from 'node:path'

/** Local test interpreter selection; this never resolves or installs a runtime. */
export function marketplaceFixturePython(root: string, platform: NodeJS.Platform = process.platform) {
  return platform === 'win32'
    ? path.win32.join(root, '.venv', 'Scripts', 'python.exe')
    : path.posix.join(root, '.venv', 'bin', 'python')
}
