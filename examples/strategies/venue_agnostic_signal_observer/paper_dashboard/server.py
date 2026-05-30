"""Embedded minimal web dashboard for paper strategy management.

Read-only — renders strategy state from the registry.  No write-back.
Disabled-by-refalsification strategies cannot be re-enabled from the UI in v0.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from ..paper.models import PaperStrategyState
from ..paper.registry import load_all_strategies, load_strategy

if TYPE_CHECKING:
    from flask import Flask as FlaskApp

try:
    from flask import Flask, render_template, abort

    _flask_available = True
except ImportError:
    Flask = None  # type: ignore[assignment]
    render_template = None  # type: ignore[assignment]
    abort = None  # type: ignore[assignment]
    _flask_available = False


def create_app(registry_dir: str | Path) -> FlaskApp | None:
    """Create a Flask app for the paper dashboard.

    Returns None if Flask is not installed.
    """
    if not _flask_available:
        return None

    registry_path = Path(registry_dir)
    app = Flask(
        __name__,
        template_folder=str(
            Path(__file__).parent / "templates"
        ),
    )

    @app.route("/")
    def index():
        strategies = load_all_strategies(registry_path)
        # Sort by promoted_at_utc descending (newest first)
        strategies.sort(
            key=lambda s: s.promoted_at_utc or "", reverse=True
        )
        return render_template("index.html", strategies=strategies)

    @app.route("/strategy/<strategy_id>")
    def strategy_detail(strategy_id: str):
        spec = load_strategy(strategy_id, registry_path)
        if spec is None:
            abort(404)
        return render_template(
            "strategy_detail.html", strategy=spec
        )

    return app


def main() -> None:
    """Run the dashboard server (for development use only)."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Paper strategy dashboard server"
    )
    parser.add_argument(
        "--registry-dir",
        type=str,
        default="reports/paper/registry",
        help="Paper strategy registry directory",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="HTTP port (default: 8080)",
    )
    args = parser.parse_args()

    app = create_app(args.registry_dir)
    if app is None:
        print("Flask is not installed. Cannot start dashboard.")
        return
    print(f"Starting paper dashboard on http://127.0.0.1:{args.port}")
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()