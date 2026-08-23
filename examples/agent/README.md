# CFIZZ Agent

From a source checkout, install the package and optional Agent dependencies:

```bash
python -m pip install -e "[agent,all]"
cfizz-agent --data-root /path/to/your/experiment
```

Alternatively run the source helper directly:

```bash
python examples/agent/run_web.py --data-root /path/to/your/experiment
```

Then open <http://127.0.0.1:8000>.  The repository intentionally contains no
experiment data; pass one or more local data roots at startup instead.
