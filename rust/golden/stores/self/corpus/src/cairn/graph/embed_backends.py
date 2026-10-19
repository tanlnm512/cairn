"""Embedding backend contract and registry for injected transports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Final, Optional, Protocol, Sequence


EmbeddingResult = tuple[list[bytes], int]
EmbedTransport = Callable[[Sequence[str]], EmbeddingResult]
BackendModel = Callable[[str], str]


class EmbeddingBackend(Protocol):
    """Resolve, probe, stamp, and invoke one embedding backend."""


    @property
    def canonical_name(self) -> str:
        """Return the registry-normalized backend name."""
        ...

    @property
    def fallback_name(self) -> Optional[str]:
        """Return the availability fallback, when one exists."""
        ...

    def effective(self) -> str:
        """Return the effective backend name for the configured backend."""
        ...

    def available(self) -> bool:
        """Return whether the backend can embed right now."""
        ...

    def model(self, corpus: str) -> str:
        """Return the model identity used to stamp rows for ``corpus``."""
        ...

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        """Return ``(blobs, dim)`` for ``texts``."""
        ...


@dataclass(frozen=True)
class CallableEmbeddingBackend:
    """Adapter whose resolution and transport behavior is supplied by callables."""

    canonical_name: str
    fallback_name: Optional[str]
    _effective_name: Callable[[], str]
    _is_available: Callable[[], bool]
    _model_for_corpus: BackendModel
    _transport: EmbedTransport

    def effective(self) -> str:
        """Return the effective backend name for the configured backend."""
        return self._effective_name()

    def available(self) -> bool:
        """Return whether the backend can embed right now."""
        return self._is_available()

    def model(self, corpus: str) -> str:
        """Return the model identity used to stamp rows for ``corpus``."""
        return self._model_for_corpus(corpus)

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        """Return ``(blobs, dim)`` for ``texts``."""
        return self._transport(texts)


EMBEDDING_BACKENDS: Final[dict[str, EmbeddingBackend]] = {}
LOCAL_BACKEND_NAME: Final = "local"


def register_embedding_backend(name: str, backend: EmbeddingBackend) -> None:
    """Register ``backend`` under ``name`` in the process-wide registry."""
    EMBEDDING_BACKENDS[name] = backend


def resolve_embedding_backend(name: str) -> EmbeddingBackend:
    """Return the registered backend, treating unknown names as local."""
    return EMBEDDING_BACKENDS.get(name, EMBEDDING_BACKENDS[LOCAL_BACKEND_NAME])


def resolve_effective_backend(name: str) -> str:
    """Return the effective name for a registered backend, else ``name``."""
    if name not in EMBEDDING_BACKENDS:
        return name
    return EMBEDDING_BACKENDS[name].effective()
