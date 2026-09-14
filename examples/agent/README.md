# CFIZZ Agent

From a source checkout, install the package and optional Agent dependencies:

```bash
python -m pip install -e ".[agent]"
cfizz-agent --check
cfizz-agent --data-root /path/to/your/experiment
```

Alternatively run the source helper directly:

```bash
python examples/agent/run_web.py --data-root /path/to/your/experiment
```

Then open <http://127.0.0.1:8000>. The Agent ships a small FOXJ1 browser demo;
pass one or more local data roots to work with your own experiment data.

On Windows, run these commands in WSL2 or use Docker Desktop. Native Windows
is not the supported route because several genomics packages lack a reliable
prebuilt wheel combination.
