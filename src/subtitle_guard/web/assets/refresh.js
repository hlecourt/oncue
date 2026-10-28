/* SPDX-License-Identifier: GPL-3.0-or-later */
const shown = document.body.dataset.status;
setInterval(async () => {
  const busy = document.querySelector('details[open]') || (document.activeElement && document.activeElement !== document.body);
  if (document.hidden || busy) return;
  try {
    const response = await fetch('/api/status', {credentials: 'same-origin'});
    if (response.ok && (await response.text()) !== shown) window.location.reload();
  } catch (error) {
    return;
  }
}, 15000);
