#!/usr/bin/env python3
"""Prepare and run the spectral-product paper Formal Islands case study.

This script is deliberately specific to the paper
"Spectrality of Product Sets with a Perturbed Interval Factor".

It implements a two-level artifact:

1. Paper-level nodes are theorem-like units from the TeX paper: the main
   theorem, lemmas, corollaries, and cited external facts.
2. Ordinary Formal Islands runs are launched on selected paper-level nodes.
   Those runs then create their own lower-level proof graphs internally.

Small local claims such as the cosine-positivity step are recorded as suggested
internal islands / continuation hints, not as paper-level nodes.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PAPER_TEX = Path(
    "/Users/adihaya/GitHub/math/summer-2024/studia_mathematica_paper/paper.tex"
)
CASE_ROOT = REPO_ROOT / "paper/case_studies/spectral_product"
UNITS_DIR = CASE_ROOT / "units"
CONTEXT_DIR = CASE_ROOT / "context"
BASELINES_DIR = CASE_ROOT / "baselines"
EXAMPLES_DIR = REPO_ROOT / "examples/manual-testing"
ARTIFACT_ROOT = REPO_ROOT / "artifacts/paper-case-study/spectral_product"
WHOLE_PAPER_DIRECT_INPUT_FILENAME = "paper_spectral_product_whole_paper_direct.json"
WHOLE_PAPER_DIRECT_INPUT_PATH = EXAMPLES_DIR / WHOLE_PAPER_DIRECT_INPUT_FILENAME
WHOLE_PAPER_DIRECT_COPY_PATH = BASELINES_DIR / WHOLE_PAPER_DIRECT_INPUT_FILENAME


@dataclass(frozen=True)
class PaperNode:
    id: str
    kind: str
    label: str | None
    title: str
    statement_tex: str
    source_lines: list[int] | None = None
    status: str = "not_attempted"
    proof_tex: str = ""
    proof_summary: str = ""
    remaining_burden: str = ""
    suggested_internal_islands: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class PaperEdge:
    source_id: str
    target_id: str
    kind: str = "depends_on"
    explanation: str = ""


@dataclass(frozen=True)
class PaperRunTarget:
    id: str
    paper_node_id: str
    theorem_title: str
    theorem_statement: str
    raw_proof_text: str
    paper_context: str
    suggested_internal_islands: list[dict[str, str]]
    recommended_continuation: str
    expected_outcome: str
    direct_root_probe_enabled: bool = True

    @property
    def input_filename(self) -> str:
        return f"paper_spectral_product_{self.id}.json"

    @property
    def input_path(self) -> Path:
        return EXAMPLES_DIR / self.input_filename

    @property
    def unit_path(self) -> Path:
        return UNITS_DIR / self.input_filename

    @property
    def continuation_path(self) -> Path:
        return CONTEXT_DIR / f"{self.id}_continuation.md"


NO_SMALL_ZEROS_PROOF = r"""Since \(E\) tiles with respect to \(\mathbb Z\) and
\(E\subset [0, 3/2-\epsilon]\), there is a set
\(G\subset [0,1/2-\epsilon]\) such that
\[
\chi_E=\chi_{[0,1]}-\chi_G+\tau_1\chi_G
\]
almost everywhere, where \(\tau_h f(x):=f(x-h)\). Indeed, for almost every
\(x\in[0,1]\), exactly one of \(x\) and \(x+1\) lies in \(E\), and no other
integer translate can occur; taking
\(G=\{x\in[0,1/2-\epsilon]:x+1\in E\}\) gives the claimed identity.

We may assume without loss of generality that \(m(G)>0\), for otherwise
\(\chi_E=\chi_{[0,1]}\) almost everywhere, and so the zeros of
\(\hat{\chi_E}\) are precisely the non-zero integers. The case \(\xi=0\) is
immediate, since \(\hat{\chi_E}(0)=m(E)=1\). Thus assume \(0<|\xi|\leq 1/2\).
Then
\[
\hat{\chi_E}(\xi)=(e^{-2\pi i\xi}-1)
\left(\hat{\chi_G}(\xi)-\frac{1}{2\pi i\xi}\right).
\]
The roots of \(e^{-2\pi i\xi}-1\) are precisely the integers. Since
\(0<|\xi|\leq 1/2\), this factor is nonzero. Also,
\(|2\pi x\xi|<\pi/2\) for all \(x\in G\), since
\(G\subset [0,1/2-\epsilon]\). This means that
\(\cos(2\pi x\xi)>0\) on \(G\), so
\[
\Re(\hat{\chi_G}(\xi))
=\int_G\cos(2\pi x\xi)\,dx
=\int_G|\cos(2\pi x\xi)|\,dx.
\]
Since \(\cos(2\pi x\xi)>0\) on \(G\) and \(m(G)>0\), the last integral is
strictly positive. Thus \(\Re(\hat{\chi_G}(\xi))>0\), while
\(1/(2\pi i\xi)\) is purely imaginary. Therefore
\(\hat{\chi_G}(\xi)-1/(2\pi i\xi)\neq 0\), which proves the result."""


PACKING_REGION_PROOF = r"""For each \(n=1,2,3,\ldots\), let
\[
D_n:=\left(0,\frac12\right)\cup\left(n-\frac12,n\right).
\]
Then \(D_n\) is open and \(m(D_n)=1\).

Suppose for contradiction that \(E\) admits no orthogonal packing region
\(D\subset\mathbb R\) with \(m(D)=1\). In particular, no set \(D_n\) is an
orthogonal packing region for \(E\). Since \(D_n\) is open, we have
\(\Delta(D_n)=D_n-D_n\), and
\[
\Delta(D_n)=(-n,-n+1)\cup(-1/2,1/2)\cup(n-1,n).
\]
Because \(D_n\) is not an orthogonal packing region for \(E\),
\(\hat{\chi_E}\) has a zero in \(\Delta(D_n)\). By the no-small-zeros lemma,
\(\hat{\chi_E}\) does not vanish on \((-1/2,1/2)\), so it has a zero either
in \((-n,-n+1)\) or in \((n-1,n)\). Since \(\chi_E\) is real-valued, the zero
set of \(\hat{\chi_E}\) is symmetric about the origin. Therefore for each
\(n\) there is a zero \(b_n\in(n-1,n)\) of \(\hat{\chi_E}\).

Since \(E\) tiles \(\mathbb R\) with respect to \(\mathbb Z\), taking Fourier
coefficients on \(\mathbb R/\mathbb Z\) gives
\(\hat{\chi_E}(k)=0\) for every nonzero integer \(k\).

Let \(\Lambda_+=\{\xi>0:\hat{\chi_E}(\xi)=0\}\). The zeros \(b_n\) lie in
disjoint intervals \((n-1,n)\), and none of them is an integer. If
\(r\in[N,N+1)\), then \(\Lambda_+\cap[0,r]\) contains \(1,\ldots,N\) and
\(b_1,\ldots,b_N\), so \(\#(\Lambda_+\cap[0,r])\geq 2N\). Hence the lower
density of \(\Lambda_+\) is at least \(2\). Since \(\chi_E\) is supported in
an interval of length \(3/2-\epsilon<2\), the zero-density lemma implies
\(\chi_E=0\) almost everywhere, contradicting \(m(E)=1\)."""


LATTICE_TILING_PROOF = r"""By the overlap lemma, \(\Delta(E)\) contains
\((-1,1)\). Since the support interval has length \(3/2-\epsilon<2\), the
proper-tiling lemma implies that any weak tiling of \(E^c\) by \(E\) is
proper. If \(\mu=\sum_{\lambda\in\Lambda'}\delta_\lambda\) is the weak tiling
measure, then \(0\notin\Lambda'\) by the support lemma, and
\[
\chi_E*(\delta_0+\mu)=\chi_E+\chi_{E^c}=1
\]
almost everywhere. Hence \(E+\Lambda\) tiles \(\mathbb R\), where
\(\Lambda=\Lambda'\cup\{0\}\). Since \(\mu\) is locally finite,
\(\Lambda\) is discrete, and the Kolountzakis-Lagarias interval theorem gives
\(\Lambda=\mathbb Z\)."""


PROPER_TILING_PROOF = r"""Let \(\mu\) be a weak tiling measure for \(E\).
After replacing \(a,b\) by essential infimum/supremum, the support lemma gives
\(\operatorname{supp}\mu\cap(-1,1)=\varnothing\). One proves first that
\(b-a\geq 1\), and that \(E\) contains almost all points of
\((b-1,a+1)\) when that interval is nonempty. Define
\[
a+s=\operatorname{essinf}(E^c\cap[a+1,\infty)).
\]
Then \(1\leq s\leq b-a\leq 2\), and \(E\) contains almost all points of
\((a+1,a+s)\). The support has no mass in \([1,s)\). A local-finiteness
argument shows that \(\mu\{s\}=1\): if the mass near \(s\) were below \(1\),
then the weak tiling identity would fail just to the right of \(a+s\), while
on \(E+s\) the atom at \(s\) cannot contribute more than total mass \(1\).

Define \(\mu_1=(\mu+\delta_0-\delta_s)*\delta_{-s}\). It is again a weak
tiling measure. Repeating the argument shows unit atoms at \(s,2s,3s,\ldots\)
with no mass between them. Reflecting \(E\) and \(\mu\) gives the same
conclusion on the negative side. Thus the weak tiling is proper."""


PAPER_NODES: list[PaperNode] = [
    PaperNode(
        id="main_theorem",
        kind="theorem",
        label="thm_main",
        title="Main theorem",
        source_lines=[156, 158],
        statement_tex=(
            "Let E be measurable with measure 1 and E subset [0, 3/2 - epsilon]. "
            "Let F subset R^m be bounded and measurable with positive measure. "
            "If E x F is spectral, then E and F are spectral."
        ),
        proof_summary=(
            "Combines cited weak-tiling facts, the lattice-tiling corollary, "
            "Fuglede's lattice case, the packing-region lemma, and the "
            "orthogonal-packing theorem."
        ),
        remaining_burden=(
            "Full root certification would require spectral-set, weak-tiling, "
            "orthogonal-packing, and Fourier-analysis infrastructure."
        ),
    ),
    PaperNode(
        id="proper_tiling_lemma",
        kind="lemma",
        label="lem:proper-tiling",
        title="Weak tiling is proper under short support and overlap",
        source_lines=[243, 245],
        statement_tex=(
            "Let E subset [a,b] be measurable, b-a <= 2, and "
            "Delta(E) contain (-1,1). Then any weak tiling of E^c by E is a "
            "proper tiling."
        ),
        proof_tex=PROPER_TILING_PROOF,
        proof_summary="Support gaps, first boundary point, unit atom, iteration, reflection.",
        remaining_burden="Measure-theoretic atom/support argument; probably a later target.",
        suggested_internal_islands=[
            {
                "id": "support_gap",
                "claim": "supp(mu) avoids (-1,1) from Delta(E) superset (-1,1).",
                "role": "early support exclusion",
            },
            {
                "id": "translated_measure",
                "claim": "mu_1=(mu+delta_0-delta_s)*delta_{-s} is positive locally finite if mu{s}=1.",
                "role": "iteration step",
            },
        ],
    ),
    PaperNode(
        id="lattice_tiling_corollary",
        kind="corollary",
        label="cor:lattice-tiling",
        title="Weak tiling implies Z tiling",
        source_lines=[275, 277],
        statement_tex=(
            "If m(E)=1, E subset [0, 3/2-epsilon], and E weakly tiles its "
            "complement, then E+Z tiles R."
        ),
        proof_tex=LATTICE_TILING_PROOF,
        proof_summary="Dependency assembly from overlap, proper tiling, support, and KL interval theorem.",
        remaining_burden="High-level dependency skeleton; use only if assumptions are labeled explicitly.",
    ),
    PaperNode(
        id="no_small_zeros_lemma",
        kind="lemma",
        label="lem:no-small-zeros",
        title="No small zeros of Fourier transform under Z tiling",
        source_lines=[290, 303],
        statement_tex=(
            "Let m(E)=1, E subset [0, 3/2-epsilon], and suppose E tiles R "
            "with respect to Z. Then hat(chi_E)(xi) != 0 whenever "
            "-1/2 <= xi <= 1/2."
        ),
        proof_tex=NO_SMALL_ZEROS_PROOF,
        proof_summary=(
            "Decomposes a Z-tiling interval perturbation into [0,1] with a "
            "moved set G, derives a Fourier identity, and uses cosine "
            "positivity on G."
        ),
        remaining_burden=(
            "A full proof needs Fourier-transform facts and a.e. tiling "
            "decomposition. Plausible local islands include cosine positivity "
            "and the elementary interval perturbation decomposition."
        ),
        suggested_internal_islands=[
            {
                "id": "cosine_positivity",
                "claim": "If x in [0,1/2-eps] and 0 < |xi| <= 1/2, then cos(2*pi*x*xi)>0.",
                "role": "certifies the concrete positivity step used in the integral argument",
            },
            {
                "id": "noninteger_exponential_factor",
                "claim": "e^{-2*pi*i*xi}-1 is nonzero when 0<|xi|<=1/2.",
                "role": "rules out one factor in the Fourier identity",
            },
            {
                "id": "real_part_positive_core",
                "claim": "If cos(2*pi*x*xi)>0 on a positive-measure G, then the real part of the integral over G is positive.",
                "role": "connects pointwise positivity to nonvanishing",
            },
        ],
    ),
    PaperNode(
        id="zero_density_lemma",
        kind="lemma",
        label="lem:zero-density",
        title="Too many real Fourier zeros force vanishing",
        source_lines=[305, 311],
        statement_tex=(
            "If f in L^1(R) is supported in [a,b] and its positive real "
            "Fourier zeros have lower density greater than b-a, then f=0 a.e."
        ),
        proof_tex=(
            "Uses Young's completeness theorem for complex exponentials after "
            "rescaling [a,b] to [-pi L, pi L]."
        ),
        proof_summary="External nonharmonic Fourier series infrastructure plus a rescaling.",
        remaining_burden="Treat as external/heavy unless doing a dependency-skeleton run.",
    ),
    PaperNode(
        id="packing_region_lemma",
        kind="lemma",
        label="lem:packing-region",
        title="Z-tiling interval perturbation admits an orthogonal packing region",
        source_lines=[325, 344],
        statement_tex=(
            "Let m(E)=1, E subset [0, 3/2-epsilon], and suppose E tiles R "
            "with respect to Z. Then E admits an orthogonal packing region "
            "D subset R with m(D)=1."
        ),
        proof_tex=PACKING_REGION_PROOF,
        proof_summary=(
            "Defines D_n, computes D_n-D_n, forces one far Fourier zero per "
            "unit interval, combines with integer zeros, and applies "
            "zero-density."
        ),
        remaining_burden=(
            "Full proof needs Fourier zeros and zero-density theorem. "
            "Plausible internal islands include D_n-D_n and zero counting."
        ),
        suggested_internal_islands=[
            {
                "id": "Dn_difference",
                "claim": "D_n-D_n = (-n,-n+1) union (-1/2,1/2) union (n-1,n).",
                "role": "localizes possible Fourier zeros",
            },
            {
                "id": "zero_count_density",
                "claim": "one noninteger zero in each (n-1,n) plus integer zeros gives lower density at least 2",
                "role": "discrete counting spine before applying zero-density",
            },
        ],
    ),
    PaperNode(
        id="overlap_external",
        kind="external_lemma",
        label="lem:overlap",
        title="Interval perturbation overlap lemma",
        source_lines=[186, 188],
        status="external_cited",
        statement_tex="If E subset [0,3/2-epsilon] has measure 1, Delta(E) contains (-1,1).",
    ),
    PaperNode(
        id="support_external",
        kind="external_lemma",
        label="lem:support",
        title="Weak tiling support avoids Delta(E)",
        source_lines=[226, 228],
        status="external_cited",
        statement_tex="If chi_E * mu = chi_{E^c}, then supp(mu) subset Delta(E)^c.",
    ),
    PaperNode(
        id="spectral_weak_external",
        kind="external_theorem",
        label="thm:spectral-weak",
        title="Spectral sets weakly tile complements",
        source_lines=[214, 216],
        status="external_cited",
        statement_tex="If Omega is spectral, then Omega^c admits a weak tiling by translates of Omega.",
    ),
    PaperNode(
        id="product_weak_external",
        kind="external_theorem",
        label="thm:product-weak",
        title="Product weak tiling splits to factors",
        source_lines=[219, 221],
        status="external_cited",
        statement_tex="E x F weakly tiles its complement iff E and F weakly tile their complements.",
    ),
    PaperNode(
        id="orthogonal_packing_external",
        kind="external_theorem",
        label="thm:orthogonal-packing",
        title="Orthogonal packing region implies F spectral",
        source_lines=[199, 201],
        status="external_cited",
        statement_tex="If E has an orthogonal packing region D with m(D)=m(E)^-1 in a spectral product E x F, then F is spectral.",
    ),
    PaperNode(
        id="fuglede_lattice_external",
        kind="external_theorem",
        label=None,
        title="Fuglede lattice case",
        status="external_cited",
        statement_tex="The lattice tiling case implies Z is a spectrum for E.",
    ),
    PaperNode(
        id="KL_interval_spectrum_external",
        kind="external_theorem",
        label="thm_KL",
        title="Kolountzakis-Lagarias interval theorem",
        source_lines=[143, 149],
        status="external_cited",
        statement_tex="For E subset [0,3/2-epsilon] of measure 1, tiling or spectrality with a discrete set containing 0 forces the set to be Z.",
    ),
]


PAPER_EDGES: list[PaperEdge] = [
    PaperEdge("main_theorem", "spectral_weak_external"),
    PaperEdge("main_theorem", "product_weak_external"),
    PaperEdge("main_theorem", "lattice_tiling_corollary"),
    PaperEdge("main_theorem", "fuglede_lattice_external"),
    PaperEdge("main_theorem", "packing_region_lemma"),
    PaperEdge("main_theorem", "orthogonal_packing_external"),
    PaperEdge("lattice_tiling_corollary", "overlap_external"),
    PaperEdge("lattice_tiling_corollary", "proper_tiling_lemma"),
    PaperEdge("lattice_tiling_corollary", "support_external"),
    PaperEdge("lattice_tiling_corollary", "KL_interval_spectrum_external"),
    PaperEdge("proper_tiling_lemma", "support_external"),
    PaperEdge("packing_region_lemma", "no_small_zeros_lemma"),
    PaperEdge("packing_region_lemma", "zero_density_lemma"),
    PaperEdge("no_small_zeros_lemma", "lattice_tiling_corollary"),
]


def node_by_id() -> dict[str, PaperNode]:
    return {node.id: node for node in PAPER_NODES}


def make_target(
    node_id: str,
    *,
    context: str,
    expected: str,
    continuation: str,
    direct_root_probe_enabled: bool = True,
) -> PaperRunTarget:
    node = node_by_id()[node_id]
    return PaperRunTarget(
        id=node_id,
        paper_node_id=node_id,
        theorem_title=f"Spectral product paper node: {node.title}",
        theorem_statement=node.statement_tex,
        raw_proof_text=node.proof_tex or node.proof_summary,
        paper_context=context,
        suggested_internal_islands=node.suggested_internal_islands,
        recommended_continuation=continuation,
        expected_outcome=expected,
        direct_root_probe_enabled=direct_root_probe_enabled,
    )


RUN_TARGETS: dict[str, PaperRunTarget] = {
    "no_small_zeros_lemma": make_target(
        "no_small_zeros_lemma",
        context=(
            "This run targets the full paper-level node `lem:no-small-zeros`. "
            "The point is to let Formal Islands decompose this lemma into "
            "smaller internal proof nodes. Prior paper context: the upstream "
            "corollary `cor:lattice-tiling` supplies that E tiles R by Z. "
            "The parent paper node `lem:packing-region` later uses this lemma "
            "to exclude zeros in (-1/2,1/2). Do not treat this as a request "
            "to formalize the whole main theorem."
        ),
        expected=(
            "Best first target. We hope for at least one verified internal "
            "island, probably the cosine-positivity step."
        ),
        continuation=(
            "If the first run does not isolate a useful internal candidate, "
            "continue the most relevant failed/broad node with guidance to "
            "target the cosine-positivity step: prove that if x in "
            "[0,1/2-eps] and 0<|xi|<=1/2, then cos(2*pi*x*xi)>0."
        ),
    ),
    "packing_region_lemma": make_target(
        "packing_region_lemma",
        context=(
            "This run targets the full paper-level node `lem:packing-region`. "
            "The proof invokes the paper node `lem:no-small-zeros` and the "
            "unformalized heavy node `lem:zero-density`. Do not introduce those "
            "dependencies as Lean hypotheses, axioms, constants, or unverified "
            "stand-ins for a full proof of `lem:packing-region`. If those "
            "dependencies are needed, leave the paper node/root informal and "
            "look for certifiable internal islands in the displayed proof."
        ),
        expected=(
            "Second target after no_small_zeros_lemma. A good result is a "
            "verified D_n difference-set island or a faithful core around "
            "the discrete zero-count argument."
        ),
        continuation=(
            "If the first run is too broad, continue a failed/broad node with "
            "guidance to target D_n-D_n: write D_n=(0,1/2) union (n-1/2,n) "
            "and prove the needed inclusion/equality for D_n-D_n."
        ),
        direct_root_probe_enabled=False,
    ),
    "lattice_tiling_corollary": make_target(
        "lattice_tiling_corollary",
        context=(
            "This paper node cites the overlap lemma, support lemma, "
            "proper-tiling lemma, and Kolountzakis-Lagarias interval theorem. "
            "Those cited results should remain unformalized paper-graph nodes "
            "unless separately verified; do not introduce them as Lean "
            "hypotheses, axioms, constants, or unverified stand-ins. This run "
            "is only useful if it certifies an internal local island without "
            "pretending to certify the full corollary."
        ),
        expected="Later target; useful mostly for dependency assembly.",
        continuation=(
            "If attempted, guide the node toward honest dependency assembly "
            "and assumption provenance, not weak-tiling theory from scratch."
        ),
        direct_root_probe_enabled=False,
    ),
    "proper_tiling_lemma": make_target(
        "proper_tiling_lemma",
        context=(
            "This run targets the paper's longest new measure-theoretic lemma. "
            "Its proof cites the external support lemma and has several local "
            "subclaims about support gaps, unit atoms, translated measures, "
            "and iteration. Do not introduce the support lemma as a Lean "
            "hypothesis, axiom, constant, or unverified stand-in for a full "
            "proof. It is likely hard; use it as a stress test after simpler "
            "paper nodes."
        ),
        expected="Hard later target; a useful result may be a small faithful core.",
        continuation=(
            "If attempted, steer toward one local support/atom subclaim rather "
            "than the full proper-tiling theorem."
        ),
        direct_root_probe_enabled=False,
    ),
}


INITIAL_TARGETS = ["no_small_zeros_lemma", "packing_region_lemma"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="write manifest, paper-node inputs, and dashboard")
    prepare.add_argument("--reset-runs", action="store_true", help="drop recorded run paths")
    prepare.add_argument(
        "--guided-inputs",
        action="store_true",
        help=(
            "Also embed suggested internal islands into the generated input JSONs. "
            "Default leaves those hints only in the dashboard and continuation files."
        ),
    )
    prepare.set_defaults(func=cmd_prepare)

    dashboard = subparsers.add_parser("dashboard", help="refresh dashboard from known run dirs")
    dashboard.set_defaults(func=cmd_dashboard)

    status = subparsers.add_parser("status", help="print current paper-node run status")
    status.set_defaults(func=cmd_status)

    run = subparsers.add_parser("run", help="run Formal Islands on a paper-level node")
    run.add_argument("--target", default="no_small_zeros_lemma", choices=sorted([*RUN_TARGETS.keys(), "initial"]))
    run.add_argument("--backends", default="codex/aristotle")
    run.add_argument("--max-attempts", type=int, default=4)
    run.add_argument("--workspace", default=None)
    run.add_argument("--attempt-all-nodes", action="store_true")
    run.add_argument(
        "--guided-input",
        action="store_true",
        help=(
            "Embed suggested internal islands in the first-run input. Default is an "
            "unguided paper-node input; use continuation files for human steering."
        ),
    )
    run.add_argument(
        "--run-graph-if-direct-root-verifies",
        "--force-graph-after-direct-root",
        dest="run_graph_if_direct_root_verifies",
        action="store_true",
        help=(
            "Keep running the graph pipeline even if the normal direct-root probe "
            "verifies and passes semantic audit. Default allows faithful root "
            "closure to short-circuit because a closed paper node is already a "
            "strong artifact."
        ),
    )
    run.add_argument("--timestamp", default=None)
    run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    return int(args.func(args))


def cmd_prepare(args: argparse.Namespace) -> int:
    prepare_case_study(
        reset_runs=bool(args.reset_runs),
        include_guidance=bool(args.guided_inputs),
    )
    print(CASE_ROOT / "index.html")
    print(CASE_ROOT / "paper_manifest.json")
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    refresh_dashboard()
    print(CASE_ROOT / "index.html")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    prepare_case_study(reset_runs=False, include_guidance=False)
    manifest = load_manifest()
    for target in manifest.get("run_targets", []):
        latest = latest_run(target)
        status = latest.get("outcome", "not_run") if latest else "not_run"
        run_dir = latest.get("run_dir", "") if latest else ""
        print(f"{target['id']}: {status} {run_dir}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    prepare_case_study(reset_runs=False, include_guidance=bool(args.guided_input))
    targets = INITIAL_TARGETS if args.target == "initial" else [args.target]
    timestamp = args.timestamp or _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    for target_id in targets:
        run_target(
            RUN_TARGETS[target_id],
            backends=args.backends,
            max_attempts=args.max_attempts,
            workspace=args.workspace,
            attempt_all_nodes=bool(args.attempt_all_nodes),
            include_guidance=bool(args.guided_input),
            run_graph_if_direct_root_verifies=bool(args.run_graph_if_direct_root_verifies),
            timestamp=timestamp,
        )
    refresh_dashboard()
    print(CASE_ROOT / "index.html")
    return 0


def prepare_case_study(*, reset_runs: bool, include_guidance: bool) -> None:
    for path in (CASE_ROOT, UNITS_DIR, CONTEXT_DIR, BASELINES_DIR, EXAMPLES_DIR, ARTIFACT_ROOT):
        path.mkdir(parents=True, exist_ok=True)

    existing = load_manifest_or_none()
    existing_runs: dict[str, list[dict[str, Any]]] = {}
    if existing and not reset_runs:
        for target in existing.get("run_targets", []):
            existing_runs[str(target.get("id"))] = list(target.get("runs", []))

    # Remove old pre-carved local-probe files from the first prototype so they
    # do not look like paper-level nodes.
    for stale in (
        "paper_spectral_product_cosine_positivity.json",
        "paper_spectral_product_dn_difference.json",
        "paper_spectral_product_zero_count_density.json",
        "paper_spectral_product_lattice_tiling_skeleton.json",
    ):
        for directory in (EXAMPLES_DIR, UNITS_DIR):
            try:
                (directory / stale).unlink()
            except FileNotFoundError:
                pass
    for stale_context in (
        "cosine_positivity_continuation.md",
        "dn_difference_continuation.md",
        "zero_count_density_continuation.md",
        "lattice_tiling_skeleton_continuation.md",
    ):
        try:
            (CONTEXT_DIR / stale_context).unlink()
        except FileNotFoundError:
            pass

    run_targets = []
    for target in RUN_TARGETS.values():
        write_target_input(target, include_guidance=include_guidance)
        write_continuation_context(target)
        run_targets.append(
            {
                "id": target.id,
                "paper_node_id": target.paper_node_id,
                "input_json": str(target.input_path.relative_to(REPO_ROOT)),
                "unit_json": str(target.unit_path.relative_to(REPO_ROOT)),
                "continuation_context": str(target.continuation_path.relative_to(REPO_ROOT)),
                "expected_outcome": target.expected_outcome,
                "recommended_continuation": target.recommended_continuation,
                "direct_root_probe_enabled": target.direct_root_probe_enabled,
                "runs": [] if reset_runs else existing_runs.get(target.id, []),
            }
        )

    manifest = {
        "schema_version": 2,
        "paper_id": "spectral_product_perturbed_interval",
        "paper_title": "Spectrality of Product Sets with a Perturbed Interval Factor",
        "source_tex": str(PAPER_TEX),
        "main_node_id": "main_theorem",
        "notes": [
            "Two-level artifact: paper nodes are theorem-like units; Formal Islands runs decompose inside selected paper nodes.",
            "Local suggestions like cosine positivity are internal island hints, not paper-level nodes.",
            "A local island does not certify the paper unless the dependency closure to the main theorem is verified.",
            "External cited nodes are paper-level provenance nodes, not assumptions, axioms, or Formal Islands targets by default.",
            "Default generated inputs are unguided; suggested internal islands live in the dashboard and continuation files unless --guided-inputs/--guided-input is used.",
        ],
        "nodes": [paper_node_to_dict(node) for node in PAPER_NODES],
        "edges": [edge.__dict__ for edge in PAPER_EDGES],
        "run_targets": run_targets,
        "initial_targets": INITIAL_TARGETS,
        "baselines": [whole_paper_direct_baseline_manifest_entry()],
    }
    apply_run_statuses(manifest)
    write_json(CASE_ROOT / "paper_manifest.json", manifest)
    rewrite_target_inputs_from_manifest(manifest, include_guidance=include_guidance)
    write_whole_paper_direct_baseline_input()
    write_json(CASE_ROOT / "paper_graph.json", {"nodes": manifest["nodes"], "edges": manifest["edges"]})
    refresh_dashboard()


def whole_paper_direct_baseline_manifest_entry() -> dict[str, Any]:
    command = (
        "uv run formal-islands direct-root "
        f"--input {WHOLE_PAPER_DIRECT_INPUT_PATH.relative_to(REPO_ROOT)} "
        "--output-dir "
        '"artifacts/paper-case-study/spectral_product/direct_whole_paper_aristotle-$(date +%Y%m%d-%H%M%S)" '
        "--max-attempts 4"
    )
    return {
        "id": "whole_paper_direct_aristotle",
        "kind": "direct_root_diagnostic",
        "input_json": str(WHOLE_PAPER_DIRECT_INPUT_PATH.relative_to(REPO_ROOT)),
        "archival_copy": str(WHOLE_PAPER_DIRECT_COPY_PATH.relative_to(REPO_ROOT)),
        "output_dir_pattern": "artifacts/paper-case-study/spectral_product/direct_whole_paper_aristotle-<timestamp>",
        "command": command,
        "purpose": (
            "Monolithic diagnostic: ask Aristotle to formalize the paper's main theorem "
            "from the entire TeX source, without the two-level Formal Islands decomposition."
        ),
    }


def write_whole_paper_direct_baseline_input() -> None:
    tex = PAPER_TEX.read_text(encoding="utf-8")
    policy = """Monolithic whole-paper direct-root baseline policy.

Formalize and prove the main theorem stated above, using the paper TeX below as the informal proof context.
This is a diagnostic comparison against the decomposed Formal Islands workflow, not the main paper-node artifact.

Important constraints:
- Do not introduce cited external results from other papers as unproved Lean hypotheses, axioms, constants, or theorem stubs.
- Any helper lemma used in the Lean proof must be proved in the submitted file or come from Mathlib.
- If the proof needs cited facts that are not available in Mathlib and are not proved in the submitted file, prefer a transparent compile failure over a misleading theorem with hidden assumptions.
- Do not weaken, over-abstract, or replace the main theorem with a dependency skeleton.
- Do not use `sorry`, `admit`, `axiom`, `constant`, or opaque placeholder declarations.
"""
    payload = {
        "theorem_title": "Spectral product paper: whole-paper direct main theorem baseline",
        "theorem_statement": node_by_id()["main_theorem"].statement_tex,
        "raw_proof_text": f"{policy}\n\nFull paper TeX source:\n\n{tex}",
        "baseline_metadata": {
            "paper_title": "Spectrality of Product Sets with a Perturbed Interval Factor",
            "source_tex": str(PAPER_TEX),
            "baseline_kind": "whole_paper_direct_root",
            "notes": [
                "This file intentionally avoids Formal Islands optional context fields so the direct-root theorem statement remains clean.",
                "The anti-axiom/citation-smuggling policy is included in raw_proof_text as part of the proof context.",
            ],
        },
    }
    write_json(WHOLE_PAPER_DIRECT_COPY_PATH, payload)
    write_json(WHOLE_PAPER_DIRECT_INPUT_PATH, payload)


def write_target_input(
    target: PaperRunTarget,
    *,
    dependency_run_context: str = "",
    include_guidance: bool = False,
) -> None:
    additional_context: dict[str, Any] = {
        "paper_context": target.paper_context,
        "important_instruction": (
            "The target is this paper-level lemma/corollary. The planning "
            "backend should decompose it into smaller Formal Islands nodes. "
            "Do not formalize the whole paper. Do not introduce external cited "
            "facts as Lean hypotheses, axioms, constants, or unverified theorem "
            "stubs. If an external cited fact is needed for the parent/root, "
            "leave that parent/root informal and certify only internal local "
            "islands that can be proved without those unverified dependencies."
        ),
    }
    if include_guidance:
        additional_context["suggested_internal_islands"] = target.suggested_internal_islands
        additional_context["guidance_note"] = (
            "These suggested internal islands are human guidance for this run, "
            "not part of the paper-level theorem statement."
        )
    if dependency_run_context:
        additional_context["previous_paper_node_run_context"] = dependency_run_context
    payload = {
        "theorem_title": target.theorem_title,
        "theorem_statement": target.theorem_statement,
        "raw_proof_text": target.raw_proof_text,
        "additional_context": additional_context,
        "paper_case_study": {
            "paper_title": "Spectrality of Product Sets with a Perturbed Interval Factor",
            "source_tex": str(PAPER_TEX),
            "paper_node_id": target.paper_node_id,
            "target_id": target.id,
            "expected_outcome": target.expected_outcome,
        },
    }
    write_json(target.unit_path, payload)
    write_json(target.input_path, payload)


def write_continuation_context(target: PaperRunTarget, *, dependency_run_context: str = "") -> None:
    lines = [
        f"Paper-level target: {target.paper_node_id}",
        "",
        "This is continuation guidance for a paper-node run. Keep the two-level structure clear:",
        "- the paper node remains the larger lemma/corollary;",
        "- this continuation may target one internal Formal Islands node or one refined local subclaim;",
        "- any resulting local theorem should be reported as an island inside the paper node, not as certification of the whole paper node.",
        "",
        "Recommended continuation:",
        target.recommended_continuation,
    ]
    if dependency_run_context:
        lines.extend(
            [
                "",
                "Previous paper-node run context:",
                dependency_run_context,
            ]
        )
    lines.extend(
        [
            "",
            "Suggested internal islands:",
            json.dumps(target.suggested_internal_islands, indent=2, ensure_ascii=False),
        ]
    )
    text = "\n".join(lines)
    target.continuation_path.write_text(text + "\n", encoding="utf-8")


def rewrite_target_inputs_from_manifest(
    manifest: dict[str, Any],
    *,
    include_guidance: bool = False,
) -> None:
    for target in RUN_TARGETS.values():
        dependency_context = dependency_run_context_for_target(target, manifest)
        write_target_input(
            target,
            dependency_run_context=dependency_context,
            include_guidance=include_guidance,
        )
        write_continuation_context(target, dependency_run_context=dependency_context)


def dependency_run_context_for_target(target: PaperRunTarget, manifest: dict[str, Any]) -> str:
    dependency_ids = [
        edge["target_id"]
        for edge in manifest.get("edges", [])
        if edge.get("source_id") == target.paper_node_id
    ]
    if not dependency_ids:
        return ""

    targets_by_paper_node: dict[str, list[dict[str, Any]]] = {}
    for run_target in manifest.get("run_targets", []):
        targets_by_paper_node.setdefault(str(run_target.get("paper_node_id")), []).append(run_target)

    lines: list[str] = []
    for dependency_id in dependency_ids:
        dependency_targets = targets_by_paper_node.get(dependency_id, [])
        dependency_lines: list[str] = []
        for dependency_target in dependency_targets:
            latest = latest_run(dependency_target)
            if not latest:
                continue
            summary = latest.get("summary") if isinstance(latest.get("summary"), dict) else {}
            parts = [
                f"- dependency paper node `{dependency_id}` has prior target `{dependency_target.get('id')}`",
                f"  run_dir: {latest.get('run_dir')}",
                f"  outcome: {latest.get('outcome')}",
            ]
            if summary:
                if summary.get("verified_nodes"):
                    parts.append(f"  verified internal nodes: {', '.join(map(str, summary['verified_nodes']))}")
                if summary.get("faithful_core_nodes"):
                    parts.append(f"  faithful core nodes: {', '.join(map(str, summary['faithful_core_nodes']))}")
                if summary.get("report_html"):
                    parts.append(f"  report_html: {summary['report_html']}")
            dependency_lines.append("\n".join(parts))
        if dependency_lines:
            lines.extend(dependency_lines)
    if not lines:
        return ""
    return (
        "These are previous Formal Islands runs for paper nodes that the current "
        "paper node depends on. Treat them as navigation and proof-role context. "
        "Only use a previous Lean theorem as a proved dependency if it is actually "
        "imported as a verified support module by the ordinary Formal Islands workflow.\n"
        + "\n".join(lines)
    )


def run_target(
    target: PaperRunTarget,
    *,
    backends: str,
    max_attempts: int,
    workspace: str | None,
    attempt_all_nodes: bool,
    include_guidance: bool,
    run_graph_if_direct_root_verifies: bool,
    timestamp: str,
) -> None:
    safe_backends = backends.replace("/", "-")
    output_dir = ARTIFACT_ROOT / f"{target.id}-{safe_backends}-{timestamp}"
    refresh_dashboard()
    manifest = load_manifest()
    dependency_context = dependency_run_context_for_target(target, manifest)
    write_target_input(
        target,
        dependency_run_context=dependency_context,
        include_guidance=include_guidance,
    )
    write_continuation_context(target, dependency_run_context=dependency_context)
    command = [
        sys.executable,
        "-m",
        "formal_islands.smoke",
        "run",
        "--input",
        str(target.input_path.relative_to(REPO_ROOT)),
        "--backends",
        backends,
        "--max-attempts",
        str(max_attempts),
        "--output-dir",
        str(output_dir.relative_to(REPO_ROOT)),
    ]
    if workspace:
        command.extend(["--workspace", workspace])
    if attempt_all_nodes:
        command.append("--attempt-all-nodes")
    if not target.direct_root_probe_enabled:
        command.append("--no-direct-root-probe")
    if run_graph_if_direct_root_verifies:
        command.append("--run-graph-if-direct-root-verifies")

    record_run(target.id, output_dir=output_dir, command=command, outcome="running")
    refresh_dashboard()
    print("Running:", " ".join(command), flush=True)
    completed = subprocess.run(command, cwd=REPO_ROOT)
    outcome = "completed" if completed.returncode == 0 else f"failed_returncode_{completed.returncode}"
    record_run(target.id, output_dir=output_dir, command=command, outcome=outcome)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def record_run(target_id: str, *, output_dir: Path, command: list[str], outcome: str) -> None:
    manifest = load_manifest()
    record = {
        "run_dir": str(output_dir.relative_to(REPO_ROOT)),
        "command": " ".join(command),
        "outcome": outcome,
        "updated_at": _dt.datetime.now().isoformat(timespec="seconds"),
    }
    for target in manifest["run_targets"]:
        if target["id"] != target_id:
            continue
        runs = list(target.get("runs", []))
        if runs and runs[-1].get("run_dir") == record["run_dir"]:
            runs[-1] = record
        else:
            runs.append(record)
        target["runs"] = runs
        break
    apply_run_statuses(manifest)
    write_json(CASE_ROOT / "paper_manifest.json", manifest)


def refresh_dashboard() -> None:
    manifest = load_manifest_or_none()
    if manifest is None:
        return
    changed = False
    for target in manifest.get("run_targets", []):
        for run in target.get("runs", []):
            run_dir = REPO_ROOT / str(run.get("run_dir", ""))
            summary = summarize_formal_islands_run(run_dir)
            if summary and run.get("summary") != summary:
                run["summary"] = summary
                if run.get("outcome") in {"running", "completed"}:
                    run["outcome"] = summary.get("outcome", run.get("outcome"))
                changed = True
    before = json.dumps(manifest.get("nodes", []), sort_keys=True)
    apply_run_statuses(manifest)
    after = json.dumps(manifest.get("nodes", []), sort_keys=True)
    changed = changed or before != after
    if changed:
        write_json(CASE_ROOT / "paper_manifest.json", manifest)
    rewrite_target_inputs_from_manifest(manifest)
    write_json(CASE_ROOT / "paper_graph.json", {"nodes": manifest["nodes"], "edges": manifest["edges"]})
    (CASE_ROOT / "index.md").write_text(render_markdown(manifest), encoding="utf-8")
    (CASE_ROOT / "index.html").write_text(render_html(manifest), encoding="utf-8")


def apply_run_statuses(manifest: dict[str, Any]) -> None:
    target_by_node: dict[str, list[dict[str, Any]]] = {}
    for target in manifest.get("run_targets", []):
        target_by_node.setdefault(str(target["paper_node_id"]), []).append(target)
    for node in manifest.get("nodes", []):
        if node.get("kind", "").startswith("external") or node.get("status") == "external_cited":
            node["status"] = "external_cited"
            continue
        targets = target_by_node.get(str(node["id"]), [])
        node["formal_islands_runs"] = [
            run | {"target_id": target["id"]}
            for target in targets
            for run in target.get("runs", [])
        ]
        node["status"] = infer_node_status(node, targets)


def summarize_formal_islands_run(run_dir: Path) -> dict[str, Any] | None:
    bundle_path = run_dir / "04_report_bundle.json"
    graph_path = run_dir / "03_formalized_graph.json"
    payload: dict[str, Any] | None = None
    if bundle_path.exists():
        try:
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            graph = bundle.get("graph")
            if isinstance(graph, dict):
                payload = graph
        except json.JSONDecodeError:
            return {"outcome": "invalid_report_bundle"}
    elif graph_path.exists():
        try:
            payload = json.loads(graph_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"outcome": "invalid_graph_json"}
    else:
        return None

    nodes = payload.get("nodes", []) if isinstance(payload, dict) else []
    if not isinstance(nodes, list):
        return {"outcome": "invalid_graph_shape"}
    verified = []
    faithful_cores = []
    failed = []
    candidates = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get("status") == "candidate_formal":
            candidates.append(node.get("id"))
        artifact = node.get("formal_artifact")
        classification = artifact.get("faithfulness_classification") if isinstance(artifact, dict) else None
        if node.get("status") == "formal_verified":
            if classification == "concrete_sublemma":
                faithful_cores.append(node.get("id"))
            else:
                verified.append(node.get("id"))
        elif node.get("status") == "formal_failed":
            failed.append(node.get("id"))
    if verified:
        outcome = "has_verified_internal_island"
    elif faithful_cores:
        outcome = "has_faithful_internal_core"
    elif failed:
        outcome = "attempted_no_island"
    else:
        outcome = "planned_no_attempts_yet"
    return {
        "outcome": outcome,
        "verified_nodes": verified,
        "faithful_core_nodes": faithful_cores,
        "failed_nodes": failed,
        "candidate_nodes": candidates,
        "node_count": len(nodes),
        "report_html": str((run_dir / "04_report.html").relative_to(REPO_ROOT))
        if (run_dir / "04_report.html").exists()
        else None,
    }


def infer_node_status(node: dict[str, Any], targets: list[dict[str, Any]]) -> str:
    if not targets:
        return node.get("status", "not_attempted")
    runs = [run for target in targets for run in target.get("runs", [])]
    if not runs:
        return "selected_for_paper_node_run"
    summaries = [run.get("summary") for run in runs if isinstance(run.get("summary"), dict)]
    if any(summary.get("verified_nodes") for summary in summaries):
        return "has_verified_internal_island"
    if any(summary.get("faithful_core_nodes") for summary in summaries):
        return "has_faithful_internal_core"
    if any(summary.get("failed_nodes") for summary in summaries):
        return "attempted_no_island"
    if any(str(run.get("outcome", "")).startswith("failed") for run in runs):
        return "run_failed"
    return "run_started"


def render_markdown(manifest: dict[str, Any]) -> str:
    lines = [
        "# Spectral Product Paper Case Study",
        "",
        f"Source TeX: `{manifest['source_tex']}`",
        "",
        "This is a two-level paper audit dashboard. Paper nodes are theorem-like units; linked Formal Islands runs decompose inside those nodes.",
        "",
        "## Direct Baselines",
        "",
    ]
    for baseline in manifest.get("baselines", []):
        lines.append(f"- `{baseline['id']}`: {baseline['purpose']}")
        lines.append(f"  - input: `{baseline['input_json']}`")
        lines.append(f"  - command: `{baseline['command']}`")
    lines.extend(
        [
            "",
        "## Paper-Node Run Targets",
        "",
        ]
    )
    for target in manifest.get("run_targets", []):
        latest = latest_run(target)
        status = latest.get("outcome", "not_run") if latest else "not_run"
        lines.append(f"- `{target['id']}`: {status}")
        lines.append(f"  - paper node: `{target['paper_node_id']}`")
        lines.append(f"  - input: `{target['input_json']}`")
        if latest:
            lines.append(f"  - run: `{latest.get('run_dir')}`")
        lines.append(f"  - expected: {target['expected_outcome']}")
    lines.extend(["", "## Paper Nodes", ""])
    for node in manifest.get("nodes", []):
        lines.append(f"- `{node['id']}` ({node['kind']}): {node.get('status', 'not_attempted')}")
        if node.get("suggested_internal_islands"):
            hints = ", ".join(hint["id"] for hint in node["suggested_internal_islands"])
            lines.append(f"  - suggested internal islands: {hints}")
        if node.get("remaining_burden"):
            lines.append(f"  - remaining burden: {node['remaining_burden']}")
    return "\n".join(lines) + "\n"


def render_html(manifest: dict[str, Any]) -> str:
    node_by_id = {node["id"]: node for node in manifest.get("nodes", [])}
    nodes_html = "\n".join(render_node_card(node, manifest) for node in manifest.get("nodes", []))
    targets_html = "\n".join(render_target_card(target) for target in manifest.get("run_targets", []))
    baselines_html = "\n".join(render_baseline_card(baseline) for baseline in manifest.get("baselines", []))
    edges_html = "\n".join(
        f"<li><code>{esc(edge['source_id'])}</code> depends on <code>{esc(edge['target_id'])}</code></li>"
        for edge in manifest.get("edges", [])
    )
    svg = render_svg_graph(manifest, node_by_id)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Spectral Product Paper Case Study</title>
  <style>
    :root {{
      --ink: #241f1b;
      --muted: #6f655c;
      --paper: #fbf7ef;
      --panel: #fffdf8;
      --line: #d7c8b5;
      --external: #e8edf7;
      --selected: #fff2cc;
      --verified: #dff3e5;
      --core: #e6f0ff;
      --failed: #f7dddd;
      --started: #efe7fb;
    }}
    body {{ margin: 0; font-family: ui-serif, Georgia, Cambria, "Times New Roman", Times, serif; color: var(--ink); background: radial-gradient(circle at top left, #fffaf0, var(--paper) 45%, #f4eadc); }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 36px 22px 64px; }}
    h1 {{ font-size: clamp(2rem, 5vw, 4rem); line-height: 0.95; margin: 0 0 12px; }}
    h2 {{ margin-top: 34px; border-bottom: 1px solid var(--line); padding-bottom: 8px; }}
    .lede {{ font-size: 1.12rem; max-width: 900px; color: var(--muted); }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(285px, 1fr)); gap: 14px; }}
    .card {{ background: color-mix(in srgb, var(--panel) 92%, white); border: 1px solid var(--line); border-radius: 16px; padding: 16px; box-shadow: 0 14px 35px rgba(60, 40, 20, 0.07); }}
    .status {{ display: inline-block; padding: 3px 8px; border-radius: 999px; font-size: 0.82rem; border: 1px solid var(--line); }}
    .external_cited {{ background: var(--external); }}
    .selected_for_paper_node_run, .not_attempted {{ background: var(--selected); }}
    .has_verified_internal_island {{ background: var(--verified); }}
    .has_faithful_internal_core {{ background: var(--core); }}
    .attempted_no_island, .run_failed {{ background: var(--failed); }}
    .run_started, .planned_no_attempts_yet {{ background: var(--started); }}
    code {{ background: rgba(80, 60, 30, 0.08); padding: 1px 4px; border-radius: 5px; }}
    a {{ color: #7a3f0e; }}
    .graph {{ background: var(--panel); border: 1px solid var(--line); border-radius: 18px; padding: 12px; overflow-x: auto; }}
    svg text {{ font-family: ui-serif, Georgia, Cambria, "Times New Roman", Times, serif; }}
    .small {{ color: var(--muted); font-size: 0.92rem; }}
    ul.edges {{ columns: 2; }}
  </style>
</head>
<body>
<main>
  <h1>Spectral Product Paper Case Study</h1>
  <p class="lede">A two-level audit dashboard for <em>{esc(manifest['paper_title'])}</em>. Paper nodes are theorem-like units from the TeX paper. Linked Formal Islands runs decompose inside selected paper nodes and may certify internal local islands. A verified internal island reduces review burden but does not certify the whole paper node or paper.</p>
  <p class="small"><strong>External cited nodes:</strong> these are provenance nodes only. They are not treated as Lean assumptions, axioms, or certified dependencies unless separately verified.</p>
  <p class="small">Source TeX: <code>{esc(manifest['source_tex'])}</code></p>
  <h2>Paper Dependency Map</h2>
  <div class="graph">{svg}</div>
  <h2>Direct Baselines</h2>
  <div class="grid">{baselines_html}</div>
  <h2>Paper-Node Formal Islands Runs</h2>
  <div class="grid">{targets_html}</div>
  <h2>Paper Nodes</h2>
  <div class="grid">{nodes_html}</div>
  <h2>Dependencies</h2>
  <ul class="edges">{edges_html}</ul>
</main>
</body>
</html>
"""


def render_baseline_card(baseline: dict[str, Any]) -> str:
    return f"""<article class="card">
  <span class="status not_attempted">diagnostic</span>
  <h3>{esc(baseline['id'])}</h3>
  <p>{esc(baseline['purpose'])}</p>
  <p class="small">Input: <code>{esc(baseline['input_json'])}</code></p>
  <p class="small">Output pattern: <code>{esc(baseline['output_dir_pattern'])}</code></p>
  <p class="small">Command: <code>{esc(baseline['command'])}</code></p>
</article>"""


def render_target_card(target: dict[str, Any]) -> str:
    latest = latest_run(target)
    status = latest.get("outcome", "not_run") if latest else "not_run"
    report_html = ""
    if latest and isinstance(latest.get("summary"), dict):
        report = latest["summary"].get("report_html")
        if report:
            report_href = os.path.relpath(REPO_ROOT / report, CASE_ROOT)
            report_html = f'<p><a href="{esc(report_href)}">Open Formal Islands report</a></p>'
    run_html = f"<p class='small'>Run: <code>{esc(latest.get('run_dir', ''))}</code></p>" if latest else ""
    return f"""<article class="card">
  <span class="status {esc(status)}">{esc(status)}</span>
  <h3>{esc(target['id'])}</h3>
  <p>Paper node: <code>{esc(target['paper_node_id'])}</code></p>
  <p>{esc(target['expected_outcome'])}</p>
  <p class="small">Direct-root probe: <code>{esc('enabled' if target.get('direct_root_probe_enabled', True) else 'disabled for unformalized external dependencies')}</code></p>
  <p class="small">Input: <code>{esc(target['input_json'])}</code></p>
  {run_html}
  {report_html}
</article>"""


def render_node_card(node: dict[str, Any], manifest: dict[str, Any]) -> str:
    status = node.get("status", "not_attempted")
    label = f" <code>{esc(node['label'])}</code>" if node.get("label") else ""
    hints = "".join(
        f"<li><code>{esc(hint['id'])}</code>: {esc(hint['role'])}</li>"
        for hint in node.get("suggested_internal_islands", [])
    )
    hints_html = f"<p class='small'>Suggested internal islands:</p><ul>{hints}</ul>" if hints else ""
    burden = f"<p><strong>Remaining burden:</strong> {esc(node.get('remaining_burden', ''))}</p>" if node.get("remaining_burden") else ""
    runs = [
        target
        for target in manifest.get("run_targets", [])
        if target.get("paper_node_id") == node.get("id")
    ]
    run_html = "".join(f"<li><code>{esc(target['id'])}</code></li>" for target in runs)
    run_block = f"<p class='small'>Formal Islands paper-node runs:</p><ul>{run_html}</ul>" if run_html else ""
    return f"""<article class="card">
  <span class="status {esc(status)}">{esc(status)}</span>
  <h3>{esc(node['title'])}{label}</h3>
  <p>{esc(node.get('statement_tex', ''))}</p>
  {burden}
  {hints_html}
  {run_block}
</article>"""


def render_svg_graph(manifest: dict[str, Any], node_by_id: dict[str, dict[str, Any]]) -> str:
    positions = {
        "main_theorem": (520, 30),
        "spectral_weak_external": (40, 150),
        "product_weak_external": (250, 150),
        "lattice_tiling_corollary": (470, 150),
        "packing_region_lemma": (710, 150),
        "orthogonal_packing_external": (950, 150),
        "overlap_external": (250, 285),
        "proper_tiling_lemma": (470, 285),
        "support_external": (40, 285),
        "KL_interval_spectrum_external": (690, 285),
        "no_small_zeros_lemma": (600, 420),
        "zero_density_lemma": (850, 420),
        "fuglede_lattice_external": (1050, 285),
    }
    defs = """
    <defs>
      <marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto" markerUnits="strokeWidth">
        <path d="M0,0 L0,6 L9,3 z" fill="#8f8172" />
      </marker>
    </defs>
    """
    edge_elems = []
    for edge in manifest.get("edges", []):
        src = positions.get(edge["source_id"])
        dst = positions.get(edge["target_id"])
        if src is None or dst is None:
            continue
        x1, y1 = src
        x2, y2 = dst
        edge_elems.append(
            f'<line x1="{x1+80}" y1="{y1+54}" x2="{x2+80}" y2="{y2}" '
            'stroke="#8f8172" stroke-width="1.6" marker-end="url(#arrow)" />'
        )
    node_elems = []
    for node_id, (x, y) in positions.items():
        node = node_by_id.get(node_id)
        if not node:
            continue
        status = node.get("status", "not_attempted")
        fill = {
            "external_cited": "#e8edf7",
            "has_verified_internal_island": "#dff3e5",
            "has_faithful_internal_core": "#e6f0ff",
            "attempted_no_island": "#f7dddd",
            "run_failed": "#f7dddd",
            "run_started": "#efe7fb",
        }.get(status, "#fff2cc")
        node_elems.append(
            f'<g><rect x="{x}" y="{y}" width="160" height="54" rx="12" fill="{fill}" '
            f'stroke="#8f8172" stroke-width="1.4" />'
            f'<text x="{x+80}" y="{y+23}" font-size="12" text-anchor="middle">{esc(shorten(node["title"], 28))}</text>'
            f'<text x="{x+80}" y="{y+40}" font-size="10" text-anchor="middle" fill="#6f655c">{esc(status)}</text></g>'
        )
    return f'<svg viewBox="0 0 1220 560" width="100%" role="img" aria-label="Paper dependency graph">{defs}{"".join(edge_elems)}{"".join(node_elems)}</svg>'


def latest_run(target: dict[str, Any]) -> dict[str, Any] | None:
    runs = target.get("runs", [])
    return runs[-1] if runs else None


def paper_node_to_dict(node: PaperNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "kind": node.kind,
        "label": node.label,
        "title": node.title,
        "statement_tex": node.statement_tex,
        "proof_summary": node.proof_summary,
        "source_lines": node.source_lines,
        "status": node.status,
        "remaining_burden": node.remaining_burden,
        "suggested_internal_islands": node.suggested_internal_islands,
        "formal_islands_runs": [],
    }


def load_manifest_or_none() -> dict[str, Any] | None:
    path = CASE_ROOT / "paper_manifest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def load_manifest() -> dict[str, Any]:
    manifest = load_manifest_or_none()
    if manifest is None:
        raise FileNotFoundError(CASE_ROOT / "paper_manifest.json")
    return manifest


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."


if __name__ == "__main__":
    raise SystemExit(main())
