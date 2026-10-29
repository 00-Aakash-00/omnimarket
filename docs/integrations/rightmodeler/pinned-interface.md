# Pinned RightModeler interface

The experimental adapter qualifies commit
`a1fed146ed738fd50be224716ad6139e35d6d173` against the actual CLI. The
[capture manifest](captures/manifest.json) records the tool bundle hash, commands,
environment names, exit codes and local gateway call counts. All inputs and
answers are synthetic. Native Node.js 24 captures qualify the wire interface;
the separate sandbox captures exercise the actual Linux container and event bus.
Neither claims external model quality.

The source was fetched with Vercel opensrc and inspected with Graphify AST
graphs. Every tracked source file used for the native captures was compared
with the verified Git archive of the qualified commit; there were zero
differences, excluding generated bundles. The archive hash is recorded in the
capture manifest. Directory names are not accepted as source provenance.

Commit `7d23e79ea6caae76c2a1cf5004f619d5a6f5762d` is not qualified: its clean
build fails because a test helper imports an unexported symbol. The captures
qualify the current immutable pin above and do not claim that this older commit
ran successfully.

Dependencies were prefetched, followed by
`pnpm install --offline --frozen-lockfile --ignore-scripts` and
`pnpm exec turbo run build --filter=@rightmodeler/cli`. The runtime receives only
`PATH`, `HOME` and a synthetic gateway-token variable. No real provider key or
external model request is involved.

## CLI behavior

`init --through report --yes --output json` returns pipeline status, while
`report --output json` returns `verdicts`, `families`, `referenceCeilings`,
`judgeDisagreement`, `spend`, `stratumWeights`, `caps`, `candidateErrors`,
`receipts`, `blockedFamilies`, `apply` and `reportPath`. The older
`recommendation-report.schema.json` in this pin describes another shape. The
adapter validates the captured CLI interface instead of accepting that schema.

Exit 0 means no recommendation, exit 1 means a recommendation, exit 2 means
input is required, exit 3 means budget refusal, and exit 10 means runtime
failure. Error objects appear on stderr. JSON warnings also appear on stderr,
so consumers must distinguish warning events from terminal errors.

At quality floor 0.85, twelve cases produce six holdout cases against a required
minimum of twenty-two. This returns a completed abstention with no replay calls.
The additional sixty-four-case capture reaches both shortlist and holdout:

| Candidate pricing | Include free | Decision | Candidate calls | Judge calls |
| --- | --- | --- | --- | --- |
| Positive | No | recommend | 64 | 128 |
| Zero | No | abstain | 0 | 0 |
| Zero | Yes | recommend | 64 | 128 |

The zero-priced candidate requires `--include-free`. Judge catalog pricing stays
nonzero so the pinned tool can select a neutral third-vendor judge. These are
list prices for tool compatibility; they are not evidence of provider charges.

## Actual sandbox and bus proof

The source-verified image is
`sha256:9b86ff4a48f7568fd830fda6344e33103d812904cdfc0ac33c67fdf091a3954c`.
It was built from the verified Git archive with networking disabled for every
build step, a frozen offline install, and lifecycle scripts disabled. The
production validator ran the actual bundled CLI in a read-only Linux arm64
container as UID 1000, with all capabilities dropped and no new privileges.
The only mounted inputs were the replay bundle and synthetic scanner repository.
The actual CLI process had exactly `HOME`, `PATH` and `RM_RUN_TOKEN` in its
environment; the image's default environment was not used as a substitute check.

| Surface | Cases | Result | Independent case evidence | Local call receipts |
| --- | --- | --- | --- | --- |
| Production validator and container | 12 | completed, abstain | 0 | 0 |
| Production validator and container | 64 | completed, recommend | 64 | 192 |
| Registered handler, native event bus and container | 64 | completed, recommend | 64 | 192 |
| Real forbidden IPv4 and IPv6 socket attempts | 12 | failed, egress_violation | 0 | 0 |

The [64-case container capture](captures/sandbox/replay-64.json) contains the
actual CLI output, typed result, process environment and isolation settings.
Its independent NFLOG monitor recorded 964 packets, all addressed to the one
allowed gateway endpoint, with zero drops. The
[bus capture](captures/sandbox/bus-replay-64.json) recorded the full command to
handler to validator to container to terminal path, with 962 allowed packets.
Redelivery returned an identical correlated terminal payload and did not start
another tool run.

The [forbidden-destination capture](captures/sandbox/forbidden-destination.json)
injected real socket attempts from the tool's UID 1000 process to
`203.0.113.1:443` and `[2001:db8:ffff::7]:443`. Both were rejected and named by
the external monitor, and the validation returned `failed/egress_violation`.
All four runs removed their tool, monitor and relay containers afterward.

The monitor records application-owned packets from UID 1000. Kernel-generated
IPv6 neighbor and multicast management packets are excluded from evidence while
all unauthorized traffic remains denied. Controlled routes ensure even otherwise
unroutable application connection attempts reach the logging and rejection rules.
Public packet captures retain representative lines, all unique destinations,
exact packet counts and the complete original trace hash. Repeated native wire
examples are sampled; complete request counts remain in the manifest. Bulky raw
traces are temporary validation output.

Each tool command's combined stdout and stderr is limited to 8 MiB. Initialization
and report share one run deadline, defaulting to 300 seconds. A missing or errored
packet capture, a missing final capture footer, or any packets dropped by the
monitor refuses validation. The complete original traces for all four captures
were checked against this final monitor-health policy and passed.

These checks establish the integration's orchestration and isolation using
recorded answers and the explicitly selected stub judge, with zero external
model calls. Measuring real model quality and provider charges additionally
requires an operator-approved replay corpus and registered judge credentials;
that external-model evaluation is not claimed here.

## Evidence extraction

The report's family verdict includes case identifiers but no individual ordered
judge verdicts. The adapter independently observes the exact gateway requests
and responses. For comparison, the captures also decode the tool's immutable
store facts: execution records join to assessment records by `executionId`;
`trajectoryId` retains the supplied case identifier; and both verdicts live at
`assessment.artifactRef.evidence.positionSwapVerdicts`. The recorded
[per-case extraction](captures/native-64-priced/per-case-evidence.json) includes
the exact relative artifact paths. Family aggregate scores must never be copied
into individual case evidence.

The result names its judge from independently observed gateway requests because
the report has no judge-model field. `reported_decision` preserves the nested
tool verdict and `recommendation_effective` preserves the tool's effective flag.
A nominal `recommend` that is gated or unconfirmed becomes `inconclusive` in the
normalized decision, and names no recommended model. Every gate is retained.

Recorded candidate replies and the explicitly selected stub judge never call an
upstream provider. Their local receipts carry zero synthetic estimated cost.
For real forwarded calls, provider-reported charges and measured-token list-price
estimates are kept separately. A durable spend ledger reserves a conservative
bound before each attempt. A failed or interrupted attempt keeps its reservation
after restart; reservation totals are budget consumption, not asserted provider
charges.

Every upstream attempt receives a ledger receipt, including HTTP or response
failures. When usage cannot be measured, the receipt is `unpriced` with a null
cost; its zero token counters mean unmeasured rather than zero provider use.
The reservation remains outstanding. Provider failures are `tool_failed`,
malformed responses are `schema_mismatch`, and a different model answering the
judge request is `no_neutral_judge`. These do not become egress violations;
egress violations identify unauthorized requests or destinations.
The sanctioned transport also refuses a response containing the resolved
platform credential, before any provider content can be returned to the tool.

Judge requests contain a generic task description and the reference/candidate
answers. They do not contain the original case identifier. Identical answer
pairs across cases cannot be attributed to an individual case without another
signal, so the adapter refuses ambiguous pairs.
