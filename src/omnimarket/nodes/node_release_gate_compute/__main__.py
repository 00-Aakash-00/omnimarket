# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""JSON adapter for check callers that cannot import the installed node."""

from __future__ import annotations

import sys

from pydantic import ValidationError

from omnimarket.models.model_release_gate import ModelReleaseGateRequest
from omnimarket.nodes.node_release_gate_compute import NodeReleaseGateCompute


def main() -> int:
    try:
        request = ModelReleaseGateRequest.model_validate_json(sys.stdin.read())
    except ValidationError as exc:
        sys.stderr.write(f"release-gate: invalid request: {exc}\n")
        return 2
    result = NodeReleaseGateCompute().handle(request)
    sys.stdout.write(result.model_dump_json() + "\n")
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
