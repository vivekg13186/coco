# coco-ui

The Coco WorkPanel (milestone 3): Nuxt 3 single-page app with PrimeVue 4 (Aura theme) and Monaco.

## The built UI lives in git

You do not need Node.js to use Coco: the built UI is committed at `server/coco_server/ui/` and ships inside
the `coco-server` package, so `pip install` plus `coco-server` is enough.

When you change anything in `ui/`, rebuild and commit the result together with your source change:

```bash
cd ui
npm install            # once
npm run build          # nuxt generate, then copies .output/public to server/coco_server/ui/
cd .. && git add ui server/coco_server/ui
```

`.gitattributes` marks `server/coco_server/ui/` as generated, so its minified files stay out of `git diff`
and GitHub's language stats. `COCO_UI_DIR` points the engine at another build if you need to.

## Develop it

```bash
coco-server            # engine on :8765
cd ui && npm run dev   # UI on http://localhost:3000 with hot reload; /api is proxied to :8765
npm test               # vitest: event formatting, steps tree, durations
npx vue-tsc --noEmit   # type check
```

## What is where

| Path | Job |
| --- | --- |
| `pages/index.vue` | The WorkPanel: five fixed tabs (Workflows, Run History, Schedulers, Environments, Notes), the **Open items** dropdown (open editors and run viewers, each closable), and the top-bar environment picker |
| `components/NotesTab.vue` | Plain-text notes: list with search, autosave, download as .txt, delete |
| `components/EnvironmentsTab.vue`, `composables/useEnvs.ts` | Environments: list, JSON editor, default flag; the environment selected for new runs |
| `composables/useTabs.ts` | Open-items store: open or focus by key (`wf:`, `run:`, `sch:`), dirty flags, close guard, close all, restore from `localStorage`, `?tab=` in the URL |
| `components/WorkflowsTab.vue`, `RunHistoryTab.vue`, `SchedulersTab.vue` | Overview tables |
| `components/WorkflowEditor.vue` | Monaco editor, live `/validate`, Flows / Problems / Versions panel, Save, Run |
| `components/RunDialog.vue`, `FlowInputForm.vue` | Flow input as a generated form or JSON |
| `components/RunViewer.vue` | Events in order with a Log or JSON view, Console (every log, filter by level and text), Steps tree, Input / Output |
| `components/LogEntry.vue`, `LogArgs.vue`, `ValueTree.vue`, `LogTable.vue` | Console rendering: values like console.log, objects as expandable trees, `log_table` with text filter and sortable columns |
| `composables/useRunStream.ts` | Loads stored events, then follows the SSE stream with `after_seq` |
| `monaco/coco-language.ts` | The `coco` language: highlighting, indentation rules, completion, hover, markers |
| `utils/` | Event categories and one-line details, steps tree builder, formatting |

## Keys

- Ctrl/Cmd+S saves the workflow in the active editor tab.
- Alt+W closes the active open item (Ctrl/Cmd+W belongs to the browser). In the Open items list, × or a middle-click closes an item.
- Ctrl/Cmd+S in a note saves it at once (notes also save on their own 0.8 s after you stop typing).
- In the event list: arrow keys, Home and End move the selection; Enter (or a double-click) shows that call in the Steps tree.

## Differences from the design

- Workflow files are plain Python, so the `coco` language in Monaco is a Python tokenizer that also marks
  `@step` / `@flow` and the coco globals (`log`, `env`, `context`, ...); completion and hover come from the
  engine's symbols.
- State lives in small reactive stores (`useTabs`, `useSignals`) instead of Pinia, and API types are written
  by hand in `types/api.ts`: the API returns plain JSON objects, so a generated OpenAPI client would have no
  response types.
- Ctrl+W is replaced by Alt+W, because browsers do not let a page take Ctrl/Cmd+W.
- The Schedulers tab is a placeholder until the scheduler API exists (milestone 4).
