// 화면 문구 (HYO-117) — 사용자에게 보이는 말은 모두 여기서 고친다. 렌더러와 main(트레이 · 알림 · capture 창)이 함께 쓴다.
// 여기 없는 것: 서버가 보내는 message · errors(받은 그대로 보여 준다) · 행동 로그 이벤트 이름 · 콘솔 로그.
// 값이 들어가는 문구는 함수다. 묶음은 화면 단위, 여러 화면에 나오는 말은 term에 한 번만 둔다.
// 말투: 해요체, 한 문장이면 마침표 없이. 사이드바(sidebar)는 영어. 토스 UX 라이팅 원칙을 따른다
// (https://toss.tech/article/8-writing-principles-of-toss).

const term = {
  inbox: 'Inbox',
  decisions: 'Decisions',
  runs: 'log',
  capture: 'capture',
  topics: '화제',
  weekList: '이번 주',
  demo: '데모',
  scheduled: '정기',
  loading: '불러오는 중…',
  cancel: '취소',
  close: '닫기',
  approve: '승인',
  edit: '고쳐서 승인',
  reject: '거부',
  ack: '확인함'
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
    undetermined: '판단 못 함'
  } as Record<string, string>,
  revisitUnchecked: '확인 전',
  revisitMetCount: (n: number) => `조건 충족 ${n}`,
  inboxState: { stale: '바뀜', blocked: '막힘', review: '검토', check: '확인 필요' },

  // 날짜 · 시각
  date: {
    weekdays: ['일', '월', '화', '수', '목', '금', '토'],
    today: '오늘',
    yesterday: '어제',
    monthDay: (m: number, d: number, weekday: string) => `${m}월 ${d}일 (${weekday})`
  },

  app: {
    backendFailed: '앱을 시작하지 못했어요',
    backendStarting: '앱을 준비하고 있어요…',
    jobFailed: (kind: string) => `${kind} 실행에 실패했어요`,
    requestFailed: (status: number) => `요청에 실패했어요 (${status})`
  },

  inbox: {
    empty: '확인할 항목이 없어요',
    listLabel: 'Inbox 항목',
    detailLabel: 'Inbox 항목 상세',
    waitToday: '오늘 도착',
    waitDays: (days: number) => `${days}일째 대기`
  },

  // Inbox 상세 패널 (components/ItemDetail.tsx)
  item: {
    prev: '이전 항목',
    prevHint: '이전 항목 (k)',
    next: '다음 항목',
    nextHint: '다음 항목 (j)',
    close: '상세 닫기',
    numberNote: '번호는 저장할 때 정해져요',
    changes: '기다리는 동안 바뀐 점',
    revisitMet: '재검토 조건을 충족했어요',
    openAdr: 'ADR 열기',
    done: '처리했어요',
    blocked: '지금은 처리할 수 없어요',
    error: '처리하지 못했어요',
    notSaved: '저장하지 않았어요. 아래 내용을 확인해 주세요',
    answerFailed: 'ADR repo에 반영하지 못했어요',
    answerFailedHint: '같은 답을 다시 하면 이어서 반영해요. 같은 ADR이 두 번 만들어지지 않아요',
    stale: {
      prompt: '기다리는 동안 기록이 바뀌었어요. 어떻게 할까요?',
      continue: '그대로 저장',
      rejudge: '다시 판정',
      discard: '초안 버리기'
    },
    rejectConfirm: {
      title: '이 항목을 거부할까요?',
      body: '거부하면 목록에서 사라져요'
    }
  },

  // 자리 질의 「확인 필요」 (components/CheckList.tsx, HYO-245) — 주당 한 항목, 후보마다 초안 만들기 · 기각
  check: {
    // 서버 제목(service._check_item_row)과 같은 모양 — 답할 때마다 남은 건수로 다시 그린다
    title: (week: string, open: number) => `자리 질의 확인 필요 — ${week} · ${open}건`,
    intro: '자리 질의가 적합도 1로 본 후보예요. 초안으로 만들 것만 고르고 나머지는 기각해요',
    draft: '초안 만들기',
    drafting: '초안을 만들고 있어요…',
    reject: '기각',
    rejectRest: '나머지 전부 기각',
    model: '모델',
    switch: (s: string) => `바꾸려면: ${s}`,
    otherSlots: (names: string) => `다른 자리: ${names}`,
    open: '새 창에서 열기',
    state: { drafted: '초안 만듦', rejected: '기각함' },
    draftedAdr: (adr: string) => `ADR ${adr}`,
    count: (open: number, total: number) => `남은 후보 ${open} / ${total}`,
    draftNote: '초안 만들기는 모델을 불러요 — 실사용 모드는 몇 분 걸려요',
    rejectRestConfirm: {
      title: '남은 후보를 전부 기각할까요?',
      body: (n: number) => `${n}건을 기각해요. 기각한 소식은 다음 주에 다시 와도 올라오지 않아요`
    }
  },

  // 고쳐서 승인 창 (components/EditDialog.tsx)
  edit: {
    note: '상태와 번호는 저장할 때 정해져요',
    title: '제목',
    technology: '기술 (고칠 수 없음)',
    use: '쓰는 곳 (use)',
    context: '배경 (context)',
    decision: '결정 (decision)',
    researchNote: '조사 메모 (한 줄에 하나씩)',
    lists: '목록 칸 (JSON: affected · evidence · related · revisit_when)',
    badJson: (msg: string) => `JSON 형식이 맞지 않아요: ${msg}`,
    checkFailed: '검사를 통과하지 못했어요',
    notSaved: '저장하지 않았어요. 아래 내용을 고친 뒤 다시 저장해 주세요',
    saving: '저장 중…'
  },

  decisions: {
    filters: { all: '전체', proposed: '제안', adopted: '채택', hold: '보류', rejected: '기각' },
    search: '번호, 제목, 기술로 검색',
    columns: {
      id: '번호',
      status: '상태',
      title: '제목',
      technology: '기술',
      revisit: '재검토',
      lastChecked: '최근 확인'
    },
    empty: '조건에 맞는 결정이 없어요',
    detailLabel: '결정 상세',
    revisitConditions: '재검토 조건',
    noConditions: '등록된 조건이 없어요',
    statusNote: '상태는 ADR 파일에서 직접 바꿔요',
    // 실험해 보기 (HYO-116) — 승인한 결정을 격리된 사본에서 레시피로. 결정은 사람이다
    experiment: {
      title: '실험',
      run: '실험해 보기',
      running: '실험 중…',
      runningHint: '격리된 사본에서 설치하고 검사해요 — 몇 분 걸려요',
      exportLabel: '통과하면 draft PR 열기',
      exportOff: '내보내기는 실사용 모드에서만 돼요',
      none: '이 결정에 맞는 실험이 아직 없어요',
      past: '지난 실험',
      stub: '스텁',
      pr: 'PR 열기',
      note: '결과는 근거예요 — 채택 여부와 상태는 사람이 정해요',
      failed: (msg: string) => `실험이 끝나지 못했어요 — ${msg}`
    }
  },

  runs: {
    upcoming: '예정',
    running: '실행 중',
    empty: '아직 실행 기록이 없어요',
    failed: '실패',
    drafts: '초안',
    alerts: '재검토 알림'
  },

  // 화제 탭 (components/TopicsView.tsx, HYO-133·152)
  topics: {
    week: '주차',
    tabs: { week: '이번 주', saved: '저장한 화제' },
    sections: { multi: '여러 출처에 나온 화제', single: '한 출처 화제' },
    // 더 보기 (HYO-154) — 상위 k 아래는 파일 순위 그대로
    moreSection: (from: number) => `더 보기 — ${from}위부터`,
    more: (shown: number, max: number) => `더 보기 (${shown} / ${max})`,
    empty: '아직 화제 파일이 없어요. 주간 수집이 끝나면 여기에 나와요',
    emptyWeek: '이 주차에 보여 줄 화제가 없어요',
    savedEmpty: '저장한 화제가 없어요',
    method: (m: string, k: number) => `${m} · 상위 ${k}`,
    sources: (n: number) => `출처 ${n}곳`,
    posts: (n: number) => `글 ${n}개`,
    showPosts: '묶인 글 보기',
    hidePosts: '묶인 글 접기',
    save: '저장',
    unsave: '저장 취소',
    saved: '저장함',
    capture: 'capture',
    captureTitle: '이 화제로 capture 창 열기',
    open: '새 창에서 열기',
    savedAt: (d: string) => `${d} 저장`,
    parent: {
      badge: (title: string, rank: number) => `${title}(${rank}위)의 하위로 보임`,
      rule: { 'P1-prefix': '이름 앞부분', 'P1-shared': '앞 2토막 공유', P2: '활용 언급' },
      accept: '맞음',
      reject: '아님',
      answered: { accept: '맞다고 답했어요', reject: '아니라고 답했어요' },
      changeHint: '답을 바꿀 수 있어요',
      manualLabel: (title: string) => `직접 묶음: ${title}의 하위`,
      release: '풀기',
      attach: '직접 묶기',
      attachPrompt: '이 화제는',
      attachSuffix: '의 하위',
      attachPlaceholder: '부모 화제 제목 검색',
      searching: '찾는 중…',
      noCandidates: '맞는 화제가 없어요',
      cancel: '취소',
      rank: (n: number) => `${n}위`
    }
  },

  // 이번 주 목록 (components/WeekListView.tsx, HYO-168) — 훑어보기 화면, 버튼 없음
  weekList: {
    week: '주차',
    sections: {
      always: '항상 — 보안 · EOL · 모델 은퇴',
      alarm: '경보 — 관문이 모르는 기술의 사고',
      tech: '기술 질의',
      slots: '자리 질의'
    },
    toDetect: (n: number) => `초안 후보 ${n}건`,
    empty: '아직 목록이 없어요. 주간 수집이 끝나면 여기에 나와요',
    emptyWeek: '이 주차에 보여 줄 항목이 없어요',
    badge: { toDetect: '초안 후보', alarm: '경보', security: '보안' },
    // detect가 이 줄을 어떻게 했나 (HYO-171) — 「초안 후보」 자리에 대신 보인다. 이유는 마우스를 올리면
    verdict: {
      draft: (adr: string | null) => (adr ? `초안 ${adr}` : '초안'),
      existing: (adr: string | null) => (adr ? `기존 결정 ${adr}` : '기존 결정'),
      excluded: '버림',
      applied: '이미 반영됨',
      dropped: '대상 밖',
      failed: '초안 실패',
      unjudged: '판정 없음'
    },
    verdictTitle: {
      existing: '이미 있는 결정과 겹쳐 새 초안을 쓰지 않았어요',
      unjudged: 'detect가 돌았지만 이 줄을 언급하지 않았어요',
      old: '판정을 줄마다 남기기 전 실행이라 버린 소식만 알 수 있어요'
    },
    // 출처 건강 검사 (HYO-172) — 이상이 있을 때만 머리말 아래 한 줄. 자세한 건 마우스를 올리면
    healthTitle: (
      ps: { name: string; url?: string | null; reasons: { detail: string }[]; streak: number }[]
    ) =>
      ps
        .map(
          (p) =>
            `${p.name}${p.url ? ` · ${p.url}` : ''} — ${p.reasons.map((r) => r.detail).join(' · ')} (${p.streak}주째)`
        )
        .join('\n'),
    detectRun: (time: string) => `detect ${time}`,
    detectNotYet: 'detect 아직',
    folded: (n: number) => `외 ${n}건`,
    foldedHide: '접기',
    alarmTitle: (names: string[]) => `걸린 이름: ${names.join(', ')}`,
    current: (models: string[]) => `지금: ${models.length > 0 ? models.join(', ') : '없음'}`,
    fit: (n: number) => `적합도 ${n}/3`,
    none: '없음',
    notOursToggle: (n: number) => `이번 주 큰 사고 중 우리 스택 아님 ${n}건`,
    points: (n: number) => `${n}점`,
    open: '새 창에서 열기'
  },

  // 지난 알림 패널 (components/NotificationsPanel.tsx)
  notifications: {
    title: '지난 알림',
    empty: '지난 알림이 없어요',
    gone: '이 항목은 더 이상 열 수 없어요',
    fallbackTitle: '알림',
    // 예전 기록(title 없음)의 kind → 제목 (HYO-141)
    kindTitle: {
      capture: '승인할 초안이 있어요',
      detect: '검토할 detect 초안이 있어요',
      pending: 'capture를 접수했어요',
      revisit: '재검토 조건을 충족했어요',
      check: '자리 질의에서 확인할 후보가 있어요',
      mixed: 'Inbox에 새 항목이 있어요',
      inbox_new: 'Inbox에 새 항목이 있어요',
      run_drafts: '새 초안이 나왔어요',
      run_alerts: '재검토 알림이 나왔어요'
    } as Record<string, string>
  },

  // 사이드바 — 영어로 쓴다
  sidebar: {
    flowHint: { revisit: 'Check revisit conditions', detect: 'Find new releases' },
    running: 'running',
    failed: 'failed',
    next: (time: string) => `next ${time}`,
    unavailable: 'Not available in this mode',
    stubNote: 'Stub model · no charges',
    run: 'run',
    theme: 'Theme',
    themes: { system: 'System', light: 'Light', dark: 'Dark' },
    shortcutFailed: 'Shortcut unavailable',
    stub: 'stub',
    stubProfile: (profile: string) => `stub · ${profile}`,
    noModel: 'No real model',
    notifications: 'Notifications'
  },

  // 빠른 capture 창 (CaptureApp.tsx)
  capture: {
    heading: 'capture로 ADR 초안 만들기',
    placeholder: '새 기술 소식이나 업데이트 내용을 붙여 넣어 주세요',
    working: '초안을 만들고 있어요…',
    added: 'Inbox에 추가했어요',
    viewInInbox: 'Inbox에서 보기',
    linked: (ids: string) => `이미 있는 결정과 같아요: ${ids}`,
    viewInDecisions: 'Decisions에서 보기',
    noDraft: '초안이 나오지 않았어요',
    hint: (mod: string) => `${mod}+Enter 보내기 · Esc 닫기`,
    submit: '초안 만들기'
  },

  // main 프로세스 — 트레이 메뉴 (main/tray.ts)
  tray: {
    tooltip: (pending: number) =>
      pending > 0 ? `Decision Radar — 대기 ${pending}건` : 'Decision Radar',
    open: '열기',
    captureFailed: `${term.capture} (단축키 없음)`,
    run: (flow: string) => flow,
    quit: '종료'
  },

  // main 프로세스 — 네이티브 알림 (main/poller.ts). 제목은 Inbox 항목 종류별
  notify: {
    title: {
      capture: '승인할 초안이 있어요',
      detect: '검토할 detect 초안이 있어요',
      pending: 'capture를 접수했어요',
      revisit: '재검토 조건을 충족했어요',
      check: '자리 질의에서 확인할 후보가 있어요'
    },
    more: (title: string, rest: number) => `${title} 외 ${rest}건`,
    batch: (n: number) => `Inbox에 새 항목이 ${n}건 있어요`
  },

  // main 프로세스 — 백엔드 기동 실패 (main/backend.ts). 화면의 app.backendFailed 아래에 나온다
  // 사용자에게 「백엔드」라고 하지 않는다. badDevData · noPython은 개발 환경에서만 나오므로 개발자용으로 둔다
  backend: {
    badDevData: (name: string) => `RADAR_DEVDATA 값이 올바르지 않아요: ${name}`,
    noPython: (path: string) =>
      `Python을 찾지 못했어요. RADAR_PYTHON을 지정하거나 ${path}를 만들어 주세요 (메인 worktree도 확인했어요)`,
    exitedEarly: '앱 실행 중 문제가 생겼어요. 다시 실행해 주세요',
    timeout: '앱을 준비하는 데 시간이 너무 오래 걸려요. 다시 실행해 주세요',
    exited: '앱 연결이 끊겼어요. 다시 실행해 주세요'
  }
}
