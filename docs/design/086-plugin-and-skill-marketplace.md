# Design Doc 086: Plugin & Skill Marketplace

**Status:** Draft  
**Author:** Bond  
**Date:** 2026-03-30  
**Depends on:** 047 (Skills Federation), 017 (MCP Integration), 084 (Multi-Agent Coordination)  
**Inspired by:** Paperclip's runtime skill injection and plugin architecture

---

## 1. Problem Statement

Bond has a growing set of built-in tools and skills, and Design Doc 047 introduced skills federation — the ability to discover and load skills from external sources. However, there is no **structured ecosystem** for:

- **Publishing skills** — A developer who builds a useful Bond skill (e.g., "Jira integration," "AWS deployment," "database migration helper") has no standard way to package and share it.
- **Discovering skills** — Users can't browse available skills, read reviews, or see compatibility information.
- **Installing skills** — Adding a new skill requires manual configuration, not a one-click install.
- **Runtime injection** — Skills are loaded at startup; there's no mechanism for an agent to discover and acquire a new skill mid-conversation ("I don't know how to do X, but there's a plugin for it").
- **Quality and security** — No verification, sandboxing, or trust model for third-party skills.

Paperclip addresses this with a formal plugin manifest format, a registry, and runtime skill injection. Bond needs a similar system tailored to its architecture.

---

## 2. Goals

1. **Plugin manifest format** — A standard `bond-plugin.yaml` that declares a plugin's capabilities, dependencies, configuration, and entry points.
2. **Plugin registry** — A browsable catalog of available plugins, initially local (bundled + user-installed), eventually remote (community marketplace).
3. **One-click install** — Users can install a plugin from the registry via the UI or CLI with automatic dependency resolution.
4. **Runtime discovery** — Agents can query the registry mid-conversation to find plugins that provide needed capabilities.
5. **Security model** — Plugins declare required permissions; users approve permissions at install time; plugins run in sandboxed contexts.

---

## 3. Plugin Manifest Format

### 3.1 `bond-plugin.yaml`

```yaml
name: jira-integration
version: 1.2.0
description: "Create, update, and query Jira issues from Bond conversations"
author: "bond-community"
license: "MIT"
homepage: "https://github.com/bond-community/bond-plugin-jira"

# What this plugin provides
capabilities:
  - jira_create_issue
  - jira_update_issue
  - jira_search
  - jira_get_sprint

# What it needs from Bond
requires:
  bond_version: ">=0.9.0"
  skills:
    - web_request        # needs HTTP capability
  permissions:
    - network:outbound   # makes external API calls
    - secrets:read       # reads Jira API token from secrets store

# Configuration the user must provide
config:
  jira_base_url:
    type: string
    required: true
    description: "Your Jira instance URL (e.g., https://mycompany.atlassian.net)"
  jira_project_key:
    type: string
    required: true
    description: "Default project key for new issues"

# Entry points
entry:
  tools: ./tools.py              # Python module exporting tool functions
  setup: ./setup.py              # Run once on install
  prompts: ./prompts/            # Directory of prompt fragments to inject

# MCP server (optional — plugin can also expose itself as an MCP server)
mcp:
  enabled: true
  transport: stdio
  command: "python -m bond_plugin_jira.mcp_server"
```

### 3.2 SpacetimeDB Tables

```rust
#[table(name = installed_plugin, public)]
pub struct InstalledPlugin {
    #[primary_key]
    pub id: String,
    pub name: String,
    pub version: String,
    pub description: String,
    pub author: String,
    pub capabilities: String,       // JSON array of capability strings
    pub permissions: String,        // JSON array of granted permissions
    pub config: String,             // JSON of user-provided configuration
    pub source: String,             // "bundled", "local", "registry"
    pub install_path: String,       // filesystem path to plugin directory
    pub enabled: bool,
    pub installed_at: Timestamp,
    pub updated_at: Timestamp,
}

#[table(name = plugin_registry_entry, public)]
pub struct PluginRegistryEntry {
    #[primary_key]
    pub id: String,
    pub name: String,
    pub latest_version: String,
    pub description: String,
    pub author: String,
    pub capabilities: String,       // JSON array
    pub download_url: String,
    pub checksum_sha256: String,
    pub downloads: u64,
    pub rating: f32,                // 0.0-5.0
    pub verified: bool,             // passed security review
    pub last_synced: Timestamp,
}
```

### 3.3 Reducers

- `install_plugin {id, name, version, description, author, capabilities, permissions, config, source, installPath}` — Register a newly installed plugin.
- `uninstall_plugin {id}` — Remove a plugin and clean up its resources.
- `update_plugin_config {id, config}` — Change plugin configuration.
- `toggle_plugin {id, enabled}` — Enable or disable a plugin without uninstalling.
- `sync_registry {}` — Pull latest plugin catalog from remote registry.

---

## 4. Architecture

### 4.1 Plugin Lifecycle

```
Discovery → Install → Configure → Activate → Use → Update → Uninstall
    │          │          │           │         │       │         │
    ▼          ▼          ▼           ▼         ▼       ▼         ▼
  Browse    Download   User sets   Load tools  Agent   Pull new  Remove
  registry  + verify   config      + prompts   calls   version   files
            checksum   values      into runtime tools   + migrate + cleanup
```

### 4.2 Plugin Loading

At startup (and when a plugin is installed/enabled at runtime):

```python
class PluginLoader:
    """Discovers and loads Bond plugins."""
    
    PLUGIN_DIR = Path("~/.bond/plugins").expanduser()
    
    async def load_all(self) -> list[LoadedPlugin]:
        """Load all enabled plugins."""
        installed = await get_enabled_plugins()
        loaded = []
        
        for plugin in installed:
            try:
                manifest = self._read_manifest(plugin.install_path)
                self._verify_compatibility(manifest)
                self._check_permissions(manifest, plugin.permissions)
                
                tools = self._load_tools(manifest, plugin.install_path)
                prompts = self._load_prompts(manifest, plugin.install_path)
                
                loaded.append(LoadedPlugin(
                    name=plugin.name,
                    tools=tools,
                    prompts=prompts,
                    config=json.loads(plugin.config),
                ))
                logger.info("Loaded plugin %s v%s (%d tools)", 
                           plugin.name, plugin.version, len(tools))
                
            except PluginLoadError as ex:
                logger.error("Failed to load plugin %s: %s", plugin.name, ex)
                # Don't crash Bond — disable the plugin and continue
                await toggle_plugin(plugin.id, enabled=False)
                
        return loaded
    
    def _load_tools(self, manifest: dict, install_path: str) -> list[Tool]:
        """Import tool functions from the plugin's tools module."""
        tools_path = Path(install_path) / manifest["entry"]["tools"]
        spec = importlib.util.spec_from_file_location("plugin_tools", tools_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        return [
            Tool(name=name, fn=fn, description=fn.__doc__)
            for name, fn in inspect.getmembers(module, inspect.isfunction)
            if hasattr(fn, "_bond_tool")  # decorated with @bond_tool
        ]
```

### 4.3 Runtime Skill Discovery

When an agent encounters a task it can't handle with current tools:

```python
async def discover_skill_for_task(task_description: str, required_capability: str) -> Optional[str]:
    """Check if there's a plugin that provides the needed capability.
    
    Returns a suggestion message if a plugin is found, None otherwise.
    """
    # Check installed-but-disabled plugins first
    disabled = await find_disabled_plugin_with_capability(required_capability)
    if disabled:
        return f"Plugin '{disabled.name}' can do this but is disabled. Enable it?"
    
    # Check registry for installable plugins
    available = await find_registry_plugin_with_capability(required_capability)
    if available:
        return (
            f"Plugin '{available.name}' ({available.description}) can handle this. "
            f"Install it? (v{available.latest_version}, {'✓ verified' if available.verified else '⚠ unverified'})"
        )
    
    return None
```

### 4.4 Security Sandboxing

Plugins run with restricted permissions:

```python
class PluginSandbox:
    """Enforces permission boundaries for plugin execution."""
    
    def __init__(self, granted_permissions: list[str]):
        self.permissions = set(granted_permissions)
    
    def check_permission(self, required: str):
        if required not in self.permissions:
            raise PermissionDeniedError(
                f"Plugin requires '{required}' permission which was not granted. "
                f"Granted: {self.permissions}"
            )
    
    async def execute_tool(self, tool: Tool, args: dict) -> Any:
        """Execute a plugin tool within the sandbox."""
        # Check declared permissions
        for perm in tool.required_permissions:
            self.check_permission(perm)
        
        # Network isolation: only allow outbound if permitted
        if "network:outbound" not in self.permissions:
            args = self._strip_network_access(args)
        
        # Filesystem isolation: plugin can only access its own directory + workspace
        if "filesystem:write" not in self.permissions:
            self._verify_read_only(args)
        
        return await tool.fn(**args)
```

---

## 5. Interaction with Existing Systems

| System | Integration |
|--------|------------|
| Skills federation (047) | Plugins are the packaging format for federated skills; the plugin loader replaces the current ad-hoc skill loading |
| MCP integration (017) | Plugins can optionally expose MCP servers; MCP tools from plugins are registered alongside built-in MCP tools |
| Multi-agent coordination (084) | Plugin capabilities are registered in the capability registry; agents can be routed to plugins |
| Audit trails (085) | Plugin tool calls are traced through the same `AuditedToolExecutor` as built-in tools |
| Cost tracking (081) | Plugin-initiated LLM calls are attributed to the plugin for cost visibility |
| Prompt management (010) | Plugin prompt fragments are injected through the existing prompt hierarchy |

---

## 6. Migration Path

1. **Phase 1**: Define the `bond-plugin.yaml` manifest format and `PluginLoader`. Convert 2-3 existing skills (e.g., web search, browser agent) into plugins as proof of concept.
2. **Phase 2**: Plugin install/uninstall via CLI (`bond plugin install jira-integration`). Local plugin directory with enable/disable.
3. **Phase 3**: Frontend plugin manager — browse installed plugins, configure, enable/disable.
4. **Phase 4**: Remote registry — a GitHub-hosted plugin catalog that Bond can sync. Community submissions with basic verification.
5. **Phase 5**: Runtime discovery — agents suggest plugin installation when they encounter tasks they can't handle.

---

## 7. Open Questions

- Should plugins be Python-only, or support other languages via MCP/subprocess? Python-only is simpler but limits the ecosystem.
- How do we handle plugin conflicts? (Two plugins both provide a `create_issue` tool.) Namespace prefixing? User chooses?
- What's the trust model for the registry? Verified-only? Allow unverified with warnings? Code signing?
- Should plugins be able to depend on other plugins? (e.g., `jira-integration` depends on `web-request`.) This adds complexity but reflects real needs.
- How do we version plugin APIs? If Bond changes its tool interface, how do plugins stay compatible?
