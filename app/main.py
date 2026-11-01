from __future__ import annotations

import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from . import config, llm_notes
from .pipeline import analyze
from .report import write_html, write_json, write_xlsx

app = FastAPI(title="P&ID Consistency Checker", version="1.0")

JOBS: dict[str, dict] = {}
POOL = ThreadPoolExecutor(max_workers=1)  # one batch at a time: protects the 4 GB GPU
LOCK = threading.Lock()


def _run(job_id: str, paths: list[Path], use_llm: bool):
    job = JOBS[job_id]
    job["status"] = "running"

    def progress(done, total, msg):
        job.update(done=done, total=total, message=msg)

    try:
        result = analyze(paths, use_llm=use_llm, progress=progress)
        out = config.DATA_DIR / "jobs" / job_id
        write_json(result, out / "report.json")
        write_xlsx(result, out / "report.xlsx")
        write_html(result, out / "report.html")
        job.update(status="done", done=len(paths), total=len(paths), message="Finished",
                   summary={s: sum(1 for f in result["findings"] if f["severity"] == s)
                            for s in ("High", "Medium", "Low", "Review")},
                   findings=result["findings"], llm_note=result["llm_note"], errors=result["errors"])
    except Exception as e:
        job.update(status="failed", message=str(e))


def _start(paths: list[Path], use_llm: bool) -> str:
    job_id = uuid.uuid4().hex[:10]
    JOBS[job_id] = {"id": job_id, "status": "queued", "done": 0, "total": len(paths), "message": "Queued"}
    POOL.submit(_run, job_id, paths, use_llm)
    return job_id


@app.get("/health")
def health():
    return {"api": "ok", "ollama": llm_notes.status()}


@app.post("/api/batches")
async def create_batch(files: List[UploadFile] = File(...), use_llm: bool = Form(True)):
    pdfs = [f for f in files if f.filename and f.filename.lower().endswith(".pdf")]
    if not pdfs:
        raise HTTPException(400, "Upload at least one .pdf file")
    job_id = uuid.uuid4().hex[:10]
    in_dir = config.DATA_DIR / "jobs" / job_id / "input"
    in_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for f in pdfs:
        name = re.sub(r"[^\w.\- ]", "_", Path(f.filename).name)
        dest = in_dir / name
        with open(dest, "wb") as fh:
            while chunk := await f.read(1 << 20):
                fh.write(chunk)
        paths.append(dest)
    JOBS[job_id] = {"id": job_id, "status": "queued", "done": 0, "total": len(paths), "message": "Queued"}
    POOL.submit(_run, job_id, paths, use_llm)
    return {"job_id": job_id, "files": len(paths)}


class FolderReq(BaseModel):
    folder: str
    use_llm: bool = True


@app.post("/api/batches/from-folder")
def from_folder(req: FolderReq):
    """Point the API at a folder on the machine running it (no upload needed)."""
    folder = Path(req.folder)
    if not folder.is_dir():
        raise HTTPException(400, f"Folder not found: {folder}")
    paths = sorted(folder.glob("*.pdf")) + sorted(folder.glob("*.PDF"))
    paths = list(dict.fromkeys(paths))
    if not paths:
        raise HTTPException(400, "No .pdf files in that folder")
    return {"job_id": _start(paths, req.use_llm), "files": len(paths)}


@app.get("/api/batches/{job_id}")
def get_batch(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job")
    return {k: v for k, v in job.items() if k != "findings"}


@app.get("/api/batches/{job_id}/findings")
def get_findings(job_id: str, severity: str | None = None):
    job = JOBS.get(job_id)
    if not job or job["status"] != "done":
        raise HTTPException(404, "Job not finished")
    fs = job["findings"]
    if severity:
        fs = [f for f in fs if f["severity"].lower() == severity.lower()]
    return fs


@app.get("/api/batches/{job_id}/report.{ext}")
def get_report(job_id: str, ext: str):
    if ext not in ("xlsx", "html", "json"):
        raise HTTPException(404)
    p = config.DATA_DIR / "jobs" / job_id / f"report.{ext}"
    if not p.exists():
        raise HTTPException(404, "Report not ready")
    return FileResponse(p, filename=f"pid_report_{job_id}.{ext}")


PAGE = """<!doctype html><meta charset=utf-8><title>P&ID Consistency Checker</title>
<style>body{font:15px Segoe UI,Arial;max-width:760px;margin:40px auto;padding:0 16px}
button{padding:8px 16px;font-size:15px}#log{white-space:pre-wrap;background:#f5f5f5;padding:12px;margin-top:16px}
</style><h2>P&amp;ID Consistency Checker</h2>
<p>Select all your P&amp;ID PDFs (20+ is fine) and press Run.</p>
<input type=file id=f multiple accept=".pdf"><br><br>
<label><input type=checkbox id=llm checked> Use local Ollama (Qwen) to read notes</label><br><br>
<button onclick=run()>Run check</button><div id=log></div>
<script>
async function run(){
 const fl=document.getElementById('f').files; if(!fl.length){alert('Choose PDFs first');return}
 const fd=new FormData(); for(const f of fl) fd.append('files',f); fd.append('use_llm',document.getElementById('llm').checked);
 const log=document.getElementById('log'); log.textContent='Uploading '+fl.length+' files...';
 const r=await fetch('/api/batches',{method:'POST',body:fd}); const j=await r.json();
 if(!r.ok){log.textContent=JSON.stringify(j);return}
 const id=j.job_id;
 const t=setInterval(async()=>{
  const s=await (await fetch('/api/batches/'+id)).json();
  log.textContent=s.status+' '+s.done+'/'+s.total+'  '+s.message;
  if(s.status=='done'){clearInterval(t);
   log.innerHTML='Done. High '+s.summary.High+', Medium '+s.summary.Medium+', Low '+s.summary.Low+', Review '+s.summary.Review+
   '<br><br><a href="/api/batches/'+id+'/report.xlsx">Download Excel</a> | <a href="/api/batches/'+id+'/report.html" target=_blank>Open HTML</a> | <a href="/api/batches/'+id+'/report.json">JSON</a>'}
  if(s.status=='failed'){clearInterval(t)}
 },1500)}
</script>"""


@app.get("/", response_class=HTMLResponse)
def home():
    return PAGE
