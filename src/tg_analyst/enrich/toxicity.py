"""Toxicity: insults, obscenity and threats, scored separately.

Negative sentiment is not hostility: in the real group only ~3% of negative-scored
messages are insults; the rest are complaints, frustration or bad news. Swearing among
friends is not hostility either. A multi-label model tells these apart, so conflict
detection can treat insults and threats as hostile and swearing alone as not.

The default model, ``cointegrated/rubert-tiny-toxicity`` (Russian, ~50 MB), outputs
independent probabilities for non-toxic, insult, obscenity, threat and dangerous.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

# A message counts as an insult / obscene / a threat at or above this probability.
TOXIC_AT = 0.5


@dataclass(frozen=True, slots=True)
class ToxicityResult:
    toxicity: float  # the strongest of the three, 0..1
    insult: float
    obscenity: float
    threat: float


class ToxicityBackend(Protocol):
    #: Stored as part of enrichment.model_version; change it to force re-scoring.
    version: str

    def score(self, texts: Sequence[str]) -> list[ToxicityResult]: ...


def result(insult: float, obscenity: float, threat: float) -> ToxicityResult:
    insult, obscenity, threat = (round(float(v), 4) for v in (insult, obscenity, threat))
    return ToxicityResult(max(insult, obscenity, threat), insult, obscenity, threat)


class TransformersToxicity:
    """Multi-label toxicity classifier (needs the ``hf`` extra)."""

    default_model = "cointegrated/rubert-tiny-toxicity"

    def __init__(self, model: str | None = None, batch_size: int = 32, device: int | str = -1):
        try:
            from transformers import pipeline
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError("Toxicity scoring needs: pip install -e '.[hf]'") from exc

        self.model = model or self.default_model
        self.version = f"tox:{self.model}"
        self.batch_size = batch_size
        self._pipe = pipeline(
            "text-classification",
            model=self.model,
            top_k=None,
            function_to_apply="sigmoid",  # independent labels, not a softmax
            truncation=True,
            max_length=256,
            device=device,
        )

    def score(self, texts: Sequence[str]) -> list[ToxicityResult]:
        if not texts:
            return []
        outputs = self._pipe(list(texts), batch_size=self.batch_size)
        return [self._to_result(out) for out in outputs]

    @staticmethod
    def _to_result(scores: list[dict]) -> ToxicityResult:
        probs = {s["label"].lower(): s["score"] for s in scores}
        return result(
            probs.get("insult", 0.0), probs.get("obscenity", 0.0), probs.get("threat", 0.0)
        )


def backends(settings, name: str | None = None):
    """The sentiment backend to use, and the toxicity backend that goes with it.

    Toxicity runs alongside the transformer sentiment backend (production); with VADER
    (English, development) there is none. Set HF_TOXICITY_MODEL to "" to turn it off.
    """
    from tg_analyst.enrich.sentiment import get_backend

    name = name or settings.sentiment_backend
    if name != "hf":
        return get_backend(name), None
    sentiment = get_backend(name, model=settings.hf_sentiment_model)
    toxicity = (
        TransformersToxicity(settings.hf_toxicity_model) if settings.hf_toxicity_model else None
    )
    return sentiment, toxicity
