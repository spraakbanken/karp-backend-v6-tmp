from pathlib import Path
from karp.domain.models.resource import Resource
from typing import Optional, Dict, List, Tuple, Any
import json
import collections
import logging
from datetime import datetime, timezone

import fastjsonschema  # pyre-ignore
from sb_json_tools import jsondiff
import json_streams

# from karp.resourcemgr import get_resource
# from .resource import Resource


from karp.errors import (
    KarpError,
    ClientErrorCodes,
    EntryNotFoundError,
    UpdateConflict,
    EntryIdMismatch,
)

from karp.domain import commands, model, errors
from karp.domain.models.entry import Entry

from karp.utility import unique_id
from . import context

# from karp.domain.services import indexing

# from karp.database import db
# import karp.indexmgr as indexmgr
# import karp.resourcemgr.entrymetadata as entrymetadata
# from karp.application import ctx

# from karp.infrastructure.unit_of_work import unit_of_work


_logger = logging.getLogger("karp")


# def get_entries_by_column(resource_obj: Resource, filters):
#     config = resource_obj.config
#     cls = resource_obj.model
#     query = cls.query
#
#     joined_filters = []
#     simple_filters = {}
#
#     for filter_key in filters.keys():
#         tmp = collections.defaultdict(dict)
#         if filter_key in config["referenceable"] and config["fields"][filter_key].get(
#             "collection", False
#         ):
#             child_cls = cls.child_tables[filter_key]
#             tmp[child_cls.__tablename__][filter_key] = filters[filter_key]
#         else:
#             simple_filters[filter_key] = filters[filter_key]
#         joined_filters.extend(tmp.values())
#
#     query = query.filter_by(**simple_filters)
#
#     for child_filters in joined_filters:
#         child_cls = cls.child_tables[list(child_filters.keys())[0]]
#         query = query.join(child_cls).filter_by(**child_filters)
#
#     return [
#         {
#             "id": db_entry.id,
#             "entry_id": db_entry.entry_id,
#             "entry": json.loads(db_entry.body),
#         }
#         for db_entry in query
#     ]
#
#
# def get_entry(resource_id: str, entry_id: str, version: Optional[int] = None):
#     resource = get_resource(resource_id, version=version)
#     return get_entry_by_entry_id(resource, entry_id)
#
#
# def get_entry_by_entry_id(resource: Resource, entry_id: str):
#     cls = resource.model
#     return cls.query.filter_by(entry_id=entry_id).first()
#
#


def add_entry(
    cmd: commands.AddEntry,
    ctx: context.Context,
) -> Entry:
    print(f"event_handlers.add_entry: cmd = {cmd}")
    with ctx.resource_uow:
        resource = ctx.resource_uow.resources.by_resource_id(cmd.resource_id)

    if not resource:
        raise errors.ResourceNotFound(cmd.resource_id)

    try:
        entry_id = resource.id_getter()(cmd.entry)
    except KeyError as err:
        raise errors.MissingIdField(
            resource_id=cmd.resource_id, entry=cmd.entry
        ) from err
    validate_entry = _compile_schema(resource.entry_json_schema)

    with ctx.entry_uows.get(cmd.resource_id) as uw:
        try:
            existing_entry = uw.repo.by_entry_id(entry_id)
            if (
                existing_entry
                and not existing_entry.discarded
                and existing_entry.id != cmd.id
            ):
                raise errors.IntegrityError(
                    f"An entry with entry_id '{entry_id}' already exists."
                )
        except errors.EntryNotFound:
            pass

        _validate_entry(validate_entry, cmd.entry)

        entry = resource.create_entry_from_dict(
            cmd.entry, user=cmd.user, message=cmd.message, entity_id=cmd.id
        )
        uw.entries.put(entry)
        uw.commit()
    return entry


def add_entry_tmp(cmd: commands.AddEntry, ctx: context.Context):
    with ctx.entry_uows.get(cmd.resource_id) as uow:
        # resource = uow.repo.by_resource_id(cmd.resource_id)
        # with unit_of_work(using=resource.entry_repository) as uow2:
        existing_entry = uow.repo.by_entry_id(cmd.entry_id)
        if (
            existing_entry
            and not existing_entry.discarded
            and existing_entry.id != cmd.id
        ):
            raise errors.IntegrityError(
                f"An entry with entry_id '{cmd.entry_id}' already exists."
            )
        entry = model.Entry(
            entity_id=cmd.id,
            entry_id=cmd.entry_id,
            resource_id=cmd.resource_id,
            body=cmd.body,
            message=cmd.message,
            last_modified=cmd.timestamp,
            last_modified_by=cmd.user,
        )
        # uow2.repo.put(entry)
        # uow2.commit()
        uow.repo.put(entry)
        uow.commit()


#
# def preview_entry(resource_id, entry, resource_version=None):
#     resource = get_resource(resource_id, version=resource_version)
#     entry_json = _validate_and_prepare_entry(resource, entry)
#     return entry_json


def update_entry(
    cmd: commands.UpdateEntry,
    ctx: context.Context,
):
    with ctx.resource_uow:
        resource = ctx.resource_uow.repo.by_resource_id(cmd.resource_id)
    #     resource = get_resource(resource_id, version=resource_version)

    if not resource:
        raise errors.ResourceNotFound(cmd.resource_id)
    schema = _compile_schema(resource.entry_json_schema)
    _validate_entry(schema, cmd.entry)

    with ctx.entry_uows.get(cmd.resource_id) as uw:
        current_db_entry = uw.repo.by_entry_id(cmd.entry_id)

        if not current_db_entry:
            raise errors.EntryNotFound(
                cmd.resource_id,
                cmd.entry_id,
                entry_version=cmd.version,
                resource_version=resource.version,
            )

        diff = jsondiff.compare(current_db_entry.body, cmd.entry)
        if not diff:
            raise KarpError("No changes made", ClientErrorCodes.ENTRY_NOT_CHANGED)

        #     db_entry_json = json.dumps(entry)
        #     db_id = current_db_entry.id
        #     latest_history_entry = (
        #         resource.history_model.query.filter_by(entry_id=db_id)
        #         .order_by(resource.history_model.version.desc())
        #         .first()
        #     )
        print(f"({current_db_entry.version}, {cmd.version})")
        if not cmd.force and current_db_entry.version != cmd.version:
            print("version conflict")
            raise errors.UpdateConflict(diff)

        id_getter = resource.id_getter()
        new_entry_id = id_getter(cmd.entry)

        current_db_entry.body = cmd.entry
        current_db_entry.stamp(cmd.user, message=cmd.message, timestamp=cmd.timestamp)
        if new_entry_id != cmd.entry_id:
            current_db_entry.entry_id = new_entry_id
            uw.repo.move(current_db_entry, old_entry_id=cmd.entry_id)
        else:
            uw.repo.update(current_db_entry)
        uw.commit()
    # if resource.is_published:
    #     if new_entry_id != entry_id:
    #         ctx.search_service.delete_entry(resource, entry_id=entry_id)
    #     indexing.add_entries(
    #         ctx.resource_repo, ctx.search_service, resource, [current_db_entry]
    #     )

    # return current_db_entry.entry_id


def update_entries(*args, **kwargs):
    return []


def add_entries_from_file(
    resource_id: str, version: int, filename: Path
) -> List[Entry]:
    return add_entries(
        resource_id,
        json_streams.load_from_file(filename),
        user_id="local_admin",
        resource_version=version,
    )


def add_entries(
    cmd: commands.AddEntries,
    ctx: context.Context,
) -> List[Entry]:
    """
    Add entries to DB and INDEX (if present and resource is active).

    Raises
    ------
    RuntimeError
        If the resource.entry_json_schema fails to compile.
    KarpError
        - If an entry fails to be validated against the json schema.
        - If the DB interaction fails.

    Returns
    -------
    List
        List of the id's of the created entries.
    """
    print(f"event_handlers.add_entries: cmd = {cmd}")

    if not isinstance(cmd.resource_id, str):
        raise ValueError(
            f"'resource_id' must be of type 'str', were '{type(cmd.resource_id)}'"
        )
    with ctx.resource_uow:
        resource = ctx.resource_uow.resources.by_resource_id(cmd.resource_id)

    if not resource:
        raise errors.ResourceNotFound(cmd.resource_id)
    # resource = get_resource(resource_id, version=resource_version)
    # resource_conf = resource.config

    validate_entry = _compile_schema(resource.entry_json_schema)

    created_db_entries = []
    with ctx.entry_uows.get(cmd.resource_id) as uw:
        for entry_raw in cmd.entries:
            _validate_entry(validate_entry, entry_raw)

            entry = resource.create_entry_from_dict(
                entry_raw,
                user=cmd.user,
                message=cmd.message,
                entity_id=unique_id.make_unique_id(),
            )
            uw.entries.put(entry)
            created_db_entries.append(entry)
        uw.commit()

    # if resource.is_published:
    #     print(f"services.entries.add_entries: indexing entries ...")
    #     indexing.add_entries(
    #         ctx.resource_repo,
    #         ctx.search_service,
    #         resource,
    #         created_db_entries,
    #     )

    return created_db_entries


# def add_entries(
#     resource_id: str,
#     entries: List[Dict],
#     user_id: str,
#     message: str = None,
#     resource_version: int = None,
# ):
#     """
#     Add entries to DB and INDEX (if present and resource is active).
#
#     Raises
#     ------
#     RuntimeError
#         If the resource.entry_json_schema fails to compile.
#     KarpError
#         - If an entry fails to be validated against the json schema.
#         - If the DB interaction fails.
#
#     Returns
#     -------
#     List
#         List of the id's of the created entries.
#     """
#     resource = get_resource(resource_id, version=resource_version)
#     resource_conf = resource.config
#
#     validate_entry = _compile_schema(resource.entry_json_schema)
#
#     try:
#         created_db_entries = []
#         for entry in entries:
#             _validate_entry(validate_entry, entry)
#
#             entry_json = json.dumps(entry)
#             db_entry = _src_entry_to_db_entry(
#                 entry, entry_json, resource.model, resource_conf
#             )
#             created_db_entries.append((db_entry, entry, entry_json))
#             db.session.add(db_entry)
#
#         db.session.commit()
#
#         created_history_entries = []
#         for db_entry, entry, entry_json in created_db_entries:
#             history_entry = resource.history_model(
#                 entry_id=db_entry.id,
#                 user_id=user_id,
#                 body=entry_json,
#                 version=1,
#                 op="ADD",
#                 message=message,
#                 timestamp=datetime.now(timezone.utc).timestamp(),
#             )
#             created_history_entries.append((db_entry, entry, history_entry))
#             db.session.add(history_entry)
#         db.session.commit()
#     except sql_exception.IntegrityError as e:
#         _logger.exception("IntegrityError")
#         print("e = {e!r}".format(e=e))
#         print("e.orig.args = {e!r}".format(e=e.orig.args))
#         raise KarpError(
#             "Database error: {msg}".format(msg=e.orig.args),
#             ClientErrorCodes.DB_INTEGRITY_ERROR,
#         )
#     except sql_exception.SQLAlchemyError as e:
#         _logger.exception("Adding entries to DB failed.")
#         print("e = {e!r}".format(e=e))
#         raise KarpError(
#             "Database error: {msg}".format(msg=e.msg), ClientErrorCodes.DB_GENERAL_ERROR
#         )
#
#     if resource.active:
#         indexmgr.add_entries(
#             resource_id,
#             [
#                 (
#                     db_entry.entry_id,
#                     entrymetadata.EntryMetadata.init_from_model(history_entry),
#                     _src_entry_to_index_entry(resource, entry),
#                 )
#                 for db_entry, entry, history_entry in created_history_entries
#             ],
#         )
#
#     return [db_entry.entry_id for db_entry, _, _ in created_db_entries]
#
#
# def _src_entry_to_db_entry(entry, entry_json, resource_model, resource_conf):
#     kwargs = _src_entry_to_db_kwargs(entry, entry_json, resource_model, resource_conf)
#     db_entry = resource_model(**kwargs)
#     return db_entry
#
#
# def _src_entry_to_db_kwargs(entry, entry_json, resource_model, resource_conf):
#     kwargs = {"body": entry_json}
#
#     for field_name in resource_conf.get("referenceable", ()):
#         field_val = entry.get(field_name)
#         if resource_conf["fields"][field_name].get("collection", False):
#             child_table = resource_model.child_tables[field_name]
#             for elem in field_val:
#                 if field_name not in kwargs:
#                     kwargs[field_name] = []
#                 kwargs[field_name].append(child_table(**{field_name: elem}))
#         else:
#             if field_val:
#                 kwargs[field_name] = field_val
#     id_field = resource_conf.get("id")
#     if id_field:
#         kwargs["entry_id"] = entry[id_field]
#     else:
#         kwargs["entry_id"] = "TODO"  # generate id for resources that are missing it
#     return kwargs


# def delete_entry(resource_id: str, entry_id: str, user_id: str):
def delete_entry(cmd: commands.DeleteEntry, ctx: context.Context):
    # with ctx.resource_uow:
    #     resource = ctx.resource_uow.repo.by_resource_id(cmd.resource_id)

    with ctx.entry_uows.get_uow(cmd.resource_id) as uw:
        try:
            entry = uw.repo.by_entry_id(cmd.entry_id)

        #     resource = get_resource(resource_id)
        #     entry = resource.model.query.filter_by(entry_id=entry_id, deleted=False).first()
        except errors.EntryNotFound as err:
            raise errors.EntryNotFound(
                resource_id=cmd.resource_id,
                entry_id=cmd.entry_id,
            ) from err

        entry.discard(
            user=cmd.user,
            message=cmd.message,
            timestamp=cmd.timestamp,
        )
        assert entry.discarded
        uw.repo.update(entry)
        uw.commit()

    # ctx.search_service.delete_entry(resource, entry=entry)


# def delete_entry(resource_id: str, entry_id: str, user_id: str):
#     resource = get_resource(resource_id)
#     entry = resource.model.query.filter_by(entry_id=entry_id, deleted=False).first()
#     if not entry:
#         raise EntryNotFoundError(resource_id, entry_id)
#     entry.deleted = True
#     history_cls = resource.history_model
#     history_entry = history_cls(
#         entry_id=entry.id,
#         user_id=user_id,
#         op="DELETE",
#         version=-1,
#         timestamp=datetime.now(timezone.utc).timestamp(),
#     )
#     db.session.add(history_entry)
#     db.session.commit()
#     indexmgr.delete_entry(resource_id, entry.entry_id)
#
#
def _src_entry_to_index_entry(resource: Resource, src_entry: Entry) -> Dict:
    return indexing.transform_to_index_entry(
        ctx.resource_repo, ctx.search_service, resource, src_entry
    )


# def _src_entry_to_index_entry(resource: Resource, src_entry: Dict):
#     """
#     Make a "src entry" into an "index entry"
#     """
#     return indexmgr.transform_to_index_entry(
#         resource, src_entry, resource.config["fields"].items()
#     )
#
#
# def _validate_and_prepare_entry(resource, entry):
#     validate_entry = _compile_schema(resource.entry_json_schema)
#     _validate_entry(validate_entry, entry)
#     return _src_entry_to_index_entry(resource, entry)


def _compile_schema(json_schema):
    try:
        validate_entry = fastjsonschema.compile(json_schema)
        return validate_entry
    except fastjsonschema.JsonSchemaDefinitionException as e:
        raise RuntimeError(e)


def _validate_entry(schema, json_obj):
    try:
        schema(json_obj)
    except fastjsonschema.JsonSchemaException as e:
        _logger.warning(
            "Entry not valid:\n{entry}\nMessage: {message}".format(
                entry=json.dumps(json_obj, indent=2), message=e.message
            )
        )
        raise KarpError("entry not valid", ClientErrorCodes.ENTRY_NOT_VALID)
