"""Reconcile native per-source project identifiers to registry slugs.

Each spend source names projects differently and none use the registry slug:
  - Claude: cwd-derived names (ExampleShop, ExampleNews)
  - GCP:    project ids (example-shop-prod, example-news-prod)
  - 3rd-party: account ids (acct_..., proj_...)

resolve(source, native_id) maps any of them to a config/projects.yaml slug via:
  1. exact match in config/cost-sources.yaml mappings[source][native_id]
  2. heuristic: strip a trailing -prod/-dev/-staging + case-insensitive match
     against registry slugs and their aliases
  3. unmapped_bucket fallback (never drops the row)

validate_mappings() returns warnings for any configured mapping whose target slug
isn't in the registry — surfaced in /api/costs to catch typos.
"""

from __future__ import annotations

from agentos.core import config

_ENV_SUFFIXES = ("-prod", "-dev", "-staging")


def _unmapped_bucket() -> str:
    return config.cost_sources().get("unmapped_bucket", "unmapped")


def _alias_index() -> dict[str, str]:
    """Lowercased {slug-or-alias -> slug} index built from projects.yaml."""
    index: dict[str, str] = {}
    for slug, cfg in config.projects().items():
        index[slug.lower()] = slug
        for alias in (cfg or {}).get("aliases", []) or []:
            index[str(alias).lower()] = slug
    return index


def resolve(source: str, native_id: str | None) -> str:
    """Return the registry slug for a native id, or the unmapped bucket."""
    bucket = _unmapped_bucket()
    if not native_id:
        return bucket

    mappings = config.cost_sources().get("mappings", {}) or {}
    source_map = mappings.get(source, {}) or {}

    # 1. exact mapping
    if native_id in source_map:
        return source_map[native_id]

    # 2. heuristic fallback against registry slugs + aliases
    index = _alias_index()
    candidate = native_id.lower()
    if candidate in index:
        return index[candidate]
    for suffix in _ENV_SUFFIXES:
        if candidate.endswith(suffix):
            stripped = candidate[: -len(suffix)]
            if stripped in index:
                return index[stripped]

    # 3. unmapped
    return bucket


def validate_mappings() -> list[str]:
    """Warnings for configured mapping targets that aren't registry slugs."""
    warnings: list[str] = []
    bucket = _unmapped_bucket()
    slugs = set(config.projects().keys())
    mappings = config.cost_sources().get("mappings", {}) or {}
    for source, source_map in mappings.items():
        for native_id, slug in (source_map or {}).items():
            if slug == bucket:
                continue
            if slug not in slugs:
                warnings.append(
                    f"mapping {source}:{native_id} -> '{slug}' is not a registry slug"
                )
    return warnings
