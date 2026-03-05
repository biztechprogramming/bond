# Docker Sandbox Environment

Guidelines for operating within the Bond execution sandbox.

## Sandbox Architecture
- **Root Access**: You have full `root` privileges inside the container.
- **Workspace Mounts**: Projects are bind-mounted at `/workspace/<project_name>`. These are the primary writable directories.
- **Ephemeral Storage**: Files created outside of `/workspace` (e.g., in `/tmp`) are lost when the container is destroyed.

## File Operations & Permissions
- **Read-Only Filesystems**: Some directories (like `/bond`) may be mounted as read-only. 
    - **Fix**: If `file_edit` or `file_write` fails, copy the file to `/tmp/`, modify it there, and copy it back to a writable mount point.
    - **Avoid**: Do not spend time investigating mount permissions; use the `/tmp/` workaround immediately.
- **Host Sync**: Changes made to `/workspace/` are immediately reflected on the host machine.

## SSH & Authentication
- **SSH Keys**: Host SSH keys are mounted at `/tmp/.ssh` and automatically copied to `/root/.ssh` by the entrypoint. 
- **Git Operations**: Use these keys for `git push`/`pull`. If authentication fails, verify the mount at `/tmp/.ssh` exists.
- **Sandbox Limits**: Do not attempt to modify host-level SSH configuration.

## Networking
- **Host Connectivity**: Use `host.docker.internal` to reach services running on the host machine (e.g., the Bond gateway).
- **Environment Isolation**: The sandbox is isolated from the host's private network unless explicitly configured.

## Best Practices
- **Persistence**: Only save important work to the `/workspace/` directory.
- **Tooling**: You can install packages (`apt-get`, `pip`) as needed, but they will not persist across sandbox restarts.
- **Pathing**: Always use absolute paths within the sandbox (`/workspace/...`), never host-specific paths.
- **Cleanup**: Remove large temporary files from `/tmp/` before finishing to save container resources.
