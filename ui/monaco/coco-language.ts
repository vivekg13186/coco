import type * as Monaco from 'monaco-editor'
import type { Diagnostic, Signature, Symbols } from '~/types/api'
import { signature } from '~/utils/format'

// The `coco` language for Monaco: a workflow file is plain Python, so this is Python highlighting plus
// completion and hover from the engine's /validate symbols (steps, flows, classes, coco globals) and markers.

const KEYWORDS = [
  'and', 'as', 'assert', 'async', 'await', 'break', 'class', 'continue', 'def', 'del', 'elif', 'else', 'except',
  'finally', 'for', 'from', 'global', 'if', 'import', 'in', 'is', 'lambda', 'nonlocal', 'not', 'or', 'pass',
  'raise', 'return', 'try', 'while', 'with', 'yield', 'match', 'case',
]
const TYPES = ['str', 'int', 'float', 'bool', 'dict', 'list', 'tuple', 'set', 'Any', 'Optional', 'Model']
const BUILTINS = [
  'len', 'min', 'max', 'abs', 'round', 'sum', 'sorted', 'range', 'print', 'enumerate', 'zip', 'isinstance', 'map',
  'filter', 'any', 'all', 'open', 'repr', 'type', 'reversed', 'getattr', 'hasattr', 'super',
]
// what `from coco import ...` gives a workflow; also available without importing
const GLOBALS: { label: string; insert: string; doc: string }[] = [
  { label: 'log', insert: 'log(${1:values})', doc: 'Log any number of values, like console.log: `log("total", n, data, True)`. Objects show as expandable trees. Works in steps and flows.' },
  { label: 'info', insert: 'info(${1:values})', doc: 'Log at info level (same as `log`).' },
  { label: 'warn', insert: 'warn(${1:values})', doc: 'Log a warning, highlighted in the run viewer.' },
  { label: 'error', insert: 'error(${1:values})', doc: 'Log an error, highlighted in the run viewer. Does not stop the step: raise an exception for that.' },
  { label: 'debug', insert: 'debug(${1:values})', doc: 'Log at debug level (dimmed; can be filtered out).' },
  { label: 'log_table', insert: 'log_table(${1:rows})', doc: 'Log rows as a table, like console.table: a list of objects, lists or values, or a dict of rows. Optional `columns=["a", "b"]`. The viewer can sort and filter it.' },
  { label: 'env', insert: 'env', doc: 'The run environment (read-only): `env.api_url`, `env["api_url"]`, `env.get("k", default)`.' },
  { label: 'context', insert: 'context', doc: 'Where the code runs: `context.run_id`, `context.flow`, `context.step`, `context.path`.' },
]
const CONTEXT_MEMBERS: { label: string; doc: string }[] = [
  { label: 'run_id', doc: 'Id of the current run.' },
  { label: 'flow', doc: 'Name of the top-level flow.' },
  { label: 'step', doc: 'Name of the running step (None in a flow).' },
  { label: 'path', doc: 'Node path of this call, e.g. onboard/for#0[2]/enrich.' },
]
const GLOBAL_NAMES = GLOBALS.map(g => g.label)

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
  return `@${kind}\ndef ${s.name}${signature(s)}`
}

function callSnippet(s: Signature) {
  const required = s.inputs.filter(p => !p.optional)
  return `${s.name}(${required.map((p, i) => `\${${i + 1}:${p.name}}`).join(', ')})`
}

export function registerCoco(monaco: typeof Monaco) {
  if (registered) return
  registered = true
  monaco.languages.register({ id: 'coco', extensions: ['.coco'], aliases: ['Coco', 'Coco (Python)'] })

  monaco.languages.setMonarchTokensProvider('coco', {
    defaultToken: '',
    keywords: KEYWORDS,
    types: TYPES,
    builtins: BUILTINS,
    globals: GLOBAL_NAMES,
    tokenizer: {
      root: [
        [/\b(def)(\s+)([A-Za-z_]\w*)/, ['keyword', 'white', 'entity.name.function']],
        [/\b(class)(\s+)([A-Za-z_]\w*)/, ['keyword', 'white', 'type.identifier']],
        [/^(\s*)(@)((?:coco\.)?(?:step|flow))\b/, ['white', 'annotation.coco', 'annotation.coco']],
        [/^(\s*)(@[A-Za-z_][\w.]*)/, ['white', 'annotation']],
        [/#.*$/, 'comment'],
        [/\b(True|False|None)\b/, 'constant'],
        [/\b(self|cls)\b/, 'variable.predefined'],
        [/[A-Z]\w*/, { cases: { '@types': 'type', '@default': 'type.identifier' } }],
        [/[a-z_]\w*(?=\s*\()/, { cases: { '@keywords': 'keyword', '@globals': 'predefined.coco', '@types': 'type', '@builtins': 'predefined', '@default': 'function' } }],
        [/[a-z_]\w*/, { cases: { '@keywords': 'keyword', '@globals': 'predefined.coco', '@types': 'type', '@builtins': 'predefined', '@default': 'identifier' } }],
        [/->/, 'operator'],
        [/[{}()[\]]/, '@brackets'],
        [/\d[\d_]*\.[\d_]*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?|0[xX][0-9a-fA-F_]+|0[bB][01_]+|\d[\d_]*j?/, 'number'],
        [/[fFrRbBuU]{0,2}"""/, 'string', '@tdq'],
        [/[fFrRbBuU]{0,2}\'\'\'/, 'string', '@tsq'],
        [/[fFrRbBuU]{0,2}"/, 'string', '@dq'],
        [/[fFrRbBuU]{0,2}'/, 'string', '@sq'],
        [/[=<>!+\-*/%&|^~@]+/, 'operator'],
        [/[:.,;]/, 'delimiter'],
      ],
      dq: [[/[^\\"]+/, 'string'], [/\\./, 'string.escape'], [/"/, 'string', '@pop']],
      sq: [[/[^\\']+/, 'string'], [/\\./, 'string.escape'], [/'/, 'string', '@pop']],
      tdq: [[/[^\\"]+/, 'string'], [/\\./, 'string.escape'], [/"""/, 'string', '@pop'], [/"/, 'string']],
      tsq: [[/[^\\']+/, 'string'], [/\\./, 'string.escape'], [/\'\'\'/, 'string', '@pop'], [/'/, 'string']],
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

      if (/(?:^|[^\w.])context\.$/.test(before)) {
        return {
          suggestions: CONTEXT_MEMBERS.map(m => ({
            label: m.label, kind: K.Property, insertText: m.label, documentation: { value: m.doc }, range,
          })),
        }
      }
      const coco = before.match(/(?:^|[^\w.])coco\.$/)
      if (coco) {
        return {
          suggestions: [
            ...['step', 'flow', 'Model'].map(l => ({ label: l, kind: K.Function, insertText: l, range })),
            ...GLOBALS.map(g => ({ label: g.label, kind: K.Function, insertText: g.insert, insertTextRules: Snippet, documentation: { value: g.doc }, range })),
          ],
        }
      }
      const envPath = before.match(/(?:^|[^\w.])env((?:\.[A-Za-z_]\w*)*)\.$/)
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
      const other = alias ? sym?.imports.find(i => i.kind === 'coco' && (i.alias ?? i.module) === alias[1]) : undefined
      if (other) return { suggestions: [] }  // members of another workflow: not known here
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
        ['@step', '@step\ndef ${1:name}(${2:value}: ${3:str}) -> ${4:str}:\n\t${5:return value}', 'New step: plain Python; type hints describe inputs, `->` the output'],
        ['@flow', '@flow\ndef ${1:main}(${2:name}: ${3:str}) -> ${4:str}:\n\t${5:pass}', 'New flow: calls steps and flows; if/for/return only'],
        ['stepn', '@step\ndef ${1:name}(${2:value}: ${3:str}) -> {"${4:a}": ${5:int}, "${6:b}": ${7:str}}:\n\treturn {"$4": ${8:0}, "$6": ${9:""}}', 'Step with named outputs'],
        ['class', 'class ${1:Name}:\n\t${2:field}: ${3:str}', 'New data class (validated, like a pydantic model)'],
        ['imp', 'from coco import step, flow, log, env', 'Import the coco API'],
        ['if', 'if ${1:condition}:\n\t$0', 'if block'],
        ['for', 'for ${1:i} in range(${2:n}):\n\t$0', 'for over a range'],
        ['foreach', 'for ${1:item} in ${2:items}:\n\t$0', 'for over a list'],
      ]
      for (const [label, insertText, doc] of snippets) {
        suggestions.push({ label, kind: K.Snippet, insertText, insertTextRules: Snippet, documentation: doc, range })
      }
      for (const k of KEYWORDS) suggestions.push({ label: k, kind: K.Keyword, insertText: k, range })
      for (const g of GLOBALS) {
        suggestions.push({ label: g.label, kind: g.insert.includes('(') ? K.Function : K.Variable, insertText: g.insert,
          insertTextRules: Snippet, documentation: { value: g.doc }, range,
          detail: g.label === 'env' ? `run environment${envName ? ` (${envName})` : ''}` : 'coco' })
      }
      for (const t of TYPES) suggestions.push({ label: t, kind: K.TypeParameter, insertText: t, range, detail: 'type' })
      for (const b of BUILTINS) suggestions.push({ label: b, kind: K.Function, insertText: b, range, detail: 'builtin' })
      for (const c of sym?.classes ?? []) {
        suggestions.push({
          label: c.name, kind: K.Class, insertText: c.name, range,
          detail: `class ${c.name}`, documentation: c.fields.map(f => `${f.name}: ${f.type}`).join('\n'),
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
      else if (cls) text = `class ${cls.name}:\n${cls.fields.map(f => `    ${f.name}: ${f.type}`).join('\n')}`
      else if (w.word === 'env') text = `env: run environment${envName ? ` (${envName})` : ''}\n${Object.keys(envData ?? {}).map(k => `env.${k}`).join('\n')}`
      else if (w.word === 'context') text = `context\n${CONTEXT_MEMBERS.map(m => `context.${m.label}`).join('\n')}`
      if (!text) {
        const g = GLOBALS.find(x => x.label === w.word && x.insert.includes('('))
        if (!g) return null
        return {
          range: new monaco.Range(position.lineNumber, w.startColumn, position.lineNumber, w.endColumn),
          contents: [{ value: '```python\n' + g.label + '(...)\n```' }, { value: g.doc }],
        }
      }
      return {
        range: new monaco.Range(position.lineNumber, w.startColumn, position.lineNumber, w.endColumn),
        contents: [{ value: '```python\n' + text + '\n```' }],
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
    { token: 'annotation.coco', foreground: dark ? 'F5A97F' : 'C2410C', fontStyle: 'bold' },
    { token: 'predefined.coco', foreground: dark ? 'F5A97F' : 'C2410C' },
  ]
  monaco.editor.defineTheme('coco-light', { base: 'vs', inherit: true, rules: rules(false), colors: {} })
  monaco.editor.defineTheme('coco-dark', { base: 'vs-dark', inherit: true, rules: rules(true), colors: { 'editor.background': '#18181b' } })
}
