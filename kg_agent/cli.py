"""Command line entry point for kg-agent."""

import argparse
from pathlib import Path

from kg_agent.config import AgentConfig
from kg_agent.embeddings import create_embeddings
from kg_agent.factory.prompt_generator import BusinessContext, generate_system_prompt
from kg_agent.kg.file_store import FileGraphStore
from kg_agent.llm import OpenAICompatibleModel
from kg_agent.runtime.agent import BusinessQAAgent
from kg_agent.runtime.tools.dense import PersistentDenseIndex
from kg_agent.eval.runner import evaluate, load_dataset, render_report


def _agent(no_llm=False, kg_path=Path("data/sample")):
    config = AgentConfig.load()
    store = FileGraphStore(kg_path)
    llm = None if no_llm else OpenAICompatibleModel(config)
    index_path = Path("data/index/dense.json")
    embedder = create_embeddings(config) if index_path.is_file() else None
    dense_index = PersistentDenseIndex(index_path, embedder) if embedder else None
    prompt_path = sorted(Path("artifacts").glob("system_prompt.v*.md"),
                         key=lambda path: int(path.stem.rsplit("v", 1)[-1])) if Path("artifacts").exists() else []
    prompt = prompt_path[-1].read_text(encoding="utf-8") if prompt_path else ""
    return BusinessQAAgent(store, llm=llm, embedder=embedder, dense_index=dense_index,
                           system_prompt=prompt)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="kg-agent")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build-agent", help="Generate a schema-grounded system prompt")
    build.add_argument("--kg", type=Path, default=Path("data/sample"))
    build.add_argument("--context", type=Path, default=Path("config/business_context.yaml"))
    build.add_argument("--artifacts", type=Path, default=Path("artifacts"))
    build.add_argument("--refine", action="store_true")
    index = commands.add_parser("index", help="Build the local dense retrieval index")
    index.add_argument("--kg", type=Path, default=Path("data/sample"))
    index.add_argument("--output", type=Path, default=Path("data/index/dense.json"))
    ask = commands.add_parser("ask", help="Ask one business question")
    ask.add_argument("question")
    ask.add_argument("--mode", choices=sorted(BusinessQAAgent.MODES), default="hybrid")
    ask.add_argument("--no-llm", action="store_true", help="Use deterministic local behavior")
    ask.add_argument("--verbose", action="store_true")
    chat = commands.add_parser("chat", help="Ask questions interactively")
    chat.add_argument("--mode", choices=sorted(BusinessQAAgent.MODES), default="agent")
    chat.add_argument("--no-llm", action="store_true")
    evaluation = commands.add_parser("eval", help="Compare retrieval modes against labeled cases")
    evaluation.add_argument("--dataset", type=Path, default=Path("eval/dataset.yaml"))
    evaluation.add_argument("--modes", default="dense,graph,keyword,hybrid,agent")
    evaluation.add_argument("--no-llm", action="store_true")
    evaluation.add_argument("--output", type=Path, default=Path("runs/eval_report.md"))
    serve = commands.add_parser("serve", help="Run the local FastAPI service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--no-llm", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "build-agent":
        store = FileGraphStore(args.kg)
        context = BusinessContext.load(args.context)
        llm = OpenAICompatibleModel(AgentConfig.load()) if args.refine else None
        artifact = generate_system_prompt(store.schema(), context, refine=args.refine,
                                          llm=llm, artifacts_dir=args.artifacts)
        print(f"Built system prompt v{artifact.version} ({artifact.schema_hash[:12]})")
        print(f"Saved system_prompt.v{artifact.version}.md under {args.artifacts}")
    elif args.command == "index":
        config = AgentConfig.load()
        store = FileGraphStore(args.kg)
        count = PersistentDenseIndex(args.output, create_embeddings(config)).build(store)
        print(f"Indexed {count} graph and text records to {args.output}")
    elif args.command == "ask":
        response = _agent(args.no_llm).run(args.question, mode=args.mode, verbose=args.verbose)
        print(response.answer)
        if response.citations:
            print("Citations: " + ", ".join(response.citations))
        print(f"Confidence: {response.confidence:.2f}")
        print(f"Trace: {response.trace['trace_file']}")
    elif args.command == "chat":
        agent = _agent(args.no_llm)
        history = []
        print("KG Agent chat. Type /exit to quit.")
        while True:
            question = input("you> ").strip()
            if question.casefold() in {"/exit", "/quit"}:
                break
            try:
                answer = agent.run(question, mode=args.mode, history=history)
                print(f"agent> {answer.answer}\nSources: {', '.join(answer.citations)}")
                history.extend([{"role": "user", "content": question},
                                {"role": "assistant", "content": answer.answer}])
            except (ValueError, RuntimeError) as exc:
                print(f"agent> {exc}")
    elif args.command == "eval":
        cases = load_dataset(args.dataset)
        results = evaluate(_agent(args.no_llm), cases,
                           modes=tuple(item.strip() for item in args.modes.split(",") if item.strip()))
        report = render_report(results)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(report)
        print(f"Saved {args.output}")
    elif args.command == "serve":
        try:
            import uvicorn
        except ImportError as exc:
            raise RuntimeError("Install API dependencies with pip install -e '.[api]'") from exc
        from kg_agent.api import create_app
        uvicorn.run(create_app(_agent(args.no_llm)), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
