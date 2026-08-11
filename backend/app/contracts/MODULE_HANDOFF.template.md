# Module Handoff: `<module>`

## Identity

- Branch: `devtzb_<module>`
- Base `origin/devtzb` commit: `<sha>`
- Head commit: `<sha>`
- Owner: `<name>`
- Handoff time: `<YYYY-MM-DD HH:mm CST>`

## Delivered Capability

Describe the user-visible scientific capability. Do not list only changed
files.

## Contract Boundary

- Canonical input fixture(s): `<files>`
- Produced contract(s): `<models>`
- Produced artifact kinds: `<kinds>`
- New optional contract fields requested: `<none or proposal>`
- Degraded and failure states: `<states>`

## Verification

```text
<exact commands and concise results>
```

- Independent demo input: `<path or ID>`
- Independent demo output: `<path or ID>`
- Live API required: `<yes/no and provider>`
- Runtime and estimated cost: `<value>`

## Integration Impact

- Global router or navigation request: `<none or exact change>`
- Database migration: `<none or migration>`
- Dependency/configuration change: `<none or exact change>`
- Cross-module assumptions: `<list>`
- Known limitations: `<list>`

## Evidence for Challenge Cup

- Screenshot/data table to retain: `<artifact>`
- Traceable innovation claim supported: `<claim>`
- Result is real, simulated, or planned: `<classification>`

## Integration Checklist

- [ ] Merged the current `origin/devtzb` into the module branch.
- [ ] Shared contract tests pass.
- [ ] Module tests pass.
- [ ] Canonical fixture works without upstream services.
- [ ] No official-question special cases were added.
- [ ] No secret, database, runtime artifact, or generated result is tracked.
- [ ] PR targets `devtzb` and contains no unrelated changes.
