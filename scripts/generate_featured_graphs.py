#!/usr/bin/env python3
"""Generate homepage mini-graph assets for featured examples."""

from __future__ import annotations

from pathlib import Path

from formal_islands.site.featured_graphs import write_featured_graph_bundle


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    write_featured_graph_bundle(
        config_path=repo_root / "docs" / "featured_graphs.json",
        output_path=repo_root / "docs" / "generated" / "featured_graphs.js",
        repo_root=repo_root,
    )


if __name__ == "__main__":
    main()
