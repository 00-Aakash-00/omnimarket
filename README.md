# OmniMarket

OmniMarket provides portable ONEX workflow packages and contract-driven automation.
Documentation: [public knowledge base](https://github.com/OmniNode-ai/knowledge_base).


### Dev deployment health protocol

A compatible CI trigger can set `dev_deploy_protocol: health-v1` on a dev
redeploy-start command. The deploy effect persists one selection from fresh
capability heartbeats before signing and publishing the rebuild. A stopped
host's heartbeat expires after 90 seconds; the next command selects another
eligible instance with its matching verification surface. An empty pool refuses.
The policy is declared in the deploy effect's `routing_policy.yaml`.

Activation requires compatible deploy-agent and CI adapters and an authorized
capability heartbeat producer. Existing standalone agents reject the new wire
field, and the current CI resolver computes a table route, so legacy triggers
keep their existing wire shape until that migration. This producer protocol
alone does not remove the infrastructure routing table or prove a live deploy.

The contract's `ModelDevDeployHealth` event carries measured deploy acceptance,
verify readiness, priority and complete verification targets. Its producer must
beat at least every 30 seconds under an authorized bus principal. Missing,
stale, future-dated, unavailable and verify-unready observations are ineligible.
Agents and CI must consume the same signed `dev_deploy_route`; CI can derive
its runner and receipt lane through `verify_outputs()`. Neither consumer may
independently reselect from changing health.

The selection is durable across process restarts and ambiguous broker failures.
A retried correlation retains its original instance; moving an already accepted
job requires executor fencing and an explicit retry policy. New commands can
select surviving or newly advertised instances without a route-table edit.
Live job acceptance and running-revision ancestry remain required evidence.
