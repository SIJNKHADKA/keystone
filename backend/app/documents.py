import csv
import io
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from pypdf import PdfReader
from pypdf.errors import PdfReadError


class DocumentParseError(ValueError):
    pass


MAX_DOCX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024


def extract_text(filename: str, content: bytes) -> str:
    extension = Path(filename.replace("\\", "/")).suffix.lower()

    if extension == ".pdf":
        try:
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted:
                raise DocumentParseError("Password-protected PDFs are not supported.")
            return "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except PdfReadError as exc:
            raise DocumentParseError("This PDF could not be read.") from exc

    if extension == ".docx":
        try:
            with ZipFile(io.BytesIO(content)) as archive:
                expanded_size = sum(entry.file_size for entry in archive.infolist())
        except BadZipFile as exc:
            raise DocumentParseError("This DOCX file could not be read.") from exc

        if expanded_size > MAX_DOCX_UNCOMPRESSED_BYTES:
            raise DocumentParseError("This DOCX file expands beyond the supported size.")

        try:
            document = Document(io.BytesIO(content))
        except (BadZipFile, PackageNotFoundError, ValueError) as exc:
            raise DocumentParseError("This DOCX file could not be read.") from exc

        paragraphs = [paragraph.text for paragraph in document.paragraphs]
        table_rows = [
            "\t".join(cell.text for cell in row.cells)
            for table in document.tables
            for row in table.rows
        ]
        return "\n".join(part for part in [*paragraphs, *table_rows] if part.strip())

    if extension == ".csv":
        try:
            decoded = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise DocumentParseError("CSV files must use UTF-8 encoding.") from exc

        try:
            rows = csv.reader(io.StringIO(decoded), strict=True)
            return "\n".join("\t".join(cell.strip() for cell in row) for row in rows)
        except csv.Error as exc:
            raise DocumentParseError("This CSV file could not be read.") from exc

    raise DocumentParseError("Only PDF, DOCX, and CSV files are supported.")
