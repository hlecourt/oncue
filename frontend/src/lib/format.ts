// SPDX-License-Identifier: GPL-3.0-or-later
import { translate } from "@/lib/i18n"

export function clock(seconds: number, digits = 0) {
  const sign = seconds < 0 ? "-" : ""
  const whole = Math.abs(seconds)
  const hours = Math.floor(whole / 3600)
  const minutes = Math.floor(whole / 60) % 60
  const rest = (whole % 60).toFixed(digits).padStart(digits ? 3 + digits : 2, "0")
  return hours ? `${sign}${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${sign}${minutes}:${rest}`
}

export function when(moment: string | null | undefined) {
  if (!moment) return ""
  const date = new Date(moment)
  if (Number.isNaN(date.getTime())) return moment
  const today = new Date()
  const yesterday = new Date(today.getTime() - 86_400_000)
  const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
  if (date.toDateString() === today.toDateString()) return translate("Today {time}", { time })
  if (date.toDateString() === yesterday.toDateString()) return translate("Yesterday {time}", { time })
  return `${date.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" })} ${time}`
}

export function bytes(count: number) {
  const units = ["B", "KB", "MB", "GB", "TB"]
  let value = count
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(unit ? 1 : 0)} ${units[unit]}`
}
