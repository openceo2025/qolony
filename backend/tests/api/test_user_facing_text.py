from app.api.user_facing_text import normalize_user_facing_text


def test_normalize_user_facing_text_repairs_mojibake() -> None:
    broken = "螳御ｺ・ち繧ｹ繧ｯ縺ｯ邱ｨ髮・〒縺阪∪縺帙ｓ"
    assert normalize_user_facing_text(broken) == "完了タスクは編集できません"


def test_normalize_user_facing_text_keeps_normal_text() -> None:
    message = "開始日は期限以前で指定してください"
    assert normalize_user_facing_text(message) == message
