-- OMN-21396: both harnesses share the hook-event projection.
ALTER TABLE omninode_internal.claude_hook_events
    ADD COLUMN IF NOT EXISTS harness TEXT NOT NULL DEFAULT 'claude'
    CHECK (harness IN ('claude', 'codex', 'unknown'));
CREATE INDEX IF NOT EXISTS claude_hook_events_harness_lane_emitted_idx
    ON omninode_internal.claude_hook_events (harness, lane, emitted_at DESC);
