# Enterprise AI Knowledge Assistant

A guided, Docker-based starter for a private company-document assistant. It connects a React interface to a FastAPI service, prepares PostgreSQL with pgvector and Ollama, and accepts PDF, DOCX, and CSV document uploads.

## Requirements

- Docker Desktop with Docker Compose

## Run locally

1. Copy `.env.example` to `.env`.
2. Start the services:

   ```powershell
   docker compose up --build
   ```

3. Open the web app at <http://localhost:5173>.
4. Check the API at <http://localhost:8000/api/health> or its interactive docs at <http://localhost:8000/docs>.

The first startup downloads container images and may take a few minutes. Ollama starts without a language model; download the configured model as described below before asking questions.

## Upload documents

Use the web app to upload one PDF, DOCX, or CSV file at a time (up to 10 MB). The API extracts text, splits it into overlapping chunks, computes local embeddings with the Hugging Face `BAAI/bge-small-en-v1.5` model, and stores the vectors in PostgreSQL/pgvector. The original file is stored in the persistent `documents_data` Docker volume. Scanned PDFs without an embedded text layer are not supported yet.

The local ONNX embedding runtime downloads the Hugging Face model on first use and caches it in the `hf_cache` Docker volume; company-document contents are embedded locally. For answer generation, download the configured Ollama model once:

```powershell
docker compose exec ollama ollama pull llama3.2:3b
```

Ask questions in the chat panel. The API retrieves relevant chunks, asks Ollama to answer only from those sources, and returns source references. If retrieval finds no sufficiently similar chunks, it returns a fixed “I don't know” response without calling the LLM. Answers with missing or invalid citation numbers are rejected. This citation check does not replace a full factuality evaluation.

This development version has no authentication. Services are bound to `localhost`; do not expose them publicly or upload sensitive company documents until authentication and access controls are implemented.

## Development milestones

1. Foundation: React, FastAPI, PostgreSQL/pgvector, and Ollama services.
2. Document upload and PDF, DOCX, and CSV parsing.
3. Embeddings, semantic retrieval, and answers with source citations.
4. Authentication, evaluation, and deployment hardening.

The credentials in `.env.example` are for local development only. Replace them and review network exposure before deploying anywhere shared or public.
