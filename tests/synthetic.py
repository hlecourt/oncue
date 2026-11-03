# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic dialogue: a reference track and a translation segmented differently, for tests without media."""

import numpy


def dialogue(seed, minutes=22):
    rng = numpy.random.default_rng(seed)
    starts, ends, moment = [], [], 5.0
    while moment < minutes * 60:
        duration = rng.uniform(0.8, 4.0)
        starts.append(moment)
        ends.append(moment + duration)
        moment += duration + rng.exponential(1.2) + (rng.random() < 0.05) * rng.uniform(5, 40)
    return numpy.array(starts), numpy.array(ends)


def translation(starts, ends, seed):
    rng = numpy.random.default_rng(seed + 1000)
    cues, index = [], 0
    while index < len(starts):
        if rng.random() < 0.05:
            index += 1
            continue
        if index + 1 < len(starts) and rng.random() < 0.15:
            cues.append((starts[index] + rng.uniform(-0.1, 0.1), ends[index + 1], f"line {index}"))
            index += 2
            continue
        cues.append((starts[index] + rng.uniform(-0.1, 0.1), ends[index], f"line {index}"))
        index += 1
    return cues


def split_translation(starts, ends, seed):
    """A correct translation timed by hand: half of the long lines split in two, starts and ends a quarter second off."""
    rng = numpy.random.default_rng(seed + 2000)
    cues = []
    for start, end in zip(starts, ends, strict=False):
        if end - start > 1.6 and rng.random() < 0.5:
            middle = start + (end - start) * rng.uniform(0.4, 0.6)
            cues += [(start + rng.normal(0, 0.25), middle, "first half"), (middle + rng.normal(0, 0.25), end + rng.normal(0, 0.25), "second half")]
            continue
        cues.append((start + rng.normal(0, 0.25), end + rng.normal(0, 0.25), "line"))
    return cues
