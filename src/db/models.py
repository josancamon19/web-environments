"""Peewee ORM models for the web-envs database."""

import json
from typing import Any, Dict

from peewee import (
    AutoField,
    BlobField,
    CharField,
    FloatField,
    ForeignKeyField,
    IntegerField,
    Model,
    SqliteDatabase,
    TextField,
)


# Database instance - will be initialized by Database class
db = SqliteDatabase(None)


class BaseModel(Model):
    """Base model that all models inherit from."""

    class Meta:
        database = db


class TaskModel(BaseModel):
    """ORM model for tasks table."""

    id = AutoField()
    description = TextField()
    task_type = CharField()
    source = CharField()
    website = CharField(null=True)
    answer = TextField(null=True)
    video_path = TextField(null=True)
    created_at = CharField()
    ended_at = CharField(null=True)
    duration_seconds = FloatField(null=True)
    environment_fingerprint = TextField(null=True)

    class Meta:
        table_name = "tasks"


class StepModel(BaseModel):
    """ORM model for steps table."""

    id = AutoField()
    task = ForeignKeyField(TaskModel, backref="steps", on_delete="CASCADE")
    timestamp = CharField()
    event_type = CharField()
    event_data = TextField(null=True)
    dom_snapshot = TextField(null=True)
    dom_snapshot_metadata = TextField(null=True)
    screenshot_path = TextField(null=True)

    class Meta:
        table_name = "steps"

    @property
    def event_data_json(self) -> Dict[str, Any]:
        """Parse event_data as JSON. Returns empty dict if parsing fails."""
        if not self.event_data:
            return {}
        try:
            return json.loads(self.event_data)
        except json.JSONDecodeError:
            return {}


class RequestModel(BaseModel):
    """ORM model for requests table."""

    id = AutoField()
    task = ForeignKeyField(TaskModel, backref="requests", on_delete="CASCADE")
    step = ForeignKeyField(
        StepModel, backref="requests", null=True, on_delete="SET NULL"
    )
    request_uid = TextField(null=True)
    url = TextField(null=True)
    method = TextField(null=True)
    headers = TextField(null=True)
    post_data = TextField(null=True)
    cookies = TextField(null=True)
    timestamp = CharField()

    class Meta:
        table_name = "requests"


class ResponseModel(BaseModel):
    """ORM model for responses table."""

    id = AutoField()
    task = ForeignKeyField(TaskModel, backref="responses", on_delete="CASCADE")
    request = ForeignKeyField(
        RequestModel, backref="response", null=True, on_delete="SET NULL"
    )
    status = IntegerField(null=True)
    headers = TextField(null=True)
    body = BlobField(null=True)
    timestamp = CharField()

    class Meta:
        table_name = "responses"


# List of all models for easy iteration
ALL_MODELS = [TaskModel, StepModel, RequestModel, ResponseModel]
