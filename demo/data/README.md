# Optional local analysis data

This top-level directory is intentionally reserved for untracked local data.
The installed package separately includes the small FOXJ1 browser fixture and
the complete built-in hg38 annotation, so the UI demo works immediately.

Prepare your own data here or elsewhere, then start the Agent with its directory:

```bash
cfizz-agent --data-root /path/to/data
```

The web interface scans the authorized directory and lets you choose the
compatible files for each CFIZZ figure or workflow.
