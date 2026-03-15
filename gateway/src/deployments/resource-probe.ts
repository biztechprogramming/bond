/**
 * Resource Probe — discovers capabilities of deployment targets.
 */

import { execSync } from "node:child_process";

export interface ProbeResult {
  capabilities: Record<string, any>;
  state: Record<string, any>;
}

/**
 * Probe a resource based on its connection info and type.
 */
export async function probeResource(
  connection: any,
  resourceType: string,
): Promise<ProbeResult> {
  switch (resourceType) {
    case "linux-server":
      return probeSSH(connection);
    case "local":
      return probeLocal();
    default:
      return probeGeneric(resourceType);
  }
}

/**
 * SSH probe — returns a template of what would be discovered.
 * Actual SSH execution is not implemented yet.
 */
function probeSSH(conn: any): ProbeResult {
  return {
    capabilities: {
      ssh: true,
      host: conn.host || "unknown",
      port: conn.port || 22,
      user: conn.user || "deploy",
      docker: "unknown",
      node: "unknown",
      python: "unknown",
      note: "SSH probe not yet implemented — connect manually to verify",
    },
    state: {
      status: "pending",
      message: "SSH probe requires actual connection — results are templated",
      cpus: "unknown",
      memory_gb: "unknown",
      disk_available_gb: "unknown",
    },
  };
}

/**
 * Local probe — actually checks what's installed on this machine.
 */
function probeLocal(): ProbeResult {
  const capabilities: Record<string, any> = { local: true };
  const state: Record<string, any> = { status: "online" };

  const tryCmd = (cmd: string): string | null => {
    try {
      return execSync(cmd, { timeout: 5000, stdio: ["pipe", "pipe", "pipe"] })
        .toString()
        .trim();
    } catch {
      return null;
    }
  };

  // Software versions
  capabilities.docker = tryCmd("docker --version");
  capabilities.node = tryCmd("node --version");
  capabilities.python = tryCmd("python3 --version");
  capabilities.git = tryCmd("git --version");
  capabilities.bun = tryCmd("bun --version");

  // System info
  const cpus = tryCmd("nproc");
  if (cpus) state.cpus = parseInt(cpus);

  const memLine = tryCmd("free -g");
  if (memLine) {
    const match = memLine.match(/Mem:\s+(\d+)/);
    if (match) state.memory_gb = parseInt(match[1]);
  }

  const diskLine = tryCmd("df -BG / | tail -1");
  if (diskLine) {
    const parts = diskLine.split(/\s+/);
    if (parts.length >= 4) {
      state.disk_total_gb = parts[1];
      state.disk_used_gb = parts[2];
      state.disk_available_gb = parts[3];
    }
  }

  const osRelease = tryCmd("cat /etc/os-release 2>/dev/null | head -2");
  if (osRelease) {
    const nameMatch = osRelease.match(/PRETTY_NAME="([^"]+)"/);
    if (nameMatch) state.os = nameMatch[1];
  }

  state.hostname = tryCmd("hostname");
  state.uptime = tryCmd("uptime -p");

  return { capabilities, state };
}

/**
 * Generic probe for cloud resource types — returns capability template.
 */
function probeGeneric(resourceType: string): ProbeResult {
  const templates: Record<string, ProbeResult> = {
    kubernetes: {
      capabilities: {
        kubernetes: true,
        kubectl: "required",
        helm: "optional",
        note: "Kubernetes probe requires kubeconfig — connect manually",
      },
      state: {
        status: "pending",
        message: "Kubernetes probe not yet implemented",
      },
    },
    "docker-host": {
      capabilities: {
        docker: true,
        compose: "optional",
        note: "Docker host probe requires SSH or API access",
      },
      state: {
        status: "pending",
        message: "Docker host probe not yet implemented",
      },
    },
    "aws-ecs": {
      capabilities: {
        ecs: true,
        ecr: true,
        fargate: "optional",
        note: "AWS ECS probe requires AWS credentials",
      },
      state: {
        status: "pending",
        message: "AWS ECS probe not yet implemented",
      },
    },
  };

  return templates[resourceType] || {
    capabilities: {
      type: resourceType,
      note: `No probe template for type '${resourceType}'`,
    },
    state: {
      status: "pending",
      message: `Probe not implemented for '${resourceType}'`,
    },
  };
}
