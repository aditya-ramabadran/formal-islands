"""External Mathlib search helpers for Loogle and LeanSearch.

This module stays outside Lean files entirely.  It is used as an external
retrieval helper and as the backing implementation for the optional
``formal-islands-search`` CLI, which gives the agentic worker a local shell
command for one or two highly targeted follow-up searches.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


USER_AGENT = "formal-islands-search/0.1"
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_TOP_K = 4
DEFAULT_MAX_QUERIES = 2

STOPWORDS = {
    "a",
    "an",
    "and",
    "apply",
    "be",
    "by",
    "can",
    "claim",
    "conclude",
    "derive",
    "do",
    "does",
    "each",
    "for",
    "from",
    "give",
    "hence",
    "if",
    "in",
    "is",
    "it",
    "let",
    "means",
    "node",
    "of",
    "on",
    "or",
    "prove",
    "then",
    "the",
    "to",
    "use",
    "using",
    "with",
    "will",
}

MATH_KEYWORDS = {
    "boundary",
    "brownian",
    "convex",
    "convexity",
    "convexon",
    "deriv",
    "derivative",
    "entropy",
    "estimate",
    "exp",
    "function",
    "gradient",
    "identity",
    "integral",
    "inequality",
    "laplacian",
    "limit",
    "log",
    "measure",
    "norm",
    "pinsker",
    "probability",
    "quadratic",
    "remainder",
    "sqrt",
    "sobolev",
    "sum",
    "taylor",
    "variance",
}

LEAN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_'.]*(?:\.[A-Za-z0-9_'.]+)*")


@dataclass(frozen=True)
class MathlibSearchQuery:
    provider: str
    query: str
    reason: str


@dataclass(frozen=True)
class MathlibSearchHit:
    provider: str
    query: str
    name: str
    module: str | None = None
    statement: str | None = None
    doc: str | None = None
    url: str | None = None
    score: float = 0.0
    distance: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MathlibSearchQueryResult:
    spec: MathlibSearchQuery
    hits: list[MathlibSearchHit]
    error: str | None = None


@dataclass(frozen=True)
class MathlibSearchBundle:
    query_results: list[MathlibSearchQueryResult]
    combined_hits: list[MathlibSearchHit]


def build_mathlib_search_bundle(
    *,
    theorem_title: str,
    theorem_statement: str,
    node_title: str,
    node_statement: str,
    node_proof_text: str,
    coverage_summary: str | None = None,
    coverage_components: Iterable[tuple[str, str]] | None = None,
    max_queries: int = DEFAULT_MAX_QUERIES,
    top_k: int = DEFAULT_TOP_K,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> MathlibSearchBundle:
    """Run a small, external Mathlib retrieval pass."""

    queries = suggest_mathlib_search_queries(
        theorem_title=theorem_title,
        theorem_statement=theorem_statement,
        node_title=node_title,
        node_statement=node_statement,
        node_proof_text=node_proof_text,
        coverage_summary=coverage_summary,
        coverage_components=coverage_components,
        max_queries=max_queries,
    )
    query_results_by_index: list[tuple[int, MathlibSearchQueryResult]] = []
    all_hits: list[MathlibSearchHit] = []

    with ThreadPoolExecutor(max_workers=max(1, len(queries))) as executor:
        future_to_index: dict[Any, int] = {}
        future_to_spec: dict[Any, MathlibSearchQuery] = {}
        for index, spec in enumerate(queries):
            future = executor.submit(
                _search_provider,
                spec.provider,
                spec.query,
                top_k=top_k,
                timeout_seconds=timeout_seconds,
            )
            future_to_index[future] = index
            future_to_spec[future] = spec
        for future in as_completed(future_to_spec):
            spec = future_to_spec[future]
            try:
                hits = future.result()
                query_results_by_index.append(
                    (future_to_index[future], MathlibSearchQueryResult(spec=spec, hits=hits))
                )
                all_hits.extend(hits)
            except Exception as exc:  # pragma: no cover - defensive
                query_results_by_index.append(
                    (
                        future_to_index[future],
                        MathlibSearchQueryResult(spec=spec, hits=[], error=str(exc)),
                    )
                )

    query_results = [result for _, result in sorted(query_results_by_index, key=lambda item: item[0])]
    combined_hits = _dedupe_and_rank_hits(all_hits)
    return MathlibSearchBundle(query_results=query_results, combined_hits=combined_hits)


def suggest_mathlib_search_queries(
    *,
    theorem_title: str,
    theorem_statement: str,
    node_title: str,
    node_statement: str,
    node_proof_text: str,
    coverage_summary: str | None = None,
    coverage_components: Iterable[tuple[str, str]] | None = None,
    max_queries: int = DEFAULT_MAX_QUERIES,
) -> list[MathlibSearchQuery]:
    """Suggest a few targeted search queries from the node context."""

    coverage_components_list = list(coverage_components or [])
    context_text = "\n".join(
        [
            theorem_title,
            theorem_statement,
            node_title,
            node_statement,
            node_proof_text,
            coverage_summary or "",
            " ".join(component_text for _, component_text in coverage_components_list),
        ]
    )
    exact_terms = _select_exact_terms(context_text, limit=5)
    natural_terms = _select_natural_terms(context_text, limit=12)

    queries: list[MathlibSearchQuery] = []
    loogle_query = _format_loogle_query(exact_terms, natural_terms)
    if loogle_query:
        queries.append(
            MathlibSearchQuery(
                provider="loogle",
                query=loogle_query,
                reason="Exact theorem-shape search over the main identifiers and symbols.",
            )
        )

    leansearch_query = _format_natural_query(
        theorem_title=theorem_title,
        node_title=node_title,
        node_statement=node_statement,
        node_proof_text=node_proof_text,
        coverage_summary=coverage_summary,
        coverage_components=coverage_components_list,
        natural_terms=natural_terms,
    )
    if leansearch_query:
        queries.append(
            MathlibSearchQuery(
                provider="leansearch",
                query=leansearch_query,
                reason="Natural-language search for the likely theorem family and proof shape.",
            )
        )

    if max_queries > len(queries):
        fallback_terms = _select_natural_terms(
            "\n".join(
            [
                coverage_summary or "",
                " ".join(text for _, text in coverage_components_list),
            ]
        ),
            limit=8,
        )
        fallback_query = _format_natural_query(
            theorem_title=theorem_title,
            node_title=node_title,
            node_statement=" ".join(fallback_terms) or node_statement,
            node_proof_text="",
            coverage_summary=None,
            coverage_components=None,
            natural_terms=fallback_terms,
        )
        if fallback_query and fallback_query not in {spec.query for spec in queries}:
            queries.append(
                MathlibSearchQuery(
                    provider="loogle",
                    query=fallback_query,
                    reason=(
                        "Secondary exact-shape query seeded from the coverage sketch and parent proof."
                    ),
                )
            )

    return queries[:max_queries]


def format_mathlib_search_bundle(
    bundle: MathlibSearchBundle,
    *,
    max_queries: int = 2,
    max_hits_per_query: int = 3,
    include_helper_hints: bool = False,
) -> str:
    """Render a compact prompt section from a search bundle."""

    parts = ["Mathlib search results:"]
    if not bundle.query_results:
        parts.append("No search queries were run.")
    else:
        for result in bundle.query_results[:max_queries]:
            parts.append(
                f"- {result.spec.provider}: `{result.spec.query}`\n"
                f"  reason: {result.spec.reason}"
            )
            if result.error:
                parts.append(f"  search error: {result.error}")
                continue
            if not result.hits:
                parts.append("  no hits")
                continue
            for hit in result.hits[:max_hits_per_query]:
                parts.append(f"  - {format_mathlib_search_hit(hit)}")

    if bundle.combined_hits:
        parts.append("Best combined hits:")
        for hit in bundle.combined_hits[:max_hits_per_query]:
            parts.append(f"- {format_mathlib_search_hit(hit)}")

    if include_helper_hints:
        parts.append(
            (
                "Search policy: use the bundled hits first. If you truly need more, "
                "use `formal-islands-search` for at most 2 additional highly targeted "
                "searches total, preferably one exact Loogle-shaped query and one "
                "LeanSearch natural-language query."
            )
        )

    return "\n".join(parts)


def format_mathlib_search_hit(hit: MathlibSearchHit, *, max_len: int = 180) -> str:
    pieces = [hit.name]
    if hit.module:
        pieces.append(hit.module)
    if hit.statement:
        pieces.append(_truncate_text(hit.statement, max_len))
    elif hit.doc:
        pieces.append(_truncate_text(hit.doc, max_len))
    return " — ".join(piece for piece in pieces if piece)


def search_loogle(
    query: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[MathlibSearchHit]:
    url = "https://loogle.lean-lang.org/json?" + urllib.parse.urlencode({"q": query})
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.load(response)
    except Exception as exc:  # pragma: no cover - network failures are handled upstream
        raise RuntimeError(f"Loogle query failed: {exc}") from exc

    hits = payload.get("hits", []) if isinstance(payload, dict) else []
    parsed: list[MathlibSearchHit] = []
    for raw in hits[:top_k]:
        if not isinstance(raw, dict):
            continue
        name = _stringify_name(raw.get("name")) or _stringify_name(raw.get("declname")) or ""
        module = _stringify_name(raw.get("module"))
        statement = _first_nonempty_string(raw.get("type"), raw.get("doc"), raw.get("statement"))
        doc = _first_nonempty_string(raw.get("doc"), raw.get("description"))
        parsed.append(
            MathlibSearchHit(
                provider="loogle",
                query=query,
                name=name or "(unnamed)",
                module=module,
                statement=statement,
                doc=doc,
                url=_first_nonempty_string(raw.get("url"), raw.get("doc_url")),
                score=_score_hit(query, name=name, module=module, statement=statement, doc=doc),
                raw=raw,
            )
        )
    return parsed


def search_leansearch(
    query: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[MathlibSearchHit]:
    body = json.dumps({"query": [query], "num_results": top_k}).encode("utf-8")
    request = urllib.request.Request(
        "https://leansearch.net/search",
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.load(response)
    except Exception as exc:  # pragma: no cover - network failures are handled upstream
        raise RuntimeError(f"LeanSearch query failed: {exc}") from exc

    raw_hits = _extract_leansearch_hits(payload)
    parsed: list[MathlibSearchHit] = []
    for raw in raw_hits[:top_k]:
        if not isinstance(raw, dict):
            continue
        result = raw.get("result") if isinstance(raw.get("result"), dict) else raw
        if not isinstance(result, dict):
            continue
        name = _stringify_name(result.get("name")) or _stringify_name(
            result.get("informal_name")
        )
        module = _stringify_name(result.get("module_name"))
        statement = _first_nonempty_string(
            result.get("signature"),
            result.get("type"),
            result.get("value"),
            result.get("informal_description"),
        )
        doc = _first_nonempty_string(result.get("docstring"), result.get("informal_description"))
        distance = raw.get("distance")
        parsed.append(
            MathlibSearchHit(
                provider="leansearch",
                query=query,
                name=name or "(unnamed)",
                module=module,
                statement=statement,
                doc=doc,
                url=_first_nonempty_string(result.get("doc_url"), raw.get("url")),
                score=_score_leansearch_hit(distance, query, name=name, module=module, statement=statement, doc=doc),
                distance=distance if isinstance(distance, (int, float)) else None,
                raw=raw,
            )
        )
    return parsed


def search_mathlib(
    query: str,
    *,
    provider: str = "both",
    top_k: int = DEFAULT_TOP_K,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[MathlibSearchHit]:
    """Run a single search query against one or both providers."""

    providers = _normalize_provider_choice(provider)
    results: list[MathlibSearchHit] = []
    for provider_name in providers:
        results.extend(
            _search_provider(
                provider_name,
                query,
                top_k=top_k,
                timeout_seconds=timeout_seconds,
            )
        )
    return _dedupe_and_rank_hits(results)


def _search_provider(
    provider: str,
    query: str,
    *,
    top_k: int,
    timeout_seconds: float,
) -> list[MathlibSearchHit]:
    if provider == "loogle":
        return search_loogle(query, top_k=top_k, timeout_seconds=timeout_seconds)
    if provider == "leansearch":
        return search_leansearch(query, top_k=top_k, timeout_seconds=timeout_seconds)
    raise ValueError(f"unsupported provider: {provider}")


def _normalize_provider_choice(provider: str) -> list[str]:
    normalized = provider.lower().strip()
    if normalized in {"both", "all"}:
        return ["loogle", "leansearch"]
    if normalized not in {"loogle", "leansearch"}:
        raise ValueError("provider must be one of: loogle, leansearch, both")
    return [normalized]


def _dedupe_and_rank_hits(hits: Iterable[MathlibSearchHit]) -> list[MathlibSearchHit]:
    best: dict[tuple[str, str | None, str | None], MathlibSearchHit] = {}
    for hit in hits:
        key = (hit.name, hit.module, hit.statement)
        current = best.get(key)
        if current is None or _rank_hit(hit) < _rank_hit(current):
            best[key] = hit
    return sorted(best.values(), key=_rank_hit)


def _rank_hit(hit: MathlibSearchHit) -> tuple[int, float, str, str]:
    provider_rank = 0 if hit.provider == "loogle" else 1
    return (provider_rank, -hit.score, hit.name, hit.module or "")


def _score_hit(
    query: str,
    *,
    name: str | None,
    module: str | None,
    statement: str | None,
    doc: str | None,
) -> float:
    query_terms = _search_terms(query)
    haystack = " ".join(part for part in [name, module, statement, doc] if part).lower()
    score = 0.0
    for term in query_terms:
        if term and term in haystack:
            score += 1.0
    if name and query.lower() in name.lower():
        score += 2.0
    if module and query.lower() in module.lower():
        score += 1.0
    return score


def _score_leansearch_hit(
    distance: Any,
    query: str,
    *,
    name: str | None,
    module: str | None,
    statement: str | None,
    doc: str | None,
) -> float:
    if isinstance(distance, (int, float)):
        return 1000.0 - float(distance)
    return _score_hit(query, name=name, module=module, statement=statement, doc=doc)


def _search_terms(text: str) -> list[str]:
    return [term.lower() for term in LEAN_TOKEN_RE.findall(text) if term.lower() not in STOPWORDS]


def _select_exact_terms(text: str, *, limit: int) -> list[str]:
    scored: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for index, token in enumerate(LEAN_TOKEN_RE.findall(text)):
        canonical = token.strip("._")
        if not canonical:
            continue
        lowered = canonical.lower()
        if lowered in STOPWORDS:
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        score = 0
        if any(ch.isupper() for ch in canonical):
            score += 3
        if "." in canonical or "_" in canonical or "'" in canonical or any(ch.isdigit() for ch in canonical):
            score += 3
        if lowered in MATH_KEYWORDS:
            score += 2
        if len(canonical) > 5:
            score += 1
        if len(canonical) <= 2:
            score -= 2
        scored.append((-score, index, canonical))
    scored.sort()
    return [token for _, _, token in scored[:limit]]


def _select_natural_terms(text: str, *, limit: int) -> list[str]:
    scored: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for index, token in enumerate(LEAN_TOKEN_RE.findall(text)):
        canonical = token.strip("._")
        if not canonical:
            continue
        lowered = canonical.lower()
        if lowered in STOPWORDS or lowered in seen:
            continue
        seen.add(lowered)
        score = 0
        if lowered in MATH_KEYWORDS:
            score += 3
        if any(ch.isupper() for ch in canonical):
            score += 2
        if "." in canonical or "_" in canonical or any(ch.isdigit() for ch in canonical):
            score += 2
        if len(canonical) > 6:
            score += 1
        scored.append((-score, index, canonical))
    scored.sort()
    return [token for _, _, token in scored[:limit]]


def _format_loogle_query(exact_terms: list[str], natural_terms: list[str]) -> str:
    terms = exact_terms[:4] or natural_terms[:4]
    return ", ".join(terms)


def _format_natural_query(
    *,
    theorem_title: str,
    node_title: str,
    node_statement: str,
    node_proof_text: str,
    coverage_summary: str | None,
    coverage_components: Iterable[tuple[str, str]] | None,
    natural_terms: list[str],
) -> str:
    coverage_components_list = list(coverage_components or [])
    base_text = " ".join(
        part
        for part in [
            theorem_title,
            node_title,
            coverage_summary or "",
            node_statement,
            node_proof_text,
            " ".join(text for _, text in coverage_components_list),
        ]
        if part
    )
    words = [word for word in re.split(r"[^A-Za-z0-9_'.]+", base_text) if word]
    cleaned: list[str] = []
    seen: set[str] = set()
    for word in words:
        lowered = word.lower()
        if lowered in STOPWORDS or lowered in seen:
            continue
        if lowered in {"by", "let", "show", "use", "then"}:
            continue
        seen.add(lowered)
        cleaned.append(word)
    if natural_terms:
        cleaned = natural_terms[:6] + [word for word in cleaned if word.lower() not in {t.lower() for t in natural_terms}]
    return " ".join(cleaned[:12]).strip()


def _extract_leansearch_hits(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        if payload and isinstance(payload[0], list):
            return [item for item in payload[0] if isinstance(item, dict)]
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("results", "hits", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _stringify_name(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        parts = [_stringify_name(item) for item in value]
        parts = [part for part in parts if part]
        return ".".join(parts) if parts else None
    return str(value).strip() or None


def _first_nonempty_string(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
        else:
            text = str(value).strip()
            if text:
                return text
    return None


def _truncate_text(text: str, max_len: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_len:
        return normalized
    return normalized[: max_len - 1].rstrip() + "…"


def main(argv: list[str] | None = None) -> int:
    """CLI wrapper for external Mathlib search."""

    parser = argparse.ArgumentParser(prog="formal-islands-search")
    parser.add_argument("--query", default=None, help="Search query to run directly.")
    parser.add_argument(
        "--provider",
        default="both",
        choices=["loogle", "leansearch", "both"],
        help="Search provider to use.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help="Maximum number of hits to keep per query.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Timeout for each remote search request.",
    )
    parser.add_argument("--title", default=None, help="Optional theorem title for auto queries.")
    parser.add_argument(
        "--statement",
        default=None,
        help="Optional theorem or node statement for auto queries.",
    )
    parser.add_argument("--proof", default=None, help="Optional informal proof text for auto queries.")
    parser.add_argument(
        "--coverage-summary",
        default=None,
        help="Optional coverage summary for auto queries.",
    )
    parser.add_argument(
        "--coverage-component",
        action="append",
        default=[],
        help="Optional repeated coverage component in the form kind:text.",
    )
    parser.add_argument(
        "--max-queries",
        type=int,
        default=DEFAULT_MAX_QUERIES,
        help="Maximum number of auto-generated queries.",
    )
    args = parser.parse_args(argv)

    if args.query is not None:
        providers = _normalize_provider_choice(args.provider)
        query_results: list[MathlibSearchQueryResult] = []
        all_hits: list[MathlibSearchHit] = []
        for provider in providers:
            hits = _search_provider(
                provider,
                args.query,
                top_k=args.top_k,
                timeout_seconds=args.timeout_seconds,
            )
            query_results.append(
                MathlibSearchQueryResult(
                    spec=MathlibSearchQuery(
                        provider=provider,
                        query=args.query,
                        reason="Explicit CLI query",
                    ),
                    hits=hits,
                )
            )
            all_hits.extend(hits)
        bundle = MathlibSearchBundle(
            query_results=query_results,
            combined_hits=_dedupe_and_rank_hits(all_hits),
        )
    else:
        if not args.title or not args.statement:
            parser.error("either --query or both --title and --statement must be provided")
        components = [_parse_component(spec) for spec in args.coverage_component]
        bundle = build_mathlib_search_bundle(
            theorem_title=args.title,
            theorem_statement=args.statement,
            node_title=args.title,
            node_statement=args.statement,
            node_proof_text=args.proof or "",
            coverage_summary=args.coverage_summary,
            coverage_components=components,
            max_queries=args.max_queries,
            top_k=args.top_k,
            timeout_seconds=args.timeout_seconds,
        )

    print(json.dumps(asdict(bundle), indent=2))
    return 0


def _parse_component(spec: str) -> tuple[str, str]:
    if ":" not in spec:
        return ("component", spec)
    kind, text = spec.split(":", 1)
    return kind.strip() or "component", text.strip()
