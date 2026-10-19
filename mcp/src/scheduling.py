"""Backtracking hữu hạn: một lớp/học phần; giữ lịch đã đăng ký.

Kiểm tra giao nhau cả thứ, giờ và tuần. Hai buổi nối tiếp nhau không xung đột.
Không dùng LLM để tính toán. Đây là gợi ý lịch, chưa kiểm tra tiên quyết/học phí.
"""

from itertools import combinations

from src.models import Meeting, ScheduleRequest, Section


def overlap(a: Meeting, b: Meeting) -> bool:
    return (
        a.weekday == b.weekday
        and bool(a.weeks & b.weeks)
        and a.start_minute < b.end_minute
        and b.start_minute < a.end_minute
    )


def conflict(a: Section, b: Section) -> bool:
    return any(overlap(x, y) for x in a.meetings for y in b.meetings)


def internally_valid(section: Section) -> bool:
    return not any(overlap(a, b) for a, b in combinations(section.meetings, 2))


def fits_preferences(section: Section, request: ScheduleRequest) -> bool:
    """Sở thích áp dụng cho mọi buổi học của lớp mới."""
    return all(
        meeting.weekday not in request.avoid_weekdays
        and meeting.start_minute >= request.earliest_minute
        and meeting.end_minute <= request.latest_minute
        for meeting in section.meetings
    )


def suggest(repository, user_id, request: ScheduleRequest, max_nodes=10000):
    codes = request.course_codes
    if codes is None:
        codes = repository.selected_courses(user_id, request.semester)
    if not codes:
        return {"plans": [], "reason": "missing_courses", "hint": "Hãy cung cấp mã học phần."}
    if len(codes) > 8:
        return {"plans": [], "reason": "too_many_courses", "hint": "Mỗi lần tối đa 8 học phần."}
    fixed = repository.enrolled_classes(user_id, request.semester)
    if any(not internally_valid(s) for s in fixed) or any(
        conflict(a, b) or a.course_code == b.course_code for a, b in combinations(fixed, 2)
    ):
        return {"plans": [], "reason": "existing_schedule_inconsistent"}
    fixed_codes = {s.course_code for s in fixed}
    groups = []
    for code in codes:
        if code in fixed_codes:
            continue
        options = []
        for section in repository.candidates(code, request.semester):
            if not internally_valid(section) or not fits_preferences(section, request):
                continue
            if any(conflict(section, registered) for registered in fixed):
                continue
            options.append(section)
        if not options:
            return {"plans": [], "reason": "no_available_class", "course_code": code}
        groups.append(options)
    groups.sort(key=len)  # Môn ít lựa chọn trước để cắt nhánh sớm.
    plans, visited = [], 0
    exhausted = False

    def visit(index, chosen):
        nonlocal visited, exhausted
        if visited >= max_nodes:
            exhausted = True
            return
        visited += 1
        if len(plans) >= request.max_results:
            return
        if index == len(groups):
            sections = fixed + chosen
            plans.append(
                {
                    "classes": [section.model_dump(mode="json") for section in sections],
                    "total_credits": sum(section.credits for section in sections),
                }
            )
            return
        for candidate in groups[index]:
            if not any(conflict(candidate, s) for s in chosen):
                visit(index + 1, chosen + [candidate])
            if exhausted or len(plans) >= request.max_results:
                break

    visit(0, [])
    return {
        "plans": plans,
        "reason": "ok" if plans else ("search_limit" if exhausted else "no_conflict_free_schedule"),
        "search_truncated": exhausted or len(plans) >= request.max_results,
        "visited_nodes": visited,
        "ranking": "first_feasible_by_section_id",
        "note": "Giữ lớp đã đăng ký; sở thích chỉ áp dụng lớp mới. Chưa kiểm tra tiên quyết, "
        "giới hạn tín chỉ, di chuyển giữa cơ sở; chỗ trống có thể thay đổi.",
    }
