#!/usr/bin/env bash
# Ask for a data service's username and password and save them where
# lynch-forecast reads them: ~/.config/lynch-forecast/credentials.env
# (only you can read it; it is outside the repository, so never committed).
#
#   scripts/save-credentials.sh          # RTS (rts.wscada.net)
#   scripts/save-credentials.sh gss      # gss.alertnepal.online, later
#
# Running it again replaces that service's lines and keeps the others.
# Set LYNCH_FORECAST_CREDENTIALS to use another file.
set -euo pipefail

service="${1:-rts}"
case "$service" in
    rts) prefix=RTS; label="RTS (rts.wscada.net)" ;;
    gss) prefix=GSS; label="gss (gss.alertnepal.online)" ;;
    *)   echo "Unknown service '$service': use rts or gss." >&2; exit 1 ;;
esac

file="${LYNCH_FORECAST_CREDENTIALS:-$HOME/.config/lynch-forecast/credentials.env}"
umask 077                                   # every file made here: owner only
mkdir -p "$(dirname "$file")"
chmod 700 "$(dirname "$file")"

if [[ ! -t 0 ]]; then
    echo "Run this in a terminal: it asks for the password without showing it." >&2
    exit 1
fi

echo "Saving the $label sign-in to $file"
read -r -p "Username: " user
read -r -s -p "Password (not shown): " pass; echo
read -r -s -p "Password again: " pass2; echo

if [[ -z "$user" || -z "$pass" ]]; then
    echo "Nothing saved: the username and the password must not be empty." >&2
    exit 1
fi
if [[ "$pass" != "$pass2" ]]; then
    echo "Nothing saved: the two passwords differ." >&2
    exit 1
fi

# Keep the other services' lines, replace this one's.
tmp="$(mktemp "$(dirname "$file")/.credentials.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
if [[ -f "$file" ]]; then
    grep -v -e "^${prefix}_USERNAME=" -e "^${prefix}_PASSWORD=" "$file" > "$tmp" || true
fi
# Raw KEY=value lines, read by lynch-forecast itself (not meant to be sourced).
printf '%s_USERNAME=%s\n%s_PASSWORD=%s\n' "$prefix" "$user" "$prefix" "$pass" >> "$tmp"
chmod 600 "$tmp"
mv "$tmp" "$file"
trap - EXIT
unset pass pass2

echo "Saved. $file now holds sign-ins for: $(grep -o '^[A-Z]*_USERNAME' "$file" | sed 's/_USERNAME//' | tr '\n' ' ')"
