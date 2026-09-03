# ReviewX Visual-CEM benchmark

This controlled benchmark sends eight generated scientific charts to a configured Qwen vision model. It checks paired clean controls and injected figure/caption/claim faults, then reports detection, false rejection, localization, and API trace metadata.

Run from `backend/`:

```bash
PYTHONPATH=. .venv/bin/python experiments/reviewx_visual_cem/run.py \
  --provider qwen \
  --model qwen3-vl-plus
```

The default output is written to the gitignored `docs/tempdocs/0903ReviewX视觉证据实验/` directory. No API key or image payload is persisted in the result trace.
