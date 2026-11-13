import unittest
from unittest.mock import patch

import httpx

from backend.app.generation import AnswerGenerationError, generate_answer


class AnswerGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = {
            "filename": "leave-policy.pdf",
            "chunk_index": 0,
            "content": "Employees receive 20 days of annual leave.",
            "excerpt": "Employees receive 20 days of annual leave.",
            "similarity": 0.82,
        }

    def test_sends_retrieved_context_to_ollama(self) -> None:
        response = httpx.Response(
            200,
            json={"message": {"content": "Employees receive 20 days [1]."}},
            request=httpx.Request("POST", "http://ollama/api/chat"),
        )
        with patch("backend.app.generation.httpx.post", return_value=response) as post:
            answer = generate_answer("How much leave?", [self.source])

        self.assertEqual(answer, "Employees receive 20 days [1].")
        request = post.call_args.kwargs["json"]
        self.assertEqual(request["stream"], False)
        self.assertIn(self.source["content"], request["messages"][1]["content"])

    def test_reports_missing_ollama_model(self) -> None:
        response = httpx.Response(
            404,
            request=httpx.Request("POST", "http://ollama/api/chat"),
        )
        with (
            patch("backend.app.generation.httpx.post", return_value=response),
            self.assertRaisesRegex(AnswerGenerationError, "not available"),
        ):
            generate_answer("How much leave?", [self.source])

    def test_reports_unavailable_ollama_service(self) -> None:
        with (
            patch(
                "backend.app.generation.httpx.post",
                side_effect=httpx.ConnectError("offline"),
            ),
            self.assertRaisesRegex(AnswerGenerationError, "Ollama is unavailable"),
        ):
            generate_answer("How much leave?", [self.source])


if __name__ == "__main__":
    unittest.main()
