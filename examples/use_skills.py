#!/usr/bin/env python3
"""
Example: How an agent would use the skills system.

This demonstrates the workflow an agent would follow when working with skills.
"""

import asyncio
import os
import sys

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

async def agent_workflow():
    """Simulate an agent using the skills tool."""
    
    print("🤖 Agent: I need to create a frontend design for a dashboard.")
    print("Let me check if there are any skills that can help me...\n")
    
    # Step 1: Search for relevant skills
    print("1. Searching for skills related to 'frontend' or 'design':")
    # In real usage: await tools.skills(action="search", query="frontend design")
    
    # Simulated response
    search_results = [
        {"name": "frontend-design", "description": "Create distinctive, production-grade frontend interfaces...", "match_type": "name"},
        {"name": "theme-factory", "description": "Toolkit for styling artifacts with a theme...", "match_type": "description"},
        {"name": "canvas-design", "description": "Create beautiful visual art...", "match_type": "description"},
    ]
    
    for result in search_results:
        print(f"   • {result['name']}: {result['description'][:80]}...")
    
    # Step 2: Load the most relevant skill
    print("\n2. Loading the 'frontend-design' skill:")
    # In real usage: await tools.skills(action="load", skill_name="frontend-design")
    
    # Simulated skill content
    skill_content = """# Frontend Design Skill

## Overview
Create distinctive, production-grade frontend interfaces with high design quality.

## Key Principles
1. Use consistent spacing and alignment
2. Choose accessible color palettes
3. Implement responsive design
4. Follow component-based architecture

## Tools & Libraries
- React/Next.js for component structure
- Tailwind CSS for styling
- Framer Motion for animations
- Storybook for component documentation

## Workflow
1. Start with wireframes
2. Define design tokens (colors, typography, spacing)
3. Build reusable components
4. Assemble pages from components
5. Test across breakpoints
6. Polish animations and interactions

## Example Components
- Card: Rounded corners, subtle shadow, padding
- Button: Primary/secondary variants, hover states
- Navigation: Responsive hamburger menu on mobile
- Form: Accessible labels, validation states"""
    
    print(f"   Skill loaded: {len(skill_content)} characters of instructions")
    print(f"   Preview: {skill_content[:200]}...\n")
    
    # Step 3: Apply the skill to the task
    print("3. Applying the skill to create a dashboard:")
    print("   • Following component-based architecture")
    print("   • Using Tailwind CSS for styling")
    print("   • Implementing responsive design")
    print("   • Adding subtle animations with Framer Motion")
    print("   • Testing across mobile, tablet, and desktop")
    
    # Step 4: List all available skills for future reference
    print("\n4. Listing all available skills for future tasks:")
    # In real usage: await tools.skills(action="list")
    
    all_skills = [
        {"name": "frontend-design", "description": "Frontend interfaces"},
        {"name": "claude-api", "description": "Building apps with Claude API"},
        {"name": "webapp-testing", "description": "Testing local web apps"},
        {"name": "skill-creator", "description": "Creating new skills"},
        {"name": "mcp-builder", "description": "Creating MCP servers"},
    ]
    
    print(f"   Found {len(all_skills)} skills total")
    print("   Available skills: " + ", ".join([s["name"] for s in all_skills]))
    
    print("\n✅ Agent: Skill applied successfully! I now have clear guidelines")
    print("   for creating a production-grade frontend dashboard.")

if __name__ == "__main__":
    asyncio.run(agent_workflow())