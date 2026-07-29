#!/usr/bin/env node

import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import readline from "node:readline";
import { parse as micromarkParse, postprocess, preprocess } from "micromark";
import ts from "typescript";
import { unified } from "unified";
import remarkParse from "remark-parse";

const require = createRequire(import.meta.url);
const PROTOCOL_VERSION = 1;
const EXPECTED_VERSIONS = Object.freeze({
  typescript: "6.0.3",
  unified: "11.0.5",
  "remark-parse": "11.0.0",
  micromark: "4.0.2",
});
const MAX_BLOB_BYTES = 4 * 1024 * 1024;
const MAX_BATCH_BYTES = 16 * 1024 * 1024;
const MAX_REQUEST_ID_BYTES = 128;
const MAX_PATH_BYTES = 4096;
const MAX_SYMBOL_BYTES = 256;
const JAVASCRIPT_EXTENSIONS = new Set([".js", ".jsx", ".mjs", ".cjs"]);
const TYPESCRIPT_EXTENSIONS = new Set([".ts", ".tsx", ".mts", ".cts"]);
const MARKDOWN_EXTENSIONS = new Set([".md", ".markdown", ".mdown", ".mkdn"]);
const SEARCHABLE_MARKDOWN = new Set([
  "text", "inlineCode", "code", "definition", "link", "image",
  "linkReference", "imageReference",
]);

class HelperError extends Error {
  constructor(code, id) {
    super(code);
    this.code = code;
    this.id = id ?? null;
  }
}

function fail(code, id = null) {
  throw new HelperError(code, id);
}

function emit(record) {
  process.stdout.write(`${JSON.stringify(record)}\n`);
}

function errorDetail(code, id) {
  return `${code}: ${id ?? "null"}`;
}

function exactKeys(record, keys) {
  const actual = Object.keys(record).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function packageManifest(name) {
  let directory = dirname(require.resolve(name));
  while (true) {
    try {
      const manifest = JSON.parse(readFileSync(join(directory, "package.json"), "utf8"));
      if (manifest.name === name) return manifest;
    } catch {
      // Continue toward the package root; package entry points can be nested.
    }
    const parent = dirname(directory);
    if (parent === directory) fail("dependency_mismatch");
    directory = parent;
  }
}

function loadedVersions() {
  const versions = Object.fromEntries(Object.keys(EXPECTED_VERSIONS).map(name => [name, packageManifest(name).version]));
  if (Object.entries(EXPECTED_VERSIONS).some(([name, version]) => versions[name] !== version) || ts.version !== EXPECTED_VERSIONS.typescript) {
    fail("dependency_mismatch");
  }
  return versions;
}

function extensionFor(path) {
  const base = path.toLowerCase();
  const index = base.lastIndexOf(".");
  return index < 0 ? "" : base.slice(index);
}

function validateHello(record) {
  if (!record || typeof record !== "object" || Array.isArray(record) || !exactKeys(record, ["type", "protocol"]) ||
      record.type !== "hello" || record.protocol !== PROTOCOL_VERSION) {
    fail("protocol_mismatch");
  }
}

function enforceMetadataLimits(record) {
  if (typeof record.id === "string" && Buffer.byteLength(record.id, "utf8") > MAX_REQUEST_ID_BYTES) {
    fail("metadata_limit");
  }
  if (typeof record.path === "string" && Buffer.byteLength(record.path, "utf8") > MAX_PATH_BYTES) {
    fail("metadata_limit", typeof record.id === "string" ? record.id : null);
  }
  for (const symbol of Array.isArray(record.symbols) ? record.symbols : []) {
    if (typeof symbol === "string" && Buffer.byteLength(symbol, "utf8") > MAX_SYMBOL_BYTES) {
      fail("metadata_limit", typeof record.id === "string" ? record.id : null);
    }
  }
}

function validateParseRecord(record) {
  if (record && typeof record === "object" && !Array.isArray(record)) enforceMetadataLimits(record);
  if (!record || typeof record !== "object" || Array.isArray(record) ||
      !exactKeys(record, ["type", "id", "path", "language", "source_base64", "symbols"]) || record.type !== "parse" ||
      typeof record.id !== "string" || typeof record.path !== "string" || typeof record.source_base64 !== "string" ||
      !["javascript", "typescript", "markdown"].includes(record.language) || !Array.isArray(record.symbols) ||
      record.symbols.some(symbol => typeof symbol !== "string")) {
    fail("invalid_request", typeof record?.id === "string" ? record.id : null);
  }
  if (record.symbols.some(symbol => symbol.length === 0)) fail("invalid_symbol", record.id);
  const extension = extensionFor(record.path);
  const supported = (record.language === "javascript" && JAVASCRIPT_EXTENSIONS.has(extension)) ||
    (record.language === "typescript" && TYPESCRIPT_EXTENSIONS.has(extension)) ||
    (record.language === "markdown" && MARKDOWN_EXTENSIONS.has(extension));
  if (!supported) fail("unsupported_language", record.id);
}

function decodeCanonicalBase64(value, id) {
  if (value.length % 4 !== 0 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) {
    fail("invalid_base64", id);
  }
  const decoded = Buffer.from(value, "base64");
  if (decoded.toString("base64") !== value) fail("invalid_base64", id);
  return decoded;
}

function utf8ByteOffsets(sourceText) {
  const offsets = new Array(sourceText.length + 1);
  let utf16 = 0;
  let bytes = 0;
  offsets[0] = 0;
  while (utf16 < sourceText.length) {
    const codePoint = sourceText.codePointAt(utf16);
    const width = codePoint > 0xffff ? 2 : 1;
    offsets[utf16] = bytes;
    if (width === 2) offsets[utf16 + 1] = bytes;
    bytes += Buffer.byteLength(String.fromCodePoint(codePoint), "utf8");
    utf16 += width;
    offsets[utf16] = bytes;
  }
  return offsets;
}

function isIdentifierByte(value) {
  return value === 0x24 || value === 0x5f ||
    (value >= 0x30 && value <= 0x39) ||
    (value >= 0x41 && value <= 0x5a) ||
    (value >= 0x61 && value <= 0x7a);
}

function mergeRanges(ranges) {
  const merged = [];
  for (const [start, end] of [...ranges].sort((left, right) => left[0] - right[0] || left[1] - right[1])) {
    const previous = merged.at(-1);
    if (previous && start <= previous[1]) previous[1] = Math.max(previous[1], end);
    else merged.push([start, end]);
  }
  return merged;
}

function rangeCovered(start, end, ranges) {
  let low = 0;
  let high = ranges.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (ranges[middle][0] <= start) low = middle + 1;
    else high = middle;
  }
  const candidate = ranges[low - 1];
  return candidate !== undefined && candidate[0] <= start && end <= candidate[1];
}

function exactCoveredMatches(sourceBytes, coveredByteRanges, symbolBytes) {
  const matches = [];
  const searchable = mergeRanges(coveredByteRanges);
  for (let start = sourceBytes.indexOf(symbolBytes); start >= 0; start = sourceBytes.indexOf(symbolBytes, start + 1)) {
    const end = start + symbolBytes.length;
    const bounded = (start === 0 || !isIdentifierByte(sourceBytes[start - 1])) &&
      (end === sourceBytes.length || !isIdentifierByte(sourceBytes[end]));
    if (bounded && rangeCovered(start, end, searchable)) matches.push([start, end]);
  }
  return matches;
}

function deduplicateSpans(spans) {
  const seen = new Set();
  return spans.filter(([start, end]) => {
    const key = `${start}:${end}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).sort((left, right) => left[0] - right[0] || left[1] - right[1]);
}

function identifierSpan(node, offsets) {
  return [offsets[node.getStart()], offsets[node.getEnd()]];
}

function entityNameParts(node) {
  if (ts.isIdentifier(node) || ts.isPrivateIdentifier(node)) return [node];
  if (ts.isPropertyAccessExpression(node) || ts.isQualifiedName(node)) return [...entityNameParts(node.left ?? node.expression), ...entityNameParts(node.right ?? node.name)];
  return [];
}

function extractQualifiedTypeScriptSpans(sourceFile, offsets, requestedSymbols) {
  const found = new Map([...requestedSymbols].map(symbol => [symbol, []]));
  const addRelationship = parts => {
    const symbol = parts.map(part => part.text).join(".");
    if (requestedSymbols.has(symbol)) found.get(symbol).push(...parts.map(part => identifierSpan(part, offsets)));
  };
  const walk = (node, scope) => {
    if (ts.isPropertyAccessExpression(node) || ts.isQualifiedName(node)) addRelationship(entityNameParts(node));
    const isScope = ts.isModuleDeclaration(node) || ts.isClassDeclaration(node) || ts.isInterfaceDeclaration(node) || ts.isEnumDeclaration(node);
    if (isScope && node.name && (ts.isIdentifier(node.name) || ts.isPrivateIdentifier(node.name))) {
      ts.forEachChild(node, child => walk(child, [...scope, node.name]));
      return;
    }
    if (node.name && (ts.isIdentifier(node.name) || ts.isPrivateIdentifier(node.name))) addRelationship([...scope, node.name]);
    ts.forEachChild(node, child => walk(child, scope));
  };
  walk(sourceFile, []);
  for (const [symbol, spans] of found) found.set(symbol, deduplicateSpans(spans));
  return found;
}

function isQualifiedFullMatch(match, spans) {
  const contained = spans.filter(([start, end]) => match[0] <= start && end <= match[1]);
  return contained.length > 1;
}

function extractTypeScriptSpans(sourceText, sourceBytes, symbols, scriptKind) {
  const parsed = ts.createSourceFile("input", sourceText, ts.ScriptTarget.Latest, true, scriptKind);
  if (parsed.parseDiagnostics.length) fail("parse_diagnostic");
  const offsets = utf8ByteOffsets(sourceText);
  const scanner = ts.createScanner(ts.ScriptTarget.Latest, true, parsed.languageVariant, sourceText);
  const covered = [];
  for (let token = scanner.scan(); token !== ts.SyntaxKind.EndOfFileToken; token = scanner.scan()) {
    covered.push([offsets[scanner.getTokenPos()], offsets[scanner.getTextPos()]]);
  }
  const qualified = extractQualifiedTypeScriptSpans(parsed, offsets, new Set(symbols));
  return Object.fromEntries(symbols.map(symbol => {
    const qualifiedSpans = qualified.get(symbol) ?? [];
    const exact = exactCoveredMatches(sourceBytes, covered, Buffer.from(symbol, "utf8"));
    const nonEnclosingExact = symbol.includes(".") ? exact.filter(match => !isQualifiedFullMatch(match, qualifiedSpans)) : exact;
    return [symbol, deduplicateSpans([...nonEnclosingExact, ...qualifiedSpans])];
  }));
}

function nodeRange(node, offsets) {
  const start = node?.position?.start?.offset;
  const end = node?.position?.end?.offset;
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || start >= end || end >= offsets.length) {
    fail("invalid_markdown_position");
  }
  return [offsets[start], offsets[end]];
}

function htmlTagAt(sourceText, start, end) {
  let cursor = start + 1;
  let closing = false;
  if (sourceText[cursor] === "/") {
    closing = true;
    cursor += 1;
  }
  const nameStart = cursor;
  while (cursor < end && /[A-Za-z0-9-]/.test(sourceText[cursor])) cursor += 1;
  if (cursor === nameStart || !/[A-Za-z]/.test(sourceText[nameStart])) return null;
  const name = sourceText.slice(nameStart, cursor).toLowerCase();
  let quote = null;
  for (; cursor < end; cursor += 1) {
    const character = sourceText[cursor];
    if (quote !== null) {
      if (character === quote) quote = null;
    } else if (character === "\"" || character === "'") {
      quote = character;
    } else if (character === ">") {
      const beforeClose = sourceText.slice(start, cursor).trimEnd();
      return { closing, end: cursor + 1, name, selfClosing: beforeClose.endsWith("/") };
    }
  }
  return { closing, end, name, selfClosing: false };
}

function findRawClosingTag(sourceText, start, end, name) {
  for (let cursor = sourceText.indexOf("<", start); cursor >= 0 && cursor < end;
       cursor = sourceText.indexOf("<", cursor + 1)) {
    const candidate = sourceText.slice(cursor + 2, cursor + 2 + name.length);
    const boundary = sourceText[cursor + 2 + name.length];
    if (sourceText[cursor + 1] === "/" && candidate.toLowerCase() === name &&
        (boundary === undefined || boundary === ">" || boundary === "/" || /\s/.test(boundary))) {
      return cursor;
    }
  }
  return end;
}

function htmlCommentCharacterRanges(sourceText, start, end) {
  const ranges = [];
  const rawTextElements = new Set([
    "iframe", "noembed", "noframes", "plaintext", "script", "style", "textarea", "title", "xmp",
  ]);
  let cursor = start;
  let rawElement = null;
  while (cursor < end) {
    if (rawElement !== null) {
      cursor = findRawClosingTag(sourceText, cursor, end, rawElement);
      rawElement = null;
      continue;
    }
    if (sourceText.startsWith("<!--", cursor)) {
      const close = sourceText.indexOf("-->", cursor + 4);
      const commentEnd = close < 0 || close + 3 > end ? end : close + 3;
      ranges.push([cursor, commentEnd]);
      cursor = commentEnd;
      continue;
    }
    if (sourceText[cursor] === "<") {
      const tag = htmlTagAt(sourceText, cursor, end);
      if (tag !== null) {
        if (!tag.closing && !tag.selfClosing && rawTextElements.has(tag.name)) rawElement = tag.name;
        cursor = tag.end;
        continue;
      }
      let quote = null;
      let markupEnd = cursor + 1;
      for (; markupEnd < end; markupEnd += 1) {
        const character = sourceText[markupEnd];
        if (quote !== null) {
          if (character === quote) quote = null;
        } else if (character === "\"" || character === "'") {
          quote = character;
        } else if (character === ">") {
          markupEnd += 1;
          break;
        }
      }
      cursor = markupEnd;
      continue;
    }
    cursor += 1;
  }
  return ranges;
}

function markdownHtmlCommentRanges(sourceText, offsets) {
  const events = postprocess(
    micromarkParse().document().write(preprocess()(sourceText, undefined, true)),
  );
  const ranges = [];
  for (const [eventType, token] of events) {
    if (eventType !== "enter" || (token.type !== "htmlFlow" && token.type !== "htmlText")) continue;
    const start = token?.start?.offset;
    const end = token?.end?.offset;
    if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || start >= end || end >= offsets.length) {
      fail("invalid_markdown_position");
    }
    for (const [commentStart, commentEnd] of htmlCommentCharacterRanges(sourceText, start, end)) {
      ranges.push([offsets[commentStart], offsets[commentEnd]]);
    }
  }
  return mergeRanges(ranges);
}

function extractMarkdownSpans(sourceText, sourceBytes, symbols) {
  const tree = unified().use(remarkParse).parse(sourceText);
  const offsets = utf8ByteOffsets(sourceText);
  const covered = [];
  const excluded = markdownHtmlCommentRanges(sourceText, offsets);
  const visit = node => {
    if (node.type === "html") {
      covered.push(nodeRange(node, offsets));
    } else if (SEARCHABLE_MARKDOWN.has(node.type)) covered.push(nodeRange(node, offsets));
    for (const child of node.children ?? []) visit(child);
  };
  visit(tree);
  const searchable = covered.flatMap(([start, end]) => {
    let pieces = [[start, end]];
    for (const [cutStart, cutEnd] of excluded) {
      pieces = pieces.flatMap(([left, right]) => cutEnd <= left || right <= cutStart
        ? [[left, right]]
        : [[left, Math.max(left, cutStart)], [Math.min(right, cutEnd), right]].filter(([pieceStart, pieceEnd]) => pieceStart < pieceEnd));
    }
    return pieces;
  });
  return Object.fromEntries(symbols.map(symbol => [symbol, exactCoveredMatches(sourceBytes, searchable, Buffer.from(symbol, "utf8"))]));
}

function scriptKindFor(path) {
  switch (extensionFor(path)) {
    case ".js": return ts.ScriptKind.JS;
    case ".jsx": return ts.ScriptKind.JSX;
    case ".mjs": return ts.ScriptKind.JS;
    case ".cjs": return ts.ScriptKind.JS;
    case ".ts": return ts.ScriptKind.TS;
    case ".tsx": return ts.ScriptKind.TSX;
    case ".mts": return ts.ScriptKind.TS;
    case ".cts": return ts.ScriptKind.TS;
    default: fail("unsupported_language");
  }
}

function extract(record, sourceText, sourceBytes) {
  if (record.language === "markdown") return extractMarkdownSpans(sourceText, sourceBytes, record.symbols);
  return extractTypeScriptSpans(sourceText, sourceBytes, record.symbols, scriptKindFor(record.path));
}

async function main() {
  const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  let helloSeen = false;
  let decodedBytes = 0;
  const requestIds = new Set();
  for await (const line of input) {
    if (!line) continue;
    let record;
    try {
      record = JSON.parse(line);
    } catch {
      fail("invalid_json");
    }
    if (!helloSeen) {
      validateHello(record);
      helloSeen = true;
      emit({ type: "hello", protocol: PROTOCOL_VERSION, versions: loadedVersions() });
      continue;
    }
    validateParseRecord(record);
    if (requestIds.has(record.id)) fail("duplicate_request", record.id);
    requestIds.add(record.id);
    const sourceBytes = decodeCanonicalBase64(record.source_base64, record.id);
    decodedBytes += sourceBytes.length;
    if (sourceBytes.length > MAX_BLOB_BYTES || decodedBytes > MAX_BATCH_BYTES) fail("input_limit", record.id);
    let sourceText;
    try {
      sourceText = new TextDecoder("utf-8", { fatal: true }).decode(sourceBytes);
    } catch {
      fail("invalid_utf8", record.id);
    }
    if (sourceText.includes("\0")) fail("invalid_source", record.id);
    try {
      emit({ type: "result", id: record.id, spans: extract(record, sourceText, sourceBytes) });
    } catch (error) {
      if (error instanceof HelperError && error.id === null) {
        throw new HelperError(error.code, record.id);
      }
      throw error;
    }
  }
  if (!helloSeen) fail("missing_hello");
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch(error => {
    const code = error instanceof HelperError ? error.code : "internal_error";
    const id = error instanceof HelperError ? error.id : null;
    emit({ type: "error", id, code, detail: errorDetail(code, id) });
    process.exitCode = 1;
  });
}

export {
  exactCoveredMatches,
  extractMarkdownSpans,
  extractQualifiedTypeScriptSpans,
  extractTypeScriptSpans,
  markdownHtmlCommentRanges,
  utf8ByteOffsets,
};
