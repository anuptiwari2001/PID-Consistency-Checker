"""Entry point.
  python run.py                      -> start the API + web page on http://127.0.0.1:8000
  python run.py check <folder>       -> check every PDF in a folder, write reports, no server
"""
import argparse
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    c = sub.add_parser("check")
    c.add_argument("folder")
    c.add_argument("--no-llm", action="store_true")
    c.add_argument("--out", default="output")
    args = ap.parse_args()

    if args.cmd == "check":
        from app.pipeline import analyze
        from app.report import write_html, write_json, write_xlsx
        folder = Path(args.folder)
        pdfs = sorted({*folder.glob("*.pdf"), *folder.glob("*.PDF")})
        if not pdfs:
            sys.exit(f"No PDFs in {folder}")
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        res = analyze(pdfs, use_llm=not args.no_llm,
                      progress=lambda d, t, m: print(f"[{d}/{t}] {m}"))
        write_json(res, out / "report.json")
        write_xlsx(res, out / "report.xlsx")
        write_html(res, out / "report.html")
        print(f"\n{len(res['findings'])} findings -> {out.resolve()}")
        return

    import uvicorn
    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 8000)
    print(f"Open http://{host}:{port}  (API docs: /docs)")
    uvicorn.run("app.main:app", host=host, port=port)


if __name__ == "__main__":
    main()
