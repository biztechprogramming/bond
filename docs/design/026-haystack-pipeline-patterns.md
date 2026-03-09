# Design Doc 026: Haystack Pipeline Patterns for Prompt Assembly

**Status:** Draft  
**Date:** 2026-03-09  
**Depends on:** 010 (Prompt Management), 021 (Prompt Hierarchy)  
**Reference:** [deepset-ai/haystack](https://github.com/deepset-ai/haystack) (⭐ 24,439)

---

## 1. What Haystack Does

Haystack is a production LLM framework built around **explicit pipelines**. Every step in prompt assembly — retrieval, routing, building, generation — is a discrete, composable component connected in a directed graph.

Key concepts:

- **Component**: A unit of work with typed inputs and outputs (`@component` decorator)
- **Pipeline**: A DAG of components wired together
- **PromptBuilder**: Jinja2-based template that assembles the final prompt from dynamic inputs
- **Router**: Conditional branching based on metadata, embeddings, or LLM classification
- **DocumentStore / Retriever**: Fetch relevant documents (or fragments) via BM25, embedding, or hybrid search

```python
from haystack import Pipeline
from haystack.components.builders import PromptBuilder
from haystack.components.routers import MetadataRouter
from haystack.components.retrievers.in_memory import InMemoryEmbeddingRetriever

# Build a pipeline as a DAG
pipe = Pipeline()
pipe.add_component("retriever", InMemoryEmbeddingRetriever(document_store))
pipe.add_component("router", MetadataRouter(rules={...}))
pipe.add_component("prompt_builder", PromptBuilder(template="""
    {{system_prompt}}
    {% for fragment in fragments %}
    {{ fragment.content }}
    {% endfor %}
    User: {{query}}
"""))
pipe.add_component("llm", OpenAIChatGenerator())

# Wire them together
pipe.connect("retriever.documents", "router.documents")
pipe.connect("router.coding", "prompt_builder.fragments")
pipe.connect("prompt_builder", "llm")

# Run
result = pipe.run({"retriever": {"query": user_message}})
```

---

## 2. What Bond Can Learn: Pipeline as Architecture

Bond's current prompt assembly is a **procedural function** — `_select_relevant_fragments` in `context_pipeline.py` does everything in sequence inside a single async function. There's no separation between retrieval, scoring, routing, assembly, and budget enforcement. They're all interleaved in one 100-line function.

Haystack's pipeline pattern offers a structural improvement: **make each step a discrete, testable, replaceable component**.

### Bond Today (Procedural)

```python
async def _select_relevant_fragments(fragments, user_message, history, config, extra_kwargs):
    # Layer 1: Core filtering (inline)
    core = [f for f in enabled if f.get("tier") == "core"]
    
    # Layer 2: Keyword matching (inline)
    triggered = [f for f in rest if _matches_triggers(f, search_text)]
    
    # Layer 3: LLM selection (inline, with error handling mixed in)
    llm_picks = await _utility_model_select(...)
    
    # Layer 4: Budget enforcement (inline)
    while total_tokens > FRAGMENT_TOKEN_BUDGET and droppable:
        ...
    
    return selected
```

**Problems with this structure:**
- Can't test layers independently
- Can't swap one layer without touching others
- Can't run layers in parallel when they're independent
- Logging and audit are sprinkled throughout
- Adding a new selection strategy means modifying the function

### Proposed (Pipeline)

```python
# Each step is an independent, testable component
pipeline = FragmentPipeline([
    CoreFragmentFilter(),           # Always-on fragments
    SemanticMatcher(encoder),       # Embedding similarity (doc 022)
    WorkflowRouter(presets),        # Category-based bundles (doc 024)
    LLMSelector(utility_model),     # DSPy-optimized fallback (doc 023)
    TrainedClassifier(model_path),  # RouteLLM-style classifier (doc 025)
    BudgetEnforcer(max_tokens=2500),
    AuditLogger(db),
])

selected = await pipeline.run(user_message, available_fragments, history)
```

---

## 3. Implementing Haystack's Pattern in Bond (Without Haystack)

We don't need Haystack as a dependency. We need its **component protocol** — a simple interface that makes pipeline steps composable.

### 3.1 Component Protocol

```python
# backend/app/agent/pipeline/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

@dataclass
class FragmentContext:
    """Passed through the pipeline. Each component reads and writes to this."""
    user_message: str
    history: list[dict]
    config: dict
    available_fragments: list[dict]
    
    # Output — built up by components
    selected: list[dict] = field(default_factory=list)
    selected_ids: set = field(default_factory=set)
    remaining_budget: int = 2500
    metadata: dict = field(default_factory=dict)  # Preprocessing results
    audit_log: list[dict] = field(default_factory=list)


class PipelineComponent(ABC):
    """Base class for fragment selection pipeline components."""
    
    @abstractmethod
    async def run(self, ctx: FragmentContext) -> FragmentContext:
        """Process the context and return it (possibly modified)."""
        ...
    
    @property
    def name(self) -> str:
        return self.__class__.__name__


class FragmentPipeline:
    """Sequential pipeline of fragment selection components."""
    
    def __init__(self, components: list[PipelineComponent]):
        self.components = components
    
    async def run(
        self,
        user_message: str,
        fragments: list[dict],
        history: list[dict],
        config: dict,
    ) -> list[dict]:
        ctx = FragmentContext(
            user_message=user_message,
            history=history,
            config=config,
            available_fragments=fragments,
        )
        
        for component in self.components:
            try:
                ctx = await component.run(ctx)
            except Exception as e:
                ctx.audit_log.append({
                    "component": component.name,
                    "error": str(e),
                    "action": "skipped",
                })
                # Component failure is non-fatal — skip and continue
                continue
        
        return ctx.selected
```

### 3.2 Concrete Components

```python
# backend/app/agent/pipeline/components.py

class CoreFragmentFilter(PipelineComponent):
    """Layer 1: Always include core-tier fragments."""
    
    async def run(self, ctx: FragmentContext) -> FragmentContext:
        core = [
            f for f in ctx.available_fragments
            if f.get("tier") == "core" and f.get("enabled", True)
        ]
        for f in core:
            f["_selection_reason"] = "core_always"
        
        ctx.selected.extend(core)
        ctx.selected_ids.update(f.get("id") for f in core)
        ctx.remaining_budget -= sum(f.get("token_estimate", 0) for f in core)
        
        ctx.audit_log.append({
            "component": self.name,
            "action": "selected",
            "count": len(core),
            "fragments": [f.get("name") for f in core],
        })
        
        return ctx


class SemanticMatcher(PipelineComponent):
    """Layer 2: Embedding-based fragment matching (doc 022)."""
    
    def __init__(self, route_layer):
        self.route_layer = route_layer
    
    async def run(self, ctx: FragmentContext) -> FragmentContext:
        if self.route_layer is None:
            return ctx
        
        candidates = [
            f for f in ctx.available_fragments
            if f.get("id") not in ctx.selected_ids
        ]
        
        results = self.route_layer.retrieve_multiple_routes(ctx.user_message)
        
        for result in results:
            matching = [f for f in candidates if f.get("id") == result.name]
            if matching:
                frag = matching[0]
                frag["_selection_reason"] = "semantic_match"
                frag["_similarity_score"] = result.similarity_score
                ctx.selected.append(frag)
                ctx.selected_ids.add(frag.get("id"))
                ctx.remaining_budget -= frag.get("token_estimate", 0)
        
        ctx.audit_log.append({
            "component": self.name,
            "action": "matched",
            "count": len(results),
        })
        
        return ctx


class BudgetEnforcer(PipelineComponent):
    """Final layer: Drop lowest-priority fragments if over budget."""
    
    def __init__(self, max_tokens: int = 2500):
        self.max_tokens = max_tokens
    
    async def run(self, ctx: FragmentContext) -> FragmentContext:
        total = sum(f.get("token_estimate", 0) for f in ctx.selected)
        
        if total <= self.max_tokens:
            return ctx
        
        # Sort non-core by priority (lowest first = drop first)
        core_ids = {
            f.get("id") for f in ctx.selected
            if f.get("_selection_reason") == "core_always"
        }
        
        droppable = sorted(
            [f for f in ctx.selected if f.get("id") not in core_ids],
            key=lambda f: f.get("_similarity_score", f.get("_classifier_score", 0)),
        )
        
        dropped = []
        while total > self.max_tokens and droppable:
            drop = droppable.pop(0)
            total -= drop.get("token_estimate", 0)
            ctx.selected.remove(drop)
            ctx.selected_ids.discard(drop.get("id"))
            dropped.append(drop.get("name"))
        
        ctx.audit_log.append({
            "component": self.name,
            "action": "enforced",
            "dropped": dropped,
            "final_tokens": total,
        })
        
        return ctx
```

### 3.3 Pipeline Construction

```python
# backend/app/agent/pipeline/factory.py

def build_fragment_pipeline(config: dict) -> FragmentPipeline:
    """Build the fragment selection pipeline from config.
    
    Components are included based on what's available:
    - CoreFragmentFilter: always
    - SemanticMatcher: if route_layer is initialized
    - WorkflowRouter: if workflow presets are configured
    - LLMSelector: if utility model is configured
    - TrainedClassifier: if model file exists
    - BudgetEnforcer: always
    """
    components = [CoreFragmentFilter()]
    
    # Semantic router (doc 022) — fast, no API call
    route_layer = get_route_layer()
    if route_layer:
        components.append(SemanticMatcher(route_layer))
    
    # Workflow router (doc 024) — category-based bundles
    presets = config.get("workflow_presets")
    if presets:
        components.append(WorkflowRouter(presets))
    
    # Trained classifier (doc 025) — if model exists
    classifier_path = Path(__file__).parent / "fragment_classifier.pt"
    if classifier_path.exists():
        components.append(TrainedClassifier(classifier_path))
    
    # LLM selector (doc 023) — DSPy-optimized fallback
    if config.get("utility_model"):
        components.append(LLMSelector(config["utility_model"]))
    
    # Always enforce budget
    components.append(BudgetEnforcer(max_tokens=FRAGMENT_TOKEN_BUDGET))
    
    return FragmentPipeline(components)
```

---

## 4. What Haystack's PromptBuilder Teaches

Haystack uses Jinja2 templates for prompt assembly. Bond currently concatenates strings:

```python
# Bond today
prompt_parts = [system_prompt] + [f["content"] for f in selected_fragments]
full_system_prompt = "\n\n".join(prompt_parts)
```

A template-based approach is more maintainable:

```python
# Proposed: Jinja2 template
SYSTEM_PROMPT_TEMPLATE = """
{{ agent_system_prompt }}

{% for fragment in fragments %}
---
{{ fragment.content }}
{% endfor %}

{% if workspace_context %}
## Current Context
{{ workspace_context }}
{% endif %}

{% if category_manifest %}
## Available Context Categories
{{ category_manifest }}
{% endif %}
"""

from jinja2 import Template

def assemble_system_prompt(
    agent_system_prompt: str,
    fragments: list[dict],
    workspace_context: str = "",
    category_manifest: str = "",
) -> str:
    template = Template(SYSTEM_PROMPT_TEMPLATE)
    return template.render(
        agent_system_prompt=agent_system_prompt,
        fragments=fragments,
        workspace_context=workspace_context,
        category_manifest=category_manifest,
    )
```

Benefits:
- Template is readable and editable (could even be stored in SpacetimeDB)
- Clear separation between data and presentation
- Easy to add conditional sections
- Testable independently of selection logic

---

## 5. What Haystack's Routers Teach

Haystack has two router types relevant to Bond:

### MetadataRouter
Routes documents based on metadata fields. Bond equivalent: route fragments based on their `category`, `tier`, or `task_triggers` fields.

### ConditionalRouter  
Routes based on runtime conditions (Jinja2 expressions). Bond equivalent: skip the LLM selector if semantic confidence is high enough.

```python
class ConditionalGate(PipelineComponent):
    """Skip downstream LLM selection if upstream confidence is high."""
    
    def __init__(self, confidence_threshold: float = 0.7):
        self.threshold = confidence_threshold
    
    async def run(self, ctx: FragmentContext) -> FragmentContext:
        max_score = max(
            (f.get("_similarity_score", 0) for f in ctx.selected),
            default=0,
        )
        
        if max_score >= self.threshold:
            # High confidence — skip LLM selection
            ctx.metadata["skip_llm_selection"] = True
            ctx.audit_log.append({
                "component": self.name,
                "action": "gate_closed",
                "reason": f"max_score={max_score:.2f} >= {self.threshold}",
            })
        
        return ctx
```

---

## 6. Migration Path

| Step | Work | Risk |
|------|------|------|
| 1 | Define `PipelineComponent` protocol and `FragmentContext` dataclass | New code, no impact |
| 2 | Refactor `_select_relevant_fragments` into component classes | Refactor — behavior should be identical |
| 3 | Add `FragmentPipeline` orchestrator | New code |
| 4 | Replace direct `_select_relevant_fragments` call in `worker.py` with pipeline | Integration |
| 5 | Add pipeline configuration to agent settings | Config |
| 6 | Plug in semantic router, classifier, etc. as new components (docs 022-025) | Incremental |
| 7 | Add Jinja2 prompt template (optional, lower priority) | Enhancement |

### Step 2 Detail: Component Extraction

The current `_select_relevant_fragments` maps cleanly to components:

```
Lines 280-283 (core filtering)    → CoreFragmentFilter
Lines 286-288 (keyword triggers)  → KeywordTriggerMatcher (deprecated by SemanticMatcher)
Lines 291-304 (LLM selection)     → LLMSelector
Lines 307-320 (budget enforcement) → BudgetEnforcer
Lines 322-332 (audit logging)     → AuditLogger
```

---

## 7. What This Doesn't Solve

- **Selection quality** — The pipeline is a structural pattern. It doesn't make any individual component better. Quality improvements come from docs 022-025.
- **Pipeline overhead** — Adding abstraction has a small runtime cost. For Bond's use case (~4-6 components, called once per turn), this is negligible.
- **Complexity** — A pipeline is more code than a single function. The benefit is testability and composability, but it's more to understand.

---

## 8. Why Not Use Haystack Directly?

| Factor | Use Haystack | Adopt the Pattern |
|--------|---|---|
| Dependency size | ~50+ transitive deps, heavy | Zero — pure Python |
| Bond integration | Would need to wrap Bond's SpacetimeDB, LiteLLM, etc. | Uses Bond's existing infra directly |
| Learning curve | Full framework to learn | ~100 lines of base classes |
| Pipeline flexibility | Very flexible (DAG support) | Linear is sufficient for now |
| Production readiness | Battle-tested | Simple enough to be correct |

**Decision: Adopt the pattern.** Bond needs composable components, not a framework. The `PipelineComponent` protocol and `FragmentPipeline` class are ~80 lines of code that give Bond the structural benefits without the dependency.

---

## 9. Decisions

| Question | Decision |
|----------|----------|
| Use Haystack as dependency? | **No** — adopt the component/pipeline pattern only |
| Pipeline type? | **Linear** (sequential). DAG support only if needed later. |
| Jinja2 for prompt assembly? | **Yes**, low priority — after pipeline refactor |
| Component error handling? | **Non-fatal** — log and skip failed components |
| Pipeline config storage? | Agent settings in SpacetimeDB (which components are enabled) |
| Backward compatibility? | Step 2 must produce identical output to current function |
