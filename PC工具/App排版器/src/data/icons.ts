/**
 * IconTable 的名字是契约（App 端按名字查表，改名就是空白）。
 * 预览用一个近似字形代替矢量图，能看出"这里有个图标"就够了。
 */
export const ICON_NAMES = [
  'back', 'down', 'next', 'close', 'settings', 'list', 'globe', 'map',
  'star', 'starOff', 'plus', 'minus', 'play', 'stop', 'save', 'share',
  'delete', 'edit', 'search', 'filter', 'refresh', 'info', 'check', 'mic',
  'usb', 'wifi', 'sstv', 'upload', 'download', 'volume', 'location',
] as const

export const ICON_GLYPH: Record<string, string> = {
  back: '‹',
  down: '⌄',
  next: '›',
  close: '✕',
  settings: '⚙',
  list: '☰',
  globe: '◎',
  map: '⌖',
  star: '★',
  starOff: '☆',
  plus: '＋',
  minus: '－',
  play: '▶',
  stop: '■',
  save: '⤓',
  share: '↗',
  delete: '⌫',
  edit: '✎',
  search: '⌕',
  filter: '⛃',
  refresh: '↻',
  info: 'ⓘ',
  check: '✓',
  mic: '♩',
  usb: '⚿',
  wifi: '≈',
  sstv: '▤',
  upload: '⤒',
  download: '⤓',
  volume: '◁))',
  location: '⌾',
}

export function glyphOf(name: string): string {
  return ICON_GLYPH[name] ?? '▢'
}
