# Validation and release prerequisites

The qualified tool source is `a1fed146ed738fd50be224716ad6139e35d6d173`.
The clean, offline source build produced image
`sha256:9b86ff4a48f7568fd830fda6344e33103d812904cdfc0ac33c67fdf091a3954c`.
The [interface capture](pinned-interface.md) documents the actual CLI output,
source provenance and twelve-case sample-size limitation. The
[operator runbook](node.md) explains how to reproduce the build and bind the node.

Validation uses synthetic replay data and local endpoints. It establishes the
integration boundary, not model quality on an operator's workload:

- The node's 142 focused tests pass, including all twelve contract-wired
  subprocess chains, malformed and altered replay bundles, concurrent delivery,
  actual process crashes, immutable terminal recovery, gateway spend reservation,
  credential containment and sandbox refusal paths.
- The shared core contracts pass 32 new tests and their adjacent suite. The SPI
  suite passes 3,400 tests with four existing skips. Ruff, strict typing, package
  builds and the platform layering checks pass against the sibling source packages.
- An independent client drive passes 108 checks. It exercises the actual bounded
  HTTP transport against local provider fixtures and the production adapter
  against the real source-built container, without replacing the tool runner.
- In the actual 64-case run, the result contains 64 case identifiers, both judge
  orders, five passing gates and 192 local candidate/judge receipts. Duplicate
  delivery and a new handler instance replay the identical terminal without
  launching another container. A command also completes through the real local
  event bus and contract dispatch.
- The actual tool process runs as uid 1000 with only `PATH`, `HOME` and the
  per-run token variable. Its only host mounts are the read-only replay snapshot
  and synthetic repository. Final packet records contain only the gateway, with
  zero kernel drops. Deliberate IPv4 and IPv6 connection attempts are named and
  produce `egress_violation`.
- Oversized tool output, a deadline, missing monitor completion, dropped packets
  and monitor write errors refuse the run. Upstream attempts have durable unpriced receipts before forwarding;
  settlement replaces the same record without duplication. Provider failures
  retain those receipts; literal and structured credential echoes are rejected.

The metadata, runtime import sweep, topic closure, aliases and direct-model-call
policy checks pass. The canonical-file scan reports the same five pre-existing
shell-file findings as the unmodified upstream base; these changes add none.

The tool's cost estimate remains separate from measured gateway costs. Recorded
answers and the stub judge cause no upstream provider charge. A stub-judge pass
does not establish agreement with human labels or authorize a routing change.

Release is held until the core models land, the SPI protocol consumes the
maintainer-approved core dependency tag, and OmniMarket consumes the corresponding
released sibling packages. Local source-package overrides are validation aids and
are not committed dependency pins. The required hosted evidence gate refuses
fork heads; a maintainer must bring the contribution into an upstream-owned
branch to run that gate. No workflow bypass is included.

The labelled-data pilot remains a prerequisite after the node lands. The promised
operator-labelled public bundle was not available through the public repository
search, and no external judge credential was supplied. No agreement, kappa or
routing-quality claim is made from the synthetic or stub-judge results.
