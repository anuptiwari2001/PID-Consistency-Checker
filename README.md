# P&ID Consistency Checker

Checks P&ID drawings and reports consistency findings through a web interface or batch reports.

## Requirements

- Python
- Optional: [Ollama](https://ollama.com/) for local LLM-assisted notes

## Setup

```bash
python -m venv .venv
```

Activate the virtual environment, then install dependencies:

```bash
python -m pip install -r requirements.txt
```

## Run

Start the web app and API at <http://127.0.0.1:8000>:

```bash
python run.py
```

Batch-check PDFs in a folder without starting the web server:

```bash
python run.py check ./path/to/pdfs --out ./output
```

Add `--no-llm` to disable LLM assistance for a batch run. To enable local LLM-assisted notes, install and start Ollama, then run `ollama pull qwen2.5:3b`. Set `OLLAMA_MODEL` to use a different model.

## Tests

```bash
python -m pip install pytest
python -m pytest
```

## Configuration and data

- Edit `rules.yaml` to configure project rules.
- `samples/` contains example inputs used by the tests.
- Uploaded drawings and generated job reports are stored under `data/`; generated reports may contain sensitive project information. This folder is excluded from Git.

Before publishing, verify that every sample file and any other tracked content is appropriate to share publicly.
