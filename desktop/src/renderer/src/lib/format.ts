import type { RevisitSummary } from './api'
import { COPY } from '../../../copy'

export function statusLabel(s: string): string {
  return COPY.adrStatus[s] ?? s
}

export function resultLabel(r: string | null | undefined): string {
  if (!r) return COPY.revisitUnchecked
  return COPY.revisitResult[r] ?? r
}

// "조건 충족 N" is the only emphasized part (met > 0).
export function revisitParts(s: RevisitSummary): { text: string; strong: boolean }[] {
  const L = COPY.revisitResult
  const rest: string[] = []
  if (s.not_met > 0) rest.push(`${L.not_met} ${s.not_met}`)
  if (s.undetermined > 0) rest.push(`${L.undetermined} ${s.undetermined}`)
  if (s.unchecked > 0) rest.push(`${COPY.revisitUnchecked} ${s.unchecked}`)
  const out: { text: string; strong: boolean }[] = []
  if (s.met > 0) out.push({ text: COPY.revisitMetCount(s.met), strong: true })
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

const WEEKDAY = COPY.date.weekdays

function localDay(d: Date): string {
  return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`
}

// 실행 기록 묶음 머리: 오늘 · 어제 · 10월 6일 (화)
export function dayLabel(iso: string, now: Date = new Date()): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10)
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  if (localDay(d) === localDay(now)) return COPY.date.today
  if (localDay(d) === localDay(yesterday)) return COPY.date.yesterday
  return COPY.date.monthDay(d.getMonth() + 1, d.getDate(), WEEKDAY[d.getDay()])
}

// 14:05 · 정기 실행 칸은 "월 09:00"
export function timeOnly(iso: string, withWeekday = false): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso.slice(11, 16)
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  return withWeekday ? `${WEEKDAY[d.getDay()]} ${hm}` : hm
}

export function isMac(): boolean {
  return navigator.platform.toLowerCase().includes('mac')
}

// CommandOrControl+Shift+Space -> macOS "⌘+⇧+space" / Windows "Ctrl+Shift+Space".
// compact=true (사이드바 — 폭이 좁다): Windows도 Shift만 기호로 "Ctrl+⇧+space"
export function shortcutText(acc: string, compact = false): string {
  const mac = isMac()
  const short = mac || compact
  return acc
    .split('+')
    .map((p) => {
      const k = p.toLowerCase()
      if (k === 'commandorcontrol' || k === 'cmdorctrl') return mac ? '⌘' : 'Ctrl'
      if (k === 'command' || k === 'cmd' || k === 'super') return '⌘'
      if (k === 'control' || k === 'ctrl') return mac ? '⌃' : 'Ctrl'
      if (k === 'shift') return short ? '⇧' : 'Shift'
      if (k === 'alt' || k === 'option') return mac ? '⌥' : 'Alt'
      return short ? k : p
    })
    .join('+')
}
