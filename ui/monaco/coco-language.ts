import type * as Monaco from 'monaco-editor'
import type { Diagnostic, Signature, Symbols } from '~/types/api'

// The `coco` language for Monaco: highlighting for Coco and the Python inside steps,
// editing rules, completion and hover from the engine's /validate symbols, and markers.

const COCO_KEYWORDS = ['package', 'import', 'from', 'as', 'class', 'step', 'flow', 'if', 'elif', 'else', 'for', 'in', 'return']
const PY_KEYWORDS = [
  'and', 'or', 'not', 'is', 'while', 'break', 'continue', 'pass', 'def', 'lambda', 'try', 'except', 'finally',
  'raise', 'with', 'yield', 'del', 'global', 'nonlocal', 'assert', 'async', 'await',
]
const TYPES = ['string', 'boolean', 'int', 'float', 'object', 'array']
const BUILTINS = [
  'len', 'min', 'max', 'abs', 'round', 'sum', 'str', 'bool', 'sorted', 'list', 'range', 'print', 'dict', 'set',
  'tuple', 'enumerate', 'zip', 'isinstance', 'map', 'filter', 'any', 'all', 'open', 'repr', 'type',
]
const SELF_MEMBERS: { label: string; insert: string; doc: string }[] = [
  { label: 'log', insert: 'log(${1:values})', doc: 'Log any number of values, like console.log: `self.log("total", n, data, True)`. Objects show as expandable trees.' },
  { label: 'info', insert: 'info(${1:values})', doc: 'Log at info level (same as `log`).' },
  { label: 'warn', insert: 'warn(${1:values})', doc: 'Log a warning, highlighted in the run viewer.' },
  { label: 'error', insert: 'error(${1:values})', doc: 'Log an error, highlighted in the run viewer. Does not stop the step: raise an exception for that.' },
  { label: 'debug', insert: 'debug(${1:values})', doc: 'Log at debug level (dimmed; can be filtered out).' },
  { label: 'log_table', insert: 'log_table(${1:rows})', doc: 'Log rows as a table, like console.table: a list of objects, lists or values, or a dict of rows. Optional `columns=["a", "b"]`. The viewer can sort and filter it.' },
  { label: 'env', insert: 'env', doc: 'The run environment (read-only): `self.env.api_url`, `self.env["api_url"]`, `self.env.get("k", default)`.' },
  { label: 'run_id', insert: 'run_id', doc: 'Id of the current run.' },
  { label: 'flow', insert: 'flow', doc: 'Name of the top-level flow.' },
  { label: 'step', insert: 'step', doc: 'Name of this step.' },
  { label: 'path', insert: 'path', doc: 'Node path of this call, e.g. onboard/for#0[2]/enrich.' },
]

const symbolsByModel = new Map<string, Symbols>()
let registered = false
let envData: Record<string, unknown> | null = null
let envName: string | null = null

/** Keys offered after `env.` / `self.env.`: the environment selected for new runs. */
export function setEnvData(data: Record<string, unknown> | null, name: string | null = null) {
  envData = data
  envName = name
}

function preview(v: unknown): string {
  if (v !== null && typeof v === 'object') return Array.isArray(v) ? `array (${v.length})` : 'object'
  const s = JSON.stringify(v)
  return s.length > 40 ? `${s.slice(0, 39)}…` : s
}

export function setSymbols(model: Monaco.editor.ITextModel, symbols: Symbols | null) {
  if (symbols) symbolsByModel.set(model.uri.toString(), symbols)
  else symbolsByModel.delete(model.uri.toString())
}

export function setDiagnostics(monaco: typeof Monaco, model: Monaco.editor.ITextModel, diags: Diagnostic[]) {
  const lines = model.getLineCount()
  monaco.editor.setModelMarkers(model, 'coco', diags.map((d) => {
    const line = Math.min(Math.max(d.line, 1), lines)
    return {
      startLineNumber: line,
      startColumn: Math.max(d.col, 1),
      endLineNumber: line,
      endColumn: d.end_col ? Math.min(d.end_col, model.getLineMaxColumn(line)) : model.getLineMaxColumn(line),
      code: d.code,
      message: `${d.code}: ${d.message}`,
      severity: d.severity === 'error' ? monaco.MarkerSeverity.Error : monaco.MarkerSeverity.Warning,
    }
  }))
}

function sigText(kind: string, s: Signature) {
  const p = (xs: { name: string; type: string }[]) => xs.map(x => `${x.type} ${x.name}`).join(', ')
  return `${kind} ${s.name}(${p(s.inputs)})(${p(s.outputs)})`
}

function callSnippet(s: Signature) {
  return `${s.name}(${s.inputs.map((p, i) => `\${${i + 1}:${p.name}}`).join(', ')})`
}

export function registerCoco(monaco: typeof Monaco) {
  if (registered) return
  registered = true
  monaco.languages.register({ id: 'coco', extensions: ['.coco'], aliases: ['Coco'] })

  monaco.languages.setMonarchTokensProvider('coco', {
    defaultToken: '',
    keywords: [...COCO_KEYWORDS, ...PY_KEYWORDS],
    types: TYPES,
    builtins: BUILTINS,
    tokenizer: {
      root: [
        [/\b(step|flow)(\s+)([A-Za-z_]\w*)/, ['keyword', 'white', 'entity.name.function']],
        [/\b(class)(\s+)([A-Za-z_]\w*)/, ['keyword', 'white', 'type.identifier']],
        [/\b(package)(\s+)([A-Za-z_]\w*)/, ['keyword', 'white', 'namespace']],
        [/#.*$/, 'comment'],
        [/\b(True|False|None)\b/, 'constant'],
        [/\b(self|env)\b/, 'variable.predefined'],
        [/@[A-Za-z_]\w*/, 'annotation'],
        [/[A-Z]\w*/, 'type.identifier'],
        [/[a-z_]\w*(?=\s*\()/, { cases: { '@keywords': 'keyword', '@types': 'type', '@builtins': 'predefined', '@default': 'function' } }],
        [/[a-z_]\w*/, { cases: { '@keywords': 'keyword', '@types': 'type', '@builtins': 'predefined', '@default': 'identifier' } }],
        [/\[\]/, 'type'],
        [/[{}()[\]]/, '@brackets'],
        [/\d+\.\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?|0[xX][0-9a-fA-F]+|\d+/, 'number'],
        [/[fFrRbBuU]{0,2}"""/, 'string', '@tdq'],
        [/[fFrRbBuU]{0,2}'''/, 'string', '@tsq'],
        [/[fFrRbBuU]{0,2}"/, 'string', '@dq'],
        [/[fFrRbBuU]{0,2}'/, 'string', '@sq'],
        [/[=<>!+\-*/%&|^~]+/, 'operator'],
        [/[:.,;]/, 'delimiter'],
      ],
      dq: [[/[^\\"]+/, 'string'], [/\\./, 'string.escape'], [/"/, 'string', '@pop']],
      sq: [[/[^\\']+/, 'string'], [/\\./, 'string.escape'], [/'/, 'string', '@pop']],
      tdq: [[/[^\\"]+/, 'string'], [/\\./, 'string.escape'], [/"""/, 'string', '@pop'], [/"/, 'string']],
      tsq: [[/[^\\']+/, 'string'], [/\\./, 'string.escape'], [/'''/, 'string', '@pop'], [/'/, 'string']],
    },
  } as Monaco.languages.IMonarchLanguage)

  monaco.languages.setLanguageConfiguration('coco', {
    comments: { lineComment: '#' },
    brackets: [['{', '}'], ['[', ']'], ['(', ')']],
    autoClosingPairs: [
      { open: '{', close: '}' }, { open: '[', close: ']' }, { open: '(', close: ')' },
      { open: '"', close: '"', notIn: ['string'] }, { open: "'", close: "'", notIn: ['string', 'comment'] },
    ],
    surroundingPairs: [
      { open: '{', close: '}' }, { open: '[', close: ']' }, { open: '(', close: ')' },
      { open: '"', close: '"' }, { open: "'", close: "'" },
    ],
    onEnterRules: [
      { beforeText: /:\s*(#.*)?$/, action: { indentAction: monaco.languages.IndentAction.Indent } },
      { beforeText: /^\s*(return|pass|break|continue)\b.*$/, action: { indentAction: monaco.languages.IndentAction.Outdent } },
    ],
    folding: { offSide: true },
  })

  monaco.languages.registerCompletionItemProvider('coco', {
    triggerCharacters: ['.'],
    provideCompletionItems(model, position) {
      const K = monaco.languages.CompletionItemKind
      const Snippet = monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet
      const word = model.getWordUntilPosition(position)
      const range = {
        startLineNumber: position.lineNumber, endLineNumber: position.lineNumber,
        startColumn: word.startColumn, endColumn: word.endColumn,
      }
      const before = model.getLineContent(position.lineNumber).slice(0, word.startColumn - 1)
      const sym = symbolsByModel.get(model.uri.toString())

      if (/\bself\.$/.test(before)) {
        return {
          suggestions: SELF_MEMBERS.map(m => ({
            label: m.label, kind: m.insert.includes('(') ? K.Method : K.Property, insertText: m.insert,
            insertTextRules: Snippet, documentation: { value: m.doc }, range,
          })),
        }
      }
      const envPath = before.match(/(?:^|[^\w.])(?:self\.)?env((?:\.[A-Za-z_]\w*)*)\.$/)
      if (envPath) {
        let node: unknown = envData ?? {}
        for (const key of envPath[1].split('.').filter(Boolean)) {
          node = node && typeof node === 'object' && !Array.isArray(node) ? (node as Record<string, unknown>)[key] : undefined
        }
        const entries = node && typeof node === 'object' && !Array.isArray(node) ? Object.entries(node as Record<string, unknown>) : []
        const from = envName ? ` (${envName})` : ''
        return {
          suggestions: [
            ...entries.map(([k, v]) => ({
              label: k, kind: v !== null && typeof v === 'object' ? K.Module : K.Constant, insertText: k,
              detail: `env${envPath[1]}.${k} = ${preview(v)}${from}`, range,
            })),
            ...(envPath[1] ? [] : [{ label: 'get', kind: K.Method, insertText: 'get("${1:key}", ${2:default})', insertTextRules: Snippet, detail: 'env.get(key, default)', range }]),
          ],
        }
      }
      const alias = before.match(/\b([A-Za-z_]\w*)\.$/)
      if (alias) {
        // fields of a declared class are unknown here (no types per variable), so offer class fields by name
        const fields = new Map<string, string>()
        for (const c of sym?.classes ?? []) for (const f of c.fields) fields.set(f.name, `${c.name}.${f.name}: ${f.type}`)
        return {
          suggestions: [...fields].map(([name, detail]) => ({ label: name, kind: K.Field, insertText: name, detail, range })),
        }
      }

      const suggestions: Monaco.languages.CompletionItem[] = []
      const snippets: [string, string, string][] = [
        ['step', 'step ${1:name}(${2:string input})(${3:string output}):\n\t${4:return input}', 'New step'],
        ['flow', 'flow ${1:main}(${2:string input})(${3:string output}):\n\t${4:pass}', 'New flow'],
        ['class', 'class ${1:Name}:\n\t${2:string field}', 'New class'],
        ['if', 'if ${1:condition}:\n\t$0', 'if block'],
        ['for', 'for ${1:i} in range(${2:0}, ${3:n}, ${4:1}):\n\t$0', 'for over a range'],
        ['foreach', 'for ${1:item} in ${2:items}:\n\t$0', 'for over an array'],
      ]
      for (const [label, insertText, doc] of snippets) {
        suggestions.push({ label, kind: K.Snippet, insertText, insertTextRules: Snippet, documentation: doc, range })
      }
      for (const k of COCO_KEYWORDS) suggestions.push({ label: k, kind: K.Keyword, insertText: k, range })
      suggestions.push({ label: 'env', kind: K.Variable, insertText: 'env', range, detail: `run environment${envName ? ` (${envName})` : ''}`,
        documentation: 'Read-only run environment: `env.key`, `env["key"]`, `env.get("key", default)`.' })
      for (const t of TYPES) suggestions.push({ label: t, kind: K.TypeParameter, insertText: t, range, detail: 'type' })
      for (const b of BUILTINS) suggestions.push({ label: b, kind: K.Function, insertText: b, range, detail: 'builtin' })
      for (const c of sym?.classes ?? []) {
        suggestions.push({
          label: c.name, kind: K.Class, insertText: c.name, range,
          detail: `class ${c.name}`, documentation: c.fields.map(f => `${f.type} ${f.name}`).join('\n'),
        })
      }
      for (const s of sym?.steps ?? []) {
        suggestions.push({ label: s.name, kind: K.Function, insertText: callSnippet(s), insertTextRules: Snippet, detail: sigText('step', s), range })
      }
      for (const f of sym?.flows ?? []) {
        suggestions.push({ label: f.name, kind: K.Module, insertText: callSnippet(f), insertTextRules: Snippet, detail: sigText('flow', f), range })
      }
      return { suggestions }
    },
  })

  // quick fixes: `true` / `false` / `null` (JSON) -> `True` / `False` / `None` (Python), one or all
  const JSON_FIX: Record<string, string> = { true: 'True', false: 'False', null: 'None' }
  monaco.languages.registerCodeActionProvider('coco', {
    provideCodeActions(model, _range, context) {
      const fixable = (m: Monaco.editor.IMarkerData) => {
        const code = typeof m.code === 'string' ? m.code : m.code?.value
        const word = model.getValueInRange(m)
        return code === 'E235' && word in JSON_FIX ? word : null
      }
      const actions: Monaco.languages.CodeAction[] = []
      const here = context.markers.filter(m => fixable(m))
      for (const m of here) {
        const word = fixable(m)!
        actions.push({
          title: `Replace \`${word}\` with \`${JSON_FIX[word]}\``,
          kind: 'quickfix',
          diagnostics: [m],
          isPreferred: true,
          edit: { edits: [{ resource: model.uri, versionId: model.getVersionId(), textEdit: { range: m, text: JSON_FIX[word] } }] },
        })
      }
      if (here.length) {
        const all = monaco.editor.getModelMarkers({ resource: model.uri, owner: 'coco' }).filter(m => fixable(m))
        if (all.length > 1) {
          actions.push({
            title: `Replace all JSON literals in this file (${all.length}) with Python`,
            kind: 'quickfix',
            diagnostics: all,
            edit: {
              edits: all.map(m => ({
                resource: model.uri, versionId: model.getVersionId(),
                textEdit: { range: m, text: JSON_FIX[fixable(m)!] },
              })),
            },
          })
        }
      }
      return { actions, dispose() {} }
    },
  })

  monaco.languages.registerHoverProvider('coco', {
    provideHover(model, position) {
      const w = model.getWordAtPosition(position)
      const sym = symbolsByModel.get(model.uri.toString())
      if (!w || !sym) return null
      const step = sym.steps.find(s => s.name === w.word)
      const flow = sym.flows.find(f => f.name === w.word)
      const cls = sym.classes.find(c => c.name === w.word)
      let text: string | null = null
      if (step) text = sigText('step', step)
      else if (flow) text = sigText('flow', flow)
      else if (cls) text = `class ${cls.name}:\n${cls.fields.map(f => `    ${f.type} ${f.name}`).join('\n')}`
      else if (w.word === 'env') text = `env: run environment${envName ? ` (${envName})` : ''}\n${Object.keys(envData ?? {}).map(k => `env.${k}`).join('\n')}`
      else if (w.word === 'self') text = `self: StepContext\n${SELF_MEMBERS.map(m => `self.${m.label}`).join('\n')}`
      if (!text) return null
      return {
        range: new monaco.Range(position.lineNumber, w.startColumn, position.lineNumber, w.endColumn),
        contents: [{ value: '```coco\n' + text + '\n```' }],
      }
    },
  })

  const rules = (dark: boolean): Monaco.editor.ITokenThemeRule[] => [
    { token: 'keyword', foreground: dark ? 'C586C0' : 'AF00DB' },
    { token: 'type', foreground: dark ? '4EC9B0' : '267F99' },
    { token: 'type.identifier', foreground: dark ? '4EC9B0' : '267F99' },
    { token: 'entity.name.function', foreground: dark ? 'DCDCAA' : '795E26', fontStyle: 'bold' },
    { token: 'function', foreground: dark ? 'DCDCAA' : '795E26' },
    { token: 'predefined', foreground: dark ? '4FC1FF' : '0070C1' },
    { token: 'variable.predefined', foreground: dark ? '9CDCFE' : '001080', fontStyle: 'italic' },
    { token: 'namespace', foreground: dark ? '4EC9B0' : '267F99' },
    { token: 'constant', foreground: dark ? '569CD6' : '0000FF' },
    { token: 'annotation', foreground: dark ? 'DCDCAA' : '795E26' },
  ]
  monaco.editor.defineTheme('coco-light', { base: 'vs', inherit: true, rules: rules(false), colors: {} })
  monaco.editor.defineTheme('coco-dark', { base: 'vs-dark', inherit: true, rules: rules(true), colors: { 'editor.background': '#18181b' } })
}
