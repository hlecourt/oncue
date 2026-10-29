# The editor plays a cached light audio copy, drawn by wavesurfer.js from server-side peaks

The first time a video is opened in the editor, a job extracts one light mono audio copy of it and computes its waveform peaks on the server; both are cached on disk under a size cap with least-recently-used eviction. The browser streams that copy as a normal media file with range requests, and wavesurfer.js (BSD-3-Clause) draws the waveform from the precomputed peaks, with its Regions, Timeline, Minimap and Hover plugins. This replaces one-minute WAV clips cut on demand and a hand-written canvas: seeking anywhere is instant, playback has no seams, the whole film gets an overview, and the client drops its clip-chaining logic, at the cost of a one-time extraction wait per video and disk space for the cache.

## Consequences

- A half-day spike on the voiced demo must confirm two things before the editor is built on it: rendering from precomputed peaks with a separate media element, and two region lanes (the subtitle's lines, draggable; the reference track, fixed). If the second fails, the hand-written canvas stays.
