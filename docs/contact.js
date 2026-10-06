import {setup, pair} from './preferences.js';

const copy = {
  '.skip-link': '문의 및 개인정보 안내로 건너뛰기',
  '[data-walkthrough-link]': '사용 흐름으로 돌아가기',
'#contact-title':'문의 및 개인정보 안내',
'#support-title':'문의와 지원',
'.contact-email':'일반 문의, 비공개 문의, 개인정보 관련 질문이나 삭제 요청은 <a href="mailto:mj.apps.support@gmail.com">mj.apps.support@gmail.com</a>으로 보내 주세요. 이메일 문의에는 GitHub 계정이 필요하지 않습니다.',
'.contact-issues':'공개 버그 제보와 기능 제안은 <a href="https://github.com/mhju0/filing-digest/issues">GitHub Issues</a>를 이용해 주세요. 이슈는 공개되므로 개인정보, 비밀번호, API 키를 포함하지 마세요.',
'#privacy-title':'이 체험 페이지와 데이터',
'.privacy-scope':'이 안내는 실제 앱 화면을 녹화한 정적 체험 페이지에 적용됩니다. 이 페이지는 실시간 API 호출, 파일 업로드, 제품 이용 분석을 하지 않습니다. 로컬 iOS 앱이나 백엔드에 대한 안내는 아닙니다.',
'.privacy-storage':'언어와 테마 선택은 이 브라우저의 저장 공간에 저장됩니다. 이 사이트의 브라우저 데이터를 지우면 삭제할 수 있습니다.',
'.privacy-hosting':'이 페이지는 GitHub Pages에서 제공합니다. GitHub는 <a href="https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement">GitHub 개인정보 처리방침</a>에 따라 IP 주소 같은 요청 데이터를 처리할 수 있습니다.',
'.privacy-support':'이메일로 문의하면 관리자가 답변할 수 있도록 이메일 주소와 메시지가 Gmail을 통해 전달됩니다. GitHub Issues에 제출한 정보는 GitHub가 처리하며 제보 내용은 공개됩니다. 어느 채널에도 비밀번호, API 키 등 인증 정보를 보내지 마세요.',
  'footer a[href^="mailto:"]': '이메일 문의',
  'footer span:last-child': '녹화된 포트폴리오 체험'
};
const originals = new Map();
for (const selector of Object.keys(copy)) {
  for (const element of document.querySelectorAll(selector)) {
    originals.set(element, element.innerHTML);
  }
}
setup(lang => {
  for (const [element, html] of originals) element.innerHTML = html;
  if (lang === 'ko') {
    for (const [selector, text] of Object.entries(copy)) {
      document.querySelectorAll(selector).forEach(element => element.innerHTML = text);
    }
  }
  document.title = pair('Filing Digest · 문의 및 개인정보 안내', 'Filing Digest · Contact and privacy');
});
