"""Sentence embeddings of conversation threads, for context topics.

Vectors are L2-normalized float32, so a dot product is the cosine similarity.
"""

import hashlib
import re
from collections.abc import Sequence
from typing import Protocol

import numpy as np

# Bump when thread building or thread text changes in a way that should re-embed.
THREADS_VERSION = "threads-1"

_WORD = re.compile(r"\w{3,}")


class EmbeddingBackend(Protocol):
    version: str

    def embed(self, texts: Sequence[str]) -> np.ndarray: ...


class TransformersEmbedding:
    """Mean-pooled transformer embeddings (needs the ``hf`` extra).

    ``rubert-tiny2`` embeds ~20 threads/s on the production arm64 CPU (docs/CONTEXT_TOPICS.md).
    e5 models expect a ``"passage: "`` prefix, which is added automatically.
    """

    default_model = "cointegrated/rubert-tiny2"

    def __init__(self, model: str | None = None, batch_size: int = 16, max_length: int = 512):
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError("Embeddings need the hf extra: pip install -e '.[hf]'") from exc

        self.model = model or self.default_model
        self.version = f"hf:{self.model}|{THREADS_VERSION}"
        self.batch_size = batch_size
        self.max_length = max_length
        self.prefix = "passage: " if "e5" in self.model.lower() else ""
        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(self.model)
        self._model = AutoModel.from_pretrained(self.model).eval()

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        torch = self._torch
        vectors = []
        with torch.inference_mode():
            for i in range(0, len(texts), self.batch_size):
                batch = self._tokenizer(
                    [self.prefix + t for t in texts[i : i + self.batch_size]],
                    padding=True,
                    truncation=True,
                    max_length=self.max_length,
                    return_tensors="pt",
                )
                hidden = self._model(**batch).last_hidden_state
                mask = batch["attention_mask"].unsqueeze(-1).to(hidden.dtype)
                mean = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1)
                vectors.append(torch.nn.functional.normalize(mean, dim=-1).numpy())
        if not vectors:
            return np.zeros((0, 0), dtype=np.float32)
        return np.vstack(vectors).astype(np.float32)


class FakeEmbedding:
    """Deterministic hashed bag of words, for tests: threads sharing words are similar."""

    def __init__(self, dimensions: int = 64) -> None:
        self.dimensions = dimensions
        self.version = f"fake-{dimensions}|{THREADS_VERSION}"

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dimensions), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in _WORD.findall(text.lower()):
                if word == "user":  # the @mention placeholder carries no topic
                    continue
                bucket = int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dimensions
                out[row, bucket] += 1
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)
