// 설치본 설정 (HYO-251) — `npm run app:pack`(scripts/pack.mjs)이 쓴다.
// 이 기기에서만 쓰는 개인 설치본: 설치 프로그램 없이 폴더(macOS .app · Windows win-unpacked)로 만들고, macOS 서명은 pack.mjs가 ad-hoc으로.
// 앱은 레포 밖에서 뜨지만 백엔드는 레포의 것을 띄운다 — 레포 위치 · 환경은 out/launch.json (pack.mjs가 쓴다, launch.ts가 읽는다)
const productName = 'Decision Radar'

module.exports = {
  appId: 'io.github.hyos0415.decision-radar',
  productName,
  directories: { output: 'dist', buildResources: 'resources' },
  // 화면 라이브러리는 out/renderer에 이미 묶였다 — 메인 프로세스가 실행 때 부르는 모듈만 넣는다 (out/main의 require)
  files: [
    'out/main/**',
    'out/preload/**',
    'out/renderer/**',
    '!node_modules/**',
    'node_modules/@electron-toolkit/utils/**'
  ],
  extraResources: [{ from: 'out/launch.json', to: 'launch.json' }],
  extraMetadata: { productName }, // 설치본의 app.name — 개발 모드는 그대로 package.json name
  npmRebuild: false,
  mac: {
    target: 'dir',
    icon: 'resources/icon.png',
    identity: null,
    category: 'public.app-category.developer-tools'
  },
  win: { target: 'dir', icon: 'resources/icon.png' }
}
