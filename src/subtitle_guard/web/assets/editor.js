/* SPDX-License-Identifier: GPL-3.0-or-later */
const data = JSON.parse(document.getElementById('editor-data').textContent);
const CHUNK_SECONDS = 60;
const PEAKS_PER_SECOND = 200;
const PREFETCH_SECONDS = 12;
const SELECTION_MARGIN = 0.05;
const ZOOMS = [10, 25, 50, 100, 200];
const csrf = document.querySelector('#apply-form input[name=csrf]').value;
const element = id => document.getElementById(id);
const audio = new Audio();
const chunks = new Map();
const state = {position: data.at || Math.max(0, (data.cues[0] || [0])[0] - 2), shift: 0, scope: 'all', selection: null, zoom: 2, playing: false, chunk: null, current: -1};
let frameTimer = null;
let frameUrl = null;
let loading = Promise.resolve();

const clock = (value, digits = 1) => {
  const sign = value < 0 ? '-' : '';
  const absolute = Math.abs(value);
  const seconds = (absolute % 60).toFixed(digits).padStart(digits ? 3 + digits : 2, '0');
  const hours = Math.floor(absolute / 3600);
  const minutes = Math.floor(absolute / 60) % 60;
  return sign + (hours ? `${hours}:${String(minutes).padStart(2, '0')}:${seconds}` : `${minutes}:${seconds}`);
};
const chunkStart = time => Math.floor(Math.max(0, time) / CHUNK_SECONDS) * CHUNK_SECONDS;
const bounds = () => {
  if (state.scope === 'selected' && state.selection) return [data.cues[state.selection[0]][0], data.cues[state.selection[1]][0]];
  return [0, data.cues.length ? data.cues[data.cues.length - 1][0] : 0];
};
const moves = index => {
  const [first, last] = bounds();
  const start = data.cues[index][0];
  return start >= first - SELECTION_MARGIN && start <= last + SELECTION_MARGIN;
};
const shown = index => {
  const [start, end, text] = data.cues[index];
  const offset = moves(index) ? state.shift : 0;
  return [start + offset, end + offset, text];
};

async function post(url, fields) {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const response = await fetch(url, {method: 'POST', credentials: 'same-origin', body: new URLSearchParams({...fields, path: data.path, csrf})});
    if (response.status !== 503) return response;
    await new Promise(resolve => setTimeout(resolve, 300));
  }
  throw new Error('the server is busy');
}

function readPeaks(buffer) {
  const view = new DataView(buffer);
  let offset = 12;
  while (offset + 8 <= buffer.byteLength) {
    const name = String.fromCharCode(...new Uint8Array(buffer, offset, 4));
    const size = view.getUint32(offset + 4, true);
    if (name === 'data') {
      const length = Math.floor(Math.min(size, buffer.byteLength - offset - 8) / 2);
      const samples = new Int16Array(buffer.slice(offset + 8, offset + 8 + length * 2));
      const bucket = 16000 / PEAKS_PER_SECOND;
      const peaks = new Float32Array(Math.ceil(length / bucket));
      for (let index = 0; index < length; index += 1) {
        const value = Math.abs(samples[index]) / 32768;
        const slot = Math.floor(index / bucket);
        if (value > peaks[slot]) peaks[slot] = value;
      }
      return {peaks, seconds: length / 16000};
    }
    offset += 8 + size + (size % 2);
  }
  return {peaks: new Float32Array(0), seconds: 0};
}

function loadChunk(start) {
  if (start >= data.duration) return Promise.resolve(null);
  if (chunks.has(start)) return chunks.get(start).ready;
  const entry = {start, peaks: null, url: null};
  entry.ready = loading = loading.catch(() => null).then(async () => {
    const response = await post('/api/audio', {start, track: element('track').value});
    if (!response.ok) throw new Error(await response.text());
    const buffer = await response.arrayBuffer();
    Object.assign(entry, readPeaks(buffer), {url: URL.createObjectURL(new Blob([buffer], {type: 'audio/wav'}))});
    draw();
    return entry;
  });
  entry.ready.catch(error => {
    chunks.delete(start);
    say(`The sound could not be loaded: ${error.message}.`);
  });
  chunks.set(start, entry);
  return entry.ready;
}

async function play() {
  const start = chunkStart(state.position);
  say('Loading the sound...');
  const entry = await loadChunk(start).catch(() => null);
  if (!entry) return stop();
  state.chunk = entry;
  if (audio.src !== entry.url) audio.src = entry.url;
  audio.currentTime = Math.max(0, state.position - entry.start);
  audio.playbackRate = parseFloat(element('speed').value);
  try {
    await audio.play();
  } catch (error) {
    say('The browser did not start the sound. Press Play again.');
    return stop();
  }
  state.playing = true;
  element('play').textContent = 'Pause';
  say('Playing.');
  requestAnimationFrame(follow);
}

function stop() {
  audio.pause();
  state.playing = false;
  element('play').textContent = 'Play';
  render();
  scheduleFrame();
}

function follow() {
  if (!state.playing) return;
  state.position = state.chunk.start + audio.currentTime;
  if (audio.currentTime > CHUNK_SECONDS - PREFETCH_SECONDS) loadChunk(state.chunk.start + CHUNK_SECONDS).catch(() => null);
  render();
  requestAnimationFrame(follow);
}

audio.addEventListener('ended', () => {
  if (!state.playing) return;
  state.position = state.chunk.start + state.chunk.seconds;
  if (state.position >= data.duration - 0.1) return stop();
  play();
});

function seek(time) {
  state.position = Math.min(Math.max(0, time), data.duration);
  if (state.playing) {
    audio.pause();
    play();
    return;
  }
  loadChunk(chunkStart(state.position)).catch(() => null);
  render();
  scheduleFrame();
}

function scheduleFrame() {
  clearTimeout(frameTimer);
  frameTimer = setTimeout(async () => {
    if (state.playing) return;
    try {
      const response = await post('/api/frame', {time: state.position.toFixed(3)});
      if (!response.ok) return;
      if (frameUrl) URL.revokeObjectURL(frameUrl);
      frameUrl = URL.createObjectURL(await response.blob());
      element('frame').src = frameUrl;
    } catch (error) {
      return;
    }
  }, 250);
}

function say(words) {
  element('audio-status').textContent = words;
}

function buildLines() {
  const body = element('lines');
  data.cues.forEach(([, , text], index) => {
    const row = document.createElement('tr');
    row.dataset.index = index;
    const cells = ['number', 'start', 'end', 'length', 'text'].map(name => {
      const cell = document.createElement('td');
      cell.className = name;
      row.appendChild(cell);
      return cell;
    });
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'pick';
    button.textContent = String(index + 1);
    button.setAttribute('aria-label', `Line ${index + 1}: go there and select it`);
    cells[0].appendChild(button);
    cells[4].textContent = text;
    body.appendChild(row);
  });
  body.addEventListener('click', event => {
    const row = event.target.closest('tr');
    if (!row) return;
    const index = Number(row.dataset.index);
    state.selection = event.shiftKey && state.selection ? [Math.min(state.selection[0], index), Math.max(state.selection[1], index)] : [index, index];
    element('scope-selected').disabled = false;
    if (event.shiftKey) {
      element('scope-selected').checked = true;
      state.scope = 'selected';
    }
    updateShift();
    seek(shown(index)[0] - 0.3);
  });
}

function updateLines() {
  const rows = element('lines').rows;
  for (let index = 0; index < rows.length; index += 1) {
    const [start, end] = shown(index);
    const cells = rows[index].cells;
    cells[1].textContent = clock(start, 3);
    cells[2].textContent = clock(end, 3);
    cells[3].textContent = `${(end - start).toFixed(1)} s`;
    rows[index].classList.toggle('moved', state.shift !== 0 && moves(index));
    rows[index].classList.toggle('selected', Boolean(state.selection) && index >= state.selection[0] && index <= state.selection[1]);
  }
}

function updateShift() {
  const affected = data.cues.filter((_, index) => moves(index)).length;
  const negative = data.cues.some((cue, index) => moves(index) && cue[0] + state.shift < 0);
  const amount = Math.abs(state.shift).toFixed(2).replace(/\.?0+$/, '');
  const words = state.shift === 0 ? 'Not moved.' : `${affected} lines show ${amount} s ${state.shift < 0 ? 'earlier' : 'later'}.`;
  element('shift-words').textContent = negative ? `${words} Some would start before the video begins, so this cannot be applied.` : words;
  if (document.activeElement !== element('shift')) element('shift').value = state.shift.toFixed(2);
  const [first, last] = bounds();
  element('first').value = first.toFixed(3);
  element('last').value = last.toFixed(3);
  element('offset').value = state.shift.toFixed(2);
  element('apply').disabled = state.shift === 0 || negative || !affected;
  if (state.selection) {
    const [from, to] = state.selection;
    element('selection-words').textContent = `Selected lines: ${from + 1} to ${to + 1} (${to - from + 1} lines, ${clock(data.cues[from][0], 0)} to ${clock(data.cues[to][0], 0)})`;
  }
  updateLines();
  render();
}

function setShift(value) {
  state.shift = Math.round(Math.max(-3600, Math.min(3600, value)) * 100) / 100;
  updateShift();
}

function render() {
  element('clock').textContent = `${clock(state.position)} / ${clock(data.duration, 0)}`;
  const active = data.cues.findIndex((_, index) => shown(index)[0] <= state.position && state.position < shown(index)[1]);
  element('caption').textContent = active >= 0 ? shown(active)[2] : '';
  if (active >= 0 && active !== state.current) {
    const rows = element('lines').rows;
    if (rows[state.current]) rows[state.current].classList.remove('current');
    rows[active].classList.add('current');
    if (state.playing) rows[active].scrollIntoView({block: 'nearest'});
    state.current = active;
  }
  draw();
}

function view(canvas) {
  const pixels = ZOOMS[state.zoom];
  const left = state.position - (canvas.width / pixels) * 0.3;
  return {pixels, left, right: left + canvas.width / pixels};
}

function draw() {
  const canvas = element('wave');
  const width = canvas.clientWidth;
  if (canvas.width !== width) canvas.width = width;
  const context = canvas.getContext('2d');
  const styles = getComputedStyle(document.documentElement);
  const color = name => styles.getPropertyValue(name).trim();
  const {pixels, left, right} = view(canvas);
  const waveTop = 18;
  const waveHeight = data.reference.length ? 92 : 122;
  const x = time => (time - left) * pixels;
  context.fillStyle = color('--surface');
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.font = '11px system-ui, sans-serif';
  const step = pixels >= 100 ? 1 : pixels >= 25 ? 5 : 10;
  context.fillStyle = color('--muted');
  context.strokeStyle = color('--line');
  for (let tick = Math.ceil(left / step) * step; tick < right; tick += step) {
    context.fillRect(x(tick), 0, 1, 6);
    context.fillText(clock(tick, 0), x(tick) + 3, 11);
  }
  for (let pixel = 0; pixel < canvas.width; pixel += 1) {
    const time = left + pixel / pixels;
    const entry = chunks.get(chunkStart(time));
    if (!entry || !entry.peaks || time < 0) continue;
    const peak = entry.peaks[Math.floor((time - entry.start) * PEAKS_PER_SECOND)] || 0;
    const height = Math.max(1, peak * waveHeight);
    context.fillStyle = color('--muted');
    context.fillRect(pixel, waveTop + (waveHeight - height) / 2, 1, height);
  }
  data.cues.forEach((_, index) => {
    const [start, end, text] = shown(index);
    if (end < left || start > right) return;
    const moved = state.shift !== 0 && moves(index);
    context.fillStyle = moved ? 'rgba(74, 163, 223, 0.35)' : 'rgba(226, 169, 59, 0.28)';
    context.fillRect(x(start), waveTop, Math.max(2, (end - start) * pixels), waveHeight);
    context.fillStyle = moved ? color('--accent') : color('--medium');
    context.fillRect(x(start), waveTop, 2, waveHeight);
    context.fillStyle = color('--text');
    context.save();
    context.beginPath();
    context.rect(x(start), waveTop, Math.max(2, (end - start) * pixels) - 2, waveHeight);
    context.clip();
    context.fillText(text, x(start) + 5, waveTop + 14);
    context.restore();
  });
  data.reference.forEach(([start, end, text]) => {
    if (end < left || start > right) return;
    context.fillStyle = color('--raised');
    context.fillRect(x(start), waveTop + waveHeight + 6, Math.max(2, (end - start) * pixels), 24);
    context.fillStyle = color('--muted');
    context.save();
    context.beginPath();
    context.rect(x(start), waveTop + waveHeight + 6, Math.max(2, (end - start) * pixels) - 2, 24);
    context.clip();
    context.fillText(text, x(start) + 4, waveTop + waveHeight + 22);
    context.restore();
  });
  context.fillStyle = color('--text');
  context.fillRect(x(state.position), 0, 2, canvas.height);
  for (let start = chunkStart(left); start < right; start += CHUNK_SECONDS) {
    if (!state.playing && !chunks.has(start) && start < data.duration) loadChunk(start).catch(() => null);
  }
}

function startDrag(event) {
  const canvas = element('wave');
  const {pixels, left} = view(canvas);
  const origin = {x: event.offsetX, shift: state.shift, moved: false};
  const inLines = event.offsetY < 18 + (data.reference.length ? 92 : 122);
  canvas.setPointerCapture(event.pointerId);
  const move = moveEvent => {
    if (Math.abs(moveEvent.offsetX - origin.x) > 3) origin.moved = true;
    if (origin.moved && inLines) setShift(origin.shift + (moveEvent.offsetX - origin.x) / pixels);
  };
  const end = upEvent => {
    canvas.removeEventListener('pointermove', move);
    canvas.removeEventListener('pointerup', end);
    if (!origin.moved) seek(left + upEvent.offsetX / pixels);
  };
  canvas.addEventListener('pointermove', move);
  canvas.addEventListener('pointerup', end);
}

element('play').addEventListener('click', () => (state.playing ? stop() : play()));
element('back').addEventListener('click', () => seek(state.position - 5));
element('forward').addEventListener('click', () => seek(state.position + 5));
element('speed').addEventListener('change', () => { audio.playbackRate = parseFloat(element('speed').value); });
element('track').addEventListener('change', () => {
  stop();
  chunks.forEach(entry => entry.url && URL.revokeObjectURL(entry.url));
  chunks.clear();
  seek(state.position);
});
document.querySelectorAll('[data-nudge]').forEach(button => button.addEventListener('click', () => setShift(state.shift + parseFloat(button.dataset.nudge))));
element('shift').addEventListener('change', () => setShift(parseFloat(element('shift').value) || 0));
element('reset').addEventListener('click', () => setShift(0));
document.querySelectorAll('input[name=scope]').forEach(radio => radio.addEventListener('change', () => {
  state.scope = radio.value;
  updateShift();
}));
element('zoom-in').addEventListener('click', () => { state.zoom = Math.min(ZOOMS.length - 1, state.zoom + 1); element('zoom-words').textContent = `${ZOOMS[state.zoom]} px/s`; draw(); });
element('zoom-out').addEventListener('click', () => { state.zoom = Math.max(0, state.zoom - 1); element('zoom-words').textContent = `${ZOOMS[state.zoom]} px/s`; draw(); });
element('wave').addEventListener('pointerdown', startDrag);
element('apply-form').addEventListener('submit', () => { element('at').value = state.position.toFixed(1); });
window.addEventListener('resize', draw);

buildLines();
updateShift();
seek(state.position);
