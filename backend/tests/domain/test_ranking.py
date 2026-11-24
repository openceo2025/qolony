from app.tms_core.domain.ranking import RankEntry, apply_publication_rule


def _entry(user_id: str, value: float) -> RankEntry:
    return RankEntry(UserId=user_id, UserName=user_id, Value=value)


def test_publication_rule_hides_tie_group_when_it_crosses_top_half_boundary() -> None:
    rows = apply_publication_rule(
        [_entry("a", 100), _entry("b", 100), _entry("c", 100), _entry("d", 0)],
        self_user_id="d",
        publish_all=True,
    )

    assert [(row.UserId, row.Rank, row.IsPublished) for row in rows] == [
        ("a", 1, False),
        ("b", 1, False),
        ("c", 1, False),
        ("d", 4, False),
    ]


def test_publication_rule_keeps_whole_tie_group_when_it_fits_top_half() -> None:
    rows = apply_publication_rule(
        [_entry("a", 100), _entry("b", 100), _entry("c", 50), _entry("d", 0)],
        self_user_id="d",
        publish_all=True,
    )

    assert [(row.UserId, row.Rank, row.IsPublished) for row in rows] == [
        ("a", 1, True),
        ("b", 1, True),
        ("c", 3, False),
        ("d", 4, False),
    ]


def test_publication_rule_keeps_self_row_but_marks_hidden_when_self_is_not_public() -> None:
    rows = apply_publication_rule(
        [_entry("a", 100), _entry("b", 100), _entry("c", 100), _entry("d", 0)],
        self_user_id="d",
        publish_all=False,
    )

    assert [(row.UserId, row.Rank, row.IsSelf, row.IsPublished) for row in rows] == [
        ("d", 4, True, False),
    ]


def test_publication_rule_publishes_only_groups_that_end_before_boundary() -> None:
    rows = apply_publication_rule(
        [_entry("a", 100), _entry("b", 90), _entry("c", 90), _entry("d", 10)],
        self_user_id="d",
        publish_all=True,
    )

    assert [(row.UserId, row.Rank, row.IsPublished) for row in rows] == [
        ("a", 1, True),
        ("b", 2, False),
        ("c", 2, False),
        ("d", 4, False),
    ]
