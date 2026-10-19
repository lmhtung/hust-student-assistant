import sqlite3

import pytest
from pydantic import ValidationError

from src.models import Meeting, ScheduleRequest, Section
from src.scheduling import conflict, overlap, suggest


def test_curriculum_includes_common_courses_and_excludes_other_program(repository):
    result = repository.search_courses("demo-et", "", True, 30, 0)
    assert {c["code"] for c in result["items"]} == {"ET101", "ET102", "MI101"}
    classes = repository.open_classes("demo-et", "2026-1", None, True, 30, 0)
    assert "IT101" not in {c["course_code"] for c in classes["items"]}
    assert "MI101" in {c["course_code"] for c in classes["items"]}


def test_private_context_uses_exact_user(repository):
    assert repository.academic_context("demo-it", "2026-1")["selected_courses"] == ["IT101"]
    with pytest.raises(ValueError):
        repository.program("unknown")


def test_selected_courses_are_scoped_to_semester(repository):
    assert repository.academic_context("demo-et", "2026-2")["selected_courses"] == []
    assert (
        suggest(repository, "demo-et", ScheduleRequest(semester="2026-2"))["reason"]
        == "missing_courses"
    )


def test_database_is_read_only(repository):
    with repository.connection() as db, pytest.raises(sqlite3.OperationalError):
        db.execute("DELETE FROM courses")
    assert repository.course("ET101")["course"] is not None


def test_sql_injection_is_data(repository):
    assert repository.course("ET101' OR 1=1 --")["course"] is None
    assert repository.course("ET101")["course"]["credits"] == 3


def test_pagination_has_explicit_next_offset(repository):
    first = repository.search_courses("demo-et", "", False, 1, 0)
    second = repository.search_courses("demo-et", "", False, 1, first["next_offset"])
    assert first["items"] != second["items"]


def test_lecturer_reviews_only_published_aggregate(repository):
    result = repository.lecturer("GV-DEMO", "2026-1")
    assert result["published_review_summary"] == {"review_count": 10, "average_score": 4.2}
    assert set(result["lecturer"]) == {
        "lecturer_id",
        "name",
        "department",
        "qualification",
        "academic_title",
    }


def meeting(start=480, end=570, weeks=(1, 2), weekday=0):
    return Meeting(weekday=weekday, start_minute=start, end_minute=end, weeks=weeks)


def test_conflict_respects_week_and_half_open_time_intervals():
    assert overlap(meeting(), meeting(start=500))
    assert not overlap(meeting(), meeting(start=570, end=660))
    assert not overlap(meeting(), meeting(weeks=(3, 4)))
    assert not overlap(meeting(), meeting(weekday=1))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"start": 600, "end": 500},
        {"weeks": ()},
        {"weeks": (0,)},
        {"weekday": 7},
    ],
)
def test_invalid_meetings_are_rejected(kwargs):
    with pytest.raises(ValidationError):
        meeting(**kwargs)


def test_scheduling_uses_selected_courses_and_preserves_registered_class(repository):
    result = suggest(repository, "demo-et", ScheduleRequest(semester="2026-1"))
    assert result["plans"]
    for plan in result["plans"]:
        classes = [Section.model_validate(c) for c in plan["classes"]]
        assert "MI101-A" in {c.section_id for c in classes}
        assert {c.course_code for c in classes} == {"ET101", "ET102", "MI101"}
        assert plan["total_credits"] == 10
        for i, a in enumerate(classes):
            assert all(not conflict(a, b) for b in classes[i + 1 :])


def test_full_closed_and_avoid_days_are_respected(repository):
    # Ghi fixture bằng kết nối riêng của test; repository runtime vẫn chỉ đọc.
    with sqlite3.connect(repository.path) as db:
        db.execute("UPDATE sections SET enrolled=capacity WHERE section_id='ET101-A'")
        db.execute("UPDATE sections SET is_open=0 WHERE section_id='ET101-B'")
    result = suggest(repository, "demo-et", ScheduleRequest(semester="2026-1"))
    assert result["reason"] == "no_available_class"
    assert result["course_code"] == "ET101"


def test_no_plan_and_search_budget_are_not_reported_as_valid_schedule(repository):
    result = suggest(
        repository, "demo-et", ScheduleRequest(semester="2026-1", avoid_weekdays=[0, 1])
    )
    assert result["plans"] == []
    limited = suggest(repository, "demo-et", ScheduleRequest(semester="2026-1"), max_nodes=1)
    assert limited["reason"] == "search_limit"
    assert limited["search_truncated"] is True


def test_duplicate_courses_rejected():
    with pytest.raises(ValidationError):
        ScheduleRequest(semester="2026-1", course_codes=["ET101", "ET101"])


def test_seed_refuses_to_overwrite_existing_database(repository):
    from scripts.seed_demo import seed

    before = repository.path.read_bytes()
    with pytest.raises(FileExistsError):
        seed(repository.path)
    assert repository.path.read_bytes() == before


def test_loading_sections_uses_two_selects_in_one_transaction(repository, monkeypatch):
    from contextlib import contextmanager

    statements = []
    original = repository.connection

    @contextmanager
    def tracked():
        with original() as db:
            db.set_trace_callback(statements.append)
            yield db

    monkeypatch.setattr(repository, "connection", tracked)
    sections = repository._sections("s.semester=? ORDER BY s.section_id", ("2026-1",))
    assert len(sections) > 2
    assert all(section.meetings for section in sections)
    selects = [sql for sql in statements if sql.strip().upper().startswith("SELECT")]
    assert len(selects) == 2
    assert any(sql.strip().upper() == "BEGIN" for sql in statements)
