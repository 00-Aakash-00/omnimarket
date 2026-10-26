# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""CLI for release-gate.yml: print the gate request for the running event.

Exit 3 when any fact cannot be read; the workflow treats that as a refusal.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

from omnimarket.nodes.node_release_gate_collect_effect import (
    NodeReleaseGateCollectEffect,
)
from omnimarket.nodes.node_release_gate_collect_effect.models.model_release_gate_collect import (
    ModelReleaseGateCollectRequest,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--event-path", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument(
        "--identities", default=os.environ.get("RELEASE_GATE_IDENTITIES_FILE", "")
    )
    args = parser.parse_args(argv)
    try:
        request = NodeReleaseGateCollectEffect().handle(
            ModelReleaseGateCollectRequest(
                repo=args.repo,
                event_name=args.event_name,
                event_path=args.event_path,
                policy_path=args.policy,
                identities_path=args.identities,
            )
        )
    except Exception as exc:
        sys.stderr.write(
            f"release-gate: cannot collect facts: {type(exc).__name__}: {exc}\n"
        )
        return 3
    sys.stdout.write(request.model_dump_json() + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
