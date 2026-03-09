# Design Doc 025: RouteLLM Classifier-Based Routing

**Status:** Draft  
**Date:** 2026-03-09  
**Depends on:** 010 (Prompt Management), 021 (Prompt Hierarchy)  
**Reference:** [lm-sys/RouteLLM](https://github.com/lm-sys/RouteLLM) (⭐ 4,666)

---

## 1. What RouteLLM Does

RouteLLM is a framework for training and serving **lightweight classifiers** that route queries between a strong model (expensive) and a weak model (cheap). Its key contribution isn't model routing per se — it's the **methodology for training small, fast classifiers on preference data**.

The framework provides four router architectures:

| Router | How It Works | Speed | Accuracy |
|--------|-------------|-------|----------|
| **Matrix Factorization (MF)** | Learns embeddings for queries and models, predicts quality via dot product | ~1ms | Good |
| **BERT Classifier** | Fine-tuned BERT on preference pairs | ~5ms | Better |
| **SW Ranking** | Similarity-weighted ranking against reference examples | ~10ms | Best |
| **Causal LLM** | Small LLM trained to predict which model wins | ~50ms | Best |

The training pipeline:

```
1. Collect preference data: (query, strong_model_wins: bool)
   Source: Chatbot Arena battles, or your own A/B tests

2. Train a lightweight classifier:
   Input: query text (embedded)
   Output: probability that the strong model is needed

3. At runtime:
   If P(strong_needed) > threshold → route to strong model
   Else → route to weak model
```

**The insight for Bond:** The same classifier architecture can route queries to prompt configurations instead of models. Replace "strong model vs. weak model" with "fragment set A vs. fragment set B vs. fragment set C."

---

## 2. What RouteLLM's Classifiers Actually Look Like

### Matrix Factorization Router (most relevant to Bond)

```python
# Simplified from routellm/routers/matrix_factorization/
class MFRouter:
    def __init__(self, embedding_dim=128):
        # Query encoder: maps text → embedding
        self.query_encoder = SentenceTransformer("all-MiniLM-L6-v2")
        
        # Learned projection: maps embedding → category scores
        self.projection = nn.Linear(384, num_categories)  # 384 = MiniLM dim
    
    def route(self, query: str) -> str:
        embedding = self.query_encoder.encode(query)
        scores = self.projection(embedding)
        return categories[scores.argmax()]
```

This is a **trained classifier**, not a similarity search. The difference matters:

| Approach | Learns From | Good At |
|----------|-------------|---------|
| Similarity search (semantic-router) | Example utterances per route | Clear-cut categories with distinct vocabulary |
| Trained classifier (RouteLLM) | Labeled outcomes (which route was correct) | Ambiguous cases where vocabulary overlaps |

---

## 3. Applying RouteLLM to Bond's Fragment Selection

### 3.1 The Reframing

Bond's fragment selection is a multi-label classification problem:
- **Input:** User message (text)
- **Output:** Set of relevant fragment IDs
- **Training data:** Historical selections with outcome labels

RouteLLM's matrix factorization router can be adapted to predict **fragment relevance scores** rather than binary strong/weak routing.

### 3.2 Architecture

```python
# backend/app/agent/fragment_classifier.py

import torch
import torch.nn as nn
from sentence_transformers import SentenceTransformer

class FragmentClassifier(nn.Module):
    """Lightweight trained classifier for fragment selection.
    
    Replaces the LLM utility model call with a ~1ms local inference.
    Trained on historical selection data with outcome labels.
    """
    
    def __init__(self, num_fragments: int, embedding_dim: int = 384):
        super().__init__()
        self.encoder = SentenceTransformer("all-MiniLM-L6-v2")
        self.classifier = nn.Sequential(
            nn.Linear(embedding_dim, 256),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(256, num_fragments),
            nn.Sigmoid(),  # Multi-label: each fragment gets independent probability
        )
    
    def forward(self, query_embedding: torch.Tensor) -> torch.Tensor:
        return self.classifier(query_embedding)
    
    def predict(self, query: str, threshold: float = 0.5) -> list[int]:
        """Return indices of fragments with P(relevant) > threshold."""
        with torch.no_grad():
            embedding = self.encoder.encode(query, convert_to_tensor=True)
            scores = self.forward(embedding)
            return (scores > threshold).nonzero(as_tuple=True)[0].tolist()
    
    def predict_with_scores(self, query: str) -> list[tuple[int, float]]:
        """Return all fragments with their relevance scores."""
        with torch.no_grad():
            embedding = self.encoder.encode(query, convert_to_tensor=True)
            scores = self.forward(embedding)
            return [(i, s.item()) for i, s in enumerate(scores)]
```

### 3.3 Training Pipeline

```python
# scripts/train_fragment_classifier.py

import torch
from torch.utils.data import DataLoader, Dataset
from .fragment_classifier import FragmentClassifier

class FragmentSelectionDataset(Dataset):
    """Training data from Bond's fragment audit log.
    
    Each example: (user_message, selected_fragment_indices, task_success)
    """
    def __init__(self, audit_records, encoder, num_fragments):
        self.encoder = encoder
        self.num_fragments = num_fragments
        self.records = audit_records
    
    def __getitem__(self, idx):
        record = self.records[idx]
        embedding = self.encoder.encode(record["user_message"], convert_to_tensor=True)
        
        # Multi-hot label: 1 for selected fragments, 0 for others
        # Weight by outcome: successful selections get full weight
        label = torch.zeros(self.num_fragments)
        for frag_idx in record["selected_fragment_indices"]:
            label[frag_idx] = 1.0 if record["task_success"] else 0.5
        
        return embedding, label

def train(audit_records, fragment_catalog, epochs=50, lr=1e-3):
    num_fragments = len(fragment_catalog)
    model = FragmentClassifier(num_fragments)
    
    dataset = FragmentSelectionDataset(
        audit_records, model.encoder, num_fragments
    )
    loader = DataLoader(dataset, batch_size=32, shuffle=True)
    
    optimizer = torch.optim.Adam(model.classifier.parameters(), lr=lr)
    criterion = nn.BCELoss()
    
    for epoch in range(epochs):
        for embeddings, labels in loader:
            scores = model.classifier(embeddings)
            loss = criterion(scores, labels)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
    
    torch.save(model.state_dict(), "fragment_classifier.pt")
    return model
```

### 3.4 Integration with Existing Pipeline

```python
# In context_pipeline.py

_classifier: FragmentClassifier | None = None

def _load_classifier(num_fragments: int) -> FragmentClassifier | None:
    global _classifier
    model_path = Path(__file__).parent / "fragment_classifier.pt"
    if not model_path.exists():
        return None
    if _classifier is None:
        _classifier = FragmentClassifier(num_fragments)
        _classifier.load_state_dict(torch.load(model_path))
        _classifier.eval()
    return _classifier

async def _select_relevant_fragments(fragments, user_message, history, config, extra_kwargs):
    enabled = [f for f in fragments if f.get("enabled", True)]
    
    # Layer 1: Core (always)
    core = [f for f in enabled if f.get("tier") == "core"]
    rest = [f for f in enabled if f.get("tier") != "core"]
    
    # Layer 2: Trained classifier (if available)
    classifier = _load_classifier(len(rest))
    if classifier:
        scored = classifier.predict_with_scores(user_message)
        classifier_picks = [
            rest[idx] for idx, score in scored
            if idx < len(rest) and score > 0.5
        ]
        for f in classifier_picks:
            f["_selection_reason"] = "trained_classifier"
            f["_classifier_score"] = scored[rest.index(f)][1]
        
        selected = core + classifier_picks
        # Budget enforcement...
        return selected
    
    # Fallback: existing LLM selection
    # ...
```

---

## 4. Key Difference from Semantic Router (Doc 022)

| | Semantic Router | RouteLLM Classifier |
|--|---|---|
| **Learns from** | Example utterances (manually written) | Outcome data (automatically collected) |
| **Updates** | Add more utterances manually | Retrain on new audit data |
| **Handles ambiguity** | Cosine similarity — can't learn subtle patterns | Trained weights — learns from mistakes |
| **Cold start** | Works immediately with 5-10 utterances per route | Needs 200+ labeled examples to be useful |
| **Maintenance** | Manual utterance curation | Automated retraining pipeline |

**Semantic router is better for day 1. Trained classifier is better for month 3+** once you have enough selection data.

---

## 5. Migration Path

| Step | Work | Risk |
|------|------|------|
| 1 | `uv add sentence-transformers torch` | Large dependencies (~2GB) |
| 2 | Add fragment selection audit logging (message, fragments, outcome) | Schema addition |
| 3 | Collect 4-8 weeks of selection data | No code change |
| 4 | Build training script (`scripts/train_fragment_classifier.py`) | New code |
| 5 | Train initial classifier on collected data | Offline |
| 6 | A/B test classifier vs. current LLM selection | Feature flag |
| 7 | Set up monthly retraining cron job | Ops |

---

## 6. Practical Concerns

### Dependency Weight
Adding PyTorch + sentence-transformers is ~2GB of dependencies. This is significant for Bond's worker containers. Mitigation:
- Use `torch-cpu` only (no CUDA, saves ~1.5GB)
- Or use ONNX Runtime instead of PyTorch for inference only (~200MB)

### Fragment Catalog Changes
When fragments are added or removed, the classifier's output dimension changes → requires retraining. Mitigation:
- Train with a fixed max_fragments dimension (e.g., 100)
- Map fragment IDs to stable indices
- Retrain when catalog changes significantly

### Model Staleness
The classifier learns from historical data. If user behavior shifts, the classifier lags. Mitigation:
- Exponential decay weighting: recent selections count more
- Monthly retraining baseline
- Fall back to LLM selection when classifier confidence is low

---

## 7. What This Doesn't Solve

- **Cold start** — Needs 200+ labeled examples. Semantic router (doc 022) or LLM selection (doc 023) is required until data accumulates.
- **Explainability** — A trained classifier is a black box. You know *what* it selected, not *why*. The LLM selector at least provides reasoning.
- **New fragments** — A fragment added today has zero training data, so the classifier can't select it. New fragments need a bypass path (semantic router or manual inclusion).

---

## 8. Complementary Use in the Full Pipeline

```
Month 1-2: Semantic Router (doc 022) + LLM fallback (doc 023)
  → Collect selection audit data
  → Label outcomes automatically (task success/failure)

Month 3+: Trained Classifier (this doc) + Semantic Router + LLM fallback
  → Classifier handles common patterns (~1ms, 70% of requests)
  → Semantic router catches new fragments classifier hasn't seen
  → LLM fallback for genuinely novel situations

Month 6+: Retrained classifier dominates
  → Handles 85-90% of requests
  → Semantic router for new fragments only
  → LLM fallback nearly never fires
```

---

## 9. Decisions

| Question | Decision |
|----------|----------|
| Use RouteLLM directly? | **No** — adopt the classifier training pattern, not the model-routing framework |
| When to start? | **After 4-8 weeks of audit data** from semantic router deployment |
| PyTorch or ONNX? | **ONNX Runtime** for inference in production (smaller footprint) |
| Retraining frequency? | **Monthly**, automated via script |
| Confidence threshold? | **0.5** initially, tune based on precision/recall on test set |
| Fallback? | Semantic router → LLM selection (graceful degradation) |
