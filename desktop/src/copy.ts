// 화면 문구 (HYO-117) — 사용자에게 보이는 말은 모두 여기서 고친다. 렌더러와 main(트레이 · 알림 · capture 창)이 함께 쓴다.
// 여기 없는 것: 서버가 보내는 message · errors(받은 그대로 보여 준다) · 행동 로그 이벤트 이름 · 콘솔 로그.
// 값이 들어가는 문구는 함수다. 묶음은 화면 단위, 여러 화면에 나오는 말은 term에 한 번만 둔다.

const term = {
  inbox: 'Inbox',
  decisions: 'Decisions',
  runs: 'log',
  capture: 'capture',
  stub: '스텁',
  demo: '데모',
  scheduled: '정기',
  loading: '불러오는 중…',
  cancel: '취소',
  close: '닫기',
  approve: '승인',
  edit: '고쳐서 승인',
  reject: '거부',
  failed: '실패',
  shortcutFailed: '단축키 등록 실패'
}

export const COPY = {
  term,

  // 라벨 — 키는 API 값이다. 쓰는 쪽(lib/format.ts · lib/inbox.ts)이 키 목록을 타입으로 검사한다
  adrStatus: {
    proposed: '제안',
    adopted: '채택', // 스킬 schema.md의 값
    accepted: '채택',
    revisit: '재검토',
    rejected: '기각',
    hold: '보류',
    superseded: '대체됨'
  } as Record<string, string>,
  revisitResult: {
    met: '충족',
    not_met: '미충족',
    undetermined: '판단불가'
  } as Record<string, string>,
  revisitUnchecked: '미확인',
  revisitMetCount: (n: number) => `조건 충족 ${n}`,
  inboxState: { stale: '바뀜', blocked: '막힘', review: '검토' },

  // 날짜 · 시각
  date: {
    weekdays: ['일', '월', '화', '수', '목', '금', '토'],
    today: '오늘',
    yesterday: '어제',
    monthDay: (m: number, d: number, weekday: string) => `${m}월 ${d}일 (${weekday})`
  },

  app: {
    backendFailed: '백엔드를 시작하지 못했다.',
    backendStarting: '백엔드 기동 중…',
    jobFailed: (kind: string) => `${kind} 실행이 실패했다.`,
    requestFailed: (status: number) => `요청 실패 (${status})`
  },

  inbox: {
    empty: '대기 중인 항목이 없다',
    listLabel: 'Inbox 항목',
    detailLabel: 'Inbox 항목 상세',
    waitToday: '오늘 도착',
    waitDays: (days: number) => `${days}일 대기`
  },

  // Inbox 상세 패널 (components/ItemDetail.tsx)
  item: {
    prev: '이전 항목',
    prevHint: '이전 항목 (k)',
    next: '다음 항목',
    nextHint: '다음 항목 (j)',
    close: '상세 닫기',
    numberNote: '번호는 저장할 때 정해짐',
    changes: '대기 중 바뀐 점',
    done: '처리했다.',
    blocked: '막혔다.',
    error: '오류가 났다.',
    notSaved: '저장하지 않았다.',
    stale: {
      prompt: '대기하는 동안 기록이 바뀌었다. 어떻게 할까?',
      continue: '계속',
      rejudge: '다시 판정',
      discard: '버림'
    },
    rejectConfirm: {
      title: '이 항목을 거부할까?',
      body: '거부하면 목록에서 빠진다.'
    }
  },

  // 고쳐서 승인 창 (components/EditDialog.tsx)
  edit: {
    note: '상태와 번호는 고칠 수 없다. 저장할 때 정해진다.',
    title: '제목',
    technology: '기술 (읽기 전용)',
    use: '쓰는 곳 (use)',
    context: '배경 (context)',
    decision: '결정 (decision)',
    researchNote: '조사 메모 (한 줄에 하나)',
    lists: '목록 칸 (JSON: affected · evidence · related · revisit_when)',
    badJson: (msg: string) => `JSON 형식이 맞지 않다: ${msg}`,
    checkFailed: '검사를 통과하지 못했다.',
    notSaved: '저장하지 않았다. 아래를 고쳐 다시 시도한다.',
    saving: '저장 중…'
  },

  decisions: {
    filters: { all: '전체', proposed: '제안', adopted: '채택', hold: '보류', rejected: '기각' },
    search: '번호·제목·기술 검색',
    columns: {
      id: '번호',
      status: '상태',
      title: '제목',
      technology: '기술',
      revisit: '재검토',
      lastChecked: '최근 확인'
    },
    empty: '맞는 결정이 없다',
    detailLabel: '결정 상세',
    revisitConditions: '재검토 조건',
    noConditions: '조건이 없다',
    statusNote: '상태는 ADR 파일에서 사람이 바꾼다'
  },

  runs: {
    upcoming: '예정',
    running: '실행 중',
    empty: '아직 실행한 적이 없다',
    drafts: '초안',
    alerts: '재검토 알림'
  },

  sidebar: {
    flowHint: { revisit: '재검토 조건 확인', detect: '새 릴리스 찾기' },
    running: '실행 중',
    next: (time: string) => `다음 ${time}`,
    unavailable: '이 모드에서는 쓸 수 없다',
    stubNote: '스텁 모델 — 과금 없음',
    run: 'run',
    theme: '테마',
    themes: { system: '시스템', light: '라이트', dark: '다크' },
    stubProfile: (profile: string) => `스텁 · ${profile}`,
    noModel: '실제 모델 없음'
  },

  // 빠른 capture 창 (CaptureApp.tsx)
  capture: {
    heading: '대화 붙여넣기 → 결정 기록 초안',
    placeholder: '조사 대화를 붙여 넣는다…',
    working: '초안 만드는 중…',
    added: 'Inbox에 추가됨',
    viewInInbox: 'Inbox에서 보기',
    linked: (ids: string) => `이미 있는 결정과 같다: ${ids}`,
    viewInDecisions: 'Decisions에서 보기',
    noDraft: '초안이 나오지 않았다',
    hint: (mod: string) => `${mod}+Enter 보내기 · Esc 닫기`,
    submit: '초안 만들기'
  },

  // main 프로세스 — 트레이 메뉴 (main/tray.ts)
  tray: {
    tooltip: (pending: number) =>
      pending > 0 ? `Decision Radar — 대기 ${pending}` : 'Decision Radar',
    open: '열기',
    captureFailed: `${term.capture} (${term.shortcutFailed})`,
    run: (flow: string) => `${flow}`,
    quit: '종료'
  },

  // main 프로세스 — 네이티브 알림 (main/poller.ts). 제목은 Inbox 항목 종류별
  notify: {
    title: {
      capture: '승인 대기 초안',
      detect: '검토 대기 detect 초안',
      pending: '접수한 capture',
      revisit: '재검토 조건 충족'
    },
    more: (title: string, rest: number) => `${title} 외 ${rest}건`,
    batch: (n: number) => `새 Inbox 항목 ${n}건`
  },

  // main 프로세스 — 백엔드 기동 실패 (main/backend.ts). 화면의 app.backendFailed 아래에 나온다
  backend: {
    badDevData: (name: string) => `RADAR_DEVDATA가 올바르지 않다: ${name}`,
    noPython: (path: string) =>
      `Python을 찾지 못했다. RADAR_PYTHON을 지정하거나 ${path} 를 만든다 (메인 worktree도 확인함).`,
    exitedEarly: '백엔드가 먼저 종료됐다.',
    timeout: '30초 안에 백엔드가 응답하지 않았다.',
    exited: '백엔드가 종료됐다.'
  }
}
