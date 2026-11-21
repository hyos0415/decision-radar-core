import type { CheckDetail, CheckRow } from '@renderer/lib/api'
import { cn } from '@renderer/lib/utils'
import { Button } from './ui/button'
import { Status } from './Status'
import { COPY } from '../../../copy'

const T = COPY.check

/** 답한 줄의 상태 — 초안 만듦(+ ADR 번호) · 기각함 */
function RowState({ row }: { row: CheckRow }): React.JSX.Element | null {
  if (row.state === 'drafted') {
    return (
      <Status
        status="done"
        label={row.adr ? `${T.state.drafted} · ${T.draftedAdr(row.adr)}` : T.state.drafted}
      />
    )
  }
  if (row.state === 'rejected') return <Status status="rejected" label={T.state.rejected} />
  return null
}

// 자리 질의 「확인 필요」 (HYO-245) — 자리마다 후보 목록. 줄마다 [초안 만들기] [기각], 답한 줄은 상태만.
// 요청이 도는 동안(busy) 모든 버튼을 막는다 — 초안 만들기는 실사용 모드에서 몇 분 걸린다.
// 링크는 새 창 요청으로 낸다 — main이 가로채 기본 브라우저로 연다 (setWindowOpenHandler)
export function CheckList({
  check,
  busy,
  working,
  onDraft,
  onReject
}: {
  check: CheckDetail
  busy: boolean
  working: string | null
  onDraft: (row: CheckRow) => void
  onReject: (row: CheckRow) => void
}): React.JSX.Element {
  return (
    <div className="check-list">
      <p className="text-xs text-muted-foreground">
        {T.intro} · {T.count(check.open, check.total)}
      </p>
      {check.groups.map((g) => (
        <section key={g.slot} className="mt-4">
          <header className="flex min-w-0 items-baseline gap-2 border-b pb-1 text-xs">
            <span className="font-medium text-foreground">{g.name ?? g.slot}</span>
            {g.repo && <span className="mono text-muted-foreground">{g.repo}</span>}
          </header>
          <ul className="m-0 list-none p-0">
            {g.rows.map((r) => (
              <li
                key={r.id}
                className={cn(
                  'flex items-start gap-3 border-b py-2.5',
                  r.state !== 'open' && 'opacity-70'
                )}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex min-w-0 items-baseline gap-2">
                    <span className="truncate text-[13px] font-medium">{r.model || r.title}</span>
                    <span className="tag mono">{r.source}</span>
                  </div>
                  {r.url ? (
                    <a
                      href={r.url}
                      target="_blank"
                      rel="noreferrer"
                      title={`${r.title} — ${T.open}`}
                      className="block truncate text-xs text-muted-foreground hover:underline"
                    >
                      {r.title}
                    </a>
                  ) : (
                    <span className="block truncate text-xs text-muted-foreground">{r.title}</span>
                  )}
                  {r.reason && <p className="mt-1 text-xs">{r.reason}</p>}
                  {r.switch && (
                    <p className="text-xs text-muted-foreground">{T.switch(r.switch)}</p>
                  )}
                  {r.other_slots.length > 0 && (
                    <p className="text-xs text-muted-foreground">
                      {T.otherSlots(r.other_slots.join(', '))}
                    </p>
                  )}
                  {r.state === 'open' && r.message && (
                    <p className="tone-warning mt-1 text-xs">{r.message}</p>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  {r.state === 'open' ? (
                    <>
                      <Button size="sm" disabled={busy} onClick={() => onDraft(r)}>
                        {working === r.id ? T.drafting : T.draft}
                      </Button>
                      <Button size="sm" variant="ghost" disabled={busy} onClick={() => onReject(r)}>
                        {T.reject}
                      </Button>
                    </>
                  ) : (
                    <RowState row={r} />
                  )}
                </div>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}
