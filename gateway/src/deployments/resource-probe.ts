/**
 * Resource Probe — discovers capabilities of deployment targets.
 */

import { execSync, spawn } from "node:child_process";

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
    case "kubernetes":
      return probeKubernetes(connection);
    case "aws-ecs":
      return probeAWS(connection);
    default:
      return probeGeneric(resourceType);
  }
}

// ── SSH helpers ──────────────────────────────────────────────────────────────

/**
 * Run a command over SSH, returning stdout or null on failure.
 */
function sshExec(
  host: string,
  port: number,
  user: string,
  cmd: string,
  keyPath?: string,
  timeoutMs = 10000,
): Promise<string | null> {
  return new Promise((resolve) => {
    const args = [
      "-o", "StrictHostKeyChecking=no",
      "-o", "BatchMode=yes",
      "-o", `ConnectTimeout=${Math.ceil(timeoutMs / 1000)}`,
      "-p", String(port),
    ];
    if (keyPath) args.push("-i", keyPath);
    args.push(`${user}@${host}`, cmd);

    let stdout = "";
    let stderr = "";
    const proc = spawn("ssh", args, { stdio: ["ignore", "pipe", "pipe"] });

    const timer = setTimeout(() => {
      proc.kill("SIGTERM");
      resolve(null);
    }, timeoutMs);

    proc.stdout.on("data", (d: Buffer) => { stdout += d.toString(); });
    proc.stderr.on("data", (d: Buffer) => { stderr += d.toString(); });

    proc.on("close", (code) => {
      clearTimeout(timer);
      resolve(code === 0 ? stdout.trim() : null);
    });

    proc.on("error", () => {
      clearTimeout(timer);
      resolve(null);
    });
  });
}

/**
 * SSH probe — connects to remote host and checks software versions + system state.
 */
async function probeSSH(conn: any): Promise<ProbeResult> {
  const host = conn.host;
  const port = conn.port || 22;
  const user = conn.user || "deploy";
  const keyPath = conn.key_path || conn.identity_file;

  if (!host) {
    return {
      capabilities: { ssh: true, error: "No host specified" },
      state: { status: "error", message: "Connection config missing 'host'" },
    };
  }

  const run = (cmd: string) => sshExec(host, port, user, cmd, keyPath);

  // Test connectivity first
  const connectTest = await run("echo ok");
  if (connectTest === null) {
    return {
      capabilities: {
        ssh: true,
        host,
        port,
        user,
        error: "Connection failed",
      },
      state: {
        status: "unreachable",
        message: `SSH connection to ${user}@${host}:${port} failed (timeout or auth error)`,
      },
    };
  }

  // Probe software versions in parallel
  const [docker, node, python, git, bun] = await Promise.all([
    run("docker --version 2>/dev/null"),
    run("node --version 2>/dev/null"),
    run("python3 --version 2>/dev/null"),
    run("git --version 2>/dev/null"),
    run("bun --version 2>/dev/null"),
  ]);

  // Probe system resources
  const [cpus, memLine, diskLine, osRelease, hostname, uptime] = await Promise.all([
    run("nproc"),
    run("free -g"),
    run("df -BG / | tail -1"),
    run("cat /etc/os-release 2>/dev/null | head -2"),
    run("hostname"),
    run("uptime -p"),
  ]);

  const capabilities: Record<string, any> = {
    ssh: true,
    host,
    port,
    user,
    docker: docker || null,
    node: node || null,
    python: python || null,
    git: git || null,
    bun: bun || null,
  };

  const state: Record<string, any> = { status: "online" };

  if (cpus) state.cpus = parseInt(cpus);

  if (memLine) {
    const match = memLine.match(/Mem:\s+(\d+)/);
    if (match) state.memory_gb = parseInt(match[1]);
  }

  if (diskLine) {
    const parts = diskLine.split(/\s+/);
    if (parts.length >= 4) {
      state.disk_total_gb = parts[1];
      state.disk_used_gb = parts[2];
      state.disk_available_gb = parts[3];
    }
  }

  if (osRelease) {
    const nameMatch = osRelease.match(/PRETTY_NAME="([^"]+)"/);
    if (nameMatch) state.os = nameMatch[1];
  }

  state.hostname = hostname || null;
  state.uptime = uptime || null;

  return { capabilities, state };
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

// ── Kubernetes probe ─────────────────────────────────────────────────────────

/**
 * Kubernetes probe — checks cluster info via kubectl.
 */
async function probeKubernetes(conn: any): Promise<ProbeResult> {
  const kubeconfig = conn.kubeconfig || conn.kubeconfig_path;
  const context = conn.context;

  const tryCmd = (cmd: string): string | null => {
    try {
      const envVars: Record<string, string> = { ...process.env as Record<string, string> };
      if (kubeconfig) envVars.KUBECONFIG = kubeconfig;
      const fullCmd = context ? `kubectl --context ${context} ${cmd}` : `kubectl ${cmd}`;
      return execSync(fullCmd, { timeout: 15000, stdio: ["pipe", "pipe", "pipe"], env: envVars })
        .toString()
        .trim();
    } catch {
      return null;
    }
  };

  // Test connectivity
  const clusterInfo = tryCmd("cluster-info 2>/dev/null | head -3");
  if (!clusterInfo) {
    return {
      capabilities: {
        kubernetes: true,
        kubectl: false,
        error: "kubectl not available or cluster unreachable",
      },
      state: {
        status: "unreachable",
        message: "Could not connect to Kubernetes cluster",
      },
    };
  }

  const nodeCount = tryCmd("get nodes --no-headers 2>/dev/null | wc -l");
  const namespaces = tryCmd("get namespaces --no-headers -o custom-columns=:metadata.name 2>/dev/null");
  const version = tryCmd("version --short 2>/dev/null || kubectl version --client 2>/dev/null | head -1");
  const nodeResources = tryCmd("top nodes --no-headers 2>/dev/null");

  const capabilities: Record<string, any> = {
    kubernetes: true,
    kubectl: true,
    cluster_info: clusterInfo,
    version: version || null,
  };

  // Check for helm
  try {
    const helmVersion = execSync("helm version --short 2>/dev/null", { timeout: 5000, stdio: ["pipe", "pipe", "pipe"] }).toString().trim();
    capabilities.helm = helmVersion;
  } catch {
    capabilities.helm = null;
  }

  const state: Record<string, any> = { status: "online" };
  if (nodeCount) state.node_count = parseInt(nodeCount);
  if (namespaces) state.namespaces = namespaces.split("\n").filter(Boolean);
  if (nodeResources) state.node_resources = nodeResources;

  return { capabilities, state };
}

// ── AWS probe ────────────────────────────────────────────────────────────────

/**
 * AWS ECS probe — checks clusters, services, and task definitions via aws CLI.
 */
async function probeAWS(conn: any): Promise<ProbeResult> {
  const region = conn.region || "us-east-1";
  const profile = conn.profile;

  const tryCmd = (cmd: string): string | null => {
    try {
      const envVars: Record<string, string> = { ...process.env as Record<string, string> };
      if (conn.access_key_id) envVars.AWS_ACCESS_KEY_ID = conn.access_key_id;
      if (conn.secret_access_key) envVars.AWS_SECRET_ACCESS_KEY = conn.secret_access_key;
      if (conn.session_token) envVars.AWS_SESSION_TOKEN = conn.session_token;

      const profileFlag = profile ? ` --profile ${profile}` : "";
      const fullCmd = `aws${profileFlag} --region ${region} ${cmd}`;
      return execSync(fullCmd, { timeout: 15000, stdio: ["pipe", "pipe", "pipe"], env: envVars })
        .toString()
        .trim();
    } catch {
      return null;
    }
  };

  // Test connectivity
  const identity = tryCmd("sts get-caller-identity 2>/dev/null");
  if (!identity) {
    return {
      capabilities: {
        aws: true,
        ecs: false,
        error: "AWS CLI not available or credentials invalid",
      },
      state: {
        status: "unreachable",
        message: "Could not authenticate with AWS",
      },
    };
  }

  const clusters = tryCmd("ecs list-clusters --output json 2>/dev/null");
  const services = tryCmd("ecs list-services --output json 2>/dev/null");
  const taskDefs = tryCmd("ecs list-task-definitions --output json 2>/dev/null");

  const capabilities: Record<string, any> = {
    aws: true,
    ecs: true,
    region,
  };

  let identityParsed: any = null;
  try { identityParsed = JSON.parse(identity); } catch { /* keep null */ }
  if (identityParsed) {
    capabilities.account_id = identityParsed.Account;
    capabilities.arn = identityParsed.Arn;
  }

  const state: Record<string, any> = { status: "online" };

  try {
    if (clusters) {
      const parsed = JSON.parse(clusters);
      state.clusters = parsed.clusterArns || [];
      state.cluster_count = state.clusters.length;
    }
  } catch { /* skip */ }

  try {
    if (services) {
      const parsed = JSON.parse(services);
      state.services = parsed.serviceArns || [];
    }
  } catch { /* skip */ }

  try {
    if (taskDefs) {
      const parsed = JSON.parse(taskDefs);
      state.task_definitions = parsed.taskDefinitionArns || [];
    }
  } catch { /* skip */ }

  return { capabilities, state };
}

/**
 * Generic probe for cloud resource types — returns capability template.
 */
function probeGeneric(resourceType: string): ProbeResult {
  const templates: Record<string, ProbeResult> = {
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
