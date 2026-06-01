from __future__ import annotations

from collections.abc import Callable

from cerno.config import CernoConfigError, Settings
from cerno.providers.openai import OpenAIEmbeddingProvider, OpenAIOCREngine
from cerno.providers.protocols import EmbeddingProvider, OCREngine

OCRFactory = Callable[[Settings], OCREngine]
EmbeddingFactory = Callable[[Settings], EmbeddingProvider]

_OCR_ENGINES: dict[str, OCRFactory] = {}
_EMBEDDING_PROVIDERS: dict[str, EmbeddingFactory] = {}


def register_ocr_engine(name: str, factory: OCRFactory) -> None:
    _OCR_ENGINES[_normalize_key(name)] = factory


def register_embedding_provider(name: str, factory: EmbeddingFactory) -> None:
    _EMBEDDING_PROVIDERS[_normalize_key(name)] = factory


def resolve_ocr_engine(settings: Settings) -> OCREngine:
    key = _normalize_key(settings.processing.ocr.engine)
    factory = _OCR_ENGINES.get(key)
    if factory is None:
        raise CernoConfigError(f"Unknown OCR engine configured: {key!r}")
    return factory(settings)


def resolve_embedding_provider(settings: Settings) -> EmbeddingProvider:
    key = _normalize_key(settings.processing.embedding.provider)
    factory = _EMBEDDING_PROVIDERS.get(key)
    if factory is None:
        raise CernoConfigError(f"Unknown embedding provider configured: {key!r}")
    return factory(settings)


def available_ocr_engines() -> tuple[str, ...]:
    return tuple(sorted(_OCR_ENGINES))


def available_embedding_providers() -> tuple[str, ...]:
    return tuple(sorted(_EMBEDDING_PROVIDERS))


def _normalize_key(value: str) -> str:
    return value.strip().lower()


def _register_defaults() -> None:
    register_ocr_engine(
        "openai",
        lambda settings: OpenAIOCREngine(settings=settings, model=settings.processing.ocr.model),
    )
    register_embedding_provider(
        "openai",
        lambda settings: OpenAIEmbeddingProvider(
            settings=settings,
            model=settings.processing.embedding.model,
            dimension=settings.processing.embedding.dimension,
        ),
    )


_register_defaults()
