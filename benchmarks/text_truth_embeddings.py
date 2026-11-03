# SPDX-License-Identifier: GPL-3.0-or-later
"""Embed once every text the text check will see on the text_truth.py cases, so engine variants reuse the vectors instead of each worker
running the model again.

usage: text_truth_embeddings.py DATA_FOLDER MODEL_FOLDER OUTPUT.npz [--threads N]
"""
import argparse
import itertools
import time

import numpy
import text_truth

from oncue import text_check
from oncue.text_model import load_model


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("data")
    parser.add_argument("model")
    parser.add_argument("output")
    parser.add_argument("--threads", type=int, default=0)
    arguments = parser.parse_args()
    data = text_truth.load_data(arguments.data)
    texts = set()
    for case in text_truth.library_cases(data) + text_truth.synthetic_cases(data):
        lines = [text_check.clean(text) for _, _, text in case["cues"]]
        reference = [text for text in (text_check.clean(text) for _, _, text in case["texts"] or []) if text]
        texts.update(line or "-" for line in lines)
        texts.update(reference)
        texts.update(f"{first} {second}" for first, second in itertools.pairwise(reference))
    ordered = sorted(texts)
    started = time.time()
    vectors = load_model(arguments.model, arguments.threads).encode(ordered)
    numpy.savez(arguments.output, texts=numpy.array(ordered, dtype=object), vectors=vectors.astype(numpy.float32))
    print(f"{len(ordered)} texts embedded in {time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
