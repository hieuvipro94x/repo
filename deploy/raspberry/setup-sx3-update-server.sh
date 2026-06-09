#!/bin/sh
set -eu

sudo install -d -m 0755 -o "$USER" -g "$USER" /srv/sx3-update-server/updates

sudo tee /etc/systemd/system/sx3-update-server.service >/dev/null <<EOF
[Unit]
Description=SX3 Scanner update file server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=/srv/sx3-update-server
ExecStart=/usr/bin/python3 -m http.server 8000 --bind 0.0.0.0 --directory /srv/sx3-update-server
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now sx3-update-server.service

echo "Server san sang tai http://100.121.199.45:8000/updates/manifest.json"
echo "Tai file manifest.json va goi ZIP vao /srv/sx3-update-server/updates/"
