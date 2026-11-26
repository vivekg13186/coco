// Loaded on demand (dynamic import) so Monaco never blocks the first paint.
import * as monaco from 'monaco-editor'
import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'
import JsonWorker from 'monaco-editor/esm/vs/language/json/json.worker?worker'
import { registerCoco } from './coco-language'

;(self as any).MonacoEnvironment = {
  getWorker(_id: string, label: string) {
    return label === 'json' ? new JsonWorker() : new EditorWorker()
  },
}

registerCoco(monaco)

export function themeName(dark: boolean) {
  return dark ? 'coco-dark' : 'coco-light'
}

export { monaco }
