<!-- SPDX-FileCopyrightText: 2026 OmniNode.ai Inc. -->
<!-- SPDX-License-Identifier: MIT -->

# Operate the model-swap validation node

`node_model_swap_validation_effect` collects evidence about one candidate model.
Every terminal has `evidence_only: true`. A recommendation does not authorize a
routing change; the platform's routing and quality gates retain that authority.
The node is experimental. Read the [qualified CLI interface](pinned-interface.md)
for captured output, decision normalization and evidence provenance.

## Installation prerequisites and release hold

Use Python 3.12, `uv`, and a Linux Docker engine with bridge networking, IPv4 and
IPv6 iptables, NFLOG and packet capture. Docker Desktop or a Linux VM can provide
the engine on another host. The operator account must be able to create and
remove containers and private networks. The monitor image must provide `sh`,
`iptables`, `ip6tables`, `tcpdump`, `pkill`, `pgrep` and `socat`.

The shared request/result types must first ship from `omnibase_core`, followed
by a released `omnibase_spi` containing `ProtocolModelSwapValidator`. Update
OmniMarket's dependency floors, overrides and lockfile together after those
releases. Until then, the node changes are held for dependent review; a normal
installation from the existing published dependency pins cannot load the new
APIs. Cross-repository source-wheel validation does not remove this release
hold. Do not deploy by weakening dependency or import checks.

## Build the qualified tool image

The supported tool source pin is
`a1fed146ed738fd50be224716ad6139e35d6d173`. The contract, adapter and qualified
capture must agree. A different source pin requires a new capture and a reviewed
update of the output model in the same change. Use Git's archived content at the
commit; a fetched directory name is not source provenance.

Choose an immutable Node.js 24 Linux base image that includes Git and a shell.
Prefetch on the same platform and with the same image used by the offline build.
Example paths and image references below are operator-owned placeholders:

```bash
export RM_TOOL_PIN=a1fed146ed738fd50be224716ad6139e35d6d173
export RM_SOURCE=/srv/model-swap/tool-source
export RM_BUILD_CONTEXT=/srv/model-swap/image-context
export RM_PREFETCH_SOURCE=/srv/model-swap/prefetch-source
export RM_DOCKER_CONTEXT=operator-context
export RM_BASE_IMAGE='node:24-bookworm@sha256:REPLACE_WITH_VERIFIED_DIGEST'

git clone --no-checkout https://github.com/elm-os/rightmodeler.git "$RM_SOURCE"
git -C "$RM_SOURCE" rev-parse "$RM_TOOL_PIN^{commit}"
mkdir -p "$RM_BUILD_CONTEXT" "$RM_PREFETCH_SOURCE"
docker --context "$RM_DOCKER_CONTEXT" pull "$RM_BASE_IMAGE"
```

Perform the networked preparation once, without any operator replay bundle or
provider credential mounted. Bootstrap exactly pnpm 11.7.0 into the build's
separate tooling directory; repository work uses that pinned pnpm. Preserve the
resulting tooling lockfile, package store, cache and home directory. pnpm 11
also caches supply-chain policy metadata; preserving only the package store is
insufficient for a closed build.

```bash
docker --context "$RM_DOCKER_CONTEXT" run --rm --network bridge \
  --mount "type=bind,src=$RM_SOURCE,dst=/checkout,readonly" \
  --mount "type=bind,src=$RM_BUILD_CONTEXT,dst=/build" \
  --mount "type=bind,src=$RM_PREFETCH_SOURCE,dst=/prefetch" \
  --env RM_TOOL_PIN --entrypoint /bin/sh "$RM_BASE_IMAGE" -eu -c '
    git -C /checkout archive "$RM_TOOL_PIN" | tar -x -C /prefetch
    mkdir -p /build/tooling /build/store /build/cache /build/home
    npm install --prefix /build/tooling --ignore-scripts --save-exact pnpm@11.7.0
    cd /prefetch
    env PATH=/build/tooling/node_modules/.bin:/usr/local/bin:/usr/bin:/bin \
      XDG_CACHE_HOME=/build/cache HOME=/build/home \
      pnpm fetch --store-dir /build/store
    env PATH=/build/tooling/node_modules/.bin:/usr/local/bin:/usr/bin:/bin \
      XDG_CACHE_HOME=/build/cache HOME=/build/home \
      pnpm install --lockfile-only --frozen-lockfile --ignore-scripts \
        --store-dir /build/store
  '
```

Start with an empty prefetch directory and a fresh build context. The context
must contain `tooling/`, `store/`, `cache/` and `home/`, but no `source/` yet.
The supported builder creates `source/` itself from the verified Git archive:

```bash
uv run --frozen python - <<'PY'
import os
from pathlib import Path
from omnimarket.nodes.node_model_swap_validation_effect.adapters.rightmodeler.container import build_pinned_image

image = build_pinned_image(
    Path(os.environ["RM_BUILD_CONTEXT"]),
    source_repository=Path(os.environ["RM_SOURCE"]),
    base_image=os.environ["RM_BASE_IMAGE"],
    tool_commit=os.environ["RM_TOOL_PIN"],
    docker=("docker", "--context", os.environ["RM_DOCKER_CONTEXT"]),
)
print(image)
PY
```

The builder inspects the prefetched base image and uses `--pull=false` and
`--network=none`. Dependency installation uses
`pnpm install --offline --frozen-lockfile --ignore-scripts`, followed by the
explicit CLI build. Its Docker context excludes committed/generated bundles,
`dist` and `node_modules`; the runtime receives the newly built CLI, never the
npm release or a committed bundle. Retain the returned `sha256:...` image ID,
the source archive hash, base-image digest and build log as build evidence.
Use that immutable image ID in the operator overlay. Prefetch and pin the
monitor image separately as `image@sha256:...`.

## State and verified replay bundles

Choose a persistent, private state directory for the runtime account, for
example `/var/lib/onex/model-swap`, and an operator replay root such as
`/var/lib/onex/model-swap/replay-sets`. Keep these paths on storage supporting
SQLite transactions and OS file locks. All workers handling the same command
must share the same state and lock semantics. The implementation is not a
distributed lease service across unrelated hosts or storage systems.

Install bundles through `install_replay_set(root, manifest, files)`, where
`files` maps relative file names to bytes. The installer flushes files and
atomically installs the bundle under the SHA-256 of the canonical manifest;
partial temporary installs have no valid digest address. The request carries
that digest, not a caller-supplied arbitrary directory.

A bundle contains:

- `traces.jsonl`: text-only OpenAI records with exactly `case_id`, `name`,
  `model`, `messages`, `response` and `usage`. `name` equals the request's
  `task_type`; `model` equals `reference_model`. Token counts are nonnegative
  integers under `prompt_tokens` and `completion_tokens`.
- For recorded candidate answers, `candidate_answers.json`: entries keyed by
  `message_key(messages)`, with `content`, `tokens_in` and `tokens_out`. Use
  the helper rather than hashing an independently formatted message array;
  it reproduces the pinned tool's system-message ordering.
- A manifest with `schema_version: 1`, `source_type`, sorted unique `case_ids`,
  a `files` mapping of file hashes, and `provenance` for every case. Each
  provenance entry includes `source` and `approved_external_judge`.

The loader checks every declared file hash and case/provenance join before a
sandbox starts. Missing, altered or incomplete bundles fail
`replay_set_missing`. Only cases explicitly approved for an external judge may
reach a backend judge. Stub judging stays local and does not grant approval for
future backend requests. The work-unit repository is attribution only; the
actual repository is never mounted. The adapter creates and scans a synthetic
Git repository, and never executes its TypeScript file.

## Resolve credentials outside the tool

Provider keys belong to the platform secret store. Pass a `ProtocolSecretStore`
when constructing the adapter, or configure
`rightmodeler.secret_resolver_config_path` with an explicit resolver file.
The bounded upstream transport receives only logical `secret_ref` values and
resolves the key at the effect boundary. It does not use `api_key_env` or an
environment fallback.

For example, an operator with provisioned secret-volume files can use:

```yaml
enable_convention_fallback: false
require_infisical_for_required_secrets: false
required_secrets:
  - validation.judge.api_key
  - validation.candidate.api_key
mappings:
  - logical_name: validation.judge.api_key
    source:
      source_type: file
      source_path: /run/secrets/model-swap-judge
  - logical_name: validation.candidate.api_key
    source:
      source_type: file
      source_path: /run/secrets/model-swap-candidate
```

These are references and paths, not key values. Provision the files through the
operator's secret-management system; do not place values in the contract,
request, command line or environment. Infisical-backed mappings can use the
same platform resolver interface. Neither the resolver config nor the secret
files enter the tool container. Its explicit execution environment contains
only `PATH`, `HOME` and `RM_RUN_TOKEN`; that random per-run token authenticates
the local gateway and is not a provider credential.

## Configure an operator contract

Merge the following operator-only block into a copy of the packaged
`contract.yaml` and save the complete rendered file outside the source tree,
for example `/etc/onex/model-swap/contract.yaml`. This is a top-level overlay;
retain all packaged topic declarations, terminal maps, analysis allowlists and
`tool_pin` unchanged. The handler refuses an overlay that changes those
boundaries. Replace every example image, model, price, endpoint and path with
operator-approved values before use. Prices are USD **per token**, not per
million tokens, and the example prices are synthetic.

```yaml
rightmodeler:
  replay_root: /var/lib/onex/model-swap/replay-sets
  image_digest: sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
  secret_resolver_config_path: /etc/onex/model-swap/secret-resolver.yaml
  stub_judge_model: example-judge/neutral
  prices:
    example-reference/large: {input: 0.00001, output: 0.00003}
    example-candidate/small: {input: 0.000001, output: 0.000003}
    example-judge/neutral: {input: 0.00002, output: 0.00006}
  judge_backends:
    example-judge-backend:
      backend_id: example-judge-backend
      model_id: example-judge/neutral
      endpoint_ref: https://judge.example.invalid/v1/chat/completions
      max_tokens: 4096
      timeout_ms: 30000
      secret_ref: validation.judge.api_key
      supports_response_format_json_schema: true
  candidate_backend:
    backend_id: example-candidate-backend
    model_id: example-candidate/small
    endpoint_ref: https://candidate.example.invalid/v1/chat/completions
    max_tokens: 4096
    timeout_ms: 30000
    secret_ref: validation.candidate.api_key
  runtime:
    docker: [docker, --context, operator-context]
    monitor_image: registry.example.invalid/monitor@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
    gateway_host_address: host.docker.internal
    timeout_seconds: 300
```

The three model IDs must be distinct and have distinct vendor prefixes before
`/`, so the pinned tool has exactly one neutral judge choice. Configure a price
for each. The candidate must be cheaper than the reference for the verified
replay's recorded token usage. Missing pricing, backend configuration or an
image pin refuses before the tool starts. The qualified adapter includes free
candidates explicitly; catalog judge pricing must still support judge selection.

`candidate_backend` is required only for `answer_source: live`.
`judge_backends[judge_backend_id]` is required only for `judge_mode: backend`.
Recorded candidates and the explicit stub judge make no upstream calls.
Backend mode also requires the configured secret store, including for a local
endpoint. Endpoint URLs are complete chat-completion URLs, with no embedded
credential, query or fragment. The upstream transport does not follow redirects
or retry. The gateway reserves budget separately before each attempt and bounds
output tokens. Preserve the budget ledger after interruption; deleting it would
forget outstanding reservations.

The Python gateway listens outside the tool's network namespace. Set
`gateway_host_address` to an address reachable **from the relay container** at
the host's bound gateway port. `host.docker.internal` is the default, with the
engine's host-gateway mapping; verify its meaning on the chosen Docker engine
or VM. Container loopback is not the Python host. The relay joins a private
internal network and the engine bridge; the tool shares a separate monitor's
namespace, whose firewall permits only the relay address and gateway port.
Missing NFLOG readiness or final capture is a terminal failure. The tool cannot
change those rules and receives no Docker socket or source-control credential.

## Invoke and receive a terminal

The installed package registers `node_model_swap_validation_effect` in
`onex.nodes`. For normal runtime construction, configure these non-secret path
settings on the runtime process:

```bash
export ONEX_MODEL_SWAP_STATE_DIRECTORY=/var/lib/onex/model-swap
export ONEX_MODEL_SWAP_CONTRACT_PATH=/etc/onex/model-swap/contract.yaml
```

The contract-path setting selects the full rendered file, not a partial overlay.
An explicit constructor argument takes precedence. To invoke the same handler
directly over an operator request file:

```python
import asyncio
import json
from pathlib import Path
from omnibase_core.models.model_swap_validation import ModelModelSwapValidationRequest
from omnimarket.nodes.node_model_swap_validation_effect import HandlerModelSwapValidation

request = ModelModelSwapValidationRequest.model_validate(
    json.loads(Path("/var/lib/onex/model-swap/request.json").read_text())
)
handler = HandlerModelSwapValidation(
    state_directory=Path("/var/lib/onex/model-swap"),
    contract_path=Path("/etc/onex/model-swap/contract.yaml"),
)
terminal = asyncio.run(handler.handle(request))
print(terminal.model_dump_json())
```

A request names a unique `correlation_id`, `task_type`, `reference_model`,
`candidate_model`, `replay_set_digest`, `quality_floor`, `max_cost_usd`,
`work_unit_repository`, `judge_mode` and `answer_source`. Use `0.8 < quality_floor
< 1.0` and a positive finite budget. Backend judging additionally names
`judge_backend_id`. Start synthetic operator checks with `judge_mode: stub` and
`answer_source: recorded`; a completed stub result remains labelled as such.

For bus operation, use the existing runtime publisher and command envelope.
Read the command topic from `event_bus.subscribe_topics` in the packaged
contract, and the two reply topics from `runtime_dispatch.terminal_events`.
Subscribe to both terminals before publishing. The payload is the same typed
request; envelope and payload must carry the same correlation ID. Runtime
dispatch calls `handle_async`, and its result applier derives the terminal topic
from the concrete event class and contract. Read back a terminal matching the
request's correlation ID and validate its payload as
`ModelModelSwapValidationResult`. A registered entry point or a successful
publish alone does not prove completion. The local event-bus tests exercise
this command-to-terminal path; production transport credentials and runtime
boot remain the operator's deployment responsibilities.

## Redelivery, recovery and retention

The handler persists a request hash, owner fence and start intent before invoking
the validator. Concurrent duplicates wait for that owner. After terminal storage,
redelivery republishes the identical terminal and does not call the validator
again. Reusing a correlation ID with a changed request is rejected; submit a new
ID for a new validation.

An interrupted pre-intent owner can be reacquired. After start intent, recovery
stops deterministic tool/monitor/relay resources and stores a timeout instead of
launching a replacement. Resource launches share a cancellation fence with
recovery, so a worker paused during preflight cannot start a container later.
Keep the durable state directory when restarting the runtime. Keep a stable
request identity; deleting state and resubmitting the old ID defeats deduplication.

The CLI store and copied run inputs are temporary and removed after the run.
Only validated terminal evidence and node-owned budget/state records survive;
operator replay bundles retain their own access and retention policy. Do not
write terminal data into routing tables or treat it as an apply instruction.

## Verification and public-data proof

Run the node's focused tests, lint/type checks, runtime import sweep and metadata
checks with the target repository's `uv` workflow. A real sandbox check must also
verify the tool image label, exact source pin, recorded digest, clean environment,
read-only mounts and final kernel egress capture. Include a separate deliberate
forbidden-destination probe and require `egress_violation`; a unit stub cannot
prove the firewall works. Capture one correlated terminal through the operator's
actual event transport before promoting a deployment.

Synthetic fixtures, a stub judge, fake-process command chains and an in-memory
bus prove implementation behavior, not agreement with human labels. The genuine
public-data pilot still requires the maintainer-supplied labelled replay bundle,
recorded candidate answers and labels, plus an operator-selected real judge
backend and its credential. With those prerequisites, join observed per-case
verdicts to labels, report refusals and missing cases, raw agreement and Cohen's
kappa with 95% intervals, and separate reported judge charges from estimates.
Until that pilot exists, no public-data accuracy or production routing benefit
is established, and no routing change is recommended.
