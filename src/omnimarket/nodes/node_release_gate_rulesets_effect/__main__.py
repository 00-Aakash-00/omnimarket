# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""CLI for apply-release-gate-rulesets.yml: plan by default, --apply to write."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Sequence

from omnimarket.nodes.node_release_gate_rulesets_effect import (
    NodeReleaseGateRulesetsEffect,
)
from omnimarket.nodes.node_release_gate_rulesets_effect.models.model_release_gate_rulesets import (
    ModelReleaseGateRulesetsRequest,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--directory", default=".github/rulesets")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    request = ModelReleaseGateRulesetsRequest(
        repo=args.repo,
        directory=args.directory,
        train_app_id=os.environ.get("RELEASE_TRAIN_APP_ID", ""),
        apply=args.apply,
    )
    try:
        result = NodeReleaseGateRulesetsEffect().handle(request)
    except (ValueError, subprocess.CalledProcessError) as exc:
        sys.stderr.write(f"release-gate rulesets: {type(exc).__name__}: {exc}\n")
        return 1
    sys.stdout.write("\n".join(result.lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
