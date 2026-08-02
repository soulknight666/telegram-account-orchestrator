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

## Linux without a desktop

TAO supports terminal-only configuration. No Linux desktop packages are required.

```bash
git clone https://github.com/soulknight666/telegram-account-orchestrator.git
cd telegram-account-orchestrator
python -m tam.cli setup --headless
```

For the optional advanced Web setup page, keep it bound to the server loopback interface and open it through an SSH tunnel:

```bash
python -m tam.cli setup --headless --web --ssh-user deploy --ssh-host example.com
ssh -L 8849:127.0.0.1:8849 deploy@example.com
```

### Docker Compose

```bash
./deploy/install.sh --docker
docker compose ps
```

Configuration is stored in `./config/tao.env`; persistent application data is stored in `./data`.

### systemd

```bash
sudo ./deploy/install.sh --systemd
sudo systemctl status tao
sudo journalctl -u tao -f
```

The systemd layout is:

- Application: `/opt/tao`
- Configuration: `/etc/tao/tao.env`
- Persistent data: `/var/lib/tao`

Upgrade an existing installation without replacing configuration or data:

```bash
sudo ./deploy/install.sh --upgrade
```
