import asyncio

from mcp.server.fastmcp import FastMCP

from etiology.agent.model_gateway import ModelGateway
from etiology.agent.model_gateway.providers.anthropic_provider import AnthropicProvider
from etiology.config import get_settings
from etiology.platform_core.event_bus import EventPublisher
from etiology.platform_core.mcp_gateway import build_server


def build_app() -> FastMCP:
    """Composition root: собирает реальные зависимости (AnthropicProvider, EventPublisher)
    и отдаёт готовый к запуску MCP Gateway. Единственное место в кодовой базе, где
    конструируются конкретные реализации, а не принимаются через DI-параметры.
    """
    settings = get_settings()
    gateway = ModelGateway([AnthropicProvider(api_key=settings.anthropic_api_key)])
    publisher = EventPublisher()
    return build_server(gateway=gateway, publisher=publisher)


def main() -> None:
    server = build_app()
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
