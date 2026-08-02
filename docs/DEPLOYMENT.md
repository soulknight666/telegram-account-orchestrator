# Deployment

## Local Web UI

```bash
python setup.py --auto --deploy local --frontend web
```

Local mode binds to `127.0.0.1` by default and may use token-free access when explicitly selected during setup.

## Server

```bash
python setup.py --auto --deploy server --frontend both --token
```

Server deployments must use a strong `TAM_WEB_TOKEN`, HTTPS through a reverse proxy, restricted firewall rules, and a protected `TAM_DATA_DIR`. Do not expose account databases, session material, ZIP jobs, or `.env` through a static file server.

## Direct entry points

```bash
tao --help
tao-run --deploy local --frontend web --no-menu
python -m tam.cli doctor
```
