import {
  WORKFLOW_MARKETPLACE_CANONICAL_COMPOSITIONS,
  WORKFLOW_MARKETPLACE_CANONICAL_DECOMPOSITIONS,
  WORKFLOW_MARKETPLACE_CASEFOLD_ENTRIES,
  WORKFLOW_MARKETPLACE_COMBINING_CLASSES
} from './workflow-marketplace-casefold.generated'

const canonicalDecompositions = new Map(WORKFLOW_MARKETPLACE_CANONICAL_DECOMPOSITIONS)
const canonicalCompositions = new Map(WORKFLOW_MARKETPLACE_CANONICAL_COMPOSITIONS)
const casefoldEntries = new Map(WORKFLOW_MARKETPLACE_CASEFOLD_ENTRIES)
const combiningClasses = new Map(WORKFLOW_MARKETPLACE_COMBINING_CLASSES)

const HANGUL_S_BASE = 0xac00
const HANGUL_L_BASE = 0x1100
const HANGUL_V_BASE = 0x1161
const HANGUL_T_BASE = 0x11a7
const HANGUL_L_COUNT = 19
const HANGUL_V_COUNT = 21
const HANGUL_T_COUNT = 28
const HANGUL_N_COUNT = HANGUL_V_COUNT * HANGUL_T_COUNT
const HANGUL_S_COUNT = HANGUL_L_COUNT * HANGUL_N_COUNT

function decomposeCharacter(character: string, result: string[]): void {
  const codepoint = character.codePointAt(0)

  if (codepoint !== undefined && codepoint >= HANGUL_S_BASE && codepoint < HANGUL_S_BASE + HANGUL_S_COUNT) {
    const syllableIndex = codepoint - HANGUL_S_BASE
    result.push(String.fromCodePoint(HANGUL_L_BASE + Math.floor(syllableIndex / HANGUL_N_COUNT)))
    result.push(String.fromCodePoint(HANGUL_V_BASE + Math.floor((syllableIndex % HANGUL_N_COUNT) / HANGUL_T_COUNT)))
    const trailingIndex = syllableIndex % HANGUL_T_COUNT

    if (trailingIndex !== 0) {
      result.push(String.fromCodePoint(HANGUL_T_BASE + trailingIndex))
    }

    return
  }

  const decomposition = canonicalDecompositions.get(character)

  if (decomposition === undefined) {
    result.push(character)

    return
  }

  for (const part of decomposition) {
    decomposeCharacter(part, result)
  }
}

function canonicallyOrder(characters: readonly string[]): string[] {
  const result: string[] = []
  let combining: Array<{ character: string; order: number; position: number }> = []

  const flushCombining = () => {
    combining.sort((left, right) => left.order - right.order || left.position - right.position)
    result.push(...combining.map(item => item.character))
    combining = []
  }

  for (const character of characters) {
    const order = combiningClasses.get(character) ?? 0

    if (order === 0) {
      flushCombining()
      result.push(character)
    } else {
      combining.push({ character, order, position: combining.length })
    }
  }

  flushCombining()

  return result
}

function composeHangul(starter: string, character: string): string | null {
  const starterCodepoint = starter.codePointAt(0)
  const characterCodepoint = character.codePointAt(0)

  if (starterCodepoint === undefined || characterCodepoint === undefined) {
    return null
  }

  const leadingIndex = starterCodepoint - HANGUL_L_BASE

  if (leadingIndex >= 0 && leadingIndex < HANGUL_L_COUNT) {
    const vowelIndex = characterCodepoint - HANGUL_V_BASE

    if (vowelIndex >= 0 && vowelIndex < HANGUL_V_COUNT) {
      return String.fromCodePoint(HANGUL_S_BASE + (leadingIndex * HANGUL_V_COUNT + vowelIndex) * HANGUL_T_COUNT)
    }
  }

  const syllableIndex = starterCodepoint - HANGUL_S_BASE
  const trailingIndex = characterCodepoint - HANGUL_T_BASE

  if (
    syllableIndex >= 0 &&
    syllableIndex < HANGUL_S_COUNT &&
    syllableIndex % HANGUL_T_COUNT === 0 &&
    trailingIndex > 0 &&
    trailingIndex < HANGUL_T_COUNT
  ) {
    return String.fromCodePoint(starterCodepoint + trailingIndex)
  }

  return null
}

function normalizeNfc(value: string): string {
  const decomposed: string[] = []

  for (const character of value) {
    decomposeCharacter(character, decomposed)
  }

  const ordered = canonicallyOrder(decomposed)

  if (ordered.length === 0) {
    return ''
  }

  const composed = [ordered[0]]
  let starterIndex = 0
  let lastCombiningClass = 0

  for (let index = 1; index < ordered.length; index += 1) {
    const character = ordered[index]
    const combiningClass = combiningClasses.get(character) ?? 0
    const starter = composed[starterIndex]

    const replacement =
      lastCombiningClass === 0 || lastCombiningClass < combiningClass
        ? (composeHangul(starter, character) ?? canonicalCompositions.get(starter + character))
        : undefined

    if (replacement !== undefined && replacement !== null) {
      composed[starterIndex] = replacement
    } else {
      if (combiningClass === 0) {
        starterIndex = composed.length
      }

      composed.push(character)
      lastCombiningClass = combiningClass
    }
  }

  return composed.join('')
}

/** Mirrors Python 3.11's Unicode 14 NFC + str.casefold marketplace identity. */
export function workflowMarketplaceCanonicalIdentity(value: string): string {
  return workflowMarketplaceCasefold(normalizeNfc(value))
}

/** Mirrors Python 3.11's Unicode 14 str.casefold without normalization. */
export function workflowMarketplaceCasefold(value: string): string {
  let result = ''

  for (const character of value) {
    result += casefoldEntries.get(character) ?? character
  }

  return result
}
