# Minimal contract — evidence, not a claim

`minimal.key.yaml` governs one automatic test gate and one attested security
review. Its three root sections are the load-bearing core: a route, the gates
that route must execute, and the evidence those gates consume.

Reproduce the lint result:

```bash
python sk_lint.py examples/minimal/minimal.key.yaml
```

The verifier remains strict when optional capabilities are declared. In
particular, declaring validation layers makes a missing validation report a
CRITICAL RUN12 bypass; omitting validation because the build has no validation
layer does not manufacture that requirement.
