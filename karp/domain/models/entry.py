"""Model for a lexical entry."""
import enum
import logging
from typing import Dict, Optional, List
import typing
from uuid import UUID

from karp.domain import constraints, events
from karp.domain.errors import ConfigurationError
from karp.domain.common import _now, _unknown_user
from karp.domain.models import event_handler
from karp.domain.models.entity import TimestampedVersionedEntity

from karp.utility import unique_id


logger = logging.getLogger("karp")


class EntryOp(enum.Enum):
    ADDED = "ADDED"
    DELETED = "DELETED"
    UPDATED = "UPDATED"


class EntryStatus(enum.Enum):
    IN_PROGRESS = "IN-PROGRESS"
    IN_REVIEW = "IN_REVIEW"
    OK = "OK"


class Entry(TimestampedVersionedEntity):
    def __init__(
        self,
        *,
        entry_id: str,
        body: Dict,
        message: str,
        resource_id: str,
        status: EntryStatus = EntryStatus.IN_PROGRESS,  # IN-PROGRESS, IN-REVIEW, OK, PUBLISHED
        op: EntryOp = EntryOp.ADDED,
        version: int = 1,
        **kwargs,
        # version: int = 0
    ):
        super().__init__(version=version, **kwargs)
        self._entry_id = entry_id
        self._body = body
        self._op = op
        self._message = "Entry added." if message is None else message
        self.resource_id = resource_id
        self._status = status
        self.resource_id = resource_id

    @property
    def entry_id(self):
        """The entry_id of this entry."""
        return self._entry_id

    @entry_id.setter
    def entry_id(self, entry_id: str):
        self._check_not_discarded()
        self._entry_id = constraints.length_gt_zero("entry_id", entry_id)

    @property
    def body(self):
        """The body of the entry."""
        return self._body

    @body.setter
    def body(self, body: Dict):
        self._check_not_discarded()
        self._body = body

    @property
    def op(self):
        """The latest operation of this entry."""
        return self._op

    @property
    def status(self):
        """The workflow status of this entry."""
        return self._status

    @status.setter
    def status(self, status: EntryStatus):
        """The workflow status of this entry."""
        self._check_not_discarded()
        self._status = status

    @property
    def message(self):
        """The message for the latest operation of this entry."""
        return self._message

    def discard(
        self,
        *,
        user: str,
        timestamp: float,
        message: str = None,
    ):
        self._check_not_discarded()
        self._op = EntryOp.DELETED
        self._message = message or "Entry deleted."
        self._discarded = True
        self._last_modified_by = user
        self._last_modified = timestamp
        self._version += 1
        self.queue_event(
            events.EntryDeleted(
                id=self.id,
                entry_id=self.entry_id,
                timestamp=self.last_modified,
                user=user,
                message=message,
                version=self.version,
                resource_id=self.resource_id,
            )
        )
        # event.mutate(self)
        # event_handler.publish(event)

    def stamp(
        self,
        user: str,
        *,
        message: str = None,
        timestamp: float = _now,
        increment_version: bool = True,
    ):
        super().stamp(user, timestamp=timestamp, increment_version=increment_version)
        self._message = message
        self._op = EntryOp.UPDATED
        self.queue_event(
            events.EntryUpdated(
                timestamp=self.last_modified,
                id=self.id,
                resource_id=self.resource_id,
                entry_id=self.entry_id,
                body=self.body,
                message=self.message,
                user=self.last_modified_by,
                version=self.version,
            )
        )

    def __repr__(self) -> str:
        return f"Entry(id={self._id}, entry_id={self._entry_id}, version={self.version}, last_modified={self._last_modified}, body={self.body})"


# === Factories ===
def create_entry(
    entry_id: str,
    body: Dict,
    *,
    entity_id: unique_id.UniqueId,
    resource_id: str,
    last_modified_by: str = None,
    message: Optional[str] = None,
    last_modified: typing.Optional[float] = None,
) -> Entry:
    if not isinstance(entry_id, str):
        entry_id = str(entry_id)
    entry = Entry(
        entry_id=entry_id,
        body=body,
        message="Entry added." if not message else message,
        status=EntryStatus.IN_PROGRESS,
        op=EntryOp.ADDED,
        # entity_id=unique_id.make_unique_id(),
        version=1,
        last_modified_by="Unknown user" if not last_modified_by else last_modified_by,
        resource_id=resource_id,
        entity_id=entity_id,
        last_modified=last_modified,
    )
    entry.queue_event(
        events.EntryAdded(
            resource_id=resource_id,
            id=entry.id,
            entry_id=entry.entry_id,
            body=entry.body,
            message=entry.message,
            user=entry.last_modified_by,
            timestamp=entry.last_modified,
        )
    )
    return entry


# === Repository ===


# class EntryRepositorySettings:
#     """Settings for an EntryRepository."""
#
#     pass
#
#
# @singledispatch
# def create_entry_repository(settings: EntryRepositorySettings) -> EntryRepository:
#     raise RuntimeError(f"Don't know how to handle {settings!r}")
