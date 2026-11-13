import { useEffect, useState } from "react";

const milestones = [
  { title: "Project foundation", complete: true },
  { title: "Document upload and parsing", complete: true },
  { title: "Semantic search and cited answers", complete: true },
  { title: "Authentication and evaluation", complete: false },
];

function renderCitations(answer, turnId) {
  return answer.split(/(\[\d+\])/g).map((part, index) => {
    const citation = part.match(/^\[(\d+)\]$/);
    if (!citation) {
      return part;
    }

    return (
      <a
        className="citation-link"
        href={`#${turnId}-source-${citation[1]}`}
        key={`${turnId}-citation-${index}`}
      >
        {part}
      </a>
    );
  });
}

export default function App() {
  const [apiStatus, setApiStatus] = useState("checking");
  const [documents, setDocuments] = useState([]);
  const [documentsError, setDocumentsError] = useState("");
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [question, setQuestion] = useState("");
  const [conversation, setConversation] = useState([]);
  const [isAsking, setIsAsking] = useState(false);
  const [chatError, setChatError] = useState("");

  async function uploadFile(file) {
    if (!file) {
      return;
    }

    setIsUploading(true);
    setUploadError("");
    const formData = new FormData();
    formData.append("file", file);

    try {
      const response = await fetch("/api/documents", {
        method: "POST",
        body: formData,
      });
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.detail || "The document could not be uploaded.");
      }
      setDocuments((current) => [result, ...current]);
    } catch (error) {
      setUploadError(error.message || "The document could not be uploaded.");
    } finally {
      setIsUploading(false);
    }
  }

  async function askQuestion(event) {
    event.preventDefault();
    const submittedQuestion = question.trim();
    if (!submittedQuestion) {
      return;
    }

    setIsAsking(true);
    setChatError("");
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: submittedQuestion }),
      });
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.detail || "The assistant could not answer.");
      }
      setConversation((current) => [
        ...current,
        { ...result, question: submittedQuestion, id: `${Date.now()}-${current.length}` },
      ]);
      setQuestion("");
    } catch (error) {
      setChatError(error.message || "The assistant could not answer.");
    } finally {
      setIsAsking(false);
    }
  }

  useEffect(() => {
    fetch("/api/health")
      .then((response) => {
        if (!response.ok) {
          throw new Error(`API returned ${response.status}`);
        }
        return response.json();
      })
      .then((data) => setApiStatus(data.status === "ok" ? "online" : "unavailable"))
      .catch(() => setApiStatus("unavailable"));
  }, []);

  useEffect(() => {
    if (apiStatus !== "online") {
      return;
    }

    let active = true;
    fetch("/api/documents")
      .then(async (response) => {
        const result = await response.json();
        if (!response.ok) {
          throw new Error(result.detail || "Could not load documents.");
        }
        return result;
      })
      .then((result) => {
        if (active) {
          setDocuments(result);
          setDocumentsError("");
        }
      })
      .catch((error) => {
        if (active) {
          setDocumentsError(error.message || "Could not load documents.");
        }
      });

    return () => {
      active = false;
    };
  }, [apiStatus]);

  return (
    <main className="page-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Knowledge home">
          <span className="brand-mark">K</span>
          <span>keystone</span>
        </a>
        <div className="workspace-label">
          <span className="workspace-dot" />
          Private workspace
        </div>
      </header>

      <section className="welcome-card" aria-labelledby="welcome-title">
        <div className="eyebrow">YOUR COMPANY KNOWLEDGE, CONNECTED</div>
        <h1 id="welcome-title">A better way to find the answers.</h1>
        <p className="intro">
          Bring your company documents together and ask questions in plain
          language. Answers will be grounded in your sources, not guesses.
        </p>

        <div className="status-card" role="status" aria-live="polite">
          <span
            className={`status-indicator ${apiStatus === "online" ? "is-online" : ""}`}
          />
          <span>
            API{" "}
            {apiStatus === "checking"
              ? "connecting…"
              : apiStatus === "online"
                ? "connected"
                : "not reachable"}
          </span>
          {apiStatus === "unavailable" && (
            <span className="status-hint">Start the app with Docker Compose.</span>
          )}
        </div>
      </section>

      <section className="documents-panel" aria-labelledby="documents-title">
        <div className="section-heading">
          <div>
            <div className="eyebrow">YOUR SOURCES</div>
            <h2 id="documents-title">Add company documents</h2>
          </div>
          <span className="document-count">
            {documents.length} {documents.length === 1 ? "DOCUMENT" : "DOCUMENTS"}
          </span>
        </div>
        <div className="upload-box">
          <span className="upload-icon" aria-hidden="true">↑</span>
          <div className="upload-copy">
            <strong>
              {documents.length > 0
                ? "Add another trusted source"
                : "Start with your trusted sources"}
            </strong>
            <span>PDF, DOCX, or CSV · up to 10 MB each</span>
          </div>
          <label
            className={`upload-button ${isUploading ? "is-disabled" : ""}`}
            htmlFor="document-upload"
          >
            {isUploading ? "Indexing…" : "Choose a file"}
          </label>
          <input
            accept=".pdf,.docx,.csv"
            className="visually-hidden"
            disabled={isUploading || apiStatus !== "online"}
            id="document-upload"
            onChange={(event) => {
              uploadFile(event.target.files?.[0]);
              event.target.value = "";
            }}
            type="file"
          />
        </div>
        {uploadError && <p className="upload-error" role="alert">{uploadError}</p>}
        {documentsError && <p className="upload-error" role="alert">{documentsError}</p>}
        {documents.length > 0 && (
          <ul className="document-list" aria-label="Uploaded documents">
            {documents.map((document) => (
              <li className="document-item" key={document.document_id}>
                <span className="file-type">{document.file_type}</span>
                <span className="document-details">
                  <strong>{document.filename}</strong>
                  <span>
                    {document.chunk_count} {document.chunk_count === 1 ? "chunk" : "chunks"} indexed
                    {document.character_count
                      ? ` · ${document.character_count.toLocaleString()} characters extracted`
                      : ""}
                  </span>
                </span>
                <span className="document-ready">READY</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="chat-panel" aria-labelledby="chat-title">
        <div className="section-heading">
          <div>
            <div className="eyebrow">GROUNDED IN YOUR SOURCES</div>
            <h2 id="chat-title">Ask your documents</h2>
          </div>
          <span className="chat-model">OLLAMA · LLAMA 3.2</span>
        </div>

        {conversation.length === 0 ? (
          <p className="chat-empty">
            {documents.length > 0
              ? "Ask a question to find an answer in your uploaded sources."
              : "Upload a document first. Answers will include references to the source passages."}
          </p>
        ) : (
          <ol className="conversation-list" aria-live="polite">
            {conversation.map((turn) => (
              <li className="conversation-turn" key={turn.id}>
                <p className="turn-question">{turn.question}</p>
                <p className="turn-answer">{renderCitations(turn.answer, turn.id)}</p>
                {turn.sources.length > 0 && (
                  <div className="source-list" aria-label="Answer sources">
                    {turn.sources.map((source, index) => (
                      <article
                        className="source-card"
                        id={`${turn.id}-source-${index + 1}`}
                        key={`${turn.id}-${source.document_id}-${source.chunk_index}`}
                      >
                        <span className="source-reference">[{index + 1}]</span>
                        <div className="source-content">
                          <strong>{source.filename}</strong>
                          <span>
                            Section {source.chunk_index + 1} ·{" "}
                            {Math.round(Math.min(1, source.similarity) * 100)}% match
                          </span>
                          <p>{source.excerpt}</p>
                        </div>
                      </article>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ol>
        )}

        {chatError && <p className="upload-error" role="alert">{chatError}</p>}
        <form className="chat-form" onSubmit={askQuestion}>
          <input
            aria-label="Question for your documents"
            disabled={documents.length === 0 || apiStatus !== "online" || isAsking}
            maxLength={1000}
            minLength={3}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="Ask a question about your sources…"
            value={question}
          />
          <button
            className="chat-submit"
            disabled={
              !question.trim() ||
              documents.length === 0 ||
              apiStatus !== "online" ||
              isAsking
            }
            type="submit"
          >
            {isAsking ? "Thinking…" : "Ask"}
          </button>
        </form>
        <p className="chat-disclaimer">
          Answers are based on retrieved passages. Check the cited sources for context.
        </p>
      </section>

      <section className="roadmap" aria-labelledby="roadmap-title">
        <div className="section-heading">
          <div>
            <div className="eyebrow">BUILDING YOUR ASSISTANT</div>
            <h2 id="roadmap-title">The milestones</h2>
          </div>
          <span className="step-count">STEP 3 OF 4</span>
        </div>
        <ol className="milestone-list">
          {milestones.map((milestone, index) => (
            <li
              className={milestone.complete ? "milestone is-complete" : "milestone"}
              key={milestone.title}
            >
              <span className="milestone-number">
                {milestone.complete ? "✓" : String(index + 1).padStart(2, "0")}
              </span>
              <span>{milestone.title}</span>
              {milestone.complete && <span className="milestone-tag">COMPLETED</span>}
            </li>
          ))}
        </ol>
      </section>

      <footer>
        <span>Built for your team. Grounded in your sources.</span>
        <span>Development workspace</span>
      </footer>
    </main>
  );
}
