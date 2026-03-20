# nthlayer-learn — The Atomic Unit of AI Judgment

AI systems today are fire-and-forget. They make thousands of decisions per day (approving code, correlating signals, triaging incidents, moderating content, generating recommendations) and almost never find out whether those decisions were right. Quality degrades silently, confidence doesn't track reality, and the same mistakes repeat indefinitely because there is no feedback loop.

Verdicts close that loop. A verdict is a structured record of an AI decision that tracks what was evaluated, what was decided, how confident the AI was, and (critically) whether the decision turned out to be correct. The outcome phase is what makes verdicts different from logging: it turns isolated decisions into measurable, improvable judgment quality.

Verdicts are independent of the [OpenSRM](https://github.com/rsionnach/opensrm) ecosystem, independent of any specific agent framework, and independent of any specific model provider. Any system where an AI makes decisions can emit verdicts.

---

## The Problem

Most AI systems record what they decided but never measure whether those decisions were good. The consequences compound:

- **Silent degradation.** A model update, prompt change, or context shift reduces judgment quality by 15%. Nobody notices for weeks because there is no accuracy measurement, only activity logs.
- **Uncalibrated confidence.** An agent says "0.85 confidence" on every decision regardless of difficulty. Is 0.85 reliable? Nobody knows, because confidence has never been compared against actual outcomes.
- **No learning.** A code reviewer approves a change that causes an incident. The reviewer keeps reviewing with the same prompt, the same blind spot, the same failure mode, because there is no signal flowing back from the outcome to the judgment.
- **Invisible gaming.** An agent optimises for the evaluation rubric rather than actual correctness. Its scores are high but its real-world outcomes are poor. Without tracking both sides, the divergence is invisible.

The root cause is the same in every case: the decision and its outcome are never connected. Verdicts connect them.

---

## The Three Phases

Every verdict has three phases:

1. **Judgment** (filled at decision time): what was the input, what did the AI decide, why, and how confident is it?
2. **Outcome** (filled later): was the decision confirmed, overridden, or contradicted by downstream evidence?
3. **Lineage** (optional): which other verdicts informed this one, and which verdicts does this one inform?

The outcome phase is what makes verdicts powerful. It transforms AI decisions from opaque events into measurable data points with known correctness.

---

## How the Loop Closes at Scale

The obvious question: if an AI makes thousands of decisions per day, who confirms or overrides all of them? The answer is that the feedback loop doesn't depend on a human reviewing every verdict. Outcomes resolve through multiple mechanisms, most of which are automatic:

### Downstream outcome signals

Real-world consequences resolve verdicts without human intervention. When an AI approves a code change and that change causes a test failure in CI, that's automatic ground truth. When an approved deploy triggers a latency spike, that's a signal. When code gets reverted within 7 days, that's a signal. These downstream events flow back and resolve the original verdict's outcome automatically.

```yaml
outcome_tracking:
  sources:
    - type: deployment_metrics
      signal: "error_rate increase > 2x within 1h of deploy"
    - type: test_results
      signal: "test failure within same PR"
    - type: revert
      signal: "git revert of the commit within 7 days"
    - type: incident_correlation
      signal: "SitRep correlation linking this change to an incident"
```

### Lineage propagation

When verdicts are linked through lineage (a correlation verdict informs an investigation verdict which informs a remediation verdict), one human override at any point in the chain propagates calibration signals to every verdict upstream. A single human action resolves five verdicts. This is the efficiency multiplier that makes human review scalable: humans review the decisions that matter most (remediations, governance actions), and lineage carries that signal back through the entire decision chain.

```
SitRep correlation verdict (vrd-001)
  "deploy v2.3.1 caused latency spike, confidence 0.71"
    |
    +-> Mayday investigation verdict (vrd-002, context: [vrd-001])
          "root cause: connection pooling removed, confidence 0.65"
            |
            +-> Mayday remediation verdict (vrd-003, parent: vrd-002)
            |     "rollback to v2.3.0, confidence 0.90"
            |
            +-> Human override (vrd-004, parent: vrd-002)
                  "root cause correct, but hotfix not rollback"
                  (overrides vrd-003, confirms vrd-002)

Result: SitRep gets a positive signal. Investigation agent gets a positive signal.
Remediation agent gets a negative signal. All from one human action.
```

### Calibration sampling

Not every verdict needs direct validation. A configurable percentage (default 5%) of auto-approved outputs are randomly sampled and sent through full evaluation. If the sample consistently confirms the original judgments, the system is calibrated. If the sample finds problems, the approval criteria tighten. Statistical confidence without exhaustive review.

### Score-outcome divergence

The `gaming-check` query compares an agent's average judgment score against its actual outcome confirmation rate over a rolling window. An agent scoring 0.88 on average but with only 71% of its decisions confirmed by outcomes has a 17-point divergence, which is a signal that its scores don't reflect reality. This surfaces problems across thousands of verdicts without reviewing any of them individually.

```bash
nthlayer-learn gaming-check --producer arbiter --agent code-reviewer --window 90d
# code-reviewer: score 0.88, outcome confirmation 0.71, divergence 0.17 -> ALERT
# doc-writer: score 0.79, outcome confirmation 0.81, divergence -0.02 -> OK
```

### Time-based expiry

A verdict that remains unresolved past its TTL (default 90 days) expires with a weak negative signal. This doesn't mean the decision was wrong. It means the feedback loop is broken for this verdict, which is itself useful information: if 60% of verdicts expire unresolved, the system isn't generating enough outcome signal to calibrate.

---

## Quick Start

### Install

```bash
pip install nthlayer-learn
```

### Create a verdict

```python
from nthlayer_learn import create, resolve

# Record a judgment
v = create(
    subject={"type": "review", "ref": "git:abc123", "summary": "Auth middleware change"},
    judgment={"action": "approve", "confidence": 0.82, "reasoning": "Auth check is sound"},
    producer={"system": "my-reviewer", "model": "claude-sonnet-4-20250514"},
)

# Later, when you know if the decision was right
v = resolve(v, status="confirmed")
```

### Measure accuracy

```python
from nthlayer_learn import MemoryStore, AccuracyFilter

store = MemoryStore()
store.put(v)

report = store.accuracy(AccuracyFilter(producer_system="my-reviewer"))
print(f"Confirmation rate: {report.confirmation_rate}")
print(f"Override rate: {report.override_rate}")
print(f"Calibration gap: {report.mean_confidence_on_confirmed - report.confirmation_rate}")
```

### Serialise and deserialise

```python
from nthlayer_learn import to_json, from_json

json_str = to_json(v)
v2 = from_json(json_str)
```

### CLI (coming soon)

```bash
nthlayer-learn accuracy --producer my-reviewer --window 30d
nthlayer-learn replay --producer my-reviewer --from 2026-02-01 --to 2026-03-01
nthlayer-learn gaming-check --producer my-reviewer --agent code-reviewer --window 90d
```

---

## What You Get

**Replay.** Every verdict contains a reference to its input and a content hash for integrity verification. Change a prompt, swap a model, adjust context, then replay historical verdicts and diff: X improved, Y regressed, Z unchanged. This is regression testing for judgment quality.

**Calibration.** The `accuracy()` query computes confirmation rate, override rate, calibration gap (the difference between an agent's confidence and its actual accuracy), and per-dimension breakdowns from resolved verdicts. Track these over rolling windows and you know exactly how your AI's judgment quality is trending.

**Gaming detection.** The gap between `judgment.score` and `outcome.status` across a population of verdicts is the gaming signal. High scores with poor real-world outcomes means something is wrong, whether that's prompt misalignment, evaluation rubric blind spots, or deliberate optimisation for the rubric rather than actual correctness.

**Lineage.** Trace how one decision influenced another across components, teams, or systems. When components exchange verdicts instead of bespoke formats, the provenance of every judgment is traceable and the calibration signal from any override flows through the entire chain.

No framework required. No ecosystem required. Just a schema and a small library.

---

## Schema

See [SPEC.md](SPEC.md) for the full schema specification, or [schema/verdict.json](schema/verdict.json) for the JSON Schema.

### Outcome statuses

| Status | Meaning | Calibration Signal |
|--------|---------|-------------------|
| `pending` | No outcome yet. Judgment stands but hasn't been validated. | None (not counted in accuracy until resolved) |
| `confirmed` | Human or downstream signal confirmed the judgment was correct. | Positive |
| `overridden` | Human or downstream signal contradicted the judgment. | Negative |
| `partial` | Judgment was partially correct. Some dimensions right, others wrong. | Mixed (per-dimension) |
| `superseded` | A newer verdict on the same subject replaced this one. | None |
| `expired` | TTL elapsed without resolution. | Weak negative (feedback loop may be broken) |

---

## OTel Integration

Verdicts are the data primitive. OpenTelemetry is the transmission format. The verdict library maps between the two automatically.

When a verdict is created, resolved, or overridden, it emits OTel events using the `gen_ai.*` semantic conventions that the broader OTel community is developing. This means verdict data flows into any OTel-compatible backend (Prometheus, Grafana, Datadog, Honeycomb) without custom integrations.

### Events

| OTel Event | Trigger |
|------------|---------|
| `gen_ai.decision.created` | `verdict.create()` |
| `gen_ai.decision.confirmed` | `verdict.resolve(status: confirmed)` |
| `gen_ai.override.recorded` | `verdict.resolve(status: overridden)` |

### Metrics (via OTel Collector -> Prometheus)

| Metric | Type | What It Measures |
|--------|------|------------------|
| `gen_ai_decision_total` | counter | Total judgments produced |
| `gen_ai_decision_score` | gauge | Quality score per judgment |
| `gen_ai_decision_confidence` | gauge | Producer confidence per judgment |
| `gen_ai_override_reversal_total` | counter | Judgments overridden by humans |
| `gen_ai_override_correction_total` | counter | Judgments partially corrected |
| `gen_ai_decision_cost_tokens` | counter | Token consumption |
| `gen_ai_decision_cost_currency` | gauge | Estimated cost in USD |

All metrics carry `system`, `agent`, and `environment` labels derived from the verdict's `producer` and `subject` fields.

### Attribute Mapping

Key verdict fields map to standard OTel attributes:

| Verdict Field | OTel Attribute |
|--------------|----------------|
| `producer.system` | `gen_ai.system` |
| `producer.model` | `gen_ai.request.model` |
| `judgment.action` | `gen_ai.decision.action` |
| `judgment.confidence` | `gen_ai.decision.confidence` |
| `subject.service` | `service.name` |
| `outcome.override.by` | `gen_ai.override.actor` |

For systems using verdicts outside of generative AI contexts (traditional ML, rule-based systems, manual decision tracking), the library can emit using a `decision.*` namespace instead. The verdict schema is the same regardless of namespace.

See [conventions/](conventions/) for the full semantic convention specifications.

---

## OpenSRM Ecosystem

Verdicts are independent of any specific ecosystem, but within the [OpenSRM](https://github.com/rsionnach/opensrm) reliability stack, the Verdict Store is the shared data substrate that all judgment-producing components communicate through:

```
Static Layer (Data + Tools)
  OpenSRM Manifests → NthLayer → Generated Artifacts
        │
        ▼
Verdict Layer (Data Primitive)  ← this library
  verdict.create()  verdict.resolve()  verdict.query()
        │
        ▼
Agent Layer (Reasoning)
  SitRep → [verdict] → Mayday Agents ← [verdict.accuracy()] → Arbiter
  All agents emit verdicts with lineage.
        │ OTel side-effects
        ▼
Semantic Conventions (OTel)
  Change Events │ Decision Telemetry │ Outcomes
```

Verdicts with lineage are the primary cross-component communication mechanism. SitRep emits correlation verdicts, Mayday agents emit triage/investigation/remediation verdicts linked via lineage, and Arbiter queries `verdict.accuracy()` for self-calibration. One human override at any point in the chain propagates calibration signals to every verdict upstream.

| Component | How it uses Verdict |
|-----------|-------------------|
| [nthlayer-measure](https://github.com/rsionnach/nthlayer-measure) | Produces `agent_output` verdicts for every evaluation; queries `accuracy()` for self-calibration |
| [nthlayer-correlate](https://github.com/rsionnach/nthlayer-correlate) | Produces `correlation` verdicts; ingests Arbiter quality verdicts as events |
| [nthlayer-respond](https://github.com/rsionnach/nthlayer-respond) | Produces `triage`, `investigation`, `communication`, `remediation` verdicts; consumes SitRep verdicts as context |
| [NthLayer](https://github.com/rsionnach/nthlayer) | Queries Prometheus metrics that originate from verdict OTel emission |

---

## Storage

| Tier | Store | Use Case |
|------|-------|----------|
| Tier 1 | SQLite | Single file, zero dependencies. Default. |
| Tier 2 | PostgreSQL | Concurrent access, full-text search, real-time consumption. |
| Tier 3 | ClickHouse | Analytics over millions of verdicts. |
| Any | Git | Evaluation datasets (curated verdicts with known outcomes). |

---

## Project Structure

```
verdicts/
├── SPEC.md                      # Full schema specification
├── schema/                      # JSON Schema and annotated examples
├── conventions/                 # OTel semantic conventions
├── lib/                         # Transport libraries (Python, Go, TypeScript)
├── stores/                      # Storage implementations
├── cli/                         # CLI tools
└── eval/                        # Example evaluation datasets
```

---

## License

Apache 2.0
