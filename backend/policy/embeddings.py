"""Embedding providers. EMBEDDINGS=hash (dev) | openai | sentence-transformers."""
import os
from typing import List
from .store import hash_embedder


def get_embedder():
    kind = os.environ.get("EMBEDDINGS", "hash").lower()
    if kind == "openai":
        import requests
        key, model = os.environ["OPENAI_API_KEY"], os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")

        def emb(texts: List[str]) -> List[List[float]]:
            r = requests.post("https://api.openai.com/v1/embeddings", headers={"Authorization": f"Bearer {key}"},
                              json={"model": model, "input": texts}, timeout=60)
            r.raise_for_status()
            return [d["embedding"] for d in r.json()["data"]]
        return emb
    if kind == "sentence-transformers":
        from sentence_transformers import SentenceTransformer
        m = SentenceTransformer(os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"))
        return lambda texts: m.encode(texts, normalize_embeddings=True).tolist()
    return hash_embedder
