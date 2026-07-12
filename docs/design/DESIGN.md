# Filing Digest — 디자인 방향 (Redesign Direction)

2026-07-12 리디자인 세션 산출물. 목업 원본은 [mockups/](mockups/)에 있고,
Stitch 프로젝트 "Filing Digest Redesign"에서 재편집할 수 있다.

## 현재 상태 진단 (왜 리디자인하나)

- 커스텀 색/폰트/아이콘 0개, `Assets.xcassets` 자체가 없음 → 앱 아이콘 없음,
  액센트는 시스템 블루. 전형적인 "기본 SwiftUI" 룩.
- 화면에 raw 데이터 노출: `2023-annual`(보고서 코드), `generated_at:` 타임스탬프,
  `KRW_PER_SHARE` 등. (단위 표기는 `FigureDisplay.swift`로 일부 개선됨)
- 카드가 전부 `secondarySystemBackground` 회색 필 + 12pt 라운드 — 위계도
  브랜드도 없음.
- 로직/데이터 레이어는 견고 (3-state answer UX, citation chip, Decimal 처리).
  **프레젠테이션 레이어만 갈아끼우면 된다.**

## 채택 방향: **A. "Ledger" — 에디토리얼 라이트** (권장)

프리미엄 애뉴얼 리포트 / 리서치 저널의 지면 느낌. 공시(문서)를 읽는 앱이라는
정체성과 정확히 일치하고, 기본 SwiftUI 룩과 가장 확실하게 차별화된다.

| 토큰 | 값 | iOS 매핑 |
|---|---|---|
| 배경 `paper` | `#F7F4EE` (웜 아이보리) | `Color("Paper")` asset, 다크 변형 `#14130F` |
| 텍스트 `ink` | `#1A1917` | `Color("Ink")`, 다크 변형 `#ECE9E3` |
| 보조 텍스트 | `#6B6965` | `Color("InkMuted")` |
| 액센트 `ledgerGreen` | `#1D5C45` | `AccentColor` asset, 다크 변형 `#3E8E6E` |
| 헤어라인 | ink @ 20% | 1px 보더 — 회색 필 카드 대체 |

**타이포그래피** (서드파티 0 유지 — 전부 시스템 폰트):

- 디스플레이(회사명·섹션 제목): **New York** (`.fontDesign(.serif)`) semibold
- 본문: SF Pro (`.body`), 한국어 본문은 line-spacing +10%
- 숫자(모든 수치): SF Mono 또는 `.monospacedDigit()` — tabular 정렬 필수

**컴포넌트 규칙**:

- 카드: 회색 필 금지 → 1px 헤어라인 보더 + 0~2pt 라운드 (각진 지면 느낌)
- 인용 마커: 초록 사각 박스 `1` (현재 파란 캡슐 `[1]` 대체)
- DART/SEC 배지: 사각 헤어라인 박스, DART=green / SEC=ink
- 질문 표시: 2px ink 왼쪽 룰의 인용구(pull-quote) 블록
- `확정 수치`: 초록 1px 보더 콜아웃 박스 + 헤어라인 행 테이블
- 섹션 헤더: letter-spaced small-caps + 얇은 룰
- 그림자·그라디언트·필 캡슐 전부 금지

**목업**: [ledger_search.png](mockups/ledger_search.png) ·
[ledger_digest.png](mockups/ledger_digest.png) ·
[ledger_answer.png](mockups/ledger_answer.png)

## 대안 방향: B. "Terminal" — 딥 다크

차콜 `#0E0F11` + 웜 오프화이트 `#ECE9E3` + 앰버 액센트 `#D9A441`, 카드 없이
얇은 디바이더와 우측 정렬 모노 숫자로만 구조를 만드는 시네마틱 데이터 터미널.
Fey/Bloomberg 계열 무드. A를 채택하되, A의 다크 모드를 설계할 때 이 목업의
디바이더-리스트 레이아웃(메트릭 그리드 대신 풀폭 행)을 참고할 것.

**목업**: [terminal_digest.png](mockups/terminal_digest.png) ·
[terminal_answer.png](mockups/terminal_answer.png)

## 벤치마크 (실제 서비스)

- **Quartr** — 공시·어닝콜 리서치 앱의 대표작. IR 자료를 "팟캐스트처럼" 소비하게
  만든 흐름, 필링 첨부 UX. <https://quartr.com/products/mobile-app>,
  모션 레퍼런스 <https://60fps.design/apps/quartr>
- **Fey** — 다크 하이컨트라스트 + 모노 숫자 + 절제된 액센트의 프리미엄 금융 데이터
  UI. <https://nicelydone.club/apps/fey>
- **토스/토스증권 (TDS)** — 미니멀리즘, 절제된 색, '해요체' UX 라이팅, 금융을
  쉽게 만드는 위계. <https://toss.tech/design>,
  <https://developers-apps-in-toss.toss.im/design/components.html>

## 브랜드 시스템 (2026-07-13 확정)

브랜드 약속: **"모든 문장에 영수증이 있다"** — 마크는 인용 그 자체다.

| 역할 | 마크 | 쓰임 |
|---|---|---|
| 프라이머리 심볼 | **[F]** — 그린 브래킷 + 잉크 Didot F ([10_bracket_f.png](logos/10_bracket_f.png)) | 앱 아이콘, GitHub 아바타 |
| 브랜드 원자 | **[■]** — 괄호 안의 그린 사각형 ([9_bracket_fact.png](logos/9_bracket_fact.png)) | 파비콘·로딩 인디케이터·장식. "괄호 안 = 검증된 사실" |
| 락업 | **[F] Filing Digest** ([11_lockup.png](logos/11_lockup.png)) | README 헤더, 소셜 프리뷰, 발표 자료 |

영문 태그라인: *Every claim carries a citation.* / 한국어: **"공시를 읽다"**.
이니셜 모노그램(FD 계열)은 탐색 후 기각 — 모노그램은 기성 브랜드의
문법이라 무명 제품에선 가치를 전달하지 못한다. 기각 시안 포함 전체 탐색은
[logos/](logos/) 참조.

## 구현 시 반드시 지울 것 (안티패턴 목록)

1. `2023-annual` 제목 → "사업보고서 2023" 등 사람 언어로
2. `generated_at: 2026-07-06T01:32:02...` 화면 노출 → 제거 (필요하면 "7월 6일 기준")
3. `258,935,494,000,000 K...` 잘리는 원시 숫자 → 조/억 단위 축약 ("258.9조 원")
4. `YoY —` 플레이스홀더 → 데이터 없으면 행 자체를 숨김 (멀티이어 적재 후 표시)
5. 시스템 블루 액센트, 회색 필 카드, 파란 캡슐 인용 칩

구현 단계 계획은 루트 [ROADMAP.md](../../ROADMAP.md) Phase B 참조.
