"""Skills system for Bond agents."""

from __future__ import annotations

import os
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger(__name__)


class Skill:
    """Represents an Agent Skill."""
    
    def __init__(self, path: Path):
        self.path = path
        self.name = path.name
        self.skill_md_path = path / "SKILL.md"
        self.frontmatter: Dict[str, Any] = {}
        self.content: str = ""
        self.loaded = False
        
    def load(self) -> None:
        """Load the skill from disk."""
        if not self.skill_md_path.exists():
            raise FileNotFoundError(f"SKILL.md not found in {self.path}")
            
        with open(self.skill_md_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Parse frontmatter
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                frontmatter_str = parts[1].strip()
                self.content = parts[2].strip()
                try:
                    self.frontmatter = yaml.safe_load(frontmatter_str) or {}
                except yaml.YAMLError as e:
                    logger.warning(f"Failed to parse frontmatter for skill {self.name}: {e}")
                    self.frontmatter = {}
            else:
                self.content = content
        else:
            self.content = content
            
        # Validate required fields
        if 'name' not in self.frontmatter:
            self.frontmatter['name'] = self.name
        if 'description' not in self.frontmatter:
            self.frontmatter['description'] = "No description provided"
            
        self.loaded = True
        
    def get_metadata(self) -> Dict[str, Any]:
        """Get skill metadata (frontmatter only)."""
        if not self.loaded:
            self.load()
        return self.frontmatter.copy()
        
    def get_full_content(self) -> str:
        """Get full skill content (frontmatter + instructions)."""
        if not self.loaded:
            self.load()
        return self.content
        
    def __repr__(self) -> str:
        return f"Skill(name={self.name}, path={self.path})"


class SkillsManager:
    """Manages loading and accessing skills."""
    
    def __init__(self, skills_path: Optional[str] = None):
        self.skills_path = Path(skills_path or os.getenv('BOND_SKILLS_PATH', '/skills'))
        self.skills: Dict[str, Skill] = {}
        self.loaded = False
        
    def load_skills(self) -> None:
        """Load all skills from the skills directory."""
        if not self.skills_path.exists():
            logger.warning(f"Skills directory not found: {self.skills_path}")
            return
            
        # Look for skills in the skills/skills subdirectory (Anthropic format)
        skills_dir = self.skills_path / "skills"
        if not skills_dir.exists():
            # Try the root directory
            skills_dir = self.skills_path
            
        for item in skills_dir.iterdir():
            if item.is_dir():
                skill_md = item / "SKILL.md"
                if skill_md.exists():
                    try:
                        skill = Skill(item)
                        skill.load()
                        self.skills[skill.name] = skill
                        logger.info(f"Loaded skill: {skill.name}")
                    except Exception as e:
                        logger.error(f"Failed to load skill {item.name}: {e}")
                        
        self.loaded = True
        
    def get_skill(self, name: str) -> Optional[Skill]:
        """Get a skill by name."""
        if not self.loaded:
            self.load_skills()
        return self.skills.get(name)
        
    def list_skills(self) -> List[Dict[str, Any]]:
        """List all available skills with metadata."""
        if not self.loaded:
            self.load_skills()
        return [
            {
                'name': skill.name,
                'description': skill.frontmatter.get('description', ''),
                'path': str(skill.path),
            }
            for skill in self.skills.values()
        ]
        
    def search_skills(self, query: str) -> List[Dict[str, Any]]:
        """Search skills by name or description."""
        if not self.loaded:
            self.load_skills()
            
        query_lower = query.lower()
        results = []
        
        for skill in self.skills.values():
            name_match = query_lower in skill.name.lower()
            desc_match = query_lower in skill.frontmatter.get('description', '').lower()
            
            if name_match or desc_match:
                results.append({
                    'name': skill.name,
                    'description': skill.frontmatter.get('description', ''),
                    'path': str(skill.path),
                    'match_type': 'name' if name_match else 'description',
                })
                
        return results


# Global skills manager instance
_skills_manager: Optional[SkillsManager] = None


def get_skills_manager() -> SkillsManager:
    """Get or create the global skills manager."""
    global _skills_manager
    if _skills_manager is None:
        _skills_manager = SkillsManager()
        _skills_manager.load_skills()
    return _skills_manager