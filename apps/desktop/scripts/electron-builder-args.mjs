export function forceNoPublishArgs(argv) {
  const args = []
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    if (arg === '--publish' || arg === '-p') {
      i += 1
    } else if (!arg.startsWith('--publish=') && !arg.startsWith('-p=')) {
      args.push(arg)
    }
  }
  return ['--publish', 'never', ...args]
}
