import logging
import os
import re
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import SQLAlchemyError

from .documents import DocumentParseError, extract_text
from .database import initialize_database
from .generation import AnswerGenerationError, generate_answer
from .rag import EmbeddingUnavailableError, index_document, list_documents, search_chunks

logger = logging.getLogger(__name__)
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "uploads"))
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".csv"}
NO_ANSWER = "I don't know based on the documents I can access."
UNVERIFIED_ANSWER = (
    "I couldn't verify a cited answer from the retrieved sources. "
    "Please try rephrasing your question."
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_database()
    yield

app = FastAPI(
    title="Enterprise AI Knowledge Assistant",
    description="Ask questions about your company's trusted documents.",
    version="0.1.0",
    lifespan=lifespan,
)

origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}


class DocumentUploadResponse(BaseModel):
    document_id: str
    filename: str
    file_type: str
    chunk_count: int
    character_count: int
    preview: str


class DocumentListItem(BaseModel):
    document_id: str
    filename: str
    file_type: str
    chunk_count: int


class ChatRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    top_k: int = Field(default=4, ge=1, le=8)

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, question: str) -> str:
        question = question.strip()
        if not question:
            raise ValueError("Question must not be blank.")
        return question


class ChatSource(BaseModel):
    document_id: str
    filename: str
    chunk_index: int
    excerpt: str
    similarity: float


class ChatResponse(BaseModel):
    answer: str
    sources: list[ChatSource]


def _remove_saved_upload(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not remove unindexed upload %s", path, exc_info=True)


@app.get("/api/documents", response_model=list[DocumentListItem], tags=["documents"])
def get_documents() -> list[DocumentListItem]:
    try:
        documents = list_documents()
    except SQLAlchemyError as exc:
        logger.exception("Could not list documents")
        raise HTTPException(
            status_code=503,
            detail="The document index is unavailable. Try again shortly.",
        ) from exc
    return [DocumentListItem(**document) for document in documents]


@app.post(
    "/api/documents",
    response_model=DocumentUploadResponse,
    status_code=201,
    tags=["documents"],
)
def upload_document(file: UploadFile = File(...)) -> DocumentUploadResponse:
    try:
        filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
        extension = Path(filename).suffix.lower()
        if not filename or extension not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=415,
                detail="Upload a PDF, DOCX, or CSV file.",
            )

        content = bytearray()
        while True:
            chunk = file.file.read(64 * 1024)
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="Files must be 10 MB or smaller.",
                )
    finally:
        file.file.close()

    if len(filename) > 255:
        raise HTTPException(status_code=400, detail="The filename is too long.")

    try:
        text = extract_text(filename, bytes(content))
    except DocumentParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not text.strip():
        raise HTTPException(
            status_code=422,
            detail="No text could be extracted from this document.",
        )

    document_id = uuid4().hex
    destination = UPLOAD_DIR / f"{document_id}{extension}"
    temporary_path: Path | None = None
    try:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=UPLOAD_DIR,
            prefix=f".{document_id}-",
            suffix=".tmp",
            delete=False,
        ) as stored_file:
            temporary_path = Path(stored_file.name)
            stored_file.write(content)
        temporary_path.replace(destination)
    except OSError as exc:
        logger.exception("Could not persist uploaded document")
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove temporary upload %s", temporary_path)
        raise HTTPException(
            status_code=500,
            detail="The document could not be saved.",
        ) from exc

    try:
        chunk_count = index_document(document_id, filename, extension.removeprefix("."), text)
    except EmbeddingUnavailableError as exc:
        _remove_saved_upload(destination)
        logger.exception("Could not index uploaded document")
        raise HTTPException(
            status_code=503,
            detail="The embedding model is unavailable. Try again after it has loaded.",
        ) from exc
    except SQLAlchemyError as exc:
        _remove_saved_upload(destination)
        logger.exception("Could not save document index")
        raise HTTPException(
            status_code=503,
            detail="The document index is unavailable. Try again shortly.",
        ) from exc

    return DocumentUploadResponse(
        document_id=document_id,
        filename=filename,
        file_type=extension.removeprefix("."),
        chunk_count=chunk_count,
        character_count=len(text),
        preview=text.strip()[:500],
    )


@app.post("/api/chat", response_model=ChatResponse, tags=["chat"])
def chat(request: ChatRequest) -> ChatResponse:
    try:
        matches = search_chunks(request.question, top_k=request.top_k)
    except EmbeddingUnavailableError as exc:
        logger.exception("Could not embed chat question")
        raise HTTPException(
            status_code=503,
            detail="The embedding model is unavailable. Try again after it has loaded.",
        ) from exc
    except SQLAlchemyError as exc:
        logger.exception("Could not search document index")
        raise HTTPException(
            status_code=503,
            detail="The document index is unavailable. Try again shortly.",
        ) from exc

    if not matches:
        return ChatResponse(answer=NO_ANSWER, sources=[])

    try:
        answer = generate_answer(request.question, matches)
    except AnswerGenerationError as exc:
        logger.exception("Could not generate an answer")
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    citations = {int(reference) for reference in re.findall(r"\[(\d+)\]", answer)}
    valid_citations = set(range(1, len(matches) + 1))
    is_refusal = answer.casefold() == NO_ANSWER.casefold()
    if not is_refusal and (not citations or not citations.issubset(valid_citations)):
        logger.warning("Rejected answer with missing or invalid source citations")
        answer = UNVERIFIED_ANSWER

    return ChatResponse(
        answer=answer,
        sources=[
            ChatSource(
                document_id=source["document_id"],
                filename=source["filename"],
                chunk_index=source["chunk_index"],
                excerpt=source["excerpt"],
                similarity=source["similarity"],
            )
            for source in matches
        ],
    )
