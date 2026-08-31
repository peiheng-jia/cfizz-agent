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

## Password-protected public IP trial

For a small shared trial without a domain, keep the application bound to
`127.0.0.1:8000` and put Nginx in front of it. The included
`nginx-ip.conf.template` provides TLS, HTTP Basic authentication, a 2 GiB
upload limit and long timeouts for figure rendering. Replace
`__CFIZZ_PUBLIC_IP__` and `__CFIZZ_HTTPS_PORT__` before installing it as an
Nginx site.

Public-IP certificates require Certbot 5.4 or newer and the ACME
`shortlived` profile. Keep TCP port 80 reachable for standalone validation and
renewal, while exposing the Nginx HTTPS port to users. The included systemd
service and timer check renewal twice daily and reload Nginx after a successful
renewal.

Only the password hash belongs in `/etc/nginx/cfizz.htpasswd`; do not commit
credentials, private keys, uploaded data or generated artifacts. This setup is
for a trusted, small trial group: CFIZZ currently shares runtime data between
authenticated users and does not provide per-user isolation.
