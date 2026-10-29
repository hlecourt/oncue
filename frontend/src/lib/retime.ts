// SPDX-License-Identifier: GPL-3.0-or-later

export type Change = { first: number; last: number; offset: number }
export type Move = Change & { group: number; held: boolean }
export type Scope = "all" | "from" | "selected"

const CENTISECONDS = 100

export function lineOffsets(count: number, history: Change[]) {
  const offsets = new Array<number>(count).fill(0)
  for (const change of history) {
    for (let index = Math.max(0, change.first); index <= Math.min(count - 1, change.last); index += 1) offsets[index]! += change.offset
  }
  return offsets.map((offset) => Math.round(offset * CENTISECONDS) / CENTISECONDS)
}

export function segments(offsets: number[]) {
  const found: Change[] = []
  offsets.forEach((offset, index) => {
    const last = found.at(-1)
    if (offset === 0) return
    if (last && last.offset === offset && last.last === index - 1) last.last = index
    else found.push({ first: index, last: index, offset })
  })
  return found
}

export function heldLines(count: number, history: Move[]) {
  const offsets = lineOffsets(count, history)
  const held = new Array<boolean>(count).fill(false)
  for (const move of history.filter((entry) => entry.held)) {
    for (let index = Math.max(0, move.first); index <= Math.min(count - 1, move.last); index += 1) held[index] = offsets[index] !== 0
  }
  return held
}

export function openRuns([first, last]: [number, number], held: boolean[]) {
  const runs: [number, number][] = []
  for (let index = first; index <= last; index += 1) {
    if (held[index]) continue
    const run = runs.at(-1)
    if (run && run[1] === index - 1) run[1] = index
    else runs.push([index, index])
  }
  return runs
}

export function scopeRange(scope: Scope, current: number, selection: [number, number] | null, count: number): [number, number] {
  if (scope === "from") return [current, count - 1]
  if (scope === "selected" && selection) return selection
  if (scope === "selected") return [current, current]
  return [0, count - 1]
}
