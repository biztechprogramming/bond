# Skills Integration for Bond

This document describes how the Anthropic skills repository has been integrated into Bond.

## Overview

Bond now supports Agent Skills from the Anthropic skills repository. Skills are reusable instructional guides that help agents perform specialized tasks more effectively.

## Architecture

### 1. Skills Directory Mount
- The `~/skills` directory is mounted into Docker containers at `/skills`
- Read-only access to prevent accidental modifications
- Environment variable: `BOND_SKILLS_PATH=/skills`

### 2. Skills Manager
Location: `backend/app/agent/skills/__init__.py`
- Loads skills from the skills directory
- Parses SKILL.md files with frontmatter
- Provides search and retrieval capabilities

### 3. Skills Tool
Location: `backend/app/agent/tools/skills.py`
- Updated from stub to fully functional implementation
- Supports actions: `list`, `search`, `load`, `execute`

## Available Skills

The following skills are available from the Anthropic repository:

1. **xlsx** - Spreadsheet file manipulation
2. **theme-factory** - Styling artifacts with themes
3. **claude-api** - Building apps with Claude API
4. **webapp-testing** - Testing local web applications
5. **slack-gif-creator** - Creating animated GIFs for Slack
6. **skill-creator** - Creating and modifying skills
7. **frontend-design** - Production-grade frontend interfaces
8. **docx** - Word document manipulation
9. **brand-guidelines** - Applying Anthropic's brand colors
10. **pdf** - PDF file manipulation
11. **internal-comms** - Writing internal communications
12. **canvas-design** - Creating visual art
13. **web-artifacts-builder** - Creating HTML artifacts
14. **pptx** - PowerPoint file manipulation
15. **mcp-builder** - Creating MCP servers
16. **algorithmic-art** - Creating algorithmic art with p5.js
17. **doc-coauthoring** - Co-authoring documentation

## Usage Examples

### From an Agent's Perspective

```python
# List all available skills
await tools.skills(action="list")

# Search for skills related to "code"
await tools.skills(action="search", query="code")

# Load a specific skill
await tools.skills(action="load", skill_name="claude-api")

# Execute a skill (loads and returns instructions)
await tools.skills(action="execute", skill_name="frontend-design")
```

### From the Command Line

```bash
# Start Bond with skills mounted
docker-compose up

# Or in development
docker-compose -f docker-compose.dev.yml up
```

## Docker Configuration

### Development (`docker-compose.dev.yml`)
```yaml
volumes:
  - ~/skills:/skills:ro
environment:
  - BOND_SKILLS_PATH=/skills
```

### Production (`docker-compose.yml`)
```yaml
volumes:
  - ~/skills:/skills:ro
environment:
  - BOND_SKILLS_PATH=/skills
```

## How Skills Work

1. **Loading**: Skills are loaded on-demand when first accessed
2. **Parsing**: Each skill's `SKILL.md` file is parsed for frontmatter and content
3. **Searching**: Skills can be searched by name or description
4. **Execution**: When a skill is "executed", its instructions are returned for the agent to follow

## Future Enhancements

1. **Skill Execution Engine**: Actually execute skill scripts and tools
2. **Skill Composition**: Combine multiple skills for complex tasks
3. **Skill Versioning**: Support different versions of skills
4. **Local Skill Development**: Allow users to create and test their own skills
5. **Skill Marketplace**: Discover and install skills from a registry

## Testing

To verify the skills system is working:

```python
from backend.app.agent.skills import SkillsManager

manager = SkillsManager("/home/andrew/skills")
skills = manager.list_skills()
print(f"Loaded {len(skills)} skills")
```

## Notes

- Skills are read-only in the container to prevent accidental modifications
- The skills system follows the Agent Skills specification from Anthropic
- Skills are instructional guides, not executable code (though they may reference scripts)
- Agents should read and apply skill instructions to their current task