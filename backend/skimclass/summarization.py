
from __future__ import annotations

import base64
import json
from typing import List

from fastapi.concurrency import run_in_threadpool

from . import db
from .llm_client import chat


def _encode_image(path: str) -> str:
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _chunks(items, n: int):
    for i in range(0, len(items), n):
        yield items[i : i + n]


def build_segments(session_id: str, images_per_segment: int = 6) -> None:
    captures = db.get_captures(session_id)
    if not captures:
        return

    db.clear_segments(session_id)

    for idx, group in enumerate(_chunks(captures, images_per_segment), start=1):
        paths = [row["image_path"] for row in group]
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一名课堂助教。下面是一段上课过程的多张截屏，请分析它们，总结这一时间段的知识点。"
                    "请用 JSON 返回：title（小节标题），summary（用中文 Markdown 段落总结），"
                    "open_questions（1-3 条学生可能仍然有疑惑的问题）。"
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "根据这些截图，总结这一小节的内容。"},
                    *[{"type": "image_url", "image_url": {"url": _encode_image(p)}} for p in paths],
                ],
            },
        ]
        raw = chat(messages, max_tokens=800)

        title = f"小节 {idx}"
        summary = raw
        open_questions_json = "[]"

        try:
            data = json.loads(raw)
            title = data.get("title", title)
            summary = data.get("summary", summary)
            oq = data.get("open_questions", [])
            if not isinstance(oq, list):
                oq = [str(oq)]
            open_questions_json = json.dumps(oq, ensure_ascii=False)
        except json.JSONDecodeError:
            open_questions_json = "[]"

        db.add_segment(session_id, idx, title, summary, open_questions_json)


async def answer_question(session_id: str, question: str) -> str:
    rows = db.get_segments(session_id)
    if not rows:
        return "还没有生成本节课的总结，请先点击“生成 Outline”后再来提问。"

    context_parts: List[str] = []
    for row in rows:
        context_parts.append(f"## {row['title']}\n{row['summary']}")

    context = "\n\n".join(context_parts)
    messages = [
        {
            "role": "system",
            "content": (
                "你是一名耐心的助教，只能根据提供的课堂笔记来回答问题。"
                "如果笔记中没有相关内容，请坦诚说明，不要胡编。"
            ),
        },
        {
            "role": "user",
            "content": f"课堂笔记如下：\n\n{context}\n\n学生的问题：{question}",
        },
    ]
    return await run_in_threadpool(chat, messages, 512, 0.2)


async def build_quiz(session_id: str) -> str:
    rows = db.get_segments(session_id)
    if not rows:
        return "[]"

    context_parts: List[str] = []
    for row in rows:
        context_parts.append(f"## {row['title']}\n{row['summary']}")
    context = "\n\n".join(context_parts)

    messages = [
        {
            "role": "system",
            "content": (
                "你现在要为学生生成一份简单的课后小测。"
                "请基于给定的课堂内容，返回 JSON 列表，其中每个元素包含："
                "question（题干，中文），options（4 个选项字符串），"
                "correct_index（0-3 的整数），explanation（中文解析）。"
            ),
        },
        {
            "role": "user",
            "content": f"课堂内容：\n{context}\n\n请生成 2-5 道单选题。",
        },
    ]

    raw = await run_in_threadpool(chat, messages, 800, 0.4)
    try:
        data = json.loads(raw)
        quiz_json = json.dumps(data, ensure_ascii=False)
    except json.JSONDecodeError:
        data = [
            {
                "question": "模型未能正确生成结构化小测，请稍后重试。",
                "options": ["无法解析大模型输出"],
                "correct_index": 0,
                "explanation": "",
            }
        ]
        quiz_json = json.dumps(data, ensure_ascii=False)

    db.save_quiz(session_id, quiz_json)
    return quiz_json


async def build_report(session_id: str) -> str:
    rows = db.get_segments(session_id)
    if not rows:
        return "暂时没有本节课的 Outline，请先生成。"

    outline_lines: List[str] = []
    for i, row in enumerate(rows, start=1):
        outline_lines.append(f"{i}. {row['title']}：{row['summary']}")
    outline = "\n".join(outline_lines)

    messages = [
        {
            "role": "system",
            "content": (
                "请根据这节课的 Outline，为学生写一份学习报告（中文）。"
                "结构包括：【本节课核心内容】【容易混淆的点】【课后建议练习】。"
                "请使用 Markdown 小标题。"
            ),
        },
        {
            "role": "user",
            "content": outline,
        },
    ]
    return await run_in_threadpool(chat, messages, 1200, 0.3)


async def build_recommendations(session_id: str) -> str:
    rows = db.get_segments(session_id)
    if not rows:
        return "暂时没有本节课的 Outline，请先生成。"

    outline_lines: List[str] = []
    for i, row in enumerate(rows, start=1):
        outline_lines.append(f"{i}. {row['title']}：{row['summary']}")
    outline = "\n".join(outline_lines)

    messages = [
        {
            "role": "system",
            "content": (
                "你是一名学习规划顾问。"
                "根据这节课的内容，为学生推荐一些拓展学习建议："
                "1）可以继续查找/观看的相关知识点；"
                "2）推荐的学习资源类型（例如：教材章节、公开课、B站关键词等）；"
                "3）如何在下一节课前进行预习或回顾。"
                "直接用中文 Markdown 输出，列出条目即可。"
            ),
        },
        {
            "role": "user",
            "content": outline,
        },
    ]

    return await run_in_threadpool(chat, messages, 800, 0.4)
