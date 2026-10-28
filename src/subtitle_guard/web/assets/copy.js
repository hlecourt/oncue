/* SPDX-License-Identifier: GPL-3.0-or-later */
document.querySelectorAll('button.copy').forEach(button => button.addEventListener('click', () => {
  const text = button.nextElementSibling.textContent;
  const done = () => { button.textContent = 'Copied'; setTimeout(() => { button.textContent = 'Copy'; }, 1500); };
  const select = () => {
    const range = document.createRange();
    range.selectNodeContents(button.nextElementSibling);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    document.execCommand('copy');
    done();
  };
  if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(text).then(done, select); return; }
  select();
}));
