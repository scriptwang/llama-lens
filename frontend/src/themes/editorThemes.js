// Monaco 编辑器主题与 .service（systemd 单元文件）语言支持。
// 主题 id 与旧 CodeMirror 版本保持一致，localStorage 中已保存的选择无需迁移。

// systemd 单元文件 Monarch tokenizer：
//  [Section] -> section；Key= -> key；值里的 ${VAR}/$VAR -> variable；
//  引号串 -> string；行内 # / ; 注释 -> comment。
// 用 ^ 行首锚点匹配 section/key，不使用跨行持久状态：
// Monaco 逐行 tokenize 时行内不含 \n，若靠 \n 触发 @pop 会让状态泄漏到后续行。
// 值部分由 root 规则处理（${VAR}->variable、引号串->string、数字->number，其余默认色）。
const SYSTEMD_TOKENIZER = {
  tokenizer: {
    root: [
      [/^\[[A-Za-z0-9_-]+\]/, 'section'],
      [/^[A-Za-z_][A-Za-z0-9_]*\s*=/, 'key'],
      [/^[;#].*$/, 'comment'],
      [/\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*/, 'variable'],
      [/"([^"\\]|\\.)*"/, 'string'],
      [/'([^'\\]|\\.)*'/, 'string'],
      [/\d+/, 'number'],
      [/[ \t]+/, ''],
    ],
  },
}

const SYSTEMD_CONFIG = {
  comments: { lineComment: '#' },
  brackets: [
    ['[', ']'],
    ['(', ')'],
    ['{', '}'],
  ],
  autoClosingPairs: [
    { open: '[', close: ']' },
    { open: '(', close: ')' },
    { open: '{', close: '}' },
    { open: '"', close: '"' },
    { open: "'", close: "'" },
  ],
}

function toMonacoTheme(theme) {
  const { base, colors } = theme
  return {
    base: theme.dark ? 'vs-dark' : 'vs',
    inherit: true,
    rules: [
      { token: 'comment', foreground: colors.comment, fontStyle: 'italic' },
      { token: 'section', foreground: colors.heading, fontStyle: 'bold' },
      { token: 'key', foreground: colors.definition },
      { token: 'string', foreground: colors.string },
      { token: 'variable', foreground: colors.atom },
      { token: 'number', foreground: colors.number },
    ],
    colors: {
      'editor.background': base.bg,
      'editor.foreground': base.fg,
      'editorLineNumber.foreground': base.gutterFg,
      'editorLineNumber.activeForeground': base.fg,
      'editor.selectionBackground': base.selection,
      'editorCursor.foreground': base.cursor,
      'editor.lineHighlightBackground': base.activeLine,
      'editorGutter.background': base.gutterBg,
      'editorWidget.background': base.gutterBg,
      'editorWidget.border': base.border,
      'editorSuggestWidget.background': base.gutterBg,
      'editorSuggestWidget.border': base.border,
      'minimap.background': base.bg,
      'scrollbarSlider.background': theme.dark ? '#ffffff22' : '#00000022',
      'scrollbarSlider.hoverBackground': theme.dark ? '#ffffff38' : '#00000038',
    },
  }
}

export const EDITOR_THEMES = [
  {
    id: 'vscode-dark-plus',
    label: 'VSCode Dark+',
    dark: true,
    base: {
      bg: '#1e1e1e', fg: '#d4d4d4', gutterBg: '#1e1e1e', gutterFg: '#858585',
      selection: '#264f78', activeLine: '#2a2d2e66', cursor: '#aeafad', border: '#333333',
    },
    colors: {
      comment: '#6a9955', heading: '#569cd6', definition: '#9cdcfe', quote: '#ce9178',
      string: '#ce9178', number: '#b5cea8', atom: '#569cd6', keyword: '#569cd6',
      function: '#dcdcaa', typeName: '#4ec9b0', variable: '#9cdcfe', operator: '#d4d4d4',
      label: '#c586c0', error: '#f44747',
    },
  },
  {
    id: 'vscode-light-plus',
    label: 'VSCode Light+',
    dark: false,
    base: {
      bg: '#ffffff', fg: '#000000', gutterBg: '#f7f7f7', gutterFg: '#237893',
      selection: '#add6ff', activeLine: '#f0f0f088', cursor: '#000000', border: '#e5e5e5',
    },
    colors: {
      comment: '#008000', heading: '#0000ff', definition: '#001080', quote: '#a31515',
      string: '#a31515', number: '#098658', atom: '#098658', keyword: '#0000ff',
      function: '#795e26', typeName: '#267f99', variable: '#001080', operator: '#000000',
      label: '#af00db', error: '#cd3131',
    },
  },
  {
    id: 'one-dark-pro',
    label: 'One Dark Pro',
    dark: true,
    base: {
      bg: '#282c34', fg: '#abb2bf', gutterBg: '#282c34', gutterFg: '#5c6370',
      selection: '#3e4451', activeLine: '#2c313c99', cursor: '#528bff', border: '#181a1f',
    },
    colors: {
      comment: '#5c6370', heading: '#c678dd', definition: '#e06c75', quote: '#98c379',
      string: '#98c379', number: '#d19a66', atom: '#56b6c2', keyword: '#c678dd',
      function: '#61afef', typeName: '#e5c07b', variable: '#e5c07b', operator: '#56b6c2',
      label: '#d19a66', error: '#e06c75',
    },
  },
  {
    id: 'dracula',
    label: 'Dracula',
    dark: true,
    base: {
      bg: '#282a36', fg: '#f8f8f2', gutterBg: '#21222c', gutterFg: '#6272a4',
      selection: '#44475a', activeLine: '#44475a55', cursor: '#f8f8f0', border: '#191a21',
    },
    colors: {
      comment: '#6272a4', heading: '#ff79c6', definition: '#50fa7b', quote: '#f1fa8c',
      string: '#f1fa8c', number: '#bd93f9', atom: '#bd93f9', keyword: '#ff79c6',
      function: '#50fa7b', typeName: '#8be9fd', variable: '#f8f8f2', operator: '#ff79c6',
      label: '#bd93f9', error: '#ff5555',
    },
  },
  {
    id: 'monokai',
    label: 'Monokai',
    dark: true,
    base: {
      bg: '#272822', fg: '#f8f8f2', gutterBg: '#272822', gutterFg: '#90908a',
      selection: '#49483e', activeLine: '#3e3d3266', cursor: '#f8f8f0', border: '#1d1e19',
    },
    colors: {
      comment: '#75715e', heading: '#f92672', definition: '#66d9ef', quote: '#e6db74',
      string: '#e6db74', number: '#ae81ff', atom: '#ae81ff', keyword: '#f92672',
      function: '#a6e22e', typeName: '#66d9ef', variable: '#f8f8f2', operator: '#f92672',
      label: '#66d9ef', error: '#f92672',
    },
  },
  {
    id: 'solarized-light',
    label: 'Solarized Light',
    dark: false,
    base: {
      bg: '#fdf6e3', fg: '#586e75', gutterBg: '#eee8d5', gutterFg: '#93a1a1',
      selection: '#eee8d5', activeLine: '#e9e2c988', cursor: '#586e75', border: '#e0d9c0',
    },
    colors: {
      comment: '#93a1a1', heading: '#859900', definition: '#268bd2', quote: '#2aa198',
      string: '#2aa198', number: '#d33682', atom: '#d33682', keyword: '#859900',
      function: '#268bd2', typeName: '#b58900', variable: '#268bd2', operator: '#8f5536',
      label: '#b58900', error: '#dc322f',
    },
  },
  {
    id: 'solarized-night',
    label: 'Solarized Night',
    dark: true,
    base: {
      bg: '#002b36', fg: '#839496', gutterBg: '#002b36', gutterFg: '#586e75',
      selection: '#073642', activeLine: '#07364299', cursor: '#93a1a1', border: '#073642',
    },
    colors: {
      comment: '#586e75', heading: '#859900', definition: '#268bd2', quote: '#2aa198',
      string: '#2aa198', number: '#d33682', atom: '#d33682', keyword: '#859900',
      function: '#268bd2', typeName: '#b58900', variable: '#268bd2', operator: '#cb4b16',
      label: '#b58900', error: '#dc322f',
    },
  },
  {
    id: 'github-dark',
    label: 'GitHub Dark',
    dark: true,
    base: {
      bg: '#0d1117', fg: '#c9d1d9', gutterBg: '#0d1117', gutterFg: '#484f58',
      selection: '#1f6feb4d', activeLine: '#161b2299', cursor: '#c9d1d9', border: '#21262d',
    },
    colors: {
      comment: '#8b949e', heading: '#7ee787', definition: '#79c0ff', quote: '#a5d6ff',
      string: '#a5d6ff', number: '#79c0ff', atom: '#79c0ff', keyword: '#ff7b72',
      function: '#d2a8ff', typeName: '#7ee787', variable: '#c9d1d9', operator: '#c9d1d9',
      label: '#d2a8ff', error: '#f85149',
    },
  },
]

export function getEditorTheme(id) {
  return EDITOR_THEMES.find((t) => t.id === id) || EDITOR_THEMES[0]
}

// 注册 systemd 语言与全部主题。monaco 实例由调用方动态 import 后传入，
// 避免把 monaco 打进主包。可重复调用（HMR 场景）。
export function registerMonaco(monaco) {
  try {
    monaco.languages.register({ id: 'systemd' })
  } catch {
    /* 已注册，忽略 */
  }
  monaco.languages.setMonarchTokensProvider('systemd', SYSTEMD_TOKENIZER)
  monaco.languages.setLanguageConfiguration('systemd', SYSTEMD_CONFIG)
  for (const theme of EDITOR_THEMES) {
    monaco.editor.defineTheme(theme.id, toMonacoTheme(theme))
  }
}
