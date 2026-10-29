# The editor plays a cached light audio copy, drawn by wavesurfer.js from server-side peaks

The first time a video is opened in the editor, a job extracts one light mono audio copy of it and computes its waveform peaks on the server; both are cached on disk under a size cap with least-recently-used eviction. The browser streams that copy as a normal media file with range requests, and wavesurfer.js (BSD-3-Clause) draws the waveform from the precomputed peaks, with its Regions, Timeline, Minimap and Hover plugins. This replaces one-minute WAV clips cut on demand and a hand-written canvas: seeking anywhere is instant, playback has no seams, the whole film gets an overview, and the client drops its clip-chaining logic, at the cost of a one-time extraction wait per video and disk space for the cache.

## Consequences

- A spike on the voiced demo (2026-10-29) confirmed both conditions with wavesurfer.js 8.0.3: drawing from server-side peaks while the browser streams the copy (seek to 4:10 instant, playback in real time), and two region lanes placed by styling each region element (the lines draggable, 100 px at 50 px/s moved a line by 2.00 s; the reference fixed). The copy must be given to wavesurfer as its `url` with the peaks and duration, not as a bare media element, or it clears the element's source.

## Amended 2026-10-29: the editor draws its own tracks

The redesigned editor (a docked timeline with three tracks: your subtitle, the sound, the video's subtitle) needs the lines in
their own lane above the waveform and the reference in a lane under it, with start markers crossing the sound. wavesurfer.js draws
regions on top of its waveform, which hid where the voice starts, and stacking separate lanes against its scroll and zoom was
fragile. The editor now plays the same cached copy through a plain audio element and draws the waveform itself from the same
server-side peaks (an SVG path for the visible window), with the lanes, ruler, playhead and whole-file overview as its own elements.
The audio copy, the peaks job and the cache are unchanged. wavesurfer.js still draws the sound track: it renders the shared audio
element's waveform from the same peaks inside the sound lane, without interaction, regions or its own scrolling, and follows the
timeline's zoom and window; the lanes, ruler, playhead and overview are the editor's own. The editor chunk is about 36 KB gzip.
