#!/usr/bin/env bash
# One-command setup on the EdgeBot VPS (systemd user units).
#   ./deploy/install.sh
set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/jackpot_predictor}"
UNIT_DIR="$HOME/.config/systemd/user"

mkdir -p "$UNIT_DIR" "$APP_DIR/data/logs"
cp "$(dirname "$0")/jackpot-predictor.service" "$UNIT_DIR/"
cp "$(dirname "$0")/jackpot-predictor.timer" "$UNIT_DIR/"

if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo ">>> Edit $APP_DIR/.env with your credentials before the first run."
fi

systemctl --user daemon-reload
systemctl --user enable --now jackpot-predictor.timer
loginctl enable-linger "$USER"   # keep user timers alive after logout

echo "Installed. Next firings:"
systemctl --user list-timers jackpot-predictor.timer --no-pager
echo "Manual test: cd $APP_DIR && python main.py --dry-run"
