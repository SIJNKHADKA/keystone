import logging
from functools import lru_cache
from pathlib import Path

from sqlalchemy import func, select

from .database import SessionLocal
from .models import Document, DocumentChunk

logger = logging.getLogger(__name__)
EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"
MAX_CHUNK_CHARS = 1000
CHUNK_OVERLAP_CHARS = 150
MIN_SIMILARITY = 0.30


class EmbeddingUnavailableError(RuntimeError):
    pass


def split_text(text: str) -> list[str]:
    normalized = text.strip()
    chunks = []
    start = 0

    while start < len(normalized):
        end = min(start + MAX_CHUNK_CHARS, len(normalized))
        if end < len(normalized):
            boundary_start = start + MAX_CHUNK_CHARS // 2
            boundary = max(
                normalized.rfind("\n", boundary_start, end),
                normalized.rfind(" ", boundary_start, end),
            )
            if boundary > start:
                end = boundary

        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(end - CHUNK_OVERLAP_CHARS, start + 1)

    return chunks


@lru_cache(maxsize=1)
def _get_embedding_model():
    try:
        from fastembed import TextEmbedding
    except ImportError as exc:
        raise EmbeddingUnavailableError(
            "The local embedding runtime is not installed."
        ) from exc

    try:
        return TextEmbedding(
            model_name=EMBEDDING_MODEL_NAME,
            cache_dir=str(Path.home() / ".cache" / "fastembed"),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        logger.exception("Could not load Hugging Face embedding model")
        raise EmbeddingUnavailableError(
            "The Hugging Face embedding model could not be loaded."
        ) from exc


def embed_texts(texts: list[str], is_query: bool = False) -> list[list[float]]:
    if not texts:
        return []

    model_inputs = texts
    if is_query:
        model_inputs = [
            f"Represent this sentence for searching relevant passages: {text}"
            for text in texts
        ]

    try:
        embeddings = [vector.tolist() for vector in _get_embedding_model().embed(model_inputs)]
    except (OSError, RuntimeError, ValueError) as exc:
        logger.exception("Could not encode text with the Hugging Face embedding model")
        raise EmbeddingUnavailableError(
            "Text could not be encoded by the local embedding model."
        ) from exc

    if len(embeddings) != len(texts):
        raise RuntimeError("The embedding model returned an unexpected number of vectors.")
    if any(len(vector) != 384 for vector in embeddings):
        raise RuntimeError("The embedding model returned vectors with an unexpected size.")
    return embeddings


def index_document(
    document_id: str,
    filename: str,
    file_type: str,
    text: str,
) -> int:
    chunks = split_text(text)
    if not chunks:
        raise ValueError("No searchable text was found in the document.")

    embeddings = embed_texts(chunks)
    with SessionLocal.begin() as session:
        document = Document(
            id=document_id,
            filename=filename,
            file_type=file_type,
        )
        session.add(document)
        session.add_all(
            DocumentChunk(
                document_id=document_id,
                chunk_index=index,
                content=chunk,
                embedding=embedding,
            )
            for index, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True))
        )
    return len(chunks)


def search_chunks(question: str, top_k: int = 4) -> list[dict[str, str | int | float]]:
    query_embedding = embed_texts([question], is_query=True)[0]
    distance = DocumentChunk.embedding.cosine_distance(query_embedding)
    statement = (
        select(DocumentChunk, Document, distance.label("distance"))
        .join(Document, Document.id == DocumentChunk.document_id)
        .order_by(distance)
        .limit(top_k)
    )

    matches = []
    with SessionLocal() as session:
        for chunk, document, distance_value in session.execute(statement):
            similarity = 1 - float(distance_value)
            if similarity < MIN_SIMILARITY:
                continue
            matches.append(
                {
                    "document_id": document.id,
                    "filename": document.filename,
                    "chunk_index": chunk.chunk_index,
                    "content": chunk.content,
                    "excerpt": chunk.content[:500],
                    "similarity": round(similarity, 4),
                }
            )
    return matches


def list_documents() -> list[dict[str, str | int]]:
    statement = (
        select(Document, func.count(DocumentChunk.id))
        .outerjoin(DocumentChunk, DocumentChunk.document_id == Document.id)
        .group_by(Document.id)
        .order_by(Document.created_at.desc())
    )
    with SessionLocal() as session:
        return [
            {
                "document_id": document.id,
                "filename": document.filename,
                "file_type": document.file_type,
                "chunk_count": chunk_count,
            }
            for document, chunk_count in session.execute(statement)
        ]
