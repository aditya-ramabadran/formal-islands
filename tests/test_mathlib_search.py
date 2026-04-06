from __future__ import annotations

import io
import json
from unittest.mock import patch

from formal_islands.mathlib_search import (
    search_leansearch,
    search_loogle,
    suggest_mathlib_search_queries,
)


class FakeResponse(io.BytesIO):
    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def test_suggest_mathlib_search_queries_prefers_exact_and_natural_queries() -> None:
    queries = suggest_mathlib_search_queries(
        theorem_title="Normalized 1D inequality",
        theorem_statement=(
            "For all u in Icc 0 2, u * Real.log u + (2 - u) * Real.log (2 - u) <= "
            "(Real.sqrt u - Real.sqrt (2 - u)) ^ 2."
        ),
        node_title="Local identity",
        node_statement="The derivative of (sqrt u - sqrt (2-u))^2 equals 2(u-1)/sqrt(u(2-u)).",
        node_proof_text="Differentiate and simplify.",
        coverage_summary="Differentiate the square and compare to the log term.",
        coverage_components=[("calculus_step", "Differentiate the square")],
        max_queries=2,
    )

    assert queries[0].provider == "loogle"
    assert "Real.log" in queries[0].query or "Real.sqrt" in queries[0].query
    assert queries[1].provider == "leansearch"
    assert "derivative" in queries[1].query.lower()


def test_search_loogle_parses_json_response() -> None:
    payload = {
        "count": 1,
        "hits": [
            {
                "name": "Real.sqrt_sq",
                "module": "Mathlib.Analysis.SpecialFunctions.Sqrt",
                "type": "theorem Real.sqrt_sq (x : ℝ) : Real.sqrt x ^ 2 = x",
                "doc": "Square of a square root.",
            }
        ],
    }

    with patch(
        "formal_islands.mathlib_search.urllib.request.urlopen",
        return_value=FakeResponse(json.dumps(payload).encode("utf-8")),
    ):
        hits = search_loogle("Real.sqrt", top_k=5)

    assert len(hits) == 1
    assert hits[0].provider == "loogle"
    assert hits[0].name == "Real.sqrt_sq"
    assert hits[0].module == "Mathlib.Analysis.SpecialFunctions.Sqrt"
    assert "sqrt" in (hits[0].statement or "").lower()


def test_search_leansearch_parses_json_response() -> None:
    payload = [
        [
            {
                "result": {
                    "name": ["Real", "sqrt_sq"],
                    "module_name": ["Mathlib", "Analysis", "SpecialFunctions", "Sqrt"],
                    "signature": "theorem Real.sqrt_sq (x : ℝ) : Real.sqrt x ^ 2 = x",
                    "docstring": "Square of a square root.",
                    "doc_url": "https://example.invalid",
                },
                "distance": 0.25,
            }
        ]
    ]

    with patch(
        "formal_islands.mathlib_search.urllib.request.urlopen",
        return_value=FakeResponse(json.dumps(payload).encode("utf-8")),
    ):
        hits = search_leansearch("square root", top_k=5)

    assert len(hits) == 1
    assert hits[0].provider == "leansearch"
    assert hits[0].name == "Real.sqrt_sq"
    assert hits[0].module == "Mathlib.Analysis.SpecialFunctions.Sqrt"
    assert hits[0].distance == 0.25
