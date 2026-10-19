-- SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
-- SPDX-License-Identifier: MIT
-- Runtime state_io target: omnibase_infra, matching the contract's database.
-- Apply through the sanctioned migration delivery before activating the feed.
-- The family key serializes grouping and distinct-PR counts across consumers.
CREATE TABLE IF NOT EXISTS public.review_uncoded_signature_state (
    family TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    state TEXT NOT NULL,
    in_flight BOOLEAN NOT NULL DEFAULT FALSE,
    payload JSONB NOT NULL,
    version INTEGER NOT NULL DEFAULT 0,
    pending_emissions JSONB,
    publish_attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_review_uncoded_signature_state_recovery
    ON public.review_uncoded_signature_state (updated_at)
    WHERE in_flight AND pending_emissions IS NOT NULL;

CREATE OR REPLACE FUNCTION public.refresh_review_uncoded_signature_state_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_review_uncoded_signature_state_updated_at
    ON public.review_uncoded_signature_state;
CREATE TRIGGER trg_review_uncoded_signature_state_updated_at
    BEFORE UPDATE ON public.review_uncoded_signature_state
    FOR EACH ROW
    EXECUTE FUNCTION public.refresh_review_uncoded_signature_state_updated_at();
