/**
 * Pipeline-as-Code — REST API router.
 *
 * Routes:
 *   POST /run       — submit YAML, execute pipeline
 *   GET  /runs/:id  — get run status
 *   POST /validate  — validate YAML without executing
 */

import { Router } from "express";
import { parsePipelineYaml } from "./pipeline-parser.js";
import { executePipeline, getRun } from "./pipeline-executor.js";

export function createPipelineRouter(): Router {
  const router = Router();

  // Validate YAML without executing
  router.post("/validate", (req: any, res: any) => {
    const { yaml: yamlContent } = req.body || {};
    if (!yamlContent || typeof yamlContent !== "string") {
      return res.status(400).json({ valid: false, errors: ["Request body must include 'yaml' string"] });
    }

    const result = parsePipelineYaml(yamlContent);
    res.json({
      valid: result.valid,
      errors: result.errors.length > 0 ? result.errors : undefined,
      warnings: result.warnings.length > 0 ? result.warnings : undefined,
    });
  });

  // Submit YAML and execute pipeline
  router.post("/run", async (req: any, res: any) => {
    const { yaml: yamlContent } = req.body || {};
    if (!yamlContent || typeof yamlContent !== "string") {
      return res.status(400).json({ error: "Request body must include 'yaml' string" });
    }

    const parsed = parsePipelineYaml(yamlContent);
    if (!parsed.valid || !parsed.pipeline) {
      return res.status(400).json({ error: "Invalid pipeline YAML", errors: parsed.errors });
    }

    try {
      const result = await executePipeline(parsed.pipeline);
      res.json(result);
    } catch (err: any) {
      console.error("[pipeline] execution failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  // Get run status
  router.get("/runs/:id", (req: any, res: any) => {
    const run = getRun(req.params.id);
    if (!run) {
      return res.status(404).json({ error: "Run not found" });
    }
    res.json(run);
  });

  return router;
}
