from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import entry_points
from typing import Any

from cerno.config import CernoConfigError, Settings

IngestCleaner = Callable[[dict[str, Any]], dict[str, Any]]
RuleCheck = Callable[[Mapping[str, Any]], Iterable[str]]


@dataclass(frozen=True)
class ClientModule:
    name: str
    ingest_cleaners: tuple[IngestCleaner, ...] = ()
    rule_checks: tuple[RuleCheck, ...] = ()


class ClientModuleRegistry:
    def __init__(self) -> None:
        self._modules: dict[str, ClientModule] = {}

    def register(self, module: ClientModule) -> None:
        key = module.name.strip()
        if not key:
            raise ValueError("client module name cannot be empty")
        if key in self._modules:
            raise ValueError(f"client module already registered: {key}")
        self._modules[key] = module

    def modules(self) -> tuple[ClientModule, ...]:
        return tuple(self._modules.values())

    def ingest_cleaners(self) -> tuple[IngestCleaner, ...]:
        return tuple(
            cleaner
            for module in self._modules.values()
            for cleaner in module.ingest_cleaners
        )

    def rule_checks(self) -> tuple[RuleCheck, ...]:
        return tuple(check for module in self._modules.values() for check in module.rule_checks)


_registry = ClientModuleRegistry()


def get_client_module_registry() -> ClientModuleRegistry:
    return _registry


def register_client_module(module: ClientModule) -> None:
    _registry.register(module)


def registered_client_modules() -> tuple[ClientModule, ...]:
    return _registry.modules()


def configure_client_modules(settings: Settings) -> ClientModuleRegistry:
    global _registry
    registry = ClientModuleRegistry()
    for entry_point in entry_points(group=settings.client_modules.entry_point_group):
        _call_register(entry_point.load(), registry)
    for spec in settings.client_modules.enabled:
        _call_register(_load_register_callable(spec), registry)
    _registry = registry
    return registry


def _load_register_callable(spec: str) -> Any:
    module_name, separator, attr_path = spec.partition(":")
    if not separator or not module_name.strip() or not attr_path.strip():
        raise CernoConfigError(
            f"Invalid modules.enabled entry {spec!r}: expected 'module.path:register'"
        )
    target: Any = import_module(module_name.strip())
    for attr in attr_path.split("."):
        target = getattr(target, attr)
    return target


def _call_register(target: Any, registry: ClientModuleRegistry) -> None:
    if not callable(target):
        raise CernoConfigError("Client module entry point must be callable")
    result = target(registry)
    if isinstance(result, ClientModule):
        registry.register(result)
