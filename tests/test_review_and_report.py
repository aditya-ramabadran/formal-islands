import json

from formal_islands.examples.fixtures import build_example_graph
from formal_islands.report.generator import export_report_bundle, render_html_report
from formal_islands.review.extractor import derive_review_obligations


def test_derive_review_obligations_covers_all_required_kinds() -> None:
    graph = build_example_graph()

    obligations = derive_review_obligations(graph)
    kinds = {obligation.kind for obligation in obligations}

    assert "informal_proof_check" in kinds
    assert "formal_semantic_match_check" in kinds
    assert "boundary_interface_check" in kinds


def test_export_report_bundle_is_json_serializable() -> None:
    graph = build_example_graph()
    obligations = derive_review_obligations(graph)

    bundle = export_report_bundle(graph, obligations)
    serialized = json.dumps(bundle)

    assert "review_obligations" in serialized
    assert "formal_verified" in serialized


def test_render_html_report_includes_core_sections() -> None:
    graph = build_example_graph()
    obligations = derive_review_obligations(graph)

    html = render_html_report(graph, obligations)

    assert "<h1>Nonnegative sum</h1>" in html
    assert "Review Checklist" in html
    assert "Formal Artifact" in html
    assert "Lean code" in html
    assert "Verification logs" in html
    assert 'type="checkbox"' in html
    assert 'class="graph-svg"' in html
    assert 'data-graph-node-id="n1"' in html
    assert 'data-obligation-id="informal-proof-n1"' in html
