import contextvars

_current_tenant_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_tenant_id", default=None
)


def set_current_tenant(tenant_id: str) -> None:
    _current_tenant_id.set(tenant_id)


def get_current_tenant() -> str | None:
    return _current_tenant_id.get()
