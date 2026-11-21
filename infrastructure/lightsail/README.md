# Talky Lightsail CI/CD

GitHub Actions runs the Python suite with an isolated PostgreSQL database and runs the frontend tests and build for pull requests and pushes to `main`. On the existing Ubuntu Lightsail instance, systemd checks `main` every two minutes. It builds and deploys the exact commit only after its `push` run of `ci.yml` succeeds. The server reads the public GitHub API over HTTPS using its existing outbound connection.

## Host configuration

The commands run from the existing server checkout at `/opt/talky`. This checkout contains `.env`, `docker-compose.lightsail.yml`, `docker-compose.https.yml`, and `Caddyfile`. Those files carry the instance-specific configuration.

## Public login

The Lightsail deployment record shows HTTPS traffic reaching FastAPI through Caddy. The public health endpoint returned HTTP 200 on 2026-11-21. The active Caddy rules and Uvicorn proxy allowlist still need verification on the instance. The base Compose file publishes the API on loopback. Confirm that the deployed overrides preserve a private origin and check whether Caddy already applies a login-specific request limit.

For the app's login limit, configure `FORWARDED_ALLOW_IPS` with only the Caddy peers that connect to Uvicorn. Keep the peer addresses stable across deploys and use an explicit allowlist. When Caddy is the public edge, its reverse proxy supplies the client address and HTTPS scheme. Verify with two clients on different networks that they receive separate login budgets. If Uvicorn sees Caddy as the client, all traffic through that proxy shares the source budget. Preserve any verified edge request limit as an additional check before FastAPI. [Caddy forwarded headers](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#defaults) · [Uvicorn proxy settings](https://github.com/Kludex/uvicorn/blob/main/docs/settings.md#http)

FastAPI applies both login limits before running PBKDF2, with PostgreSQL holding the counters across web instances. Use one production fingerprint secret across the instances and keep the expiry worker running. An account blocked on one source can sign in from a different source with the correct password or wait for the window to expire. The app has no password reset flow. See the [login policy](../../README.md#웹-화면과-api).

## Install the deployer

```sh
cd /opt/talky
sudo git fetch --no-tags origin refs/heads/main
install_dir=$(mktemp -d)
sudo git archive FETCH_HEAD infrastructure/lightsail | tar -x -C "$install_dir" -f -
sudo install -m 0755 "$install_dir/infrastructure/lightsail/deploy.py" /usr/local/sbin/talky-deploy
sudo install -m 0644 "$install_dir/infrastructure/lightsail/talky-deploy.service" /etc/systemd/system/
sudo install -m 0644 "$install_dir/infrastructure/lightsail/talky-deploy.timer" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now talky-deploy.timer
sudo systemctl start talky-deploy.service
sudo journalctl -u talky-deploy.service -n 60 --no-pager
```

Keep the source checkout and Compose project name `talky` in place. The deployment builds a new image from tracked files before changing the running services. It reuses the existing volumes, local image tag, and Caddy container. If a release adds a migration, the web and workers stop before Compose starts the migration. Plan for a brief interruption during a deployment.

If an update fails, the deployer checks the previous image against the current database schema before restarting it. An incompatible migration requires a corrective release or operator recovery. The database is never downgraded by the deployer. After correcting a transient deployment failure, retry the same commit with `sudo unlink /var/lib/talky-deploy/failed-commit` followed by `sudo systemctl start talky-deploy.service`. To pause GitHub updates, run `sudo systemctl disable --now talky-deploy.timer`. Update the installed deployer and units explicitly when those files change.
