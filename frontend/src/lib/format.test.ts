// SPDX-License-Identifier: GPL-3.0-or-later
import { describe, expect, it } from "vitest"

import { bytes, clock, when } from "./format"

describe("clock", () => {
  it("writes minutes and seconds, with hours only when there are some", () => {
    expect(clock(65)).toBe("1:05")
    expect(clock(3725.5, 1)).toBe("1:02:05.5")
    expect(clock(-2.3, 2)).toBe("-0:02.30")
  })
})

describe("when", () => {
  it("says today with a 24-hour time, and keeps what it cannot read", () => {
    const afternoon = new Date()
    afternoon.setHours(15, 4, 0, 0)
    expect(when(afternoon.toISOString())).toMatch(/^Today 15.04$/)
    expect(when("not a date")).toBe("not a date")
    expect(when(null)).toBe("")
  })
})

describe("bytes", () => {
  it("picks the largest unit under 1024", () => {
    expect(bytes(512)).toBe("512 B")
    expect(bytes(4200)).toBe("4.1 KB")
    expect(bytes(3 * 1024 ** 3)).toBe("3.0 GB")
  })
})
