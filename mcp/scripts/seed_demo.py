"""Tạo database GIẢ LẬP, không phải dữ liệu của HUST hay giảng viên thật.

Chạy từ mcp/: python scripts/seed_demo.py. Script từ chối ghi đè file đã có.
Web demo có adapter ghi riêng; AcademicRepository runtime luôn mở mode=ro.
"""

import argparse
import json
import sqlite3
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data/academic.sqlite3"


def seed(path: Path) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create bảo đảm không xóa dữ liệu nếu chạy nhầm hai lần.
    path.touch(exist_ok=False)
    try:
        with sqlite3.connect(path) as db:
            db.executescript("""
                PRAGMA foreign_keys=ON;
                CREATE TABLE courses (
                    code TEXT PRIMARY KEY, name TEXT NOT NULL,
                    credits INTEGER NOT NULL CHECK(credits>0));
                CREATE TABLE students (user_id TEXT PRIMARY KEY, program_id TEXT NOT NULL);
                CREATE TABLE curriculum (
                    program_id TEXT NOT NULL, course_code TEXT REFERENCES courses(code),
                    PRIMARY KEY(program_id,course_code));
                CREATE TABLE lecturers (
                    lecturer_id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    department TEXT NOT NULL, qualification TEXT NOT NULL,
                    academic_title TEXT NOT NULL);
                CREATE TABLE published_reviews (
                    lecturer_id TEXT PRIMARY KEY REFERENCES lecturers(lecturer_id),
                    review_count INTEGER NOT NULL, average_score REAL NOT NULL);
                CREATE TABLE sections (
                    section_id TEXT PRIMARY KEY, course_code TEXT REFERENCES courses(code),
                    semester TEXT NOT NULL, lecturer_id TEXT REFERENCES lecturers(lecturer_id),
                    capacity INTEGER NOT NULL CHECK(capacity>=0),
                    enrolled INTEGER NOT NULL CHECK(enrolled>=0),
                    is_open INTEGER NOT NULL CHECK(is_open IN (0,1)));
                CREATE TABLE meetings (
                    section_id TEXT REFERENCES sections(section_id), weekday INTEGER NOT NULL,
                    start_minute INTEGER NOT NULL, end_minute INTEGER NOT NULL,
                    weeks TEXT NOT NULL, room TEXT NOT NULL);
                CREATE TABLE selected_courses (
                    user_id TEXT REFERENCES students(user_id), semester TEXT NOT NULL,
                    course_code TEXT REFERENCES courses(code),
                    PRIMARY KEY(user_id,semester,course_code));
                CREATE TABLE enrollments (
                    user_id TEXT REFERENCES students(user_id),
                    section_id TEXT REFERENCES sections(section_id),
                    PRIMARY KEY(user_id,section_id));
                CREATE INDEX meetings_section ON meetings(section_id);
                CREATE INDEX sections_course_semester ON sections(course_code,semester);
            """)
            db.executemany(
                "INSERT INTO courses VALUES (?,?,?)",
                [
                    ("ET101", "Cơ sở điện tử (demo)", 3),
                    ("ET102", "Tín hiệu và hệ thống (demo)", 3),
                    ("MI101", "Toán chung (demo)", 4),
                    ("IT101", "Nhập môn lập trình (demo)", 3),
                ],
            )
            db.executemany(
                "INSERT INTO students VALUES (?,?)",
                [("demo-et", "DEMO-ET"), ("demo-it", "DEMO-IT")],
            )
            db.executemany(
                "INSERT INTO curriculum VALUES (?,?)",
                [
                    ("DEMO-ET", "ET101"),
                    ("DEMO-ET", "ET102"),
                    ("DEMO-ET", "MI101"),
                    ("DEMO-IT", "IT101"),
                    ("DEMO-IT", "MI101"),
                ],
            )
            db.execute(
                "INSERT INTO lecturers VALUES (?,?,?,?,?)",
                ("GV-DEMO", "Giảng viên giả lập", "Khoa minh họa", "Tiến sĩ (demo)", "Không"),
            )
            db.execute("INSERT INTO published_reviews VALUES (?,?,?)", ("GV-DEMO", 10, 4.2))
            sections = [
                ("ET101-A", "ET101", 0, 480, 570, 40, 20, 1),
                ("ET101-B", "ET101", 1, 480, 570, 40, 20, 1),
                ("ET101-F", "ET101", 2, 480, 570, 40, 40, 1),
                ("ET101-C", "ET101", 3, 480, 570, 40, 20, 0),
                ("ET102-A", "ET102", 0, 500, 590, 40, 20, 1),
                ("ET102-B", "ET102", 1, 500, 590, 40, 20, 1),
                ("MI101-A", "MI101", 2, 480, 660, 50, 25, 1),
                ("IT101-A", "IT101", 3, 480, 570, 40, 20, 1),
            ]
            for sid, code, day, start, end, capacity, enrolled, opened in sections:
                db.execute(
                    "INSERT INTO sections VALUES (?,?,?,?,?,?,?)",
                    (sid, code, "2026-1", "GV-DEMO", capacity, enrolled, opened),
                )
                db.execute(
                    "INSERT INTO meetings VALUES (?,?,?,?,?,?)",
                    (sid, day, start, end, json.dumps(list(range(1, 16))), "DEMO-101"),
                )
            db.executemany(
                "INSERT INTO selected_courses VALUES (?,?,?)",
                [
                    ("demo-et", "2026-1", "ET101"),
                    ("demo-et", "2026-1", "ET102"),
                    ("demo-et", "2026-1", "MI101"),
                    ("demo-it", "2026-1", "IT101"),
                ],
            )
            db.execute("INSERT INTO enrollments VALUES (?,?)", ("demo-et", "MI101-A"))
    except BaseException:
        path.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()
    try:
        seed(args.path)
    except FileExistsError:
        parser.error(f"File đã tồn tại, không ghi đè: {args.path}")
    print(f"Đã tạo dữ liệu giả lập: {args.path}")
