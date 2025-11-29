
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import List

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db
from .capture import start_capture, stop_capture
from .config import DATA_DIR, LLMConfig, get_llm_config, set_llm_config
from .summarization import (
    answer_question,
    build_segments,
    build_quiz,
    build_recommendations,
    build_report,
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


app = FastAPI(title="SkimClass", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    index_path = STATIC_DIR / "index.html"
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


@app.get("/health")
def health():
    return {"status": "ok"}


class SessionMode(str):
    light = "light"
    standard = "standard"
    enhanced = "enhanced"


class SessionCreateIn(BaseModel):
    course_name: str
    mode: str = SessionMode.standard
    interval_sec: int = 30


class SessionOut(BaseModel):
    id: str
    course_name: str
    mode: str
    created_at: str


@app.post("/api/session", response_model=SessionOut)
def create_session(payload: SessionCreateIn) -> SessionOut:
    sid = str(uuid.uuid4())
    created_at = db.create_session(sid, payload.course_name, payload.mode)
    if payload.mode != SessionMode.light:
        interval = max(5, payload.interval_sec)
        if payload.mode == SessionMode.enhanced:
            interval = max(3, min(interval, 15))
        start_capture(sid, interval)
    return SessionOut(id=sid, course_name=payload.course_name, mode=payload.mode, created_at=created_at)


@app.post("/api/session/{session_id}/stop")
def stop_session(session_id: str):
    stop_capture(session_id)
    return {"status": "stopped"}


@app.post("/api/session/{session_id}/summarize")
def summarize_session(session_id: str, background: BackgroundTasks):
    background.add_task(build_segments, session_id)
    return {"status": "started"}


class SegmentOut(BaseModel):
    id: int
    idx: int
    title: str
    summary: str
    open_questions: List[str]


@app.get("/api/session/{session_id}/segments", response_model=List[SegmentOut])
def get_segments(session_id: str):
    rows = db.get_segments(session_id)
    out: List[SegmentOut] = []
    for row in rows:
        oq: List[str] = []
        if row["open_questions"]:
            try:
                oq = json.loads(row["open_questions"])
            except json.JSONDecodeError:
                oq = [row["open_questions"]]
        out.append(
            SegmentOut(
                id=row["id"],
                idx=row["idx"],
                title=row["title"],
                summary=row["summary"],
                open_questions=oq,
            )
        )
    return out


class QAIn(BaseModel):
    question: str


class QAOut(BaseModel):
    answer: str


@app.post("/api/session/{session_id}/qa", response_model=QAOut)
async def qa(session_id: str, payload: QAIn) -> QAOut:
    if not payload.question.strip():
        raise HTTPException(status_code=400, detail="empty question")
    ans = await answer_question(session_id, payload.question.strip())
    return QAOut(answer=ans)


class QuizItem(BaseModel):
    question: str
    options: List[str]
    correct_index: int
    explanation: str | None = None


class QuizOut(BaseModel):
    items: List[QuizItem]


@app.post("/api/session/{session_id}/quiz", response_model=QuizOut)
async def quiz(session_id: str) -> QuizOut:
    raw = await build_quiz(session_id)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = []
    items: List[QuizItem] = []
    for item in data:
        items.append(
            QuizItem(
                question=item.get("question", ""),
                options=item.get("options", []),
                correct_index=item.get("correct_index", 0),
                explanation=item.get("explanation"),
            )
        )
    return QuizOut(items=items)


@app.get("/api/session/{session_id}/report")
async def report(session_id: str):
    text = await build_report(session_id)
    return {"report": text}


@app.get("/api/session/{session_id}/recommendations")
async def recommendations(session_id: str):
    text = await build_recommendations(session_id)
    return {"text": text}


class SettingsIn(BaseModel):
    api_base: str
    api_key: str
    model: str


class SettingsOut(BaseModel):
    api_base: str
    model: str
    api_key_set: bool


@app.get("/api/settings", response_model=SettingsOut)
def get_settings() -> SettingsOut:
    cfg = get_llm_config()
    return SettingsOut(api_base=cfg.api_base, model=cfg.model, api_key_set=bool(cfg.api_key))


@app.post("/api/settings", response_model=SettingsOut)
def update_settings(payload: SettingsIn) -> SettingsOut:
    cfg = LLMConfig(api_base=payload.api_base, api_key=payload.api_key, model=payload.model)
    set_llm_config(cfg)
    return SettingsOut(api_base=cfg.api_base, model=cfg.model, api_key_set=bool(cfg.api_key))
