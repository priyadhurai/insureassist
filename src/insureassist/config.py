"""All settings in one place, read from environment variables or the .env file.

Example: setting MAX_UPLOAD_MB=10 in .env changes `settings.max_upload_mb` to 10.
"""

from pathlib import Path
from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # PDF limits
    max_upload_mb: int = 20
    max_pdf_pages: int = 500
    # A page with fewer characters than this AND a big image is probably a scan.
    scanned_page_min_chars: int = 25

    # Chunking (see docs/ingestion-design.md section 5)
    chunk_size_chars: int = 800
    chunk_overlap_chars: int = 120
    chunk_min_chars: int = 50

    # Embeddings (see docs/retrieval-design.md section 2)
    embedding_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_device: str = "cpu"
    embedding_batch_size: int = 32

    # Which vector database: "faiss" (files in index_dir, no server) or "qdrant" (server).
    vector_store: Literal["faiss", "qdrant"] = "faiss"
    # FAISS: where the index + chunk details are saved. Git-ignored.
    index_dir: Path = Path("data/index")
    # Qdrant: start with `docker compose up -d qdrant`.
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "insureassist_chunks"

    # Retrieval: how many chunks to give the LLM, and the minimum similarity score.
    # Below the minimum we answer "not enough information" WITHOUT calling the LLM.
    retrieval_top_k: int = 5
    retrieval_min_semantic_score: float = 0.25
    # "semantic", "keyword" or "hybrid" (both, merged with Reciprocal Rank Fusion).
    retrieval_mode: Literal["semantic", "keyword", "hybrid"] = "semantic"
    retrieval_candidate_k: int = 50  # how many results each method contributes to the merge
    retrieval_rrf_k: int = 60

    # Which LLM answers questions: "gemini" (API) or "huggingface" (local open model).
    llm_provider: Literal["gemini", "huggingface"] = "gemini"
    hf_llm_model: str = "Qwen/Qwen2.5-1.5B-Instruct"
    hf_dtype: Literal["bfloat16", "float32"] = "bfloat16"
    hf_max_new_tokens: int = 384

    # LLM (Gemini). SecretStr prints as "**********", so the key never leaks into logs.
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.8-flash"
    llm_temperature: float = 0.1  # low = more factual, less creative
    llm_max_output_tokens: int = 2048
    llm_timeout_seconds: int = 30
    llm_max_retries: int = 2

    @model_validator(mode="after")
    def check_chunk_settings(self) -> Self:
        # Overlap must be smaller than the chunk, otherwise chunking could never move forward.
        if not 0 <= self.chunk_overlap_chars < self.chunk_size_chars:
            raise ValueError("CHUNK_OVERLAP_CHARS must be >= 0 and < CHUNK_SIZE_CHARS")
        return self


settings = Settings()
