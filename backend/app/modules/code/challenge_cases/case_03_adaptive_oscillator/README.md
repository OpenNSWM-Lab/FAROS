# Case 03: adaptive damped oscillator

This offline Code fixture verifies that a fixed-budget scientific simulator can
produce a real Plan Delta, paired per-seed measurements, and a statistical gate.
It uses the same SciPy ODE and least-squares implementation as
`experiments.reviewx_oscillator`, but it is not the final representative Qwen
run. The final run must use the frozen protocol and `--require-real-api`.

```bash
cd backend
./.venv/bin/python -m app.modules.code.challenge_cases.case_03_adaptive_oscillator.run --output /tmp/faros-case-03
```
