# SPDX-License-Identifier: GPL-3.0-or-later
"""A multilingual sentence embedding model run with ONNX Runtime, without PyTorch: the folder of a sentence-transformers model as published
(onnx/model.onnx, tokenizer.json, modules.json and its pooling and dense layers), downloaded once into a cache volume and loaded offline."""

import json
import os
import struct

import numpy

from .errors import ConfigError

BATCH_SIZE = 64
MAX_TOKENS = 128
SAFETENSORS_TYPES = {"F32": numpy.float32, "F16": numpy.float16}
ACTIVATIONS = {"torch.nn.modules.activation.Tanh": numpy.tanh, "torch.nn.modules.linear.Identity": lambda values: values}
LOADED = {}


def load_model(folder, threads=0):
    if folder not in LOADED:
        LOADED[folder] = SentenceModel(folder, threads)
    return LOADED[folder]


class SentenceModel:
    def __init__(self, folder, threads=0):
        try:
            import onnxruntime
            from tokenizers import Tokenizer
        except ImportError as error:
            raise ConfigError("the text check needs the text extra: pip install 'oncue[text]'") from error
        onnx_file = os.path.join(folder, "onnx", "model.onnx")
        if not os.path.isfile(onnx_file) or not os.path.isfile(os.path.join(folder, "tokenizer.json")):
            raise ConfigError(f"{folder} is not a sentence model folder: onnx/model.onnx and tokenizer.json are needed")
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = threads
        # no memory arena: one long batch would otherwise keep its peak for the life of the worker
        options.enable_cpu_mem_arena = False
        self.session = onnxruntime.InferenceSession(onnx_file, options, providers=["CPUExecutionProvider"])
        self.inputs = {item.name for item in self.session.get_inputs()}
        self.tokenizer = Tokenizer.from_file(os.path.join(folder, "tokenizer.json"))
        # subtitle lines are short: a longer cap only costs memory on the odd sign or karaoke line
        self.tokenizer.enable_truncation(max_length=min(read_config(folder, "sentence_bert_config.json").get("max_seq_length", MAX_TOKENS), MAX_TOKENS))
        self.padding = next((self.tokenizer.token_to_id(token) for token in ("[PAD]", "<pad>") if self.tokenizer.token_to_id(token) is not None), 0)
        self.prefix = read_config(folder, "config_sentence_transformers.json").get("prompts", {}).get("query", "")
        self.pooling, self.dense = "mean", []
        for module in read_config(folder, "modules.json") or [{"type": "sentence_transformers.models.Pooling", "path": ""}]:
            kind, path = module["type"].rsplit(".", 1)[-1], os.path.join(folder, module.get("path", ""))
            if kind == "Pooling":
                self.pooling = "cls" if read_config(path, "config.json").get("pooling_mode_cls_token") else "mean"
            elif kind == "Dense":
                self.dense.append(dense_layer(path))
        # which calibration applies (text_models): LaBSE has dense layers, multilingual-e5 asks for its "query: " prefix; any other model
        # has none, so the text check never writes with it
        self.family = "labse" if self.dense else "e5" if self.prefix == "query: " else "uncalibrated"

    def encode(self, texts):
        # batches of similar length, so short subtitle lines are not padded to the longest line of a random batch
        if not texts:
            return numpy.zeros((0, 0), dtype=numpy.float32)
        encodings = self.tokenizer.encode_batch([self.prefix + text for text in texts])
        order = sorted(range(len(texts)), key=lambda index: len(encodings[index].ids))
        batches = [order[start:start + BATCH_SIZE] for start in range(0, len(order), BATCH_SIZE)]
        vectors = numpy.concatenate([self.encode_batch([encodings[index] for index in batch]) for batch in batches])
        unsorted = numpy.empty_like(vectors)
        unsorted[order] = vectors
        return unsorted

    def encode_batch(self, encodings):
        width = max(len(encoding.ids) for encoding in encodings)
        ids = numpy.full((len(encodings), width), self.padding, dtype=numpy.int64)
        mask = numpy.zeros((len(encodings), width), dtype=numpy.int64)
        for row, encoding in enumerate(encodings):
            ids[row, :len(encoding.ids)] = encoding.ids
            mask[row, :len(encoding.ids)] = 1
        feed = {"input_ids": ids, "attention_mask": mask, "token_type_ids": numpy.zeros_like(ids)}
        hidden = self.session.run(None, {name: value for name, value in feed.items() if name in self.inputs})[0]
        if self.pooling == "cls":
            pooled = hidden[:, 0]
        else:
            weights = mask[:, :, None].astype(hidden.dtype)
            pooled = (hidden * weights).sum(axis=1) / numpy.maximum(weights.sum(axis=1), 1e-9)
        for weight, bias, activation in self.dense:
            pooled = activation(pooled @ weight.T + bias)
        return pooled / numpy.maximum(numpy.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)


def read_config(folder, name):
    path = os.path.join(folder, name)
    if not os.path.isfile(path):
        return {}
    with open(path) as handle:
        return json.load(handle)


def dense_layer(folder):
    config = read_config(folder, "config.json")
    activation = ACTIVATIONS.get(config.get("activation_function", "torch.nn.modules.linear.Identity"))
    if activation is None:
        raise ConfigError(f"unsupported activation in {folder}: {config.get('activation_function')}")
    tensors = read_safetensors(os.path.join(folder, "model.safetensors"))
    return tensors["linear.weight"].astype(numpy.float32), tensors.get("linear.bias", numpy.zeros(config["out_features"])).astype(numpy.float32), activation


def read_safetensors(path):
    # the safetensors layout: an 8-byte little-endian header length, a JSON header of offsets, then the raw tensors
    with open(path, "rb") as handle:
        (length,) = struct.unpack("<Q", handle.read(8))
        header = json.loads(handle.read(length))
        data = handle.read()
    tensors = {}
    for name, entry in header.items():
        if name == "__metadata__":
            continue
        if entry["dtype"] not in SAFETENSORS_TYPES:
            raise ConfigError(f"unsupported tensor type {entry['dtype']} in {path}")
        first, last = entry["data_offsets"]
        tensors[name] = numpy.frombuffer(data[first:last], dtype=SAFETENSORS_TYPES[entry["dtype"]]).reshape(entry["shape"])
    return tensors
