# Verdicts in the OpenSRM Ecosystem

Verdicts are the data primitive that turns five independent OpenSRM tools into a system that learns. Without verdicts, each component measures its own quality in its own way. With verdicts, every judgment is recorded in the same format, linked through lineage, and measured through the same accuracy queries. One human override propagates calibration signals to every component in the chain.

## Component Integration

### nthlayer-measure

The nthlayer-measure's evaluation output becomes a verdict. Quality scores, dimensions, confidence, and reasoning map directly to the verdict schema. Self-calibration becomes `nthlayer-learn accuracy --producer arbiter`.

The nthlayer-measure becomes more universal: any system that produces verdicts can be measured by it, without per-system adapters.

### nthlayer-correlate

nthlayer-correlate's correlation assessments become verdicts with `subject.type: correlation`. Each "this deploy caused this alert cluster" is a verdict. The snapshot as a whole can be a parent verdict with individual correlation verdicts as children.

Verdicts from other components (nthlayer-measure quality verdicts, change events) arrive in nthlayer-correlate as events and are indexed in the pre-correlation store alongside alerts and metric breaches.

### nthlayer-respond

nthlayer-respond consumes nthlayer-correlate's correlation verdicts and produces its own (triage, investigation, remediation). The full chain is traceable through lineage.

### NthLayer

NthLayer queries `gen_ai_*` metrics derived from verdicts via Prometheus. It generates judgment SLO recording rules and deploy gates. NthLayer doesn't interact with verdicts directly — it consumes the OTel metrics they produce.

## Dependency Graph

```
         verdict (data primitive)
        /    |    \         \
   arbiter  sitrep  mayday  nthlayer
                              |
                          prometheus
                          (verdict metrics)
```

Every component depends on the verdict library. None depend on each other.

## Incremental Adoption

- **Just nthlayer-measure?** The nthlayer-measure produces verdicts, stores them locally, humans resolve them.
- **Adding nthlayer-correlate?** nthlayer-correlate produces its own verdicts and can read the nthlayer-measure's. Lineage links automatically.
- **Adding nthlayer-respond?** nthlayer-respond consumes nthlayer-correlate's verdicts and produces its own. Full chain is traceable.

No component needs to know about any other component's internals. They all speak verdicts.
