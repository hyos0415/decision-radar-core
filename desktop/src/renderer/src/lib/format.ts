import type { RevisitSummary } from './api'

export const STATUS_LABEL: Record<string, string> = {
  proposed: '제안',
  accepted: '채택',
  rejected: '기각',
  hold: '보류',
  superseded: '대체됨'
}

export function statusLabel(s: string): string {
  return STATUS_LABEL[s] ?? s
}

export const RESULT_LABEL: Record<string, string> = {
  met: '충족',
  not_met: '미충족',
  undetermined: '판단불가'
}

export function resultLabel(r: string | null | undefined): string {
  if (!r) return '미확인'
  return RESULT_LABEL[r] ?? r
}

// "조건 충족 N" is the only emphasized part (met > 0).
export function revisitParts(s: RevisitSummary): { text: string; strong: boolean }[] {
  const rest: string[] = []
  if (s.not_met > 0) rest.push(`미충족 ${s.not_met}`)
  if (s.undetermined > 0) rest.push(`판단불가 ${s.undetermined}`)
  if (s.unchecked > 0) rest.push(`미확인 ${s.unchecked}`)
  const out: { text: string; strong: boolean }[] = []
  if (s.met > 0) out.push({ text: `조건 충족 ${s.met}`, strong: true })
  if (rest.length > 0) out.push({ text: rest.join(' · '), strong: false })
  if (out.length === 0) out.push({ text: '—', strong: false })
  return out
}

export function dateOnly(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 10) : '—'
}

export function dateTime(iso: string): string {
  return iso.slice(0, 16).replace('T', ' ')
}

export function isMac(): boolean {
  return navigator.platform.toLowerCase().includes('mac')
}

// CommandOrControl+Shift+Space -> "Ctrl+Shift+Space" / "⌘⇧Space"
export function shortcutText(acc: string): string {
  const mac = isMac()
  return acc
    .split('+')
    .map((p) => {
      const k = p.toLowerCase()
      if (k === 'commandorcontrol' || k === 'cmdorctrl') return mac ? '⌘' : 'Ctrl'
      if (k === 'command' || k === 'cmd' || k === 'super') return '⌘'
      if (k === 'control' || k === 'ctrl') return mac ? '⌃' : 'Ctrl'
      if (k === 'shift') return mac ? '⇧' : 'Shift'
      if (k === 'alt' || k === 'option') return mac ? '⌥' : 'Alt'
      return p
    })
    .join(mac ? '' : '+')
}
