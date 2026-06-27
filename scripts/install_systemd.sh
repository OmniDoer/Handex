#!/usr/bin/env bash
set -euo pipefail

install -d -m 0755 /etc/handex

if [[ ! -f /etc/handex/handex.env ]]; then
  umask 077
  {
    printf 'HANDEX_HOST=0.0.0.0\n'
    printf 'HANDEX_PORT=17395\n'
    printf 'HANDEX_DATA_DIR=/opt/handex/data\n'
    printf 'HANDEX_PROJECTS_DIR=/opt/handex/projects\n'
    printf 'HANDEX_LOGS_DIR=/opt/handex/logs\n'
    printf 'HANDEX_SECRET_KEY=%s\n' "$(openssl rand -hex 32)"
    printf 'HANDEX_ADMIN_PASSWORD=%s\n' "$(openssl rand -base64 24 | tr -d '\n')"
    printf '\n'
  } > /etc/handex/handex.env
fi

chmod 0600 /etc/handex/handex.env
cp /opt/handex/systemd/handex.service /etc/systemd/system/handex.service
systemctl daemon-reload
systemctl enable --now handex.service
