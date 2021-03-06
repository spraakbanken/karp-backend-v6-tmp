from typing import Dict

from karp.domain import model
from karp.domain.models.entry import EntryOp, EntryStatus
from karp.domain.models.resource import ResourceOp

from karp.infrastructure.sql import db


class ResourceDTO(db.Base):
    __tablename__ = "resources"
    history_id = db.Column(db.Integer, primary_key=True)
    id = db.Column(db.UUIDType, nullable=False)
    resource_id = db.Column(db.String(32), nullable=False)
    version = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(64), nullable=False)
    config = db.Column(db.NestedMutableJson, nullable=False)
    is_published = db.Column(db.Boolean, index=True, nullable=True, default=None)
    last_modified = db.Column(db.Float, nullable=False)
    last_modified_by = db.Column(db.String(100), nullable=False)
    message = db.Column(db.String(100), nullable=False)
    op = db.Column(db.Enum(ResourceOp), nullable=False)
    # entry_json_schema = db.Column(db.Text, nullable=False)
    discarded = db.Column(db.Boolean, default=False)
    __table_args__ = (
        db.UniqueConstraint(
            "resource_id", "version", name="resource_version_unique_constraint"
        ),
        # mysql_character_set="utf8mb4",
        # TODO only one resource can be active, but several can be inactive
        #    here is how to do it in MariaDB, unclear whether this is possible using SQLAlchemy
        #    `virtual_column` char(0) as (if(active,'', NULL)) persistent
        #    and
        #    UNIQUE KEY `resource_version_unique_active` (`resource_id`,`virtual_column`)
        #    this works because the tuple (saldo, NULL) is not equal to (saldo, NULL)
    )

    def __repr__(self):
        return """<ResourceDTO(
                    history_id={},
                    id={},
                    resource_id={},
                    version={},
                    name={},
                    config={},
                    is_published={},
                    last_modified={},
                    last_modified_by={},
                ) > """.format(
            self.history_id,
            self.id,
            self.resource_id,
            self.version,
            self.name,
            self.config,
            self.is_published,
            self.last_modified,
            self.last_modified_by,
            self.discarded,
        )

    def to_entity(self) -> model.Resource:
        return model.Resource(
            entity_id=self.id,
            resource_id=self.resource_id,
            version=self.version,
            name=self.name,
            config=self.config,
            is_published=self.is_published,
            last_modified=self.last_modified,
            last_modified_by=self.last_modified_by,
            discarded=self.discarded,
            message=self.message,
        )

    @staticmethod
    def from_entity(resource: model.Resource) -> "ResourceDTO":
        return ResourceDTO(
            history_id=None,
            id=resource.id,
            resource_id=resource.resource_id,
            version=resource.version,
            name=resource.name,
            config=resource.config,
            is_published=resource.is_published,
            last_modified=resource.last_modified,
            last_modified_by=resource.last_modified_by,
            message=resource.message,
            op=resource.op,
            discarded=resource.discarded,
        )


class BaseRuntimeEntry:
    entry_id = db.Column(
        # db.String(100, collation="utf8mb4_swedish_ci"), primary_key=True
        db.String(100),
        primary_key=True,
    )
    history_id = db.Column(db.Integer, nullable=False)
    id = db.Column(db.UUIDType, nullable=False)
    discarded = db.Column(db.Boolean, nullable=False)


class BaseHistoryEntry:
    history_id = db.Column(db.Integer, primary_key=True)
    id = db.Column(db.UUIDType, nullable=False)
    entry_id = db.Column(db.String(100), nullable=False)
    version = db.Column(db.Integer, nullable=False)
    last_modified = db.Column(db.Float(53), nullable=False)
    last_modified_by = db.Column(db.String(100), nullable=False)
    body = db.Column(db.JSON, nullable=False)
    status = db.Column(db.Enum(EntryStatus), nullable=False)
    message = db.Column(db.Text(length=120))
    op = db.Column(db.Enum(EntryOp), nullable=False)
    discarded = db.Column(db.Boolean, default=False)

    @classmethod
    @db.declared_attr
    def __table_args__(cls):
        return db.UniqueConstraint("id", "version", name="id_version_unique_constraint")

    @classmethod
    def from_entity(cls, entry: model.Entry):
        return cls(
            history_id=None,
            id=entry.id,
            entry_id=entry.entry_id,
            version=entry.version,
            last_modified=entry.last_modified,
            last_modified_by=entry.last_modified_by,
            body=entry.body,
            status=entry.status,
            message=entry.message,
            op=entry.op,
            discarded=entry.discarded,
        )


# Dynamic models


def get_or_create_entry_history_model(resource_id: str) -> BaseHistoryEntry:
    history_table_name = create_history_table_name(resource_id)
    if history_table_name in class_cache:
        history_model = class_cache[history_table_name]
        # history_model.__table__.create(bind=db.engine, checkfirst=True)
        return history_model

    attributes = {
        "__tablename__": history_table_name,
        "__table_args__": None,  # (BaseHistoryEntry.__table_args__,),
        # "mysql_character_set": "utf8mb4",
    }

    sqlalchemy_class = type(history_table_name, (db.Base, BaseHistoryEntry), attributes)
    # sqlalchemy_class.__table__.create(bind=db.engine, checkfirst=True)
    class_cache[history_table_name] = sqlalchemy_class
    return sqlalchemy_class


def get_or_create_entry_runtime_model(
    resource_id: str, history_model: db.Table, config: Dict
) -> BaseRuntimeEntry:
    table_name = create_runtime_table_name(resource_id)

    if table_name in class_cache:
        runtime_model = class_cache[table_name]
        # runtime_model.__table__.create(bind=db.engine, checkfirst=True)
        # for child_model in runtime_model.child_tables.values():
        #     child_model.__table__.create(bind=db.engine, checkfirst=True)
        return runtime_model

    foreign_key_constraint = db.ForeignKeyConstraint(
        ["history_id"], [f"{history_model.__tablename__}.history_id"]
    )

    attributes = {
        "__tablename__": table_name,
        "__table_args__": (foreign_key_constraint,),
    }
    child_tables = {}

    for field_name in config.get("referenceable", ()):
        field = config["fields"][field_name]

        if not field.get("collection"):
            if field["type"] == "integer":
                column_type = db.Integer()
            elif field["type"] == "number":
                column_type = db.Float()
            elif field["type"] == "boolean":
                column_type = db.Boolean()
            elif field["type"] == "string":
                column_type = db.String(128)
            else:
                raise NotImplementedError()
            attributes[field_name] = db.Column(column_type)
        else:
            child_table_name = f"{table_name}_{field_name}"
            attributes[field_name] = db.relationship(
                child_table_name,
                backref=table_name,
                cascade="save-update,merge,delete,delete-orphan",
            )
            child_attributes = {
                "__tablename__": child_table_name,
                "__table_args__": (db.PrimaryKeyConstraint("entry_id", field_name),),
                "entry_id": db.Column(
                    db.String(100), db.ForeignKey(f"{table_name}.entry_id")
                ),
            }
            if field["type"] == "object":
                raise ValueError("not possible to reference lists of objects")
            if field["type"] == "integer":
                child_db_column_type = db.Integer()
            elif field["type"] == "number":
                child_db_column_type = db.Float()
            elif field["type"] == "string":
                child_db_column_type = db.String(100)
            else:
                raise NotImplementedError()
            child_attributes[field_name] = db.Column(child_db_column_type)
            child_class = type(child_table_name, (db.Base,), child_attributes)
            child_tables[field_name] = child_class

    sqlalchemy_class = type(
        table_name,
        (db.Base, BaseRuntimeEntry),
        attributes,
    )
    # sqlalchemy_class.__table__.create(bind=db.engine, checkfirst=True)
    sqlalchemy_class.child_tables = child_tables

    # for child_model in sqlalchemy_class.child_tables.values():
    #     child_model.__table__.create(bind=db.engine, checkfirst=True)
    class_cache[table_name] = sqlalchemy_class

    return sqlalchemy_class


class_cache = {}


# Helpers


def create_runtime_table_name(resource_id: str) -> str:
    return f"runtime_{resource_id}"


def create_history_table_name(resource_id: str) -> str:
    return resource_id
