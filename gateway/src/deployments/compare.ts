/**
 * Environment Comparison — diff software versions, scripts, secrets, and resources
 * across two deployment environments.
 *
 * Design Doc 045 §8.3
 */

import type { GatewayConfig } from "../config/index.js";
import { readManifest, diffManifests } from "./manifest.js";
import { loadSecrets } from "./secrets.js";
import { getResources } from "./resources.js";
import { getAllPromotions } from "./stdb.js";

export interface EnvComparisonResult {
  envA: string;
  envB: string;
  compared_at: string;
  software: SoftwareComparison;
  scripts: ScriptComparison;
  secrets: SecretComparison;
  resources: ResourceComparison;
}

export interface SoftwareComparison {
  manifest_diff: any | null;
  envA_manifest: string | null;
  envB_manifest: string | null;
}

export interface ScriptComparison {
  only_in_envA: string[];
  only_in_envB: string[];
  version_differences: Array<{ script_id: string; envA_version: string; envB_version: string }>;
  matching: string[];
}

export interface SecretComparison {
  only_in_envA: string[];
  only_in_envB: string[];
  in_both: string[];
}

export interface ResourceComparison {
  only_in_envA: Array<{ id: string; name: string; type: string }>;
  only_in_envB: Array<{ id: string; name: string; type: string }>;
  in_both: Array<{ name: string; type: string }>;
}

/**
 * Full environment comparison across all dimensions.
 */
export async function compareEnvironments(
  config: GatewayConfig,
  envA: string,
  envB: string,
): Promise<EnvComparisonResult> {
  const [software, scripts, secrets, resources] = await Promise.all([
    compareSoftware(envA, envB),
    compareScripts(config, envA, envB),
    compareSecretKeys(envA, envB),
    compareResources(config, envA, envB),
  ]);

  return {
    envA,
    envB,
    compared_at: new Date().toISOString(),
    software,
    scripts,
    secrets,
    resources,
  };
}

/**
 * Compare software versions via discovery manifests.
 */
function compareSoftware(envA: string, envB: string): SoftwareComparison {
  const manifestA = readManifest(envA);
  const manifestB = readManifest(envB);

  return {
    envA_manifest: manifestA ? envA : null,
    envB_manifest: manifestB ? envB : null,
    manifest_diff: manifestA && manifestB ? diffManifests(manifestA, manifestB) : null,
  };
}

/**
 * Compare deployed script versions between environments.
 */
async function compareScripts(config: GatewayConfig, envA: string, envB: string): Promise<ScriptComparison> {
  const promotions = await getAllPromotions(config);

  // Get latest deployed version per script per environment
  const getLatest = (env: string): Map<string, string> => {
    const map = new Map<string, string>();
    for (const p of promotions) {
      if (p.environment_name !== env || p.status !== "deployed") continue;
      const existing = map.get(p.script_id);
      if (!existing || p.deployed_at > (promotions.find(x => x.script_id === p.script_id && x.script_version === existing && x.environment_name === env)?.deployed_at ?? 0)) {
        map.set(p.script_id, p.script_version);
      }
    }
    return map;
  };

  const aScripts = getLatest(envA);
  const bScripts = getLatest(envB);
  const allIds = new Set([...aScripts.keys(), ...bScripts.keys()]);

  const only_in_envA: string[] = [];
  const only_in_envB: string[] = [];
  const version_differences: ScriptComparison["version_differences"] = [];
  const matching: string[] = [];

  for (const id of allIds) {
    const va = aScripts.get(id);
    const vb = bScripts.get(id);
    if (va && !vb) only_in_envA.push(id);
    else if (!va && vb) only_in_envB.push(id);
    else if (va !== vb) version_differences.push({ script_id: id, envA_version: va!, envB_version: vb! });
    else matching.push(id);
  }

  return { only_in_envA, only_in_envB, version_differences, matching };
}

/**
 * Compare secret keys (no values) between environments.
 */
function compareSecretKeys(envA: string, envB: string): SecretComparison {
  const aKeys = new Set(Object.keys(loadSecrets(envA)));
  const bKeys = new Set(Object.keys(loadSecrets(envB)));

  return {
    only_in_envA: [...aKeys].filter(k => !bKeys.has(k)),
    only_in_envB: [...bKeys].filter(k => !aKeys.has(k)),
    in_both: [...aKeys].filter(k => bKeys.has(k)),
  };
}

/**
 * Compare server resources between environments.
 */
async function compareResources(config: GatewayConfig, envA: string, envB: string): Promise<ResourceComparison> {
  const [aRes, bRes] = await Promise.all([
    getResources(config, envA),
    getResources(config, envB),
  ]);

  const aNames = new Set(aRes.map((r: any) => r.name));
  const bNames = new Set(bRes.map((r: any) => r.name));

  return {
    only_in_envA: aRes.filter((r: any) => !bNames.has(r.name)).map((r: any) => ({ id: r.id, name: r.name, type: r.resource_type })),
    only_in_envB: bRes.filter((r: any) => !aNames.has(r.name)).map((r: any) => ({ id: r.id, name: r.name, type: r.resource_type })),
    in_both: aRes.filter((r: any) => bNames.has(r.name)).map((r: any) => ({ name: r.name, type: r.resource_type })),
  };
}
