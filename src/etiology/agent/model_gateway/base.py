from abc import ABC, abstractmethod

from .types import ModelRequest, ModelResponse, ModelTier


class ModelProvider(ABC):
    """Интерфейс, за которым скрывается конкретный AI-провайдер.

    Обязательна провайдер-независимость (docs/architecture.md §2, §10) — вызывающий
    код работает только через ModelGateway/ModelProvider, никогда не через SDK
    провайдера напрямую.
    """

    name: str

    @abstractmethod
    def supports_tier(self, tier: ModelTier) -> bool: ...

    @abstractmethod
    async def complete(self, request: ModelRequest) -> ModelResponse: ...
