let picked = null;
const clock = value => {
  const seconds = (value % 60).toFixed(2).padStart(5, '0');
  const hours = Math.floor(value / 3600);
  return hours ? `${hours}:${String(Math.floor(value / 60) % 60).padStart(2, '0')}:${seconds}` : `${Math.floor(value / 60)}:${seconds}`;
};
document.querySelectorAll('td.pickable[data-time]').forEach(cell => cell.addEventListener('click', () => {
  const time = parseFloat(cell.dataset.time);
  document.getElementById('by-hand').open = true;
  if (cell.classList.contains('yours')) {
    picked = time;
    document.getElementById('first').value = clock(Math.max(0, time - 0.05));
    document.getElementById('hint').textContent = 'Now click the moment in the video it should match, on the left.';
    return;
  }
  if (picked === null) return;
  document.getElementById('offset').value = (time - picked).toFixed(2);
  document.getElementById('hint').textContent = 'Done. Set where the part ends, then click Move.';
}));
