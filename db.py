import sqlite3
import json
import uuid
from contextlib import contextmanager
from datetime import datetime
from paths import get_db_path


@contextmanager
def get_connection():
    """上下文管理器，自动 commit/close。"""
    conn = sqlite3.connect(get_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """幂等建表，app 启动时调用一次。"""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS courses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                data_dir TEXT,
                has_faiss_index INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER NOT NULL,
                segment_index INTEGER,
                content TEXT NOT NULL,
                start_time REAL,
                end_time REAL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (course_id) REFERENCES courses(id)
            );
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (course_id) REFERENCES courses(id)
            );
            CREATE TABLE IF NOT EXISTS kg_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER NOT NULL,
                label TEXT NOT NULL,
                category TEXT,
                FOREIGN KEY (course_id) REFERENCES courses(id),
                UNIQUE(course_id, label)
            );
            CREATE TABLE IF NOT EXISTS kg_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER NOT NULL,
                source_label TEXT NOT NULL,
                target_label TEXT NOT NULL,
                relation TEXT,
                FOREIGN KEY (course_id) REFERENCES courses(id)
            );
            CREATE TABLE IF NOT EXISTS quiz_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER NOT NULL,
                total_questions INTEGER NOT NULL,
                correct_questions INTEGER NOT NULL,
                wrong_details_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (course_id) REFERENCES courses(id)
            );
            CREATE TABLE IF NOT EXISTS course_outlines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (course_id) REFERENCES courses(id)
            );
            CREATE TABLE IF NOT EXISTS review_plan_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER NOT NULL,
                weak_point TEXT NOT NULL,
                recommended_material TEXT NOT NULL,
                estimated_minutes INTEGER NOT NULL,
                priority TEXT NOT NULL,
                due_date TEXT,
                source_note TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                FOREIGN KEY (course_id) REFERENCES courses(id)
            );
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER NOT NULL,
                reminder_type TEXT NOT NULL,
                content TEXT NOT NULL,
                due_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                related_item_id INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY (course_id) REFERENCES courses(id)
            );
            CREATE TABLE IF NOT EXISTS shared_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT NOT NULL UNIQUE,
                course_id INTEGER NOT NULL,
                report_markdown TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                FOREIGN KEY (course_id) REFERENCES courses(id)
            );
            CREATE TABLE IF NOT EXISTS qa_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER NOT NULL,
                question TEXT,
                has_evidence INTEGER NOT NULL,
                used_doc_count INTEGER NOT NULL,
                avg_retrieval_score REAL,
                citation_ok INTEGER NOT NULL,
                response_ms INTEGER,
                source_scope TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (course_id) REFERENCES courses(id)
            );
            CREATE TABLE IF NOT EXISTS trusted_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER NOT NULL,
                domain TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (course_id) REFERENCES courses(id),
                UNIQUE(course_id, domain)
            );
        """)
        # 迁移：为旧版 summaries 表补充 start_time / end_time 列
        for col in ("start_time REAL", "end_time REAL"):
            try:
                conn.execute(f"ALTER TABLE summaries ADD COLUMN {col}")
            except Exception:
                pass  # 列已存在则忽略

        # 迁移：修复历史版本中被错误定义的 kg_edges 表结构
        try:
            cols = conn.execute("PRAGMA table_info(kg_edges)").fetchall()
            col_names = {r[1] for r in cols}
            if cols and "source_label" not in col_names:
                conn.execute("ALTER TABLE kg_edges RENAME TO kg_edges_backup_bad_schema")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS kg_edges (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        course_id INTEGER NOT NULL,
                        source_label TEXT NOT NULL,
                        target_label TEXT NOT NULL,
                        relation TEXT,
                        FOREIGN KEY (course_id) REFERENCES courses(id)
                    )
                    """
                )
        except Exception:
            pass


# ---- courses ----

def create_course(name, data_dir):
    """插入课程记录，返回 course_id。"""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO courses (name, created_at, data_dir) VALUES (?, ?, ?)",
            (name, datetime.now().isoformat(), data_dir)
        )
        return cur.lastrowid


def list_courses():
    """返回所有课程，按时间降序。"""
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM courses ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def get_course(course_id):
    """按 ID 查询单条课程。"""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()
        return dict(row) if row else None


def update_course_faiss_flag(course_id, has_index):
    """标记课程是否有 FAISS 索引。"""
    with get_connection() as conn:
        conn.execute(
            "UPDATE courses SET has_faiss_index = ? WHERE id = ?",
            (1 if has_index else 0, course_id)
        )


# ---- summaries ----

def add_summary(course_id, segment_index, content, start_time=None, end_time=None):
    """插入一条笔记摘要，可附带时间范围（秒）。"""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO summaries (course_id, segment_index, content, start_time, end_time, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (course_id, segment_index, content, start_time, end_time, datetime.now().isoformat())
        )


def get_summaries(course_id):
    """查询某课程的所有笔记，按 segment_index 升序。"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM summaries WHERE course_id = ? ORDER BY segment_index ASC",
            (course_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ---- chat_history ----

def add_chat_message(course_id, role, content):
    """插入一条聊天记录。"""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO chat_history (course_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (course_id, role, content, datetime.now().isoformat())
        )


def get_chat_history(course_id):
    """查询某课程的全部对话，按 id 升序。"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM chat_history WHERE course_id = ? ORDER BY id ASC",
            (course_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ---- knowledge_graph ----

def add_kg_nodes(course_id, nodes):
    """批量插入知识图谱节点，跳过已存在的节点。
    nodes: list[dict]，每项包含 label 和 category。
    """
    with get_connection() as conn:
        for node in nodes:
            conn.execute(
                "INSERT OR IGNORE INTO kg_nodes (course_id, label, category) VALUES (?, ?, ?)",
                (course_id, node["label"], node.get("category", "concept"))
            )


def add_kg_edges(course_id, edges):
    """批量插入知识图谱边。
    edges: list[dict]，每项包含 source, target, relation。
    """
    with get_connection() as conn:
        for edge in edges:
            conn.execute(
                "INSERT INTO kg_edges (course_id, source_label, target_label, relation) VALUES (?, ?, ?, ?)",
                (course_id, edge["source"], edge["target"], edge.get("relation", "相关"))
            )


def get_kg_graph(course_id):
    """查询某课程的全部知识图谱数据，返回 nodes 和 edges。"""
    with get_connection() as conn:
        node_rows = conn.execute(
            "SELECT label, category FROM kg_nodes WHERE course_id = ?",
            (course_id,)
        ).fetchall()
        edge_rows = conn.execute(
            "SELECT source_label, target_label, relation FROM kg_edges WHERE course_id = ?",
            (course_id,)
        ).fetchall()
        return {
            "nodes": [dict(r) for r in node_rows],
            "edges": [{"source": r["source_label"], "target": r["target_label"], "relation": r["relation"]} for r in edge_rows]
        }


# ---- quiz attempts ----

def add_quiz_attempt(course_id, total_questions, correct_questions, wrong_details):
    """保存一次测验作答结果。wrong_details 为 list[dict]。"""
    wrong_json = json.dumps(wrong_details, ensure_ascii=False) if wrong_details else "[]"
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO quiz_attempts (course_id, total_questions, correct_questions, wrong_details_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (course_id, total_questions, correct_questions, wrong_json, datetime.now().isoformat())
        )


def get_quiz_attempts(course_id):
    """查询课程测验记录（按时间升序）。"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM quiz_attempts WHERE course_id = ? ORDER BY id ASC",
            (course_id,)
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["wrong_details"] = json.loads(item.get("wrong_details_json") or "[]")
        except Exception:
            item["wrong_details"] = []
        result.append(item)
    return result


# ---- course outline ----

def save_course_outline(course_id, content):
    """保存课程大纲快照。"""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO course_outlines (course_id, content, created_at) VALUES (?, ?, ?)",
            (course_id, content, datetime.now().isoformat())
        )


def get_latest_course_outline(course_id):
    """获取课程最新大纲。"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM course_outlines WHERE course_id = ? ORDER BY id DESC LIMIT 1",
            (course_id,)
        ).fetchone()
        return dict(row) if row else None


# ---- review plan ----

def replace_review_plan_items(course_id, items):
    """替换课程复习清单，items 为结构化 list[dict]。"""
    with get_connection() as conn:
        conn.execute("DELETE FROM review_plan_items WHERE course_id = ?", (course_id,))
        for item in items:
            conn.execute(
                """
                INSERT INTO review_plan_items
                (course_id, weak_point, recommended_material, estimated_minutes, priority, due_date, source_note, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    course_id,
                    item.get("weak_point", "未命名薄弱点"),
                    item.get("recommended_material", "复习课堂笔记"),
                    int(item.get("estimated_minutes", 20)),
                    item.get("priority", "medium"),
                    item.get("due_date"),
                    item.get("source_note", ""),
                    item.get("status", "pending"),
                    datetime.now().isoformat(),
                )
            )


def get_review_plan_items(course_id, status=None):
    """查询复习清单，可按状态过滤。"""
    with get_connection() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM review_plan_items WHERE course_id = ? AND status = ? ORDER BY id ASC",
                (course_id, status)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM review_plan_items WHERE course_id = ? ORDER BY id ASC",
                (course_id,)
            ).fetchall()
        return [dict(r) for r in rows]


def update_review_plan_item_status(item_id, status):
    """更新复习任务状态（pending/done）。"""
    with get_connection() as conn:
        conn.execute("UPDATE review_plan_items SET status = ? WHERE id = ?", (status, item_id))


# ---- reminders ----

def add_reminder(course_id, reminder_type, content, due_at, related_item_id=None):
    """创建提醒。"""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO reminders
            (course_id, reminder_type, content, due_at, status, related_item_id, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?)
            """,
            (course_id, reminder_type, content, due_at, related_item_id, datetime.now().isoformat())
        )


def get_pending_reminders(course_id=None):
    """查询待处理提醒；course_id 为空时返回全部课程提醒。"""
    with get_connection() as conn:
        if course_id is None:
            rows = conn.execute(
                "SELECT * FROM reminders WHERE status = 'pending' ORDER BY due_at ASC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM reminders WHERE status = 'pending' AND course_id = ? ORDER BY due_at ASC",
                (course_id,)
            ).fetchall()
        return [dict(r) for r in rows]


def mark_reminder_done(reminder_id):
    """将提醒标记为已完成。"""
    with get_connection() as conn:
        conn.execute("UPDATE reminders SET status = 'done' WHERE id = ?", (reminder_id,))


def delete_pending_plan_reminders(course_id):
    """清理课程下由复习计划生成的历史待办提醒，避免重复堆积。"""
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM reminders WHERE course_id = ? AND status = 'pending' AND reminder_type = 'review_plan'",
            (course_id,)
        )


# ---- shared reports ----

def create_shared_report(course_id, report_markdown, expires_at=None):
    """创建可分享报告快照，返回 token。"""
    token = uuid.uuid4().hex[:12]
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO shared_reports (token, course_id, report_markdown, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (token, course_id, report_markdown, datetime.now().isoformat(), expires_at)
        )
    return token


def get_shared_report(token):
    """按 token 查询分享报告。"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM shared_reports WHERE token = ?",
            (token,)
        ).fetchone()
        return dict(row) if row else None


# ---- qa metrics ----

def add_qa_metric(
    course_id,
    question,
    has_evidence,
    used_doc_count,
    avg_retrieval_score,
    citation_ok,
    response_ms,
    source_scope,
):
    """记录一次问答质量指标。"""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO qa_metrics
            (course_id, question, has_evidence, used_doc_count, avg_retrieval_score, citation_ok, response_ms, source_scope, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                course_id,
                question,
                1 if has_evidence else 0,
                int(used_doc_count or 0),
                avg_retrieval_score,
                1 if citation_ok else 0,
                int(response_ms or 0),
                source_scope,
                datetime.now().isoformat(),
            ),
        )


def get_qa_metrics(course_id):
    """查询课程问答评测指标。"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM qa_metrics WHERE course_id = ? ORDER BY id ASC",
            (course_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---- trusted sources ----

def replace_trusted_sources(course_id, domains):
    """替换课程可信来源白名单（域名列表）。"""
    cleaned = []
    for d in domains or []:
        x = str(d or "").strip().lower()
        if x:
            cleaned.append(x)

    with get_connection() as conn:
        conn.execute("DELETE FROM trusted_sources WHERE course_id = ?", (course_id,))
        for domain in sorted(set(cleaned)):
            conn.execute(
                "INSERT OR IGNORE INTO trusted_sources (course_id, domain, created_at) VALUES (?, ?, ?)",
                (course_id, domain, datetime.now().isoformat()),
            )


def get_trusted_sources(course_id):
    """获取课程可信来源白名单域名。"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT domain FROM trusted_sources WHERE course_id = ? ORDER BY domain ASC",
            (course_id,),
        ).fetchall()
        return [r["domain"] for r in rows]
