"""Optional FastAPI wrapper for local business Q&A."""


def create_app(agent):
    try:
        from fastapi import FastAPI, HTTPException
        from pydantic import BaseModel, Field
    except ImportError as exc:
        raise RuntimeError("Install the API extra: pip install -e '.[api]'") from exc

    class AskRequest(BaseModel):
        question: str = Field(min_length=1, max_length=4000)
        mode: str = "hybrid"
        history: list[dict[str, str]] = Field(default_factory=list)

    app = FastAPI(title="KG Agent", version="0.1.0")
    traces = {}

    @app.get("/schema")
    def schema():
        return agent.store.schema().model_dump(mode="json")

    @app.post("/ask")
    def ask(request: AskRequest):
        try:
            response = agent.run(request.question, mode=request.mode, history=request.history)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        traces[response.trace["run_id"]] = response.trace
        return response.model_dump(mode="json")

    @app.get("/trace/{trace_id}")
    def trace(trace_id: str):
        if trace_id not in traces:
            raise HTTPException(status_code=404, detail="Trace not found")
        return traces[trace_id]

    return app
