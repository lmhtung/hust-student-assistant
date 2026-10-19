"""Adapter SQLite chỉ đọc, SQL luôn có tham số.

Thay lớp này khi nhóm có schema học vụ thật. KHÔNG cho LLM sinh SQL và không
dùng credentials chủ database. URI mode=ro + query_only bảo vệ adapter demo.
"""

import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from src.models import Section


class AcademicRepository:
    def __init__(self, path: Path):
        self.path = path.resolve()

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            yield db

    def rows(self, sql, params=()):
        with self.connection() as db:
            return [dict(row) for row in db.execute(sql, params).fetchall()]

    def program(self, user_id):
        rows = self.rows("SELECT program_id FROM students WHERE user_id=?", (user_id,))
        if not rows:
            raise ValueError("Không tìm thấy hồ sơ học vụ của người đang chat")
        return rows[0]["program_id"]

    def search_courses(self, user_id, query, curriculum_only, limit, offset):
        sql = """SELECT c.code,c.name,c.credits FROM courses c
                 WHERE (c.code LIKE ? OR c.name LIKE ?)"""
        params = [f"%{query}%", f"%{query}%"]
        if curriculum_only:
            sql += """ AND EXISTS (
                SELECT 1 FROM curriculum p
                WHERE p.course_code=c.code AND p.program_id=?
            )"""
            params.append(self.program(user_id))
        sql += " ORDER BY c.code LIMIT ? OFFSET ?"
        rows = self.rows(sql, (*params, limit + 1, offset))
        return self.page(rows, limit, offset)

    @staticmethod
    def page(rows, limit, offset):
        return {"items": rows[:limit], "next_offset": offset + limit if len(rows) > limit else None}

    def course(self, code):
        rows = self.rows("SELECT code,name,credits FROM courses WHERE code=?", (code,))
        return {"course": rows[0] if rows else None}

    def _sections(self, predicate, params):
        # predicate chỉ do các hàm nội bộ tạo, tuyệt đối không nhận SQL từ tool.
        selection = " FROM sections s JOIN courses c ON c.code=s.course_code WHERE " + predicate
        section_sql = f"""SELECT s.section_id,s.course_code,c.name AS course_name,
            c.credits,s.semester,s.lecturer_id,s.capacity,s.enrolled,s.is_open
            {selection}"""
        # Subquery dùng cùng bộ lọc và phân trang, tránh N+1 hoặc quá nhiều placeholder.
        meeting_sql = f"""SELECT section_id,weekday,start_minute,end_minute,weeks,room
            FROM meetings WHERE section_id IN (SELECT s.section_id {selection})
            ORDER BY section_id,weekday,start_minute"""

        # Hai truy vấn phải đọc trong cùng snapshot, kể cả khi DB vừa được cập nhật.
        with self.connection() as db:
            db.execute("BEGIN")
            sections = [dict(row) for row in db.execute(section_sql, params).fetchall()]
            if not sections:
                return []
            meetings = db.execute(meeting_sql, params).fetchall()

        meetings_by_section = {section["section_id"]: [] for section in sections}
        for row in meetings:
            meeting = dict(row)
            section_id = meeting.pop("section_id")
            meeting["weeks"] = json.loads(meeting["weeks"])
            meetings_by_section[section_id].append(meeting)

        return [
            Section.model_validate(
                {**section, "meetings": meetings_by_section[section["section_id"]]}
            )
            for section in sections
        ]

    def open_classes(self, user_id, semester, code, curriculum_only, limit, offset):
        predicate = "s.semester=? AND s.is_open=1"
        params = [semester]
        if code:
            predicate += " AND s.course_code=?"
            params.append(code)
        if curriculum_only:
            predicate += """ AND EXISTS (
                SELECT 1 FROM curriculum p
                WHERE p.course_code=s.course_code AND p.program_id=?
            )"""
            params.append(self.program(user_id))
        predicate += " ORDER BY s.course_code,s.section_id LIMIT ? OFFSET ?"
        sections = self._sections(predicate, (*params, limit + 1, offset))
        return self.page([s.model_dump(mode="json") for s in sections], limit, offset)

    def search_lecturers(self, query, limit, offset):
        # Allowlist trường công khai, không dùng SELECT * để tránh lộ cột mới.
        rows = self.rows(
            """SELECT lecturer_id,name,department,qualification,academic_title
            FROM lecturers WHERE name LIKE ? OR department LIKE ?
            ORDER BY lecturer_id LIMIT ? OFFSET ?""",
            (f"%{query}%", f"%{query}%", limit + 1, offset),
        )
        return self.page(rows, limit, offset)

    def lecturer(self, lecturer_id, semester):
        rows = self.rows(
            """SELECT lecturer_id,name,department,qualification,academic_title
            FROM lecturers WHERE lecturer_id=?""",
            (lecturer_id,),
        )
        if not rows:
            return {"lecturer": None}
        # Chỉ bảng tổng hợp đã được công bố, không đọc nội dung/người đánh giá.
        reviews = self.rows(
            """SELECT review_count,average_score FROM published_reviews
            WHERE lecturer_id=? AND review_count>=5""",
            (lecturer_id,),
        )
        teaching = self._sections(
            "s.lecturer_id=? AND s.semester=? ORDER BY s.section_id", (lecturer_id, semester)
        )
        return {
            "lecturer": rows[0],
            "published_review_summary": reviews[0] if reviews else None,
            "teaching_classes": [s.model_dump(mode="json") for s in teaching],
        }

    def selected_courses(self, user_id, semester):
        return [
            r["course_code"]
            for r in self.rows(
                "SELECT course_code FROM selected_courses WHERE user_id=? AND semester=? ORDER BY course_code",
                (user_id, semester),
            )
        ]

    def enrolled_classes(self, user_id, semester):
        return self._sections(
            """s.semester=? AND EXISTS(SELECT 1 FROM enrollments e
            WHERE e.section_id=s.section_id AND e.user_id=?) ORDER BY s.section_id""",
            (semester, user_id),
        )

    def academic_context(self, user_id, semester):
        return {
            "program_id": self.program(user_id),
            "selected_courses": self.selected_courses(user_id, semester),
            "enrolled_classes": [
                s.model_dump(mode="json") for s in self.enrolled_classes(user_id, semester)
            ],
        }

    def candidates(self, code, semester):
        # Giới hạn độ lớn trước khi tạo toàn bộ tổ hợp. Không âm thầm lấy 10 lớp đầu.
        rows = self._sections(
            """s.course_code=? AND s.semester=? AND s.is_open=1
            AND s.enrolled<s.capacity ORDER BY s.section_id LIMIT 31""",
            (code, semester),
        )
        if len(rows) > 30:
            raise ValueError("Quá nhiều lớp ứng viên; cần thu hẹp phạm vi trước khi xếp lịch")
        return rows
