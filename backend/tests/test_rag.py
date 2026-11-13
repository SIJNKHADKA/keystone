import unittest
from unittest.mock import patch

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex
from sqlalchemy import select

from backend.app.models import DocumentChunk
from backend.app.rag import MAX_CHUNK_CHARS, embed_texts, split_text


class TextChunkTests(unittest.TestCase):
    def test_chunks_respect_size_and_preserve_overlap(self) -> None:
        text = "".join(str(index % 10) for index in range(MAX_CHUNK_CHARS * 2 + 300))

        chunks = split_text(text)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= MAX_CHUNK_CHARS for chunk in chunks))
        self.assertEqual(chunks[0][-150:], chunks[1][:150])

    def test_empty_text_returns_no_chunks(self) -> None:
        self.assertEqual(split_text(" \n "), [])

    def test_query_embedding_uses_bge_search_instruction(self) -> None:
        class FakeVector(list):
            def tolist(self):
                return list(self)

        class FakeModel:
            inputs = None

            def embed(self, texts):
                self.inputs = texts
                return [FakeVector([0.0] * 384) for _ in texts]

        model = FakeModel()
        with patch("backend.app.rag._get_embedding_model", return_value=model):
            vectors = embed_texts(["Annual leave policy"], is_query=True)

        self.assertEqual(
            model.inputs,
            ["Represent this sentence for searching relevant passages: Annual leave policy"],
        )
        self.assertEqual(len(vectors[0]), 384)

    def test_pgvector_search_uses_cosine_distance_and_hnsw_index(self) -> None:
        distance = DocumentChunk.embedding.cosine_distance([0.0] * 384)
        query = select(DocumentChunk).order_by(distance)
        query_sql = str(query.compile(dialect=postgresql.dialect()))
        embedding_index = next(
            index
            for index in DocumentChunk.__table__.indexes
            if index.name == "ix_document_chunks_embedding"
        )
        index_sql = str(CreateIndex(embedding_index).compile(dialect=postgresql.dialect()))

        self.assertIn("<=>", query_sql)
        self.assertIn("USING hnsw", index_sql)
        self.assertIn("vector_cosine_ops", index_sql)


if __name__ == "__main__":
    unittest.main()
