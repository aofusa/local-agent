from furry_agent import coding, sandbox, writing
from furry_agent.sandbox import PYTHON, RUST


def test_revise_applies_edits_without_rewriting():
    revision = writing.Revision.model_validate({"notes": ["語尾"], "edits": [
        {"find": "歩いた", "replace": "駆けた"}, {"find": "存在しない文", "replace": "x"}]})
    text, notes, applied = writing.apply_edits("猫が歩いた。犬が見ていた。", revision)
    assert text == "猫が駆けた。犬が見ていた。" and notes == ["語尾"] and applied == 1
    assert writing.apply_edits("そのまま", None) == ("そのまま", [], 0)


def test_continuation_input_carries_the_draft_tail():
    artifact = writing.new_artifact("小説")
    artifact["draft"] = "あ" * 3000 + "最後の一文。"
    text = writing.draft_input(artifact, "続きを書いて", continuation=True)
    assert "これまでの本文の末尾" in text and text.count("あ") < 2100 and "最後の一文。" in text


def test_outline_fallback_and_long_chapters():
    brief, outline, open_ = writing.outline_from(None, "猫の話", False)
    assert outline == [{"id": "s1", "title": "", "beats": ["猫の話"]}] and brief["language"] == "ja"
    parsed = writing.Outline.model_validate({"outline": [{"title": f"章{i}"} for i in range(12)], "open": ["年号"]})
    _, outline, open_ = writing.outline_from(parsed, "長編", True)
    assert len(outline) == 8 and open_ == ["年号"]


def test_scene_excerpt_is_short():
    assert len(writing.scene_excerpt("段落1\n\n" + "長い" * 400)) <= 300


def test_parse_plan_files_command_and_setup():
    text = """SPEC: fizzbuzz
FILE: main.py
```python
print('fizz')
```
FILE: requirements.txt
```text
rich
```
COMMAND: python main.py --n 15
"""
    plan = coding.parse_plan(text, PYTHON)
    assert [f["path"] for f in plan.files] == ["main.py", "requirements.txt"]
    assert plan.command == ["python", "main.py", "--n", "15"]
    assert plan.setup[:4] == ["python", "-m", "pip", "install"] and not plan.problems


def test_parse_plan_refuses_shell_commands_and_bad_paths():
    text = "FILE: ../evil.py\n```python\nx\n```\nFILE: ok.py\n```python\nprint(1)\n```\nCOMMAND: python ok.py; rm -rf /\n"
    plan = coding.parse_plan(text, PYTHON)
    assert [f["path"] for f in plan.files] == ["ok.py"]
    assert plan.command == list(PYTHON.default_command) and len(plan.problems) == 2


def test_fix_keeps_previous_files_unless_replaced():
    previous = [{"path": "main.py", "content": "old\n"}, {"path": "util.py", "content": "u\n"}]
    plan = coding.parse_plan("FILE: main.py\n```python\nnew\n```\nCOMMAND: python main.py", PYTHON, previous)
    assert {f["path"]: f["content"] for f in plan.files} == {"main.py": "new\n", "util.py": "u\n"}


def test_rust_defaults():
    plan = coding.parse_plan("FILE: Cargo.toml\n```toml\n[package]\n```\nFILE: src/main.rs\n```rust\nfn main(){}\n```", RUST)
    assert plan.command == ["cargo", "run", "--quiet", "--offline"] and plan.setup == ["cargo", "fetch"]


def test_network_only_when_asked():
    assert coding.wants_network("requests を pip install して使って")
    assert coding.wants_network("依存関係を入れて")
    assert not coding.wants_network("素数を数えるコードを書いて実行して")


def test_fix_input_contains_the_failure():
    code = {"files": [{"path": "main.py", "content": "x", "language": "python"}], "command": ["python", "main.py"],
            "last_exit": 1, "stdout_tail": "out", "stderr_tail": "Traceback", "timed_out": False}
    text = coding.fix_input("依頼", code)
    assert "終了コード 1" in text and "Traceback" in text and "main.py" in text
    assert sandbox.MAX_RUNS == 2
