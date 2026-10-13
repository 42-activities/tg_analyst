"""Pluggable sentiment backends producing a polarity score in [-1, 1] and a label."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

POSITIVE, NEUTRAL, NEGATIVE = "positive", "neutral", "negative"


@dataclass(frozen=True, slots=True)
class SentimentResult:
    score: float  # -1.0 (negative) .. +1.0 (positive)
    label: str


class SentimentBackend(Protocol):
    #: Stored as part of enrichment.model_version; change it to force re-scoring.
    version: str

    def score(self, texts: Sequence[str]) -> list[SentimentResult]: ...


class VaderBackend:
    """Fast lexicon-based baseline. English only: other languages score as neutral."""

    version = "vader-3.3"
    threshold = 0.05  # VADER authors' recommended neutral band

    def __init__(self) -> None:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

        self._analyzer = SentimentIntensityAnalyzer()

    def score(self, texts: Sequence[str]) -> list[SentimentResult]:
        results = []
        for text in texts:
            compound = self._analyzer.polarity_scores(text)["compound"]
            if compound >= self.threshold:
                label = POSITIVE
            elif compound <= -self.threshold:
                label = NEGATIVE
            else:
                label = NEUTRAL
            results.append(SentimentResult(round(compound, 4), label))
        return results


class TransformersBackend:
    """Multilingual transformer classifier (needs the ``hf`` extra).

    The score is P(positive) - P(negative), so it is comparable with VADER's range.
    """

    default_model = "cardiffnlp/twitter-xlm-roberta-base-sentiment"

    def __init__(self, model: str | None = None, batch_size: int = 32, device: int | str = -1):
        try:
            from transformers import pipeline
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError("The 'hf' sentiment backend needs: pip install -e '.[hf]'") from exc

        self.model = model or self.default_model
        self.version = f"hf:{self.model}"
        self.batch_size = batch_size
        self._pipe = pipeline(
            "text-classification",
            model=self.model,
            top_k=None,
            truncation=True,
            max_length=256,
            device=device,
        )

    def score(self, texts: Sequence[str]) -> list[SentimentResult]:
        if not texts:
            return []
        outputs = self._pipe(list(texts), batch_size=self.batch_size)
        return [self._to_result(out) for out in outputs]

    @staticmethod
    def _to_result(scores: list[dict]) -> SentimentResult:
        probs = {s["label"].lower(): s["score"] for s in scores}
        # Some checkpoints use LABEL_0/1/2 for negative/neutral/positive.
        pos = probs.get(POSITIVE, probs.get("label_2", 0.0))
        neg = probs.get(NEGATIVE, probs.get("label_0", 0.0))
        neu = probs.get(NEUTRAL, probs.get("label_1", 0.0))
        label = max(((pos, POSITIVE), (neu, NEUTRAL), (neg, NEGATIVE)))[1]
        return SentimentResult(round(pos - neg, 4), label)


BACKENDS = {"vader": VaderBackend, "hf": TransformersBackend}


def get_backend(name: str, **kwargs) -> SentimentBackend:
    if name not in BACKENDS:
        raise ValueError(f"Unknown sentiment backend {name!r}; choose from {sorted(BACKENDS)}")
    return BACKENDS[name](**kwargs)
