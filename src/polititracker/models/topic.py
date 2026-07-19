"""Topic tagging (build step 6; schema lands now).

Topic assignments are interpretive, not sourced fact — method and confidence
must always be displayed alongside them, never presented as part of the record.
"""

from sqlalchemy import BigInteger, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from polititracker.models.base import Base


class Topic(Base):
    __tablename__ = "topic"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    taxonomy: Mapped[str] = mapped_column(
        String(64), default="crs_policy_area", server_default="crs_policy_area"
    )


class StatementTopic(Base):
    __tablename__ = "statement_topic"

    statement_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("statement.id"), primary_key=True
    )
    topic_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("topic.id"), primary_key=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")
    method: Mapped[str] = mapped_column(String(64))  # bill_policy_area, classifier, manual, ...
