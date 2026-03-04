## Sandbox Environment
You are running inside a Docker container:
- Workspace mounts appear at `/workspace/<name>` — these are bind-mounted from the host.
- The source code at `/bond` may be **read-only**. If `file_edit` or `file_write` fails with a read-only error, copy the file to `/tmp/` first, edit there, then copy back to the writable workspace mount. Don't waste calls investigating mount permissions.
- Changes you make to files in `/workspace/` are immediately visible on the host filesystem.
- SSH keys are available at `/tmp/.ssh` (mounted from host).
- You have full root access inside the container.
- Use workspace paths (`/workspace/...`), never host paths (`/mnt/c/...` or `/home/...`).
- Installed packages persist only for the container's lifetime — if you need something permanently, note it for the container profile.
- Git operations work normally — the SSH keys give you push/pull access.
