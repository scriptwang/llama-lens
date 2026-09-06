// Custom Monaco entry for LlamaCtl-Web.
//
// = `editor.api` (core editor) + the editing contributions, but WITHOUT
// the 81 built-in languages and their language-service workers.
//
// Why not `import "monaco-editor"` (full):
//   * registers 81 unused built-in languages + 4 language-service workers
//     (json/html/css/ts, ~9MB) that bloat the bundle.
//
// Why not `monaco-editor/editor/editor.api` (minimal) alone:
//   * it registers NO editor contributions -> 0 actions, so Ctrl+F / the find
//     widget / most keybindings are all missing.
//
// This entry restores the VSCode editing contributions (find, fontZoom, context
// menu, multi-cursor, ...) on top of the minimal core.
//
// Worker: the generic `editor.worker.js` is bundled via Vite's `?worker` suffix
// and provided through MonacoEnvironment.getWorker. It is REQUIRED for the diff
// editor: WorkerBasedDocumentDiffProvider computes diffs inside the worker, and
// a missing/no-op worker makes the $ping() handshake hang forever (no diff, no
// error, no fallback). Tokenization is NOT delegated to this worker: the custom
// `systemd` Monarch tokenizer runs on the main thread, so highlighting is
// unaffected.

import * as monaco from 'monaco-editor/editor/editor.api';
import 'monaco-editor/editor/browser/coreCommands';
import 'monaco-editor/editor/common/standaloneStrings';

// Monaco's EditorWorkerService is an Eager singleton: every editor creation
// resolves it, and the diff editor needs it to compute diffs. Bundle the real
// editor worker (Vite emits it as a separate worker chunk) and hand it to
// Monaco. Note the subpath: monaco's package `exports` maps
// `monaco-editor/editor/*` -> `esm/vs/editor/*` (a bare `esm/vs/...` import
// would double-prefix and fail to resolve).
import EditorWorker from 'monaco-editor/editor/editor.worker.js?worker'

self.MonacoEnvironment = {
  ...(self.MonacoEnvironment || {}),
  getWorker: () => new EditorWorker(),
};

// Editing contributions (side-effect imports register actions + keybindings)
import 'monaco-editor/editor/contrib/anchorSelect/browser/anchorSelect';
import 'monaco-editor/editor/contrib/bracketMatching/browser/bracketMatching';
import 'monaco-editor/editor/contrib/caretOperations/browser/transpose';
import 'monaco-editor/editor/contrib/clipboard/browser/clipboard';
import 'monaco-editor/editor/contrib/codeAction/browser/codeActionContributions';
import 'monaco-editor/editor/browser/widget/codeEditor/codeEditorWidget';
import 'monaco-editor/editor/contrib/codelens/browser/codelensController';
import 'monaco-editor/editor/contrib/colorPicker/browser/colorPickerContribution';
import 'monaco-editor/editor/contrib/comment/browser/comment';
import 'monaco-editor/editor/contrib/contextmenu/browser/contextmenu';
import 'monaco-editor/editor/contrib/cursorUndo/browser/cursorUndo';
import 'monaco-editor/editor/browser/widget/diffEditor/diffEditor.contribution';
import 'monaco-editor/editor/contrib/diffEditorBreadcrumbs/browser/contribution';
import 'monaco-editor/editor/contrib/dnd/browser/dnd';
import 'monaco-editor/editor/contrib/documentSymbols/browser/documentSymbols';
import 'monaco-editor/editor/contrib/dropOrPasteInto/browser/dropIntoEditorContribution';
import 'monaco-editor/editor/contrib/floatingMenu/browser/floatingMenu.contribution';
import 'monaco-editor/editor/contrib/folding/browser/folding';
import 'monaco-editor/editor/contrib/fontZoom/browser/fontZoom';
import 'monaco-editor/editor/contrib/format/browser/formatActions';
import 'monaco-editor/editor/contrib/gotoError/browser/gotoError';
import 'monaco-editor/editor/standalone/browser/quickAccess/standaloneGotoLineQuickAccess';
import 'monaco-editor/editor/contrib/gotoSymbol/browser/link/goToDefinitionAtPosition';
import 'monaco-editor/editor/contrib/gpu/browser/gpuActions';
import 'monaco-editor/editor/contrib/hover/browser/hoverContribution';
import 'monaco-editor/editor/contrib/indentation/browser/indentation';
import 'monaco-editor/editor/contrib/inlayHints/browser/inlayHintsContribution';
import 'monaco-editor/editor/contrib/inlineCompletions/browser/inlineCompletions.contribution';
import 'monaco-editor/editor/contrib/inlineProgress/browser/inlineProgress';
import 'monaco-editor/editor/contrib/inPlaceReplace/browser/inPlaceReplace';
import 'monaco-editor/editor/contrib/insertFinalNewLine/browser/insertFinalNewLine';
import 'monaco-editor/editor/standalone/browser/inspectTokens/inspectTokens';
import 'monaco-editor/editor/standalone/browser/iPadShowKeyboard/iPadShowKeyboard';
import 'monaco-editor/editor/contrib/lineSelection/browser/lineSelection';
import 'monaco-editor/editor/contrib/linesOperations/browser/linesOperations';
import 'monaco-editor/editor/contrib/linkedEditing/browser/linkedEditing';
import 'monaco-editor/editor/contrib/links/browser/links';
import 'monaco-editor/editor/contrib/longLinesHelper/browser/longLinesHelper';
import 'monaco-editor/editor/contrib/middleScroll/browser/middleScroll.contribution';
import 'monaco-editor/editor/contrib/multicursor/browser/multicursor';
import 'monaco-editor/editor/contrib/parameterHints/browser/parameterHints';
import 'monaco-editor/editor/contrib/placeholderText/browser/placeholderText.contribution';
import 'monaco-editor/editor/standalone/browser/quickAccess/standaloneCommandsQuickAccess';
import 'monaco-editor/editor/standalone/browser/quickAccess/standaloneHelpQuickAccess';
import 'monaco-editor/editor/standalone/browser/quickAccess/standaloneGotoSymbolQuickAccess';
import 'monaco-editor/editor/contrib/readOnlyMessage/browser/contribution';
import 'monaco-editor/editor/standalone/browser/referenceSearch/standaloneReferenceSearch';
import 'monaco-editor/editor/contrib/rename/browser/rename';
import 'monaco-editor/editor/contrib/sectionHeaders/browser/sectionHeaders';
import 'monaco-editor/editor/contrib/semanticTokens/browser/viewportSemanticTokens';
import 'monaco-editor/editor/contrib/smartSelect/browser/smartSelect';
import 'monaco-editor/editor/contrib/snippet/browser/snippetController2';
import 'monaco-editor/editor/contrib/stickyScroll/browser/stickyScrollContribution';
import 'monaco-editor/editor/contrib/suggest/browser/suggestInlineCompletions';
import 'monaco-editor/editor/standalone/browser/toggleHighContrast/toggleHighContrast';
import 'monaco-editor/editor/contrib/toggleTabFocusMode/browser/toggleTabFocusMode';
import 'monaco-editor/editor/contrib/tokenization/browser/tokenization';
import 'monaco-editor/editor/contrib/unicodeHighlighter/browser/unicodeHighlighter';
import 'monaco-editor/editor/contrib/unusualLineTerminators/browser/unusualLineTerminators';
import 'monaco-editor/editor/contrib/wordHighlighter/browser/wordHighlighter';
import 'monaco-editor/editor/contrib/wordOperations/browser/wordOperations';
import 'monaco-editor/editor/contrib/wordPartOperations/browser/wordPartOperations';
import 'monaco-editor/editor/contrib/caretOperations/browser/caretOperations';
import 'monaco-editor/editor/contrib/dropOrPasteInto/browser/copyPasteContribution';
import 'monaco-editor/editor/contrib/find/browser/findController';
import 'monaco-editor/editor/contrib/gotoSymbol/browser/goToCommands';
import 'monaco-editor/editor/contrib/gotoError/browser/markerSelectionStatus';
import 'monaco-editor/editor/contrib/semanticTokens/browser/documentSemanticTokens';
import 'monaco-editor/editor/contrib/suggest/browser/suggestController';

export default monaco;
export const { editor, languages } = monaco;
