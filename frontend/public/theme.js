/* SPDX-License-Identifier: GPL-3.0-or-later */
try {
  const chosen = localStorage.getItem("theme");
  const dark = chosen === "dark" || (chosen !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
} catch (error) {
  document.documentElement.classList.add("dark");
}
