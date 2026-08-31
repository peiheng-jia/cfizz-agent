# CFIZZ Agent trial deployment

The trial configuration runs one CFIZZ Agent container and binds it only to
`127.0.0.1:8000` on the server. Access it through an SSH tunnel instead of
exposing the unauthenticated application directly to the public internet.

## Persistent directories

- `storage/runtime`: sessions, render artifacts and caches
- `storage/uploads`: files uploaded through the application
- `storage/data`: administrator-provided experiment data (read-only)
- `storage/demo`: bundled small demo inputs (read-only)
- `storage/references`: installed reference annotations (read-only)

The large directories are intentionally not part of the Git repository and
must be copied or mounted on the server.

On a Tencent Cloud host that cannot reach Docker Hub directly, the optional
`docker-daemon.tencent.json` file configures Tencent Cloud's registry mirror.
Install it as `/etc/docker/daemon.json` and restart Docker before building.
The Python package index can likewise be overridden during a build:

```bash
docker compose build \
  --build-arg PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple \
  --build-arg DEBIAN_MIRROR=https://mirrors.cloud.tencent.com/debian \
  --build-arg DEBIAN_SECURITY_MIRROR=https://mirrors.cloud.tencent.com/debian-security
```

## Start and verify

```bash
docker compose up -d --build
docker compose ps
curl --fail http://127.0.0.1:8000/api/health
```

From a trusted workstation, create a tunnel and open
`http://127.0.0.1:8000` in a browser:

```bash
ssh -L 8000:127.0.0.1:8000 ubuntu@SERVER_IP
```

Before opening CFIZZ to other users, add a TLS reverse proxy, authentication,
per-user data isolation and request limits.
