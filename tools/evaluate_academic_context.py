"""离线学术背景回归运行器；B0/B1 场景检查不等于检索或回答质量评测。"""
import argparse
import hashlib
import json
from pathlib import Path

from fieldwork_agent.kernel.guidance.extract import apply_message_extractions

ROOT = Path(__file__).resolve().parents[1]


def evaluate(corpus_path: Path) -> dict:
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    results = []
    for case in corpus["cases"]:
        patch = apply_message_extractions(case["message"], case.get("existing", {}))
        errors = [f"{key}: expected {value!r}, got {patch.get(key)!r}"
                  for key, value in case["expected"].items() if patch.get(key) != value]
        errors += [f"unexpected field: {key}" for key in case["forbidden"] if key in patch]
        results.append({"id": case["id"], "passed": not errors, "patch": patch, "errors": errors})
    source_root = Path(apply_message_extractions.__code__.co_filename).resolve().parents[2]
    sources = ("kernel/models.py", "kernel/context_patch.py", "kernel/guidance/extract.py",
               "kernel/guidance/academic.py")
    return {"schema_version": 1, "kind": corpus["kind"],
            "corpus_sha256": hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
            "sources_sha256": {name: hashlib.sha256((source_root / name).read_bytes()).hexdigest()
                               for name in sources if (source_root / name).exists()},
            "total": len(results), "passed": sum(row["passed"] for row in results),
            "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=ROOT / "tests/fixtures/rag/academic-context-v1.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(args.corpus)
    content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    print(f"academic context scenario checks: {report['passed']}/{report['total']}")
    for row in report["results"]:
        if not row["passed"]:
            print(f"  {row['id']}: {'; '.join(row['errors'])}")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
