"""Tests for src.task — Task envelope round-trip."""

import json

from src.task import Task, TaskStatus, TaskType


def test_task_assigns_id_and_created_at():
    t = Task(prompt="do something")
    assert t.id != ""
    assert t.created_at != ""
    assert t.status == TaskStatus.PENDING
    assert t.type == TaskType.GENERAL


def test_to_json_round_trip(tmp_path):
    t = Task(prompt="research the thing", type=TaskType.RESEARCH)
    path = tmp_path / "task.json"
    t.save(path)

    loaded = Task.from_file(path)
    assert loaded.id == t.id
    assert loaded.prompt == t.prompt
    assert loaded.type == TaskType.RESEARCH
    assert loaded.status == TaskStatus.PENDING


def test_from_file_ignores_unknown_fields(tmp_path):
    path = tmp_path / "task.json"
    data = {
        "prompt": "hello",
        "type": "general",
        "id": "abc123",
        "status": "pending",
        "created_at": "2026-06-13T00:00:00",
        "completed_at": "",
        "result": "",
        "error": "",
        "model_used": "",
        "output_file": "",
        "some_future_field": "ignored",
    }
    path.write_text(json.dumps(data), encoding="utf-8")

    t = Task.from_file(path)
    assert t.id == "abc123"
    assert t.prompt == "hello"


def test_to_json_is_valid_json():
    t = Task(prompt="x")
    data = json.loads(t.to_json())
    assert data["prompt"] == "x"
    assert "id" in data
