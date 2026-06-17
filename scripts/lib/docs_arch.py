"""Summary: Deterministic renderers for the generated documentation — the OpenAPI
endpoint inventory, the AppSettings config-key table, the package module-map, and
the ERD (introspected from the SQLAlchemy models) — plus a helper to splice generated
text into the AUTOGEN regions of a Markdown document. All output is stable for a given
codebase (sorted, no timestamps) so `make docs-check` can diff it byte-for-byte.

Key classes:
- (none)

Key functions:
- render_endpoints: Markdown table of the app's HTTP routes.
- render_config_keys: Markdown table of AppSettings fields.
- render_module_map: Mermaid graph of the package layering.
- render_erd: Mermaid ER diagram built from Base.metadata (entities + FK relationships).
- replace_region: replace the body of a single <!-- AUTOGEN:name --> region.

Notes:
- render_erd reflects the live ORM metadata, so the diagram (and `make docs-check`) tracks
  the §9 schema automatically; PK/FK markers and short type tokens keep it readable.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any

from fastapi.routing import APIRoute

from fraudlens_backend.db.models import Base

if TYPE_CHECKING:
    from fastapi import FastAPI
    from pydantic_settings import BaseSettings
    from sqlalchemy.sql.schema import Table
    from sqlalchemy.types import TypeEngine

_DOCS_PATHS = ("/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect")


def _iter_api_routes(routes: Iterable[Any], prefix: str = "") -> Iterator[tuple[str, APIRoute]]:
    """Yield APIRoutes recursively across FastAPI's nested included-router structure."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield f"{prefix}{route.path}", route
            continue
        context = getattr(route, "include_context", None)
        router = getattr(route, "original_router", None)
        if context is not None and router is not None:
            yield from _iter_api_routes(router.routes, f"{prefix}{context.prefix}")


def render_endpoints(app: FastAPI) -> str:
    """Return a Markdown table of the application's HTTP routes, sorted by path."""
    rows: list[tuple[str, str, str]] = []
    for path, route in _iter_api_routes(app.routes):
        if path in _DOCS_PATHS:
            continue
        methods = sorted((route.methods or set()) - {"HEAD", "OPTIONS"})
        for method in methods:
            rows.append((path, method, route.name))
    rows.sort()
    lines = ["| Method | Path | Handler |", "| --- | --- | --- |"]
    lines += [f"| {method} | `{path}` | `{name}` |" for path, method, name in rows]
    return "\n".join(lines)


def render_config_keys(settings_cls: type[BaseSettings]) -> str:
    """Return a Markdown table of the settings model's fields (key/type/default/desc)."""
    lines = ["| Key | Type | Default | Description |", "| --- | --- | --- | --- |"]
    for name, field in settings_cls.model_fields.items():
        annotation = field.annotation
        type_name = getattr(annotation, "__name__", str(annotation)).replace("typing.", "")
        if field.is_required():
            default = "_required_"
        elif field.default_factory is not None:
            # Resolve default_factory (list/dict defaults) so the table shows the real
            # value (e.g. `[]`) instead of pydantic's internal sentinel.
            default = f"`{field.get_default(call_default_factory=True)!r}`"
        else:
            default = f"`{field.default!r}`"
        description = field.description or ""
        lines.append(f"| `{name}` | `{type_name}` | {default} | {description} |")
    return "\n".join(lines)


def render_module_map() -> str:
    """Return a Mermaid graph of the internal package layering (rule: no cycles)."""
    return "\n".join(
        [
            "```mermaid",
            "graph TD",
            '    core["fraudlens-core<br/>(domain types, tenancy)"]',
            '    llm["fraudlens-llm<br/>(catalog client, guardrails)"]',
            '    ml["fraudlens-ml<br/>(scoring, RAG, SAR protocols)"]',
            '    backend["fraudlens-backend<br/>(FastAPI service)"]',
            "    ml --> core",
            "    backend --> core",
            "    backend -.may use.-> llm",
            "    backend -.may use.-> ml",
            "    ml -. never imports .-x llm",
            "```",
        ]
    )


def _erd_type(col_type: TypeEngine[object]) -> str:
    """Return a short, single-token type label for an ER attribute (e.g. 'uuid')."""
    return type(col_type).__name__.lower()


def _erd_entity(table: Table) -> list[str]:
    """Render one Mermaid ER entity block: attributes with PK/FK markers, sorted stably."""
    pk_cols = {col.name for col in table.primary_key.columns}
    fk_cols = {fk.parent.name for fk in table.foreign_keys}
    # PK columns first, then the rest alphabetically — stable regardless of definition order.
    ordered = sorted(table.columns, key=lambda c: (c.name not in pk_cols, c.name))
    lines = [f"    {table.name} {{"]
    for col in ordered:
        markers = [m for m, on in (("PK", col.name in pk_cols), ("FK", col.name in fk_cols)) if on]
        suffix = f" {' '.join(markers)}" if markers else ""
        lines.append(f"        {_erd_type(col.type)} {col.name}{suffix}")
    lines.append("    }")
    return lines


def render_erd() -> str:
    """Return the Mermaid ER diagram built from the live ORM metadata (entities + FKs)."""
    metadata = Base.metadata
    lines = ["```mermaid", "erDiagram"]
    for name in sorted(metadata.tables):
        lines.extend(_erd_entity(metadata.tables[name]))
    relationships: set[tuple[str, str, str]] = set()
    for name in sorted(metadata.tables):
        for fk in metadata.tables[name].foreign_keys:
            relationships.add((fk.column.table.name, name, fk.parent.name))
    for parent, child, column in sorted(relationships):
        lines.append(f'    {parent} ||--o{{ {child} : "{column}"')
    lines.append("```")
    return "\n".join(lines)


def replace_region(text: str, name: str, body: str) -> str:
    """Replace the content between <!-- AUTOGEN:name --> and <!-- /AUTOGEN:name -->."""
    pattern = re.compile(
        rf"(<!-- AUTOGEN:{re.escape(name)} -->\n).*?(\n<!-- /AUTOGEN:{re.escape(name)} -->)",
        re.DOTALL,
    )
    if not pattern.search(text):
        raise ValueError(f"AUTOGEN region '{name}' not found")
    return pattern.sub(lambda m: f"{m.group(1)}{body}{m.group(2)}", text)
