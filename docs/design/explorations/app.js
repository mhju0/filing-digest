const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
const panels = Array.from(document.querySelectorAll('[role="tabpanel"]'));

function activateConcept(tab, moveFocus = false) {
  const concept = tab.dataset.concept;

  tabs.forEach((candidate) => {
    const selected = candidate === tab;
    candidate.classList.toggle('is-active', selected);
    candidate.setAttribute('aria-selected', String(selected));
    candidate.tabIndex = selected ? 0 : -1;
  });

  panels.forEach((panel) => {
    const selected = panel.dataset.panel === concept;
    panel.classList.toggle('is-active', selected);
    panel.hidden = !selected;
  });

  if (moveFocus) tab.focus();
  history.replaceState(null, '', `#${concept}`);
}

tabs.forEach((tab, index) => {
  tab.addEventListener('click', () => activateConcept(tab));
  tab.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();

    let nextIndex = index;
    if (event.key === 'ArrowLeft') nextIndex = (index - 1 + tabs.length) % tabs.length;
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % tabs.length;
    if (event.key === 'Home') nextIndex = 0;
    if (event.key === 'End') nextIndex = tabs.length - 1;
    activateConcept(tabs[nextIndex], true);
  });
});

const initialConcept = window.location.hash.slice(1);
const initialTab = tabs.find((tab) => tab.dataset.concept === initialConcept);
if (initialTab) activateConcept(initialTab);
