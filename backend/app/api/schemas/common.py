"""Shared schema building blocks."""

from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict

from app.core.time import as_utc

# Serialized as ISO 8601 with an explicit UTC offset, whatever the database returns.
UtcDatetime = Annotated[datetime, AfterValidator(as_utc)]


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
