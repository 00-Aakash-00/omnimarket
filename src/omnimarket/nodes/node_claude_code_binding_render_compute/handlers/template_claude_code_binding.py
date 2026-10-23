# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""The mod-module template (OMN-18531, T0.3).

The template holds NO branching of its own: the only decisions in the rendered text
are table lookups (the exit/stdout dispatch tables and the fixed failure table).
Every value from a contract is substituted as a JSON string/number literal by the
handler, never as code. The template lives with the render node, not in the mods
location: the rendered module is written at install and tracked nowhere.
"""

from __future__ import annotations

MODULE_HEADER = """\
// Rendered by node_claude_code_binding_render_compute from client_bindings.claude_code.
// Host maximum mode: @@max_mode@@. Generated; never hand-edited; not tracked in any repository.
import type { Register } from 'claude-code'

const KIND = { true: 'never_started', false: 'other' }
const FAILED = 'onex hook adapter did not answer: '
const OUTPUT = [
  ($, ran, next, e) => next(e),
  ($, ran, next, e) => JSON.parse(ran.stdout),
]
const DISPOSE = {
  true: ($, ran, next, e) => OUTPUT[Math.min(ran.stdout.length, 1)]($, ran, next, e),
  false: ($, ran, next, e) => FAILURE.other($, next, e, 'exit ' + ran.exitCode),
}
const FAILURE = {
  @@never_started_line@@,
  @@other_line@@,
}
"""

BINDING_CONSTANTS = """\
const ARGV_@@name@@ = @@argv@@
const PATTERN_@@name@@ = new RegExp(@@pattern@@)
const DEADLINE_@@name@@ = @@deadline_ms@@
"""

REGISTER_OPEN = """\

export const register: Register = on => {
"""

BINDING_HOOK = """\
  on(@@event@@, @@matcher@@, async ($, e, next) =>
    $.process.run(ARGV_@@name@@, { stdin: JSON.stringify(e), timeoutMs: DEADLINE_@@name@@ }).then(
      ran => DISPOSE[String(ran.exitCode === 0)]($, ran, next, e),
      err => FAILURE[KIND[String(PATTERN_@@name@@.test(String(err)))]]($, next, e, String(err)),
    ),
  ).catch(($, e, next) => FAILURE.other($, next, e, 'hook threw'))
"""

REGISTER_CLOSE = """\
}
"""

# OMN-21341 (OMN-21116 AC2): every failure path writes exactly one transcript line,
# then falls back natively or denies. The comma expression keeps the table free of
# branching; the reason is the exit code or the error text, never contract data.
REQUIRED_NEVER_STARTED = "never_started: ($, next, e, why) => ($.ui.log(FAILED + why), { deny: @@deny_text@@ })"
REQUIRED_OTHER = (
    "other: ($, next, e, why) => ($.ui.log(FAILED + why), { deny: @@deny_text@@ })"
)
NATIVE_NEVER_STARTED = (
    "never_started: ($, next, e, why) => ($.ui.log(FAILED + why), next(e))"
)
NATIVE_OTHER = "other: ($, next, e, why) => ($.ui.log(FAILED + why), next(e))"
