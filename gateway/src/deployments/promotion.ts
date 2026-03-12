/**
 * Promotion API — user-session-auth only.
 *
 * Agents cannot call these endpoints (their broker tokens are rejected).
 * This is the ONLY way to promote scripts between environments.
 */

import { Router } from "express";
import path from "node:path";
import { homedir } from "node:os";
import type { GatewayConfig } from "../config/index.js";
import { extractUserIdentity } from "./session-tokens.js";
import {
  getEnvironment,
  getEnvironments,
  getApprovers,
  getPromotion,
  getPromotionsForScript,
  getAllPromotions,
  initiatePromotion,
  recordApproval,
  updatePromotionStatus,
  getApprovalsForPromotion,
} from "./stdb.js";
import { getManifest, listScripts } from "./scripts.js";

const DEPLOYMENTS_DIR = path.join(homedir(), ".bond", "deployments");

function requireUserAuth(req: any, res: any): { user_id: string; role: string } | null {
  const identity = extractUserIdentity(req.headers.authorization);
  if (!identity) {
    res.status(403).json({ error: "Agent tokens are not allowed to call the Promotion API" });
    return null;
  }
  return identity;
}

export function createPromotionRouter(config: GatewayConfig): Router {
  const router = Router();

  // GET /api/v1/deployments/pipeline — pipeline view for all scripts
  router.get("/pipeline", async (_req: any, res: any) => {
    try {
      const [envs, promotions] = await Promise.all([
        getEnvironments(config),
        getAllPromotions(config),
      ]);

      const scripts = listScripts(DEPLOYMENTS_DIR);

      // Build pipeline: script_id → version → env → promotion status
      const pipeline: Record<string, any> = {};

      for (const script of scripts) {
        for (const version of script.versions) {
          const key = `${script.script_id}@${version}`;
          const manifest = getManifest(DEPLOYMENTS_DIR, script.script_id, version);
          pipeline[key] = {
            script_id: script.script_id,
            version,
            name: manifest?.name ?? script.script_id,
            environments: {} as Record<string, any>,
          };
        }
      }

      // Overlay promotion state
      for (const promo of promotions) {
        const key = `${promo.script_id}@${promo.script_version}`;
        if (!pipeline[key]) {
          pipeline[key] = {
            script_id: promo.script_id,
            version: promo.script_version,
            name: promo.script_id,
            environments: {},
          };
        }
        pipeline[key].environments[promo.environment_name] = {
          status: promo.status,
          initiated_by: promo.initiated_by,
          initiated_at: promo.initiated_at,
          promoted_at: promo.promoted_at,
          deployed_at: promo.deployed_at,
          receipt_id: promo.receipt_id,
          promotion_id: promo.id,
        };
      }

      res.json({
        environments: envs.filter(e => e.is_active).sort((a, b) => a.order - b.order),
        scripts: Object.values(pipeline),
      });
    } catch (err: any) {
      console.error("[promotion] pipeline failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/v1/deployments/promote
  router.post("/promote", async (req: any, res: any) => {
    const identity = requireUserAuth(req, res);
    if (!identity) return;

    const { script_id, version, target_environments, force = false } = req.body;

    if (!script_id || !version) {
      return res.status(400).json({ error: "script_id and version are required" });
    }

    // Validate script exists
    const manifest = getManifest(DEPLOYMENTS_DIR, script_id, version);
    if (!manifest) {
      return res.status(404).json({ error: `Script ${script_id}@${version} not found in registry` });
    }

    // Determine target environments
    let targets: string[] = target_environments;
    if (!targets || targets.length === 0) {
      const envs = await getEnvironments(config);
      targets = envs.filter(e => e.is_active).sort((a, b) => a.order - b.order).map(e => e.name);
    }

    const results: Record<string, any> = {};

    for (const envName of targets) {
      try {
        const env = await getEnvironment(config, envName);
        if (!env || !env.is_active) {
          results[envName] = { status: "skipped", message: "Environment not found or inactive" };
          continue;
        }

        // Check prerequisite: previous environment must have succeeded (unless force)
        if (!force) {
          const allEnvs = await getEnvironments(config);
          const sorted = allEnvs.filter(e => e.is_active).sort((a, b) => a.order - b.order);
          const myIdx = sorted.findIndex(e => e.name === envName);
          if (myIdx > 0) {
            const prevEnv = sorted[myIdx - 1]!;
            const prevPromo = await getPromotion(config, script_id, version, prevEnv.name);
            if (!prevPromo || (prevPromo.status !== "success" && prevPromo.status !== "promoted")) {
              results[envName] = {
                status: "skipped",
                message: `Previous environment '${prevEnv.name}' has not completed successfully`,
              };
              continue;
            }
          }
        }

        // Check if already promoted/deploying
        const existing = await getPromotion(config, script_id, version, envName);
        if (existing && ["promoted", "deploying", "success"].includes(existing.status)) {
          results[envName] = { status: "already_promoted", message: `Already in status: ${existing.status}` };
          continue;
        }

        // Get approvers
        const approvers = await getApprovers(config, envName);
        const requiredApprovals = env.required_approvals || 1;

        // Determine if this counts as an approval
        const promotionId = existing?.id ?? await initiatePromotion(
          config, script_id, version, manifest.sha256, envName,
          requiredApprovals <= 1 ? "promoted" : "awaiting_approvals",
          identity.user_id,
        );

        if (requiredApprovals <= 1) {
          // No multi-approval needed — promote immediately
          if (!existing) {
            // Already initiated with "promoted" status above
          } else {
            await updatePromotionStatus(config, existing.id, "promoted", {
              promoted_at: Date.now(),
            });
          }
          results[envName] = {
            status: "promoted",
            promotion_id: promotionId,
            message: `Script ${script_id}@${version} promoted to ${envName}`,
          };
        } else {
          // Multi-approval workflow
          const currentPromotionId = existing?.id ?? promotionId;
          await recordApproval(config, currentPromotionId, script_id, version, envName, identity.user_id);

          const currentApprovals = await getApprovalsForPromotion(config, currentPromotionId);
          const uniqueApprovers = new Set(currentApprovals.map(a => a.user_id));

          if (uniqueApprovers.size >= requiredApprovals) {
            await updatePromotionStatus(config, currentPromotionId, "promoted", {
              promoted_at: Date.now(),
            });
            results[envName] = {
              status: "promoted",
              promotion_id: currentPromotionId,
              approvals: { received: uniqueApprovers.size, required: requiredApprovals },
              message: `Approved (${uniqueApprovers.size}/${requiredApprovals}) — promoted to ${envName}`,
            };
          } else {
            const pendingApprovers = approvers
              .map(a => a.user_id)
              .filter(uid => !uniqueApprovers.has(uid));
            results[envName] = {
              status: "awaiting_approvals",
              promotion_id: currentPromotionId,
              approvals: { received: uniqueApprovers.size, required: requiredApprovals },
              approved_by: Array.from(uniqueApprovers),
              pending_approvers: pendingApprovers,
              message: `Approval recorded. ${requiredApprovals - uniqueApprovers.size} more needed for ${envName}`,
            };
          }
        }
      } catch (err: any) {
        results[envName] = { status: "error", message: err.message };
      }
    }

    // Single target? Return flattened response
    if (targets.length === 1) {
      return res.json(results[targets[0]!]);
    }
    res.json({ results });
  });

  // POST /api/v1/deployments/promote/approve
  router.post("/promote/approve", async (req: any, res: any) => {
    const identity = requireUserAuth(req, res);
    if (!identity) return;

    const { script_id, version, environment } = req.body;
    if (!script_id || !version || !environment) {
      return res.status(400).json({ error: "script_id, version, and environment are required" });
    }

    try {
      const promo = await getPromotion(config, script_id, version, environment);
      if (!promo) {
        return res.status(404).json({ error: "No pending promotion found" });
      }
      if (promo.status !== "awaiting_approvals") {
        return res.status(400).json({ error: `Promotion is in status '${promo.status}', not awaiting approvals` });
      }

      // Check if user already approved
      const existingApprovals = await getApprovalsForPromotion(config, promo.id);
      if (existingApprovals.some(a => a.user_id === identity.user_id)) {
        return res.status(400).json({ error: "You have already approved this promotion" });
      }

      await recordApproval(config, promo.id, script_id, version, environment, identity.user_id);

      const env = await getEnvironment(config, environment);
      const requiredApprovals = env?.required_approvals || 1;
      const allApprovals = await getApprovalsForPromotion(config, promo.id);
      const uniqueApprovers = new Set(allApprovals.map(a => a.user_id));

      if (uniqueApprovers.size >= requiredApprovals) {
        await updatePromotionStatus(config, promo.id, "promoted", { promoted_at: Date.now() });
        return res.json({
          status: "promoted",
          approvals: { received: uniqueApprovers.size, required: requiredApprovals },
          message: `Approval threshold met — promoted to ${environment}`,
        });
      }

      const approvers = await getApprovers(config, environment);
      const pendingApprovers = approvers.map(a => a.user_id).filter(uid => !uniqueApprovers.has(uid));
      res.json({
        status: "awaiting_approvals",
        approvals: { received: uniqueApprovers.size, required: requiredApprovals },
        approved_by: Array.from(uniqueApprovers),
        pending_approvers: pendingApprovers,
        message: `Approval recorded. ${requiredApprovals - uniqueApprovers.size} more needed.`,
      });
    } catch (err: any) {
      console.error("[promotion] approve failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/v1/deployments/promotions — list all promotions
  router.get("/promotions", async (_req: any, res: any) => {
    try {
      const promotions = await getAllPromotions(config);
      res.json(promotions);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/v1/deployments/promotions/:scriptId/:version — per-script promotion state
  router.get("/promotions/:scriptId/:version", async (req: any, res: any) => {
    try {
      const { scriptId, version } = req.params;
      const promotions = await getPromotionsForScript(config, scriptId, version);
      res.json(promotions);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  return router;
}
