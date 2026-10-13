// Whether an Inbox single-key shortcut (j/k · a/e/r · 1/2/3) may fire; returns the key ('j', '1', …) or null.
// Off with a modifier held (OS shortcuts), with focus in a text field, or while a dialog is open.
// Reads e.code, not e.key, so the same physical key works with the Korean IME on.
export function shortcutKey(e: KeyboardEvent): string | null {
  if (e.defaultPrevented || e.repeat || e.isComposing) return null
  if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return null
  const t = e.target
  if (t instanceof HTMLElement && (t.isContentEditable || t.closest('input, textarea, select'))) {
    return null
  }
  if (document.querySelector('[role="dialog"], [role="alertdialog"]')) return null
  const m = /^(?:Key([A-Z])|Digit([0-9])|Numpad([0-9]))$/.exec(e.code)
  return m ? (m[1] ?? m[2] ?? m[3]).toLowerCase() : null
}
