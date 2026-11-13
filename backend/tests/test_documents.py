import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document
from fastapi.testclient import TestClient

from backend.app import main


def make_text_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(stream)).encode("ascii")
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    ]

    parts = [b"%PDF-1.4\n"]
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(sum(map(len, parts)))
        parts.append(f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")

    xref_offset = sum(map(len, parts))
    parts.append(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    parts.append(b"0000000000 65535 f \n")
    parts.extend(f"{offset:010d} 00000 n \n".encode("ascii") for offset in offsets[1:])
    parts.append(
        (
            f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return b"".join(parts)


class DocumentUploadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.upload_dir = Path(self.temp_directory.name)
        self.client = TestClient(main.app)
        self.upload_dir_patch = patch.object(main, "UPLOAD_DIR", self.upload_dir)
        self.upload_dir_patch.start()
        self.addCleanup(self.upload_dir_patch.stop)
        self.index_patch = patch.object(main, "index_document", return_value=1)
        self.index_patch.start()
        self.addCleanup(self.index_patch.stop)
        self.addCleanup(self.temp_directory.cleanup)

    def test_upload_csv_extracts_text_and_saves_original(self) -> None:
        response = self.client.post(
            "/api/documents",
            files={"file": ("team.csv", b"name,team\nAva,Finance", "text/csv")},
        )

        self.assertEqual(response.status_code, 201)
        document = response.json()
        self.assertEqual(document["filename"], "team.csv")
        self.assertEqual(document["file_type"], "csv")
        self.assertEqual(document["chunk_count"], 1)
        self.assertIn("Ava\tFinance", document["preview"])
        self.assertTrue((self.upload_dir / f'{document["document_id"]}.csv').exists())

    def test_upload_docx_extracts_paragraphs(self) -> None:
        source = Document()
        source.add_paragraph("Internal policy: keep customer data private.")
        file_content = io.BytesIO()
        source.save(file_content)

        response = self.client.post(
            "/api/documents",
            files={
                "file": (
                    "policy.docx",
                    file_content.getvalue(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertIn("keep customer data private", response.json()["preview"])

    def test_upload_rejects_docx_over_expansion_limit(self) -> None:
        source = Document()
        source.add_paragraph("A document that exceeds the test limit.")
        file_content = io.BytesIO()
        source.save(file_content)

        with patch("backend.app.documents.MAX_DOCX_UNCOMPRESSED_BYTES", 1):
            response = self.client.post(
                "/api/documents",
                files={
                    "file": (
                        "large.docx",
                        file_content.getvalue(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
            )

        self.assertEqual(response.status_code, 422)
        self.assertIn("expands beyond", response.json()["detail"])

    def test_upload_pdf_extracts_text(self) -> None:
        response = self.client.post(
            "/api/documents",
            files={"file": ("guide.pdf", make_text_pdf("Knowledge source"), "application/pdf")},
        )

        self.assertEqual(response.status_code, 201)
        self.assertIn("Knowledge source", response.json()["preview"])

    def test_upload_rejects_unsupported_file_types(self) -> None:
        response = self.client.post(
            "/api/documents",
            files={"file": ("notes.txt", b"not supported", "text/plain")},
        )

        self.assertEqual(response.status_code, 415)

    def test_upload_rejects_files_over_size_limit(self) -> None:
        with patch.object(main, "MAX_UPLOAD_BYTES", 4):
            response = self.client.post(
                "/api/documents",
                files={"file": ("notes.csv", b"12345", "text/csv")},
            )

        self.assertEqual(response.status_code, 413)

    def test_chat_returns_answer_with_source_reference(self) -> None:
        matches = [
            {
                "document_id": "doc-1",
                "filename": "policy.pdf",
                "chunk_index": 0,
                "content": "Employees receive 20 days of annual leave.",
                "excerpt": "Employees receive 20 days of annual leave.",
                "similarity": 0.82,
            }
        ]
        with (
            patch.object(main, "search_chunks", return_value=matches),
            patch.object(main, "generate_answer", return_value="Employees receive 20 days [1]."),
        ):
            response = self.client.post(
                "/api/chat",
                json={"question": "How much annual leave do employees receive?"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("[1]", response.json()["answer"])
        self.assertEqual(response.json()["sources"][0]["filename"], "policy.pdf")

    def test_chat_refuses_when_no_relevant_sources_are_found(self) -> None:
        with (
            patch.object(main, "search_chunks", return_value=[]),
            patch.object(main, "generate_answer") as generate,
        ):
            response = self.client.post(
                "/api/chat",
                json={"question": "What is the annual leave policy?"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], main.NO_ANSWER)
        self.assertEqual(response.json()["sources"], [])
        generate.assert_not_called()

    def test_chat_rejects_unverified_citations(self) -> None:
        matches = [
            {
                "document_id": "doc-1",
                "filename": "policy.pdf",
                "chunk_index": 0,
                "content": "Employees receive 20 days of annual leave.",
                "excerpt": "Employees receive 20 days of annual leave.",
                "similarity": 0.82,
            }
        ]
        with (
            patch.object(main, "search_chunks", return_value=matches),
            patch.object(main, "generate_answer", return_value="Employees receive 50 days [2]."),
        ):
            response = self.client.post(
                "/api/chat",
                json={"question": "How much annual leave do employees receive?"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], main.UNVERIFIED_ANSWER)


if __name__ == "__main__":
    unittest.main()
