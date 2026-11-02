#!/bin/bash
# Run the bundled source using an existing Python; no package installation.
set -u
APKHARBOR_DIR="$(cd -- "$(dirname -- "$0")" && pwd -P)" || exit 1
APKHARBOR_PYTHON=""
for APKHARBOR_CANDIDATE in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if [ -x "$APKHARBOR_CANDIDATE" ] && "$APKHARBOR_CANDIDATE" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    APKHARBOR_PYTHON="$APKHARBOR_CANDIDATE"
    break
  fi
done
if [ -z "$APKHARBOR_PYTHON" ]; then
  echo "APKHarbor needs Python 3.9 or newer."
  echo "Install Python for macOS from https://www.python.org/downloads/macos/ and open this file again."
  read -r -p "Press Enter to finish. " APKHARBOR_REPLY
  exit 1
fi
PYTHONDONTWRITEBYTECODE=1 "$APKHARBOR_PYTHON" "$APKHARBOR_DIR/menu.py"
APKHARBOR_RESULT=$?
if [ "$APKHARBOR_RESULT" -ne 0 ]; then
  echo "APKHarbor stopped. Read START HERE.txt in this folder."
  read -r -p "Press Enter to finish. " APKHARBOR_REPLY
fi
exit "$APKHARBOR_RESULT"
