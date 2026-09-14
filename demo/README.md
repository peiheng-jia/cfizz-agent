# Local full-pipeline data (not included)

The GitHub repository does not commit full-pipeline inputs, intermediate
results or generated figures in this top-level folder. The installed Agent
does include a separate 13 MB FOXJ1 browser demo under its package resources;
load it with the “载入 FOXJ1 示例” button.

To work with your own data, provide a directory containing `.cool`/`.mcool`
and optional track files, then authorize it when starting the Agent:

```bash
cfizz-agent --data-root /path/to/your/data
```

See [`cases/README.md`](cases/README.md) and [`data/README.md`](data/README.md)
for the expected file types.
