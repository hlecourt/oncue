/* SPDX-License-Identifier: GPL-3.0-or-later */
document.addEventListener('keydown', event => {
  if (event.target.closest('input, textarea, select, summary, button, [contenteditable]') || event.metaKey || event.ctrlKey || event.altKey) return;
  const targets = {j: 'next-item', k: 'previous-item', ArrowRight: 'next-minute', ArrowLeft: 'previous-minute'};
  if (targets[event.key] && document.getElementById(targets[event.key])) { window.location = document.getElementById(targets[event.key]).href; }
});
