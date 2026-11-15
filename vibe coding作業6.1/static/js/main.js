const menuToggle = document.querySelector('.menu-toggle');
const navLinks = document.querySelector('.nav-links');

if (menuToggle && navLinks) {
  document.documentElement.classList.add('js-enabled');

  const closeMenu = (returnFocus = false) => {
    navLinks.classList.remove('is-open');
    menuToggle.setAttribute('aria-expanded', 'false');
    menuToggle.setAttribute('aria-label', '開啟導覽選單');
    if (returnFocus) menuToggle.focus();
  };

  menuToggle.addEventListener('click', () => {
    const isOpen = menuToggle.getAttribute('aria-expanded') !== 'true';
    navLinks.classList.toggle('is-open', isOpen);
    menuToggle.setAttribute('aria-expanded', String(isOpen));
    menuToggle.setAttribute('aria-label', isOpen ? '關閉導覽選單' : '開啟導覽選單');
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && menuToggle.getAttribute('aria-expanded') === 'true') {
      closeMenu(true);
    }
  });

  document.addEventListener('click', (event) => {
    if (menuToggle.getAttribute('aria-expanded') !== 'true') return;
    if (!navLinks.contains(event.target) && !menuToggle.contains(event.target)) closeMenu();
  });

  navLinks.addEventListener('click', (event) => {
    if (event.target instanceof Element && event.target.closest('a')) closeMenu();
  });

  const mobileNavigation = window.matchMedia('(max-width: 600px)');
  const resetForViewport = (event) => {
    if (!event.matches) closeMenu();
  };
  if (mobileNavigation.addEventListener) {
    mobileNavigation.addEventListener('change', resetForViewport);
  } else {
    mobileNavigation.addListener(resetForViewport);
  }
}

const resetPendingForms = () => {
  document.querySelectorAll('[data-submit-once]').forEach((button) => {
    button.disabled = false;
    button.removeAttribute('aria-busy');
    const label = button.querySelector('[data-submit-label]');
    if (label) label.textContent = button.dataset.idleLabel || '儲存';
    const form = button.closest('form');
    if (form) delete form.dataset.submitting;
  });
};

document.querySelectorAll('.item-editor-form').forEach((form) => {
  const fields = Array.from(form.querySelectorAll('input:not([type="hidden"]), textarea, select'));
  const fingerprint = () => JSON.stringify(fields.map((field) => field.value));
  let savedFingerprint = fingerprint();
  let allowLeave = false;
  const hasUnsavedChanges = () => !allowLeave && fingerprint() !== savedFingerprint;

  form.querySelectorAll('[data-count-for]').forEach((counter) => {
    const field = document.getElementById(counter.dataset.countFor);
    if (!field) return;
    const updateCount = () => {
      // Match Python's Unicode code-point length used by backend validation.
      counter.textContent = `${Array.from(field.value).length} / ${field.maxLength.toLocaleString('en-US')} 字元`;
    };
    field.addEventListener('input', updateCount);
    updateCount();
  });

  window.addEventListener('beforeunload', (event) => {
    if (!hasUnsavedChanges()) return;
    event.preventDefault();
    event.returnValue = '';
  });
  document.addEventListener('click', (event) => {
    if (!(event.target instanceof Element)) return;
    const link = event.target.closest('a[href]');
    if (!link || link.target === '_blank' || event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (!hasUnsavedChanges()) return;
    if (!window.confirm('內容尚未儲存，確定要離開嗎？')) {
      event.preventDefault();
    } else {
      allowLeave = true;
    }
  });

  form.addEventListener('submit', (event) => {
    if (form.dataset.submitting === 'true') {
      event.preventDefault();
      return;
    }

    const submitButton = form.querySelector('[data-submit-once]');
    if (!submitButton) return;

    form.dataset.submitting = 'true';
    // Native form validation runs before submit; successful POST navigation is intentional.
    allowLeave = true;
    submitButton.disabled = true;
    submitButton.setAttribute('aria-busy', 'true');
    const label = submitButton.querySelector('[data-submit-label]');
    if (label) label.textContent = submitButton.dataset.savingLabel || '儲存中…';
    const status = form.querySelector('[data-submit-status]');
    if (status) status.textContent = submitButton.dataset.savingLabel || '儲存中…';

    // If the connection fails without a response, permit retrying the same idempotent form.
    window.setTimeout(() => {
      if (form.isConnected && form.dataset.submitting === 'true') {
        allowLeave = false;
        resetPendingForms();
        if (status) status.textContent = '尚未收到回覆，可以再次嘗試儲存。';
      }
    }, 15000);
  });

  window.addEventListener('pageshow', () => { allowLeave = false; });
});

window.addEventListener('pageshow', resetPendingForms);

