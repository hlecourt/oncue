document.addEventListener('keydown', event => {
  if (event.target.matches('input, textarea') || event.metaKey || event.ctrlKey || event.altKey) return;
  const targets = {j: 'next-item', k: 'previous-item', ArrowRight: 'next-minute', ArrowLeft: 'previous-minute'};
  if (targets[event.key] && document.getElementById(targets[event.key])) { window.location = document.getElementById(targets[event.key]).href; }
  if (event.key === 'a' && document.getElementById('accept-form')) { document.getElementById('accept-form').submit(); }
});
