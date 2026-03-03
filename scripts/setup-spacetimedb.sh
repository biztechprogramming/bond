#!/bin/bash
set -e

# setup-spacetimedb.sh
# Checks for SpacetimeDB and handles installation/initialization.

echo "--- SpacetimeDB Setup ---"

# 1. Detect OS
OS_TYPE=$(uname -s)
echo "Detected OS: $OS_TYPE"

# 2. Preferred Package Manager (e.g., bun)
# We store this in ~/.bond/config.json for persistence
BOND_CONFIG="$HOME/.bond/config.json"
mkdir -p "$HOME/.bond"
if [ ! -f "$BOND_CONFIG" ]; then
    echo "{\"package_manager\": \"bun\"}" > "$BOND_CONFIG"
fi
PACKAGE_MANAGER=$(grep -oP '"package_manager":\s*"\K[^"]+' "$BOND_CONFIG" || echo "bun")
echo "Using package manager: $PACKAGE_MANAGER"

# 2. Check for SpacetimeDB CLI
if ! command -v spacetime &> /dev/null; then
    echo "SpacetimeDB CLI not found."
    read -p "Would you like to install SpacetimeDB CLI now? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Installing SpacetimeDB CLI..."
        if [ "$OS_TYPE" == "Linux" ] || [ "$OS_TYPE" == "Darwin" ]; then
            # Command for Linux and macOS
            curl -sSf https://install.spacetimedb.com | sh
            export PATH="$HOME/.spacetime/bin:$PATH"
        elif [[ "$OS_TYPE" == *"NT"* ]] || [[ "$OS_TYPE" == *"MINGW"* ]]; then
            # Command for Windows (PowerShell)
            powershell.exe -Command "iwr https://windows.spacetimedb.com -useb | iex"
            # Note: Path update on Windows might require a new shell, but we try to continue
        else
            echo "Automatic installation not supported for $OS_TYPE. Please visit https://spacetimedb.com/install"
            exit 1
        fi
    else
        echo "SpacetimeDB is required for the new Bond architecture. Setup aborted."
        exit 1
    fi
else
    echo "SpacetimeDB CLI is already installed."
fi

# 3. Initialize SpacetimeDB Module (TypeScript)
MODULE_DIR="./spacetimedb"
if [ ! -f "$MODULE_DIR/package.json" ]; then
    echo "Initializing SpacetimeDB TypeScript module in $MODULE_DIR..."
    mkdir -p "$MODULE_DIR"
    
    # In SpacetimeDB CLI 2.0+, 'login' handles authentication/identity.
    # We use --anonymous to keep everything local and avoid GitHub OAuth.
    if ! spacetime list 2>/dev/null; then
        echo "No SpacetimeDB identity detected. Creating an anonymous local identity..."
        spacetime login --anonymous
    fi

    cd "$MODULE_DIR"
    # Exact syntax for v2.0.2: spacetime init --lang <LANG> --project-path <PATH> [PROJECT_NAME]
    # We pass 'bond-core' as the project name to avoid the random suffix prompt.
    # We use 'yes' to piped input to handle any remaining interactive prompts.
    yes "" | spacetime init --lang typescript --project-path . bond-core
    cd ..
else
    echo "SpacetimeDB module already exists at $MODULE_DIR."
fi

# 4. SpacetimeDB Instance (Local)
# Set to 18787, which is one less than any Bond service port currently in use.
SPACETIME_PORT=18787

if ! curl -s http://localhost:$SPACETIME_PORT/v1/health &> /dev/null; then
    echo "Local SpacetimeDB instance not detected on port $SPACETIME_PORT."
    read -p "Would you like to start a local SpacetimeDB instance via Docker? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Starting SpacetimeDB via Docker on port $SPACETIME_PORT with persistent volume..."
        mkdir -p "$HOME/.bond/spacetimedb"
        docker run -d \
            --name bond-spacetimedb \
            --pull always \
            -p $SPACETIME_PORT:3000 \
            -v "$HOME/.bond/spacetimedb:/var/lib/spacetimedb" \
            clockworklabs/spacetime:latest \
            start
    else
        echo "Please ensure a SpacetimeDB instance is running on port $SPACETIME_PORT before starting Bond."
    fi
else
    echo "Local SpacetimeDB instance is running on port $SPACETIME_PORT."
fi

# 5. Setup Backup Cron Job
echo "Setting up backup cron job..."
chmod +x ./scripts/backup-spacetimedb.sh
BACKUP_SCRIPT_PATH=$(realpath ./scripts/backup-spacetimedb.sh)

# Create a unique line for the crontab
CRON_ENTRY="0 2 * * * $BACKUP_SCRIPT_PATH >> $HOME/.bond/backups/spacetimedb/backup.log 2>&1"

# Check if the script path is already in the crontab
if ! crontab -l 2>/dev/null | grep -Fq "$BACKUP_SCRIPT_PATH"; then
    (crontab -l 2>/dev/null; echo "$CRON_ENTRY") | crontab -
    echo "Cron job added: Runs daily at 2:00 AM."
else
    # It exists, but let's ensure it's exactly what we want (idempotent update)
    (crontab -l 2>/dev/null | grep -Fv "$BACKUP_SCRIPT_PATH"; echo "$CRON_ENTRY") | crontab -
    echo "Cron job verified/updated."
fi

echo "--- SpacetimeDB Setup Complete ---"
