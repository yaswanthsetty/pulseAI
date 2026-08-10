"""Pagination helpers matching the spec §19 envelope.

All list endpoints return::

    {"items": [...], "page": 1, "page_size": 20, "total": 4231}
"""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

MAX_PAGE_SIZE = 100

T = TypeVar("T")


class Page(BaseModel, Generic[T]):  # noqa: UP046 - keep runtime-friendly generics
    """Spec §19 paginated list envelope."""

    items: list[T]
    page: int
    page_size: int
    total: int


def paginate(
    db: Session,
    statement,
    page: int,
    page_size: int,
    to_model: type[BaseModel] | None = None,
) -> dict[str, Any]:
    """Apply offset/limit to *statement* and return the spec envelope.

    ``to_model`` (a Pydantic model with ``from_attributes``) converts ORM rows
    before returning, so FastAPI never has to serialize raw ORM objects.
    """
    page_size = min(max(page_size, 1), MAX_PAGE_SIZE)
    page = max(page, 1)

    count_stmt = select(func.count()).select_from(statement.order_by(None).subquery())
    total = db.execute(count_stmt).scalar_one()

    rows = db.execute(statement.offset((page - 1) * page_size).limit(page_size)).all()
    items = [row[0] for row in rows]
    if to_model is not None:
        items = [to_model.model_validate(item) for item in items]
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
    }
