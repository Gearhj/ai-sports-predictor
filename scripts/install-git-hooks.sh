#!/bin/sh
# Install the local secret guard as pre-commit and pre-push hooks.
# Run once per clone:  sh scripts/install-git-hooks.sh
# Hooks are not versioned by Git, so every new clone needs this step.
set -e
hooks="$(git rev-parse --git-path hooks)"
mkdir -p "$hooks"
for hook in pre-commit pre-push; do
  mode=--staged
  [ "$hook" = pre-push ] && mode=--pre-push
  cat > "$hooks/$hook" <<HOOK
#!/bin/sh
# Installed by scripts/install-git-hooks.sh -- blocks credentials in new content.
root="\$(git rev-parse --show-toplevel)"
for py in "\$root/.venv/Scripts/python.exe" "\$root/.venv/bin/python" py python python3; do
  if command -v "\$py" >/dev/null 2>&1; then
    exec "\$py" "\$root/scripts/secret_guard.py" $mode
  fi
done
echo "secret-guard: no Python interpreter found; refusing $hook" >&2
exit 1
HOOK
  chmod +x "$hooks/$hook"
  echo "installed $hooks/$hook"
done
