const shown = document.body.dataset.status;
setInterval(async () => {
  if (document.hidden || document.querySelector('details[open]') || (document.activeElement && document.activeElement.closest('form'))) return;
  const response = await fetch('/api/status', {credentials: 'same-origin'});
  if (response.ok && (await response.text()) !== shown) window.location.reload();
}, 15000);
