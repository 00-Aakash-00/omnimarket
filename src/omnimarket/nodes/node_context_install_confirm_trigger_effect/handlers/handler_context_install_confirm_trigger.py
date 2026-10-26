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
    ModelSessionCompactSnapshot,
    ModelSessionContentAppliedEvent,
    ModelSessionContentProjectionAppliedEvent,
)
from omnimarket.nodes.node_context_install_confirm_request_compute.handlers.handler_context_install_confirm_request import (
    HandlerContextInstallConfirmRequest,
)
from omnimarket.protocols.protocol_session_compact_source import (
    ProtocolInstallConfirmPublisher,
    ProtocolSessionCompactProjectionSource,
    ProtocolSessionCompactSource,
)


class HandlerContextInstallConfirmTrigger:
    """Select a publishable request without I/O beyond the supplied reader."""

    def handle(
        self,
        applied_event: ModelSessionContentAppliedEvent,
        reader: ProtocolSessionCompactSource,
    ) -> ModelInstallConfirmTriggerResult:
        return self._assemble(
            applied_event.session_id, reader.read(applied_event.session_id)
        )

    async def handle_projected(
        self,
        applied_event: ModelSessionContentProjectionAppliedEvent,
        reader: ProtocolSessionCompactProjectionSource,
        publisher: ProtocolInstallConfirmPublisher,
    ) -> ModelInstallConfirmTriggerResult:
        """Resolve the writer's payload and publish only a complete unwrapped request.

        Transport failures propagate so the caller can retry the applied event.
        Duplicate delivery emits the same request; downstream folds are idempotent.
        """
        resolved = await reader.read_applied(applied_event)
        result = self._assemble(resolved.session_id, resolved.snapshot)
        if result.request is not None:
            await publisher.publish(result.request)
        return result

    def _assemble(
        self, session_id: str, snapshot: ModelSessionCompactSnapshot
    ) -> ModelInstallConfirmTriggerResult:
        if not snapshot.chunks:
            return ModelInstallConfirmTriggerResult(
                outcome=EnumInstallConfirmTriggerOutcome.NO_COMPACT_RECORDS
            )

        assembled = HandlerContextInstallConfirmRequest().handle(
            ModelInstallConfirmAssemblyRequest(
                session_id=session_id,
                chunks=snapshot.chunks,
                activations=snapshot.activations,
                newest_captured_at=snapshot.newest_captured_at,
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
