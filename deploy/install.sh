#!/bin/sh
# Installs Uplink as a systemd user service for the current user. Safe to re-run.
set -eu

if [ "$(id -u)" -eq 0 ]; then
    echo "install.sh: run this as your own user, not root" >&2
    exit 1
fi
command -v uv >/dev/null || { echo "install.sh: uv is not installed" >&2; exit 1; }

repo=$(cd "$(dirname "$0")/.." && pwd)
config_dir="$HOME/.config/uplink"
state_dir="$HOME/.local/state/uplink"
unit_dir="$HOME/.config/systemd/user"
token_file="$config_dir/token"
env_file="$config_dir/judy.env"

install -d -m 700 "$config_dir"
install -d -m 750 "$state_dir"
install -d -m 755 "$unit_dir"

if [ ! -s "$token_file" ]; then
    (umask 077 && od -An -tx1 -N32 /dev/urandom | tr -d ' \n' > "$token_file")
    echo "created token in $token_file"
fi
chmod 600 "$token_file"
(umask 077 && printf 'UPLINK_TOKEN=%s\n' "$(cat "$token_file")" > "$env_file")

constraints=$(mktemp)
trap 'rm -f "$constraints"' EXIT
(cd "$repo" && uv export --frozen --no-dev --no-emit-project --no-hashes -o "$constraints" >/dev/null)
uv tool install --reinstall --python 3.12 --constraints "$constraints" "$repo"
if [ "$(uv tool dir --bin)" != "$HOME/.local/bin" ]; then
    echo "install.sh: uv installs tools outside ~/.local/bin; fix ExecStart in the unit" >&2
    exit 1
fi

install -m 644 "$repo/deploy/uplink.service" "$unit_dir/uplink.service"
systemctl --user daemon-reload
systemctl --user enable uplink.service
systemctl --user restart uplink.service

echo
echo "uplink is running on $state_dir/uplink.sock"
echo "env file for judy: $env_file"
echo "to keep it running while you are logged out: loginctl enable-linger $USER"
