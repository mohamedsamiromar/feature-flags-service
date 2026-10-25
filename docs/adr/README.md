# Architecture Decision Records

Each record gives the context, the decision and its consequences. Records are append-only:
a decision that changes gets a new record, and the old one is marked superseded.

| # | Decision | Status |
| --- | --- | --- |
| [0001](0001-project-is-the-tenancy-boundary.md) | The project is the tenancy boundary | Accepted |
| [0002](0002-non-members-get-404.md) | "Not yours" returns 404, not 403 | Accepted |
| [0003](0003-evaluation-precedence.md) | Kill switch first, then prerequisites, then individual targets | Accepted |
| [0004](0004-uncertainty-fails-closed.md) | Uncertainty fails closed | Accepted |
| [0005](0005-sha256-rollout-bucketing.md) | SHA-256 for rollout bucketing | Accepted |
| [0006](0006-bucketing-salts.md) | Rule-level bucketing salted, flag-level unsalted | Accepted |
| [0007](0007-segments-do-not-nest.md) | Segments do not nest | Accepted |
| [0008](0008-sdk-keys-stored-as-hash.md) | SDK keys stored as SHA-256 hash only | Accepted |
| [0009](0009-bootstrap-returns-evaluated-results.md) | Bulk download returns evaluated results | Superseded in part by 0018 |
| [0010](0010-archive-instead-of-delete.md) | Archive instead of hard-delete | Accepted |
| [0011](0011-async-evaluation-logging.md) | Evaluation logging is asynchronous | Accepted |
| [0012](0012-cache-scoped-per-environment.md) | Flag cache scoped per environment | Accepted |
| [0013](0013-invalidation-by-generation.md) | Invalidation advances a generation, then bumps `config_version` | Accepted |
| [0014](0014-cache-embeds-targeting.md) | Cached payload embeds targeting; targeting writes invalidate | Accepted |
| [0015](0015-variation-values-in-jsonfield.md) | Variation values in a JSONField | Accepted |
| [0016](0016-boolean-flags-auto-wired.md) | Boolean flags auto-wired on creation | Accepted |
| [0017](0017-three-layer-numeric-validation.md) | Numeric constraints validated at three layers | Accepted |
| [0018](0018-server-sdks-download-the-ruleset.md) | Server SDKs download the ruleset, pinned by conformance vectors | Accepted |

Sources: the decision sections of the former `MEMORY.md`, README → Design decisions, and
SDK_CONFIG_SPEC §1.
