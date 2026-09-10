import {setup,pair} from './preferences.js';
const d={
  hero:'모든 설명에\n공시 근거를',
  intro:'한국 DART와 미국 SEC 공시를 한국어와 영어로 읽습니다. 재무 수치는 공시 데이터에서 가져오고, 생성된 설명에는 확인할 수 있는 원문 근거를 연결합니다.',
  walk:'회사 선택부터\n원문 확인까지',
  evidence:'수치와 설명,\n각자의 근거로',
  closing:'구현 과정도\n확인할 수 있습니다'
};
const copy={
'.skip-link':'제품 화면으로 건너뛰기',
'.site-header nav a:nth-child(1)':'사용 흐름',
'.site-header nav a:nth-child(2)':'근거 처리',
'.site-header nav a:nth-child(3)':'Filing Agent',
'.site-header nav a:nth-child(4)':'소스 코드',
'.hero .eyebrow':'실제 앱을 녹화한 포트폴리오 데모',
'.hero .lede':d.intro,
'.hero-actions a:nth-child(1)':'사용 흐름 보기',
'.hero-actions a:nth-child(2)':'저장소 살펴보기',
'.demo-disclosure':'실제 앱 사용 장면을 녹화한 읽기 전용 데모입니다. 실시간 API 호출 없이 동작하며, 인증 정보·분석 도구·사용자 데이터를 사용하지 않습니다.',
'.capture-scope':'2026년 8월, 초기 8개 회사 기준 화면입니다. 로컬에서 검증한 데이터는 2026년 9월 9일 18개 회사로 늘어났으며, 이 녹화는 당시 공개 버전을 그대로 보여줍니다.',
'.hero-device figcaption':'구현된 SwiftUI 앱에서 촬영했습니다',
'.facts div:nth-child(1) span':'한국과 미국의 공시',
'.facts div:nth-child(2) span':'한국어·영어 요약',
'.facts div:nth-child(3) strong':'설명별 인용',
'.facts div:nth-child(3) span':'범위가 명확한 발췌문',
'.facts div:nth-child(4) strong':'공시 원문 수치',
'.facts div:nth-child(4) span':'구조화된 데이터에서 제공',
'#walkthrough .eyebrow':'01 / 사용 흐름',
'#walkthrough .section-heading p:last-child':'수집된 회사 목록을 살펴보고, 재무 수치를 읽고, 질문을 던집니다. 답변에 붙은 인용을 따라가면 근거가 된 공시 발췌문과 원문을 확인할 수 있습니다.',
'.demo-card:nth-child(1) figcaption':'<span>기본 사용 흐름</span>회사 선택 → 요약 → 질문 → 확인',
'.demo-card:nth-child(2) figcaption':'<span>근거가 부족할 때</span>인용 답변 → 설명 차단 → 검색 결과 없음',
'.flow-notes li:nth-child(1) strong':'수집된 회사 살펴보기',
'.flow-notes li:nth-child(1) p':'질문하기 전에 어떤 회사의 공시가 있는지 확인합니다.',
'.flow-notes li:nth-child(2) strong':'요약과 수치 읽기',
'.flow-notes li:nth-child(2) p':'각 재무 수치에서 해당 수치가 실린 공시를 열 수 있습니다.',
'.flow-notes li:nth-child(3) strong':'언어를 넘어서 질문하기',
'.flow-notes li:nth-child(3) p':'한국어 질문으로 미국 공시의 근거를 찾을 수 있습니다.',
'.flow-notes li:nth-child(4) strong':'인용된 근거 확인하기',
'.flow-notes li:nth-child(4) p':'인용 번호로 발췌문을 확인하고 해당 공시 원문을 엽니다.',
'.screens .eyebrow':'실제 구현 화면',
'#screens-title':'앱에서 직접 확인한 화면입니다',
'#evidence .eyebrow':'02 / 근거 처리',
'#evidence .section-heading p:last-child':'재무 수치와 생성된 설명은 별도로 처리합니다. 정해진 검사를 거친 뒤 앱에서 함께 보여줍니다.',
'.track-grid article:nth-child(1) .track-label':'재무 수치',
'.track-grid article:nth-child(1) h3':'공시 데이터 그대로',
'.track-grid article:nth-child(1) > p:not(.track-label)':'DART·SEC 데이터의 정확한 값과 출처를 유지합니다. 재무 수치는 언어 모델을 거치지 않습니다.',
'.track-grid article:nth-child(1) li:nth-child(1)':'정확한 십진수 저장',
'.track-grid article:nth-child(1) li:nth-child(2)':'회계기간별 구분',
'.track-grid article:nth-child(1) li:nth-child(3)':'해당 공시 원문 연결',
'.track-grid article:nth-child(2) .track-label':'생성된 설명',
'.track-grid article:nth-child(2) h3':'인용으로 이어지는 문장',
'.track-grid article:nth-child(2) > p:not(.track-label)':'검색된 공시 발췌문을 바탕으로 설명을 생성합니다. 근거 누락이나 잘못된 인용이 감지되면 설명을 표시하지 않습니다.',
'.track-grid article:nth-child(2) li:nth-child(1)':'범위가 명확한 공시 발췌문',
'.track-grid article:nth-child(2) li:nth-child(2)':'설명별 인용 연결',
'.track-grid article:nth-child(2) li:nth-child(3)':'숫자 표현과 근거 무결성 검사',
'.closing .eyebrow':'소스 코드와 검증 기록',
'.closing > p:not(.eyebrow)':'설계 결정, 평가 도구, 테스트와 SwiftUI·FastAPI 전체 소스를 저장소에서 확인할 수 있습니다.',
'.closing .hero-actions a:nth-child(1)':'소스 코드와 README 보기',
'.closing .hero-actions a:nth-child(2)':'아키텍처 읽기',
'.closing .hero-actions a:nth-child(3)':'Filing Agent 데모 열기',
'footer span:last-child':'녹화된 포트폴리오 체험'
};
const originals=new Map();for(const s of Object.keys(copy))for(const e of document.querySelectorAll(s))if(!originals.has(e))originals.set(e,e.innerHTML);
const heads={'#hero-title':d.hero,'#walkthrough-title':d.walk,'#evidence-title':d.evidence,'#closing-title':d.closing};for(const s of Object.keys(heads)){const e=document.querySelector(s);originals.set(e,e.innerHTML)}
const captions=['회사 목록','공시 요약','근거가 연결된 답변','별도로 보존되는 수치','미국 공시 요약','언어를 넘는 질문','검색 결과 없음','다크 모드'];const screens=[...document.querySelectorAll('.screen-card figcaption')];screens.forEach(e=>originals.set(e,e.innerHTML));
const images=[...document.querySelectorAll('main img')];images.forEach(e=>e.dataset.originalAlt=e.alt);
const buttons=[...document.querySelectorAll('[data-demo-image]')];
buttons.forEach((b,i)=>{b.onclick=()=>{const playing=b.getAttribute('aria-pressed')==='true';b.setAttribute('aria-pressed',String(!playing));const img=b.previousElementSibling.querySelector('img');img.src=playing?b.dataset.staticSrc:b.dataset.animatedSrc;labels()}});
function labels(){buttons.forEach((b,i)=>{const playing=b.getAttribute('aria-pressed')==='true';b.textContent=pair(`${i===0?'사용 흐름':'답변 상태'} ${playing?'정지':'재생'}`,`${playing?'Stop':'Play'} ${i===0?'core walkthrough':'answer states'}`)})}
setup(lang=>{for(const [e,html] of originals)e.innerHTML=html;if(lang==='ko'){for(const [s,text] of Object.entries(copy))document.querySelectorAll(s).forEach(e=>e.innerHTML=text);for(const [s,text] of Object.entries(heads))document.querySelector(s).innerHTML=text.split('\n').map(line=>`<span class="page-line">${line}</span>`).join('');screens.forEach((e,i)=>e.textContent=captions[i]);}images.forEach((e,i)=>e.alt=lang==='ko'?(i<3?'실제 Filing Digest 앱에서 녹화한 회사 탐색과 답변 화면':captions[i-3]+' · 실제 앱 화면'):e.dataset.originalAlt);document.title=pair('Filing Digest · 공시 읽기와 근거 확인','Filing Digest · Portfolio Demo');document.querySelector('meta[name="description"]').content=pair(d.intro,'A read-only product walkthrough of Filing Digest, a citation-grounded iOS reader for DART and SEC filings.');labels()});
