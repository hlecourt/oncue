// SPDX-License-Identifier: GPL-3.0-or-later
export function OnCueMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 100 100" className={className} aria-hidden="true">
      <circle cx="50" cy="50" r="29.9" fill="none" stroke="currentColor" strokeWidth="13.8" />
      <rect x="31.025" y="44.25" width="5.75" height="11.5" rx="2.875" fill="currentColor" />
      <rect x="40.8" y="37.35" width="5.75" height="25.3" rx="2.875" fill="currentColor" />
      <rect x="50.575" y="41.95" width="5.75" height="16.1" rx="2.875" fill="currentColor" />
      <rect x="60.925" y="4" width="6.9" height="92" rx="3.45" fill="#0090ff" />
    </svg>
  )
}
