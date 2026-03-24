# Verdict Specification

Version: 1

## Schema

A verdict is a structured record of an AI judgment. It captures what was evaluated, what the AI decided, how confident it was, and (eventually) whether the decision was correct.

### Full Schema (YAML)

```yaml
verdict:
  # Identity
  id: "vrd-2026-03-07-00142"           # unique, globally
  version: 1                            # schema version
  timestamp: "2026-03-07T14:22:00Z"     # when the judgment was made

  # Who made this judgment
  producer:
    system: "arbiter"                    # component or application name
    instance: "arbiter-prod-01"          # optional, for multi-instance deployments
    model: "claude-sonnet-4-20250514"    # optional, which model produced the judgment
    prompt_version: "v2.3"               # optional, for tracking prompt changes

  # What was evaluated
  subject:
    type: "agent_output"                 # agent_output | correlation | triage | investigation |
                                         # remediation | review | classification | recommendation |
                                         # moderation | communication | custom
    agent: "code-reviewer"               # optional, the agent whose output is being judged
    service: "webapp"                     # optional, the service context
    environment: "production"            # optional
    ref: "git:abc123..def456"            # pointer to the input (git ref, snapshot ID, URL, etc.)
    summary: "14-line diff to auth middleware"  # human-readable summary of what was evaluated
    content_hash: "sha256:9f86d08..."    # hash of the input content, for replay verification

  # The judgment itself
  judgment:
    action: "approve"                    # approve | reject | flag | escalate | defer | custom
    score: 0.82                          # overall quality score, 0.0-1.0 (optional)
    confidence: 0.78                     # how confident the producer is in this judgment, 0.0-1.0
    dimensions:                          # optional, breakdown by quality dimension
      correctness: 0.9
      completeness: 0.75
      safety: 0.85
    reasoning: "Auth check is sound. Missing rate limit on new endpoint."
    tags: ["auth", "security", "api"]    # optional, for filtering and aggregation

  # What happened after (updated asynchronously)
  outcome:
    status: "pending"                    # pending | confirmed | overridden | partial | superseded | expired
    resolution: null                     # human-readable description of what actually happened
    override:                            # filled if a human or downstream signal contradicts
      by: "human:rob"                    # who overrode (human:name, system:component, or auto:rule)
      at: null                           # when
      action: null                       # what the override changed the action to
      reasoning: null                    # why the override happened
    ground_truth:                        # filled when objective evidence is available
      signal: null                       # what provided the ground truth (test failure, incident, metric, human review)
      value: null                        # what the correct judgment would have been
      detected_at: null                  # when the ground truth became available
    closed_at: null                      # when this verdict's outcome was finalised

  # Lineage (optional)
  lineage:
    parent: null                         # verdict ID that this one responds to or overrides
    children: []                         # verdict IDs that were produced in response to this one
    context: []                          # verdict IDs that informed this judgment (read, not responded to)

  # Metadata
  metadata:
    cost_tokens: 1247                    # optional, tokens consumed producing this judgment
    cost_currency: 0.003                 # optional, estimated cost in USD
    latency_ms: 2340                     # optional, time to produce this judgment
    ttl: 7776000                         # seconds until this verdict can be expired (default 90 days)
    custom: {}                           # extension point for domain-specific data
```

## Outcome Statuses

| Status | Meaning | Calibration Signal |
|--------|---------|-------------------|
| `pending` | No outcome yet. The judgment stands but hasn't been validated. | None (not counted in accuracy metrics until resolved) |
| `confirmed` | A human or downstream signal confirmed the judgment was correct. | Positive (the AI was right) |
| `overridden` | A human or downstream signal contradicted the judgment. | Negative (the AI was wrong, the override is the ground truth) |
| `partial` | The judgment was partially correct. Some dimensions were right, others wrong. | Mixed (contributes to per-dimension accuracy, not binary accuracy) |
| `superseded` | A newer verdict on the same subject replaced this one (e.g., re-evaluation with more context). | None (the superseding verdict carries the calibration signal) |
| `expired` | The TTL elapsed without the outcome being resolved. | Weak negative (an unresolved verdict might indicate the judgment wasn't important enough to validate, or the feedback loop is broken) |

## Subject Types

| Type | Example Producer | Example Judgment |
|------|-----------------|-----------------|
| `agent_output` | nthlayer-measure | "This code review output is correct and complete" |
| `correlation` | nthlayer-correlate | "This deploy caused this latency spike" |
| `triage` | nthlayer-respond Triage Agent | "This incident is severity 2" |
| `investigation` | nthlayer-respond Investigation Agent | "Root cause is misconfigured connection pool" |
| `remediation` | nthlayer-respond Remediation Agent | "Rollback to v2.3.0 will resolve this" |
| `review` | Code reviewer, document reviewer | "This PR is ready to merge" |
| `classification` | Content moderator | "This content is safe" |
| `recommendation` | Any recommender system | "User would enjoy this product" |
| `moderation` | Trust and safety system | "This message violates policy" |
| `communication` | nthlayer-respond Communication agent | "Incident update is accurate and complete" |
| `custom` | Any domain-specific AI | Domain-specific judgment |

## Lineage

Lineage turns isolated verdicts into a traceable decision chain.

```
nthlayer-correlate correlation verdict (vrd-001)
  "deploy v2.3.1 caused latency spike, confidence 0.71"
    │
    └──▶ nthlayer-respond investigation verdict (vrd-002, context: [vrd-001])
          "root cause: deploy v2.3.1 removed connection pooling, confidence 0.65"
            │
            ├──▶ nthlayer-respond remediation verdict (vrd-003, parent: vrd-002)
            │     "rollback to v2.3.0, confidence 0.90"
            │
            └──▶ Human override verdict (vrd-004, parent: vrd-002)
                  "root cause correct but remediation should be hotfix, not rollback"
                  (overrides vrd-003, confirms vrd-002)
```

When vrd-004 confirms vrd-002 and overrides vrd-003, both nthlayer-correlate and nthlayer-respond's investigation agent get positive calibration signals, and nthlayer-respond's remediation agent gets a negative one. All from one human action.

Lineage is optional. A standalone verdict (no parent, no context, no children) is perfectly valid.

## Operations

### Core

```
create(subject, judgment, producer) → Verdict
link(verdict, parent?, context?) → Verdict
resolve(verdict, status, override?, ground_truth?) → Verdict
supersede(old_verdict, new_verdict) → (Verdict, Verdict)
```

### Query

```
by_producer(system, time_range?) → [Verdict]
by_subject(service?, agent?, type?, time_range?) → [Verdict]
by_status(status, producer?, time_range?) → [Verdict]
by_lineage(verdict_id, direction: up | down | both) → [Verdict]
unresolved(producer?, max_age?) → [Verdict]
accuracy(producer, time_range?, dimension?) → AccuracyReport
```

### Serialisation

Verdicts serialise to JSON or YAML. The canonical format is JSON for machine consumption and YAML for human readability.

## Storage

The verdict library abstracts storage behind an interface:

```
interface VerdictStore {
  put(verdict: Verdict): Promise<void>
  get(id: string): Promise<Verdict | null>
  query(filter: VerdictFilter): Promise<Verdict[]>
  update_outcome(id: string, outcome: Outcome): Promise<Verdict>
  accuracy(filter: AccuracyFilter): Promise<AccuracyReport>
  expire(before: string): Promise<number>
}
```

| Tier | Store | Notes |
|------|-------|-------|
| Tier 1 | SQLite | Single file, zero dependencies. Default. |
| Tier 2 | PostgreSQL | Concurrent access, full-text search, LISTEN/NOTIFY. |
| Tier 3 | ClickHouse | Columnar storage for analytics over millions of verdicts. |
| Any | Git | For evaluation datasets. Versioned, reviewable, diffable. |
