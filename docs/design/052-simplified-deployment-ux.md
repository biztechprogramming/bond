# Design Doc 052: Simplified Deployment UX \u2014 "The Ship It Flow"

**Status:** Draft
**Date:** 2026-03-22
**Depends on:** 039, 042, 045

## 1. The Problem

Bond has powerful deployment primitives: autonomous agents, SSH execution, stack discovery, and monitoring. However, these features are fragmented across multiple disconnected wizards and forms within a "Deployment" tab buried in Settings.

Current friction points:
1. **Fragmented Entry Points**: Users must choose between "Quick Deploy", "Onboard Server", or "Discover Stack" before they even start.
2. **Setup Overhead**: Creating deployment agents is a prerequisite that feels like "infrastructure work" rather than "deploying my app."
3. **Lack of Intent-Based Flow**: The UI asks "How do you want to do this?" instead of "What are you trying to achieve?"
4. **Visibility**: Deployment is a core activity but is hidden in Settings.

## 2. The Vision: "Vercel for your own Infrastructure"

The goal is to provide a "Ship It" experience where Bond handles the complexity of infrastructure mapping, agent coordination, and script generation.

### Key Principles:
1. **Intent First**: Start with the repository or the server. Bond figures out the rest.
2. **Unified Wizard**: One entry point for all deployment types.
3. **Plan Before Action**: Show the user exactly what Bond detected and what it's about to do (the "Deployment Plan").
4. **Top-Level Visibility**: Move Deployment to a primary sidebar item.

## 3. Proposed Changes

### 3.1 Top-Level Navigation
Move the "Deployment" tab out of `Settings` and into the main sidebar. It should be a first-class citizen alongside "Agents" and "Channels".

### 3.2 The Unified "New Deployment" Wizard
A single flow that replaces `QuickDeployForm`, `OnboardServerWizard`, and `DiscoverStackWizard`.

**Step 1: Source**
- "Connect a Repository" (GitHub/GitLab/URL)
- OR "Connect a Server" (IP/SSH)
- OR "Pick an Existing Resource"

**Step 2: Auto-Discovery (The "Magic" Step)**
- If a repo is provided, Bond clones it to a temporary sandbox and runs `BuildStrategyDetector`.
- If a server is provided, Bond SSHs in and runs discovery scripts to see what's already there.
- **Output**: A "Deployment Plan" card.

**Step 3: The Deployment Plan**
A summary view that says:
> "I detected a **Next.js** application. I will:
> 1. Create a **Production** deployment agent.
> 2. Build a Docker image using your `Dockerfile`.
> 3. Deploy to your **Web Server** (192.168.1.10).
> 4. Set up monitoring for port **3000**."

**Step 4: Ship It**
- One button to execute the plan.
- Real-time progress showing agent creation, building, and deployment.

### 3.3 The "App-Centric" Dashboard
Instead of focusing on Agents or Pipelines, the main view should focus on **Apps**.
- Each App card shows its status across environments (Dev, Staging, Prod).
- Clicking an App shows its timeline, logs, and health.

## 4. Mockup Structure

I will create two HTML mockups:
1. `deployment-simplified.html`: The new "Deploy" dashboard with the "New Deployment" button.
2. `deployment-progress.html`: The "Ship It" progress view showing the execution of a Deployment Plan.

## 5. Implementation Strategy

1. **Refactor Navigation**: Update `Sidebar.tsx` and `App.tsx` to include "Deploy" as a top-level item.
2. **Create `NewDeploymentWizard`**: A multi-step component that orchestrates existing forms but hides the complexity.
3. **Enhance `deploy_tools.py`**: Add a "Generate Plan" action that returns a structured JSON plan for the UI to display.
4. **App-Centric View**: Create a new `AppDashboard` component that aggregates data from `EnvironmentDashboard` and `PipelineSection`.
