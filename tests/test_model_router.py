"""Tests for model_router guardrails."""
import sys
from pathlib import Path

# Adjust path for gemini-key-pool package structure
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gemini_key_pool.model_router import select_model_for_task

class TestEmbeddingGuardrail:
    def test_production_quality_never_routes_to_embedding(self):
        result = select_model_for_task("Generate a React component", quality_level="production")
        assert result["model"] != "gemini-embedding-001"

    def test_standard_quality_never_routes_to_embedding(self):
        result = select_model_for_task("Analyse this cluster of results", quality_level="standard")
        assert result["model"] != "gemini-embedding-001"

    def test_explicit_embedding_task_still_routes_to_embedding(self):
        """True embedding tasks (no quality flag) should still route to embedding model."""
        result = select_model_for_task("Perform semantic search and clustering via embeddings")
        assert result["model"] == "gemini-embedding-001"
