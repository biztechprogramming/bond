/**
 * Infrastructure Recommendations — generated from probe results.
 *
 * After probing a resource, generates actionable recommendations
 * based on discovered capabilities and system state.
 */

export interface Recommendation {
  rank: number;
  title: string;
  description: string;
  severity: "high" | "medium" | "low" | "info";
  category: "software" | "system" | "security" | "performance";
  apply_script?: string; // bash script to apply the recommendation
}

/**
 * Generate recommendations based on probe results.
 */
export function generateRecommendations(probe: {
  capabilities: Record<string, any>;
  state: Record<string, any>;
}): Recommendation[] {
  const recs: Recommendation[] = [];
  const caps = probe.capabilities;
  const state = probe.state;

  // Skip recommendations for unreachable resources
  if (state.status === "unreachable" || state.status === "error") {
    return recs;
  }

  // ── Software recommendations ────────────────────────────────────────────

  if (!caps.docker) {
    recs.push({
      rank: 0,
      title: "Install Docker",
      description: "Docker is not detected. Recommended for containerized deployments.",
      severity: "medium",
      category: "software",
      apply_script: [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "curl -fsSL https://get.docker.com | sh",
        "sudo usermod -aG docker $USER",
        'echo "Docker installed. Log out and back in for group changes to take effect."',
      ].join("\n"),
    });
  }

  if (!caps.node) {
    recs.push({
      rank: 0,
      title: "Install Node.js",
      description: "Node.js is not detected. Required for JavaScript/TypeScript deployments.",
      severity: "low",
      category: "software",
      apply_script: [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -",
        "sudo apt-get install -y nodejs",
        "node --version",
      ].join("\n"),
    });
  } else if (typeof caps.node === "string") {
    const match = caps.node.match(/v?(\d+)/);
    if (match && parseInt(match[1]) < 18) {
      recs.push({
        rank: 0,
        title: "Upgrade Node.js",
        description: `Node.js ${caps.node} is outdated. Recommend upgrading to v20+ for LTS support.`,
        severity: "medium",
        category: "software",
        apply_script: [
          "#!/usr/bin/env bash",
          "set -euo pipefail",
          "curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -",
          "sudo apt-get install -y nodejs",
          "node --version",
        ].join("\n"),
      });
    }
  }

  if (!caps.git) {
    recs.push({
      rank: 0,
      title: "Install Git",
      description: "Git is not detected. Required for source-based deployments.",
      severity: "medium",
      category: "software",
      apply_script: [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "sudo apt-get update && sudo apt-get install -y git",
        "git --version",
      ].join("\n"),
    });
  }

  if (!caps.python) {
    recs.push({
      rank: 0,
      title: "Install Python 3",
      description: "Python 3 is not detected. Required for Python-based deployments.",
      severity: "low",
      category: "software",
      apply_script: [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "sudo apt-get update && sudo apt-get install -y python3 python3-pip",
        "python3 --version",
      ].join("\n"),
    });
  }

  // ── System recommendations ──────────────────────────────────────────────

  if (typeof state.memory_gb === "number" && state.memory_gb < 2) {
    recs.push({
      rank: 0,
      title: "Low Memory",
      description: `Only ${state.memory_gb}GB RAM detected. Consider upgrading for production workloads (4GB+ recommended).`,
      severity: "high",
      category: "system",
    });
  }

  if (state.disk_available_gb) {
    const available = parseInt(String(state.disk_available_gb).replace(/G/i, ""));
    if (!isNaN(available) && available < 5) {
      recs.push({
        rank: 0,
        title: "Low Disk Space",
        description: `Only ${available}GB disk space available. May cause deployment failures. Consider freeing space or expanding the volume.`,
        severity: "high",
        category: "system",
        apply_script: [
          "#!/usr/bin/env bash",
          "set -euo pipefail",
          "echo '=== Disk usage by directory ==='",
          "sudo du -sh /var/log/* 2>/dev/null | sort -rh | head -10",
          "echo ''",
          "echo '=== Docker cleanup (if available) ==='",
          "command -v docker >/dev/null 2>&1 && docker system prune -f || echo 'Docker not installed'",
          "echo ''",
          "echo '=== Journal cleanup ==='",
          "sudo journalctl --vacuum-size=100M 2>/dev/null || true",
          "df -h /",
        ].join("\n"),
      });
    }
  }

  if (typeof state.cpus === "number" && state.cpus < 2) {
    recs.push({
      rank: 0,
      title: "Single CPU Core",
      description: "Only 1 CPU core detected. Consider scaling up for concurrent workloads.",
      severity: "medium",
      category: "performance",
    });
  }

  // ── Docker-specific ─────────────────────────────────────────────────────

  if (caps.docker && typeof caps.docker === "string") {
    const match = caps.docker.match(/(\d+)\.\d+/);
    if (match && parseInt(match[1]) < 20) {
      recs.push({
        rank: 0,
        title: "Upgrade Docker",
        description: `Docker version ${caps.docker} is outdated. Recommend upgrading to 24+ for security and performance.`,
        severity: "medium",
        category: "software",
        apply_script: [
          "#!/usr/bin/env bash",
          "set -euo pipefail",
          "curl -fsSL https://get.docker.com | sh",
          "docker --version",
        ].join("\n"),
      });
    }
  }

  // Assign sequential ranks
  recs.forEach((r, i) => { r.rank = i + 1; });

  return recs;
}

/**
 * Get the apply script for a specific recommendation by rank.
 */
export function getRecommendationApplyScript(
  recommendations: Recommendation[],
  rank: number,
): { recommendation: Recommendation; script: string } | null {
  const rec = recommendations.find(r => r.rank === rank);
  if (!rec || !rec.apply_script) return null;
  return { recommendation: rec, script: rec.apply_script };
}
