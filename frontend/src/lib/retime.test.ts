// SPDX-License-Identifier: GPL-3.0-or-later
import { describe, expect, it } from "vitest"

import { lineOffsets, scopeRange, segments } from "./retime"

describe("lineOffsets", () => {
  it("adds every change to the lines it covers, to the hundredth", () => {
    expect(lineOffsets(5, [{ first: 0, last: 4, offset: -2.3 }, { first: 3, last: 4, offset: 1.2 }, { first: 0, last: 0, offset: 0.1 }]))
      .toEqual([-2.2, -2.3, -2.3, -1.1, -1.1])
  })

  it("ignores the part of a change outside the subtitle", () => {
    expect(lineOffsets(2, [{ first: 1, last: 9, offset: 1 }])).toEqual([0, 1])
  })
})

describe("segments", () => {
  it("groups the lines that move together and leaves out the ones that do not move", () => {
    expect(segments([0, -1, -1, 0, 0.5, 0.5, -1])).toEqual([
      { first: 1, last: 2, offset: -1 },
      { first: 4, last: 5, offset: 0.5 },
      { first: 6, last: 6, offset: -1 },
    ])
  })

  it("lets a change cancel another one", () => {
    expect(segments(lineOffsets(3, [{ first: 0, last: 2, offset: 0.1 }, { first: 0, last: 2, offset: -0.1 }]))).toEqual([])
  })
})

describe("scopeRange", () => {
  it("covers every line, the lines from the current one, or the selection", () => {
    expect(scopeRange("all", 3, null, 10)).toEqual([0, 9])
    expect(scopeRange("from", 3, null, 10)).toEqual([3, 9])
    expect(scopeRange("selected", 3, [2, 5], 10)).toEqual([2, 5])
    expect(scopeRange("selected", 3, null, 10)).toEqual([3, 3])
  })
})
