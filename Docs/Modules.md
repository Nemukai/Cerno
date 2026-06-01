# Cerno Client Modules

Cerno core stays general-purpose. Client-specific behavior is registered through a lightweight module hook instead of being added to ingest, discovery, or API code directly.

## Contract

A client module exposes a callable that accepts `ClientModuleRegistry` and registers one or more `ClientModule` objects:

```python
from cerno.client_modules import ClientModule, ClientModuleRegistry


def register(registry: ClientModuleRegistry) -> None:
    registry.register(ClientModule(name="client-name"))
```

`ClientModule` currently exposes neutral hook collections for later blocks:

- `ingest_cleaners`: callables shaped as `dict[str, Any] -> dict[str, Any]`
- `rule_checks`: callables shaped as `Mapping[str, Any] -> Iterable[str]`

Core ships with no client modules registered.

## Configuration

Enable config-based modules in `~/.cerno/config.toml`:

```toml
[modules]
enabled = ["client_package.cerno_module:register"]
```

Installed packages can also expose entry points under the default group:

```toml
[project.entry-points."cerno.client_modules"]
client_name = "client_package.cerno_module:register"
```

Core imports configured modules at API startup. A bad module spec fails startup so custom behavior cannot be silently skipped.
