const params = new URLSearchParams(location.search);
function saved(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function remember(key, value) {
  try { localStorage.setItem(key, value); } catch { /* Browsing still works without persistence. */ }
}
const requested = params.get('lang');
const stored = saved('filing-digest-language');
export let language = ['ko', 'en'].includes(requested) ? requested
  : ['ko', 'en'].includes(stored) ? stored
  : navigator.language.startsWith('ko') ? 'ko' : 'en';
export const pair = (ko, en) => language === 'ko' ? ko : en;

export function setup(onLanguage) {
  const header = document.querySelector('.site-header');
  document.documentElement.lang = language;
  const theme = saved('filing-digest-theme');
  document.body.dataset.theme = ['light', 'dark'].includes(theme) ? theme
    : matchMedia('(prefers-color-scheme:dark)').matches ? 'dark' : 'light';
  header.insertAdjacentHTML('beforeend', `<div class="language-switch" role="group" aria-label="Language"><button data-lang="ko">한국어</button><button data-lang="en">English</button></div><button class="theme-control"></button>`);
  function labels() {
    document.querySelectorAll('[data-lang]').forEach(button => {
      button.setAttribute('aria-pressed', button.dataset.lang === language);
    });
    const dark = document.body.dataset.theme === 'dark';
    document.querySelector('.theme-control').textContent = dark ? pair('밝게', 'Light') : pair('어둡게', 'Dark');
    document.querySelector('.theme-control').setAttribute('aria-label', dark ? pair('밝은 테마로 전환', 'Switch to light theme') : pair('어두운 테마로 전환', 'Switch to dark theme'));
    header.querySelector('.brand').setAttribute('aria-label', pair('Filing Digest 처음으로', 'Filing Digest home'));
    header.querySelector('nav').setAttribute('aria-label', pair('주요 메뉴', 'Primary navigation'));
    document.querySelector('.language-switch').setAttribute('aria-label', pair('언어', 'Language'));
    document.querySelector('#scope').setAttribute('aria-label', pair('제품 범위', 'Product scope'));
    document.querySelector('.architecture').setAttribute('aria-label', pair('시스템 구조', 'System architecture'));
  }
  document.querySelectorAll('[data-lang]').forEach(button => {
    button.addEventListener('click', () => {
      const threshold = header.getBoundingClientRect().bottom + 40;
      const anchor = [...document.querySelectorAll('main section[id]')]
        .filter(section => section.getBoundingClientRect().top <= threshold).at(-1);
      language = button.dataset.lang;
      remember('filing-digest-language', language);
      document.documentElement.lang = language;
      const url = new URL(location);
      url.searchParams.set('lang', language);
      history.replaceState(null, '', url);
      onLanguage(language);
      labels();
      if (anchor && anchor.id !== 'top') {
        requestAnimationFrame(() => requestAnimationFrame(() => {
          const top = scrollY + anchor.getBoundingClientRect().top - header.getBoundingClientRect().height - 16;
          scrollTo({top, behavior: 'instant'});
        }));
      }
    });
  });
  document.querySelector('.theme-control').addEventListener('click', () => {
    document.body.dataset.theme = document.body.dataset.theme === 'dark' ? 'light' : 'dark';
    remember('filing-digest-theme', document.body.dataset.theme);
    labels();
  });
  onLanguage(language);
  labels();
}
