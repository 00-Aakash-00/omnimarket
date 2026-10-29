# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""All twelve contract chains through the real adapter with a stub tool process.

The selected synthetic process reports exercise mapping and terminal behavior.
They are not a claim about the statistical verdict of a real twelve-case run;
separate captured-output and sandbox tests exercise the actual pinned CLI.
"""

from __future__ import annotations

import copy
import importlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from omnibase_core.contracts.contract_loader import load_contract
from omnibase_core.models.model_swap_validation import ModelModelSwapValidationRequest

from omnimarket.nodes.node_model_swap_validation_effect.adapters.rightmodeler.container import (
    ToolRun,
)
from omnimarket.nodes.node_model_swap_validation_effect.adapters.rightmodeler.gateway import (
    GatewaySession,
    ModelPrice,
    judge_body,
)
from omnimarket.nodes.node_model_swap_validation_effect.adapters.rightmodeler.replay_set import (
    install_replay_set,
    wire_messages,
)
from omnimarket.nodes.node_model_swap_validation_effect.adapters.rightmodeler.validator import (
    ValidatorRightModeler,
)

from .golden_fixture import golden_bundle

_REPOSITORY = Path(__file__).resolve().parents[4]
_CHAINS = _REPOSITORY / "tests/golden_chains/model_swap_validation"
_NODE = _REPOSITORY / "src/omnimarket/nodes/node_model_swap_validation_effect"
_FIXTURE = Path(__file__).parent / "fixtures/report_recommend.json"


def _report(
    req: ModelModelSwapValidationRequest, gateway: GatewaySession, decision: str
) -> dict[str, Any]:
    report = json.loads(_FIXTURE.read_text())
    verdict = report["verdicts"][0]
    verdict["familyId"] = req.task_type
    verdict["candidateId"] = req.candidate_model
    verdict["caseIds"] = [case["case_id"] for case in gateway.replay_set.cases]
    verdict["decision"] = decision
    family = report["families"][0]
    family["familyId"] = req.task_type
    family["verdict"] = copy.deepcopy(verdict)
    family["effectiveRecommendation"] = decision == "recommend"
    family["decisionDisplay"] = decision
    family["selection"]["confirmedCandidateId"] = req.candidate_model
    family["selection"]["selectedCandidateId"] = req.candidate_model
    return report


def _observe_cases(gateway: GatewaySession) -> None:
    for index, case in enumerate(gateway.replay_set.cases, 1):
        status, _ = gateway.handle(
            "POST",
            "/v1/chat/completions",
            gateway.token,
            {
                "model": gateway.request.candidate_model,
                "messages": wire_messages(case["messages"]),
                "max_tokens": 256,
                "stream": False,
            },
        )
        assert status == 200
        for first in (False, True):
            status, _ = gateway.handle(
                "POST",
                "/v1/chat/completions",
                gateway.token,
                judge_body(
                    gateway.request.task_type,
                    f"Reference {index}.",
                    f"Candidate {index}.",
                    model=gateway.judge_model,
                    candidate_first=first,
                ),
            )
            assert status == 200


@pytest.mark.parametrize(
    "chain_path", sorted(_CHAINS.glob("chain_*.json")), ids=lambda path: path.stem
)
async def test_contract_wired_golden_chain(tmp_path: Path, chain_path: Path) -> None:
    chain = json.loads(chain_path.read_text())
    req = ModelModelSwapValidationRequest.model_validate(chain["request"])
    root = tmp_path / "replay"
    manifest, files = golden_bundle()
    digest = install_replay_set(root, manifest, files)
    if "replay_set_missing" not in chain_path.name:
        assert req.replay_set_digest == digest
    starts: list[str] = []
    gateway_calls: list[int] = []

    def run_tool(request, synthetic_repo, working, gateway):
        starts.append("started")
        if "timeout" in chain_path.name:
            try:
                subprocess.run(
                    [sys.executable, "-c", "import time;time.sleep(10)"],
                    timeout=0.02,
                    check=False,
                    capture_output=True,
                )
            except subprocess.TimeoutExpired:
                return ToolRun(
                    exit_code=10,
                    stdout="",
                    stderr="",
                    report=None,
                    egress="",
                    forbidden_destinations=(),
                    timed_out=True,
                )
            raise AssertionError("sleeping fixture must exceed the process deadline")
        exit_code = chain["stub"]["exit_code"]
        if exit_code in (0, 1) and "schema_mismatch" not in chain_path.name:
            _observe_cases(gateway)
            report = _report(request, gateway, chain["stub"]["decision"])
        elif "schema_mismatch" in chain_path.name:
            report = {}
        else:
            report = None
        gateway_calls.append(len(gateway.calls))
        payload = working / "chosen-tool-report.json"
        payload.write_text(json.dumps(report))
        code = chain["stub"]["tool_error_code"]
        error = (
            json.dumps(
                {
                    "code": code,
                    "message": "Synthetic fixture failure",
                    "remedy": "Review fixture",
                }
            )
            if code
            else ""
        )
        script = "import pathlib,sys;print(pathlib.Path(sys.argv[1]).read_text());print(sys.argv[3],file=sys.stderr);sys.exit(int(sys.argv[2]))"
        process = subprocess.run(
            [sys.executable, "-c", script, str(payload), str(exit_code), error],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        return ToolRun(
            exit_code=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
            report=report,
            egress="",
            forbidden_destinations=(),
        )

    prices = {
        req.reference_model: ModelPrice(0.00001, 0.00003),
        req.candidate_model: ModelPrice(0.000001, 0.000003),
        "example-judge/neutral": ModelPrice(0.00002, 0.00006),
    }
    if "candidate_not_cheaper" in chain_path.name:
        prices[req.candidate_model] = prices[req.reference_model]
    adapter = ValidatorRightModeler(
        replay_root=root,
        state_directory=tmp_path / "provider-state",
        tool_pin=None
        if "unpinned_tool" in chain_path.name
        else "a1fed146ed738fd50be224716ad6139e35d6d173",
        image_digest="sha256:" + "b" * 64,
        prices=prices,
        stub_judge_model="example-judge/neutral",
        run_tool=run_tool,
    )
    contract = load_contract(_NODE / "contract.yaml")
    binding = contract["handler"]
    module = importlib.import_module(binding["module"])
    handler_type = getattr(module, binding["class"])
    handler = handler_type(
        validator=adapter,
        state_directory=tmp_path / "handler-state",
        timeout_seconds=20,
    )
    result = await handler.handle(req)
    payload = result.model_dump(mode="json")
    for key, expected in chain["expected"].items():
        assert payload[key] == expected, (key, payload)
    if chain["stub"]["exit_code"] is None and "timeout" not in chain_path.name:
        assert starts == []
        assert gateway_calls == []
    else:
        assert starts == ["started"]
    assert (await handler.handle(req)).model_dump_json() == result.model_dump_json()
    assert len(starts) <= 1
