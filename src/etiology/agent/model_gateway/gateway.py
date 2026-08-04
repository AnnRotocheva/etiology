from .base import ModelProvider
from .types import ModelRequest, ModelResponse


class NoProviderAvailable(RuntimeError):
    pass


class ModelGateway:
    """Единая точка входа для агентов — они запрашивают tier, никогда конкретного
    провайдера или модель напрямую (docs/architecture.md §2, §10).

    Провайдеры перебираются по порядку списка: первый, поддерживающий tier запроса
    и не упавший с ошибкой, обслуживает запрос. Это даёт fallback между минимум
    двумя AI-провайдерами и open-weights-резервом, когда они появятся — сейчас
    зарегистрирован один провайдер (Claude), остальные добавляются без изменения
    вызывающего кода.
    """

    def __init__(self, providers: list[ModelProvider]):
        if not providers:
            raise ValueError("ModelGateway требует хотя бы один провайдер")
        self._providers = providers

    async def complete(self, request: ModelRequest) -> ModelResponse:
        errors: list[str] = []
        for provider in self._providers:
            if not provider.supports_tier(request.tier):
                continue
            try:
                return await provider.complete(request)
            except Exception as exc:  # noqa: BLE001 — fallback boundary, следующий провайдер получает шанс
                errors.append(f"{provider.name}: {exc}")
        detail = "; ".join(errors) if errors else "ни один провайдер не поддерживает этот tier"
        raise NoProviderAvailable(f"Не удалось обслужить tier={request.tier}: {detail}")
