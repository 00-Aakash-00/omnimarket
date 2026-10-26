# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Read a session snapshot and assemble a deterministic install-confirm request."""

from __future__ import annotations

from omnimarket.models.context_install_confirm_request import (
    ModelInstallConfirmAssemblyRequest,
)
from omnimarket.models.context_install_confirm_trigger import (
    EnumInstallConfirmTriggerOutcome,
    ModelInstallConfirmTriggerResult,
    ModelSessionContentAppliedEvent,
)
from omnimarket.nodes.node_context_install_confirm_request_compute.handlers.handler_context_install_confirm_request import (
    HandlerContextInstallConfirmRequest,
)
from omnimarket.protocols.protocol_session_compact_source import (
    ProtocolSessionCompactSource,
)


class HandlerContextInstallConfirmTrigger:
    """Select a publishable request without I/O beyond the supplied reader."""

    def handle(
        self,
        applied_event: ModelSessionContentAppliedEvent,
        reader: ProtocolSessionCompactSource,
    ) -> ModelInstallConfirmTriggerResult:
        snapshot = reader.read(applied_event.session_id)
        if not snapshot.chunks:
            return ModelInstallConfirmTriggerResult(
                outcome=EnumInstallConfirmTriggerOutcome.NO_COMPACT_RECORDS
            )

        assembled = HandlerContextInstallConfirmRequest().handle(
            ModelInstallConfirmAssemblyRequest(
                session_id=applied_event.session_id,
                chunks=snapshot.chunks,
                activations=snapshot.activations,
                newest_captured_at=max(chunk.emitted_at for chunk in snapshot.chunks),
            )
        )
        if assembled.packages_unreadable and assembled.packages_read == 0:
            return ModelInstallConfirmTriggerResult(
                outcome=EnumInstallConfirmTriggerOutcome.ALL_PACKAGES_UNREADABLE,
                packages_unreadable=assembled.packages_unreadable,
            )

        return ModelInstallConfirmTriggerResult(
            outcome=EnumInstallConfirmTriggerOutcome.REQUEST_READY,
            request=assembled.request,
            packages_unreadable=assembled.packages_unreadable,
        )


__all__ = ["HandlerContextInstallConfirmTrigger"]
