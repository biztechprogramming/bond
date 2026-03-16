# Deployment Implementation Task List

Design Docs: 039, 042, 043
Status: **ALL COMPLETE** ✅

## SpacetimeDB Module

- [x] 1. Add `deployment_resources` table to STDB module
- [x] 2. Add `deployment_triggers` table to STDB module
- [x] 3. Add reducers for resources (create, update, delete)
- [x] 4. Add reducers for triggers (create, update, delete)
- [x] 5. Fix trigger-handler to use correct reducer names (update_deployment_trigger)

## Backend — Deploy Tool Gating

- [x] 6. Gate `deploy_action` and `file_bug_ticket` tools to `deploy-*` agents only

## Autonomous Agent Behavior

- [x] 7. Wire full autonomous deploy agent system prompt (Doc 043 §5.2) into SetupWizard
- [x] 8. Inject startup message when deploy agent comes online
- [x] 9. Notification-driven deployment (wire agent notifier to gateway messaging)

## Resource Management

- [x] 10. Complete SSH resource probing (actual SSH execution)
- [x] 11. Complete Kubernetes probing (kubectl)
- [x] 12. Complete AWS probing (aws CLI)
- [x] 13. Multi-resource deployment execution (run script once per resource)
- [x] 14. Resource-aware secret injection ($RESOURCE_HOST, $RESOURCE_USER, etc.)
- [x] 15. Infrastructure recommendations (generated from probe results)
- [x] 16. Recommendation apply flow (generate + register + promote install script)

## Pipeline-as-Code — Tier 2

- [x] 17. YAML pipeline parser (`.bond/deploy.yml` schema)
- [x] 18. Step dependency graph / execution order
- [x] 19. Step executor (run each step as bash or in container)
- [x] 20. Matrix expansion (e.g., node: [18, 20, 22])
- [x] 21. Service sidecars (e.g., postgres for tests)
- [x] 22. Wire PipelineYamlEditor.tsx to backend parser
- [x] 23. Wire PipelineStepView.tsx to step execution results

## Queue Auto-Processing

- [x] 24. Auto-dequeue and deploy next script when lock is released

## Tests

- [x] 25. Tests for script registry (9 tests)
- [x] 26. Tests for promotion workflow (7 tests)
- [x] 27. Tests for deploy handler (9 tests)
- [x] 28. Tests for lock/queue behavior (10 tests)
- [x] 29. Tests for health scheduler and drift detection (7 tests)
- [x] 30. Tests for quick deploy generation (6 tests)
- [x] 31. Tests for resource CRUD and probing (5 tests)
- [x] 32. Tests for trigger handler (4 tests)
- [x] 33. Tests for pipeline YAML parser (4 tests)

---

## Summary

- **33/33 tasks completed**
- **~2,200 lines** of new code (pipeline, recommendations, tests)
- **~10,700 lines** existing deployment code reviewed and wired together
- **61 tests** across 9 test files
- **4 new gateway modules**: pipeline-parser, pipeline-executor, pipeline-router, recommendations
- **STDB module** updated with 2 new tables + 5 new reducers
