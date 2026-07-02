"""slowrm - A lightweight ORM for Ignition.

Uses SQLAlchemy Core and declarative models to define schemas and generate
dialect-aware SQL, then executes through Ignition's system.db API with
object persistence semantics.

Usage:

    from sqlalchemy.ext.declarative import declarative_base
    from sqlalchemy import Column, Integer, String, select, bindparam
    from slowrm import Session, create_all

    Base = declarative_base()

    class WorkOrder(Base):
        __tablename__ = "work_orders"
        id = Column(Integer, primary_key=True)
        title = Column(String(255))
        status = Column(String(50))

    create_all([WorkOrder], "MESDB")

    with Session("MESDB") as uow:
        # Create
        wo = WorkOrder(title="Replace filter", status="open")
        uow.add(wo)

        # Read
        wo = uow.get(WorkOrder, 42)

        # Update - just mutate the object
        wo.status = "complete"

        # Delete
        uow.delete(wo)

        # Flush and commit
        uow.commit()
"""

from sqlalchemy import select
from sqlalchemy.dialects import postgresql, mysql, mssql, sqlite
from sqlalchemy.inspection import inspect
from sqlalchemy.schema import CreateTable, CreateIndex
from java.lang import Exception as JavaException
import system


# ---------------------------------------------------------------------------
# Dialect detection
# ---------------------------------------------------------------------------

def _dialect_for_database(database):
    """Resolve the SQLAlchemy dialect for an Ignition datasource."""
    info = system.db.getConnectionInfo(database)
    db_type = _extract_db_type(info)

    if db_type == "POSTGRES":
        dia = postgresql.dialect()
    elif db_type == "MYSQL":
        dia = mysql.dialect()
    elif db_type == "MSSQL":
        dia = mssql.dialect()
    elif db_type == "SQLITE":
        dia = sqlite.dialect()
    else:
        raise ValueError("Unsupported datasource type: {}".format(db_type))

    dia.paramstyle = "qmark"
    dia.positional = True
    return dia, db_type


def _extract_db_type(info):
    """Extract the datasource type from system.db.getConnectionInfo()."""
    if isinstance(info, (list, tuple)):
        if info and isinstance(info[0], (list, tuple)):
            return str(info[0][2]).upper()
        if len(info) > 2:
            return str(info[2]).upper()

    if hasattr(info, "getColumnNames"):
        cols = [str(c).lower() for c in info.getColumnNames()]
        if info.rowCount > 0:
            row = info[0]
            for key in ("type", "dbtype", "database type"):
                if key in cols:
                    return str(row[cols.index(key)]).upper()

    text = str(info).upper()
    for candidate in ("POSTGRES", "MYSQL", "MSSQL", "SQLITE"):
        if candidate in text:
            return candidate

    raise ValueError("Could not determine datasource type from connection info")


# ---------------------------------------------------------------------------
# Compile
# ---------------------------------------------------------------------------

def compile(stmt, params=None, database=None, dialect=None):
    # type: (object, dict, str, object) -> tuple
    """Compile a SQLAlchemy statement into (sql, ordered_params).

    Args:
        stmt: A SQLAlchemy Core statement.
        params: Optional dict of bind parameters.
        database: Optional Ignition datasource name to resolve dialect.
        dialect: Optional explicit SQLAlchemy dialect instance.

    Returns:
        Tuple of (sql_string, params_list) for system.db.runPrepQuery/Update.
    """
    if dialect is not None:
        dia = dialect
    elif database:
        dia, _ = _dialect_for_database(database)
    else:
        dia = postgresql.dialect()
        dia.paramstyle = "qmark"
        dia.positional = True

    compiled = stmt.compile(
        dialect=dia,
        compile_kwargs={"render_postcompile": True}
    )

    sql = str(compiled)
    params = params or {}

    positiontup = getattr(compiled, "positiontup", None)
    if positiontup:
        ordered_params = [params.get(k, compiled.params.get(k)) for k in positiontup]
    else:
        if compiled.params:
            ordered_params = [params.get(k, v) for k, v in compiled.params.items()]
        else:
            ordered_params = []

    return sql, ordered_params


# ---------------------------------------------------------------------------
# DB execution wrappers (unwrap Java exceptions)
# ---------------------------------------------------------------------------

def _raise_java_cause(error):
    if error.cause:
        raise error.cause
    raise error


def _run_query(sql, database):
    try:
        return system.db.runQuery(sql, database)
    except JavaException as error:
        _raise_java_cause(error)


def _run_prep_query(sql, params, database):
    try:
        return system.db.runPrepQuery(sql, params, database)
    except JavaException as error:
        _raise_java_cause(error)


def _run_update_query(sql, database):
    try:
        return system.db.runUpdateQuery(sql, database)
    except JavaException as error:
        _raise_java_cause(error)


def _run_prep_update(sql, params, database):
    try:
        return system.db.runPrepUpdate(sql, params, database)
    except JavaException as error:
        _raise_java_cause(error)


def _run_prep_query_tx(sql, params, database, tx):
    try:
        return system.db.runPrepQuery(sql, params, database=database, tx=tx)
    except JavaException as error:
        _raise_java_cause(error)


def _run_prep_update_tx(sql, params, database, tx):
    try:
        return system.db.runPrepUpdate(sql, params, database=database, tx=tx)
    except JavaException as error:
        _raise_java_cause(error)


def _run_prep_update_tx_key(sql, params, database, tx):
    try:
        return system.db.runPrepUpdate(sql, params, database=database, tx=tx, getKey=1)
    except JavaException as error:
        _raise_java_cause(error)


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def create_all(models, database):
    # type: (list, str) -> list[str]
    """Create tables for the given model classes if they don't already exist."""
    dia, db_type = _dialect_for_database(database)
    existing = _get_existing_tables(database, db_type)
    created = []

    for model in models:
        table = model.__table__
        table_name = table.name

        if table_name.lower() in existing:
            continue

        ddl = str(CreateTable(table).compile(dialect=dia))
        _run_update_query(ddl, database)
        created.append(table_name)

        for index in table.indexes:
            idx_ddl = str(CreateIndex(index).compile(dialect=dia))
            _run_update_query(idx_ddl, database)

    return created


def drop_all(models, database):
    # type: (list, str) -> list[str]
    """Drop tables for the given model classes."""
    _, db_type = _dialect_for_database(database)
    existing = _get_existing_tables(database, db_type)
    dropped = []

    for model in models:
        table_name = model.__table__.name
        if table_name.lower() in existing:
            if db_type == "SQLITE":
                sql = "DROP TABLE IF EXISTS {}".format(table_name)
            else:
                sql = "DROP TABLE IF EXISTS {} CASCADE".format(table_name)
            _run_update_query(sql, database)
            dropped.append(table_name)

    return dropped


def sync_schema(models, database):
    # type: (list, str) -> list[str]
    """Sync model definitions to the database schema.

    For each model:
    - If the table doesn't exist, create it (same as create_all).
    - If the table exists, add any columns that are in the model but missing
      from the database.

    Does NOT drop columns, rename columns, or change column types.

    Args:
        models: List of SQLAlchemy declarative model classes.
        database: Ignition database connection name.

    Returns:
        List of actions taken (e.g. "created table x", "added column x.y").

    Example:
        from slowrm import sync_schema
        actions = sync_schema([WorkOrder, LineItem], "MESDB")
        for action in actions:
            print(action)
    """
    from sqlalchemy.schema import CreateColumn, CreateTable, CreateIndex

    dia, db_type = _dialect_for_database(database)
    existing_tables = _get_existing_tables(database, db_type)
    actions = []

    for model in models:
        table = model.__table__
        table_name = table.name

        if table_name.lower() not in existing_tables:
            # Table doesn't exist - create it
            ddl = str(CreateTable(table).compile(dialect=dia))
            _run_update_query(ddl, database)
            actions.append("created table {}".format(table_name))

            for index in table.indexes:
                idx_ddl = str(CreateIndex(index).compile(dialect=dia))
                _run_update_query(idx_ddl, database)
                actions.append("created index on {}".format(table_name))
        else:
            # Table exists - check for missing columns
            existing_cols = _get_existing_columns(database, db_type, table_name)

            for col in table.columns:
                if col.name.lower() in existing_cols:
                    continue

                # Generate ALTER TABLE ADD COLUMN
                col_ddl = str(CreateColumn(col).compile(dialect=dia)).strip()
                alter_sql = "ALTER TABLE {} ADD COLUMN {}".format(table_name, col_ddl)
                _run_update_query(alter_sql, database)
                actions.append("added column {}.{}".format(table_name, col.name))

    return actions


def _get_existing_columns(database, db_type, table_name):
    """Get set of existing column names (lowercase) for a table."""
    if db_type == "POSTGRES":
        sql = (
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = '{}' AND table_schema = 'public'"
        ).format(table_name)
    elif db_type == "MYSQL":
        sql = (
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = '{}' AND table_schema = DATABASE()"
        ).format(table_name)
    elif db_type == "MSSQL":
        sql = (
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = '{}'"
        ).format(table_name)
    elif db_type == "SQLITE":
        sql = "PRAGMA table_info('{}')".format(table_name)
    else:
        raise ValueError("Unsupported datasource type: {}".format(db_type))

    results = _run_query(sql, database)

    if db_type == "SQLITE":
        # PRAGMA table_info returns: cid, name, type, notnull, dflt_value, pk
        return set(row["name"].lower() for row in results)
    else:
        return set(row["column_name"].lower() for row in results)


def _get_existing_tables(database, db_type):
    if db_type == "POSTGRES":
        sql = "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    elif db_type == "MYSQL":
        sql = "SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()"
    elif db_type == "MSSQL":
        sql = "SELECT table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE'"
    elif db_type == "SQLITE":
        sql = "SELECT name AS table_name FROM sqlite_master WHERE type = 'table'"
    else:
        raise ValueError("Unsupported datasource type: {}".format(db_type))

    results = _run_query(sql, database)
    return set(row["table_name"].lower() for row in results)


# ---------------------------------------------------------------------------
# Object state helpers
# ---------------------------------------------------------------------------

def _pk_columns(model):
    """Get primary key column objects for a model class."""
    mapper = inspect(model)
    return list(mapper.primary_key)


def _pk_values(instance):
    """Get primary key values from an instance."""
    pk_cols = _pk_columns(instance.__class__)
    return tuple(getattr(instance, col.key, None) for col in pk_cols)


def _identity_key(instance):
    """Return (ModelClass, pk_tuple) or None if PK is incomplete."""
    pk = _pk_values(instance)
    if None in pk:
        return None
    return (instance.__class__, pk)


def _snapshot(instance):
    """Capture current column values as a frozen dict keyed by DB column name."""
    mapper = inspect(instance.__class__)
    return {attr.columns[0].name: getattr(instance, attr.key, None) for attr in mapper.column_attrs}


def _dirty_columns(instance, original):
    """Compare current values to snapshot, return dict of changed columns keyed by DB column name."""
    mapper = inspect(instance.__class__)
    changed = {}
    for attr in mapper.column_attrs:
        col_name = attr.columns[0].name
        current_value = getattr(instance, attr.key, None)
        if original.get(col_name) != current_value:
            changed[col_name] = current_value
    return changed


def _columns_dict(instance, skip_none_pk=True):
    """Get all column values from an instance for INSERT.

    Uses the actual database column name (col.name) as key,
    not the Python attribute name, so table.insert().values(**dict) works.
    """
    mapper = inspect(instance.__class__)
    values = {}
    for attr in mapper.column_attrs:
        key = attr.key  # Python attribute name
        col = attr.columns[0]
        col_name = col.name  # actual DB column name
        value = getattr(instance, key, None)

        if skip_none_pk and col.primary_key and value is None:
            continue

        if value is None and col.default is not None:
            default_arg = getattr(col.default, "arg", None)
            if callable(default_arg):
                value = default_arg(None)
            else:
                value = default_arg

        values[col_name] = value
    return values


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class Session(object):
    """A lightweight ORM session for Ignition.

    Provides object persistence (add/get/delete), dirty tracking,
    and transaction management over Ignition's system.db.

    Args:
        database: Ignition database connection name.
        isolation: Optional transaction isolation level.
        timeout: Optional transaction timeout in milliseconds.
        transactional: If False, skip transaction management.
        autocommit: If True, commit on clean context exit.

    Example:
        with Session("MESDB") as uow:
            wo = WorkOrder(title="Fix pump", status="open")
            uow.add(wo)
            uow.commit()
    """

    def __init__(self, database="", isolation=None, timeout=None,
                 transactional=True, autocommit=False):
        # type: (str, int, int, bool, bool) -> None
        self.database = database
        self.dialect, self.db_type = _dialect_for_database(database)
        self.isolation = isolation
        self.timeout = timeout
        self.transactional = transactional
        self.autocommit = autocommit
        self.tx = None
        self.txId = None
        self._closed = False
        self._committed = False

        # Object tracking
        self._new = []
        self._deleted = []
        self._identity_map = {}
        self._snapshots = {}

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None:
                self.rollback()
            elif self.tx is not None:
                if self.autocommit and not self._committed:
                    self.commit()
                elif not self._committed:
                    self.rollback()
        finally:
            self.close()

    def _ensure_open(self):
        if self._closed:
            raise ValueError("Session is closed")
        if not self.transactional:
            return
        if self.tx is None:
            kwargs = {}
            if self.isolation is not None:
                kwargs["isolationLevel"] = self.isolation
            if self.timeout is not None:
                kwargs["timeout"] = self.timeout
            self.tx = system.db.beginTransaction(self.database, **kwargs)
            self.txId = self.tx
            self._committed = False

    # ------------------------------------------------------------------
    # ORM methods
    # ------------------------------------------------------------------

    def add(self, instance):
        # type: (object | list) -> None
        """Mark instance(s) for INSERT on next flush/commit.

        Accepts a single instance or a list.

        Example:
            session.add(WorkOrder(title="New", status="open"))
            session.add([LineItem(desc="A"), LineItem(desc="B")])
        """
        if self._closed:
            raise ValueError("Session is closed")
        if isinstance(instance, (list, tuple)):
            for item in instance:
                if item not in self._new:
                    self._new.append(item)
        else:
            if instance not in self._new:
                self._new.append(instance)

    def get(self, model, pk, include=None):
        # type: (type, int | tuple, list) -> object | None
        """Load an instance by primary key.

        Returns the cached instance from the identity map if already loaded,
        otherwise queries the database.

        Args:
            model: The model class.
            pk: Primary key value (int or tuple for composite).
            include: Optional list of relationships to eager-load.

        Example:
            wo = session.get(WorkOrder, 42)
            wo = session.get(WorkOrder, 42, include=[WorkOrder.line_items])
        """
        if not isinstance(pk, tuple):
            pk = (pk,)

        key = (model, pk)
        if key in self._identity_map:
            instance = self._identity_map[key]
            if include:
                self.load(instance, include)
            return instance

        pk_cols = _pk_columns(model)
        if len(pk_cols) != len(pk):
            raise ValueError("Expected {} PK values, got {}".format(
                len(pk_cols), len(pk)))

        table = model.__table__
        stmt = select([model])
        for i, col in enumerate(pk_cols):
            stmt = stmt.where(getattr(table.c, col.key) == pk[i])

        self._ensure_open()
        sql, ordered_params = self.compile(stmt)

        if self.transactional:
            results = _run_prep_query_tx(sql, ordered_params, self.database, self.tx)
        else:
            results = _run_prep_query(sql, ordered_params, self.database)

        cols = results.getColumnNames() if hasattr(results, 'getColumnNames') else []
        for row in results:
            row_dict = {col: row[col] for col in cols}
            instance = self._materialize(model, row_dict)
            if include:
                self.load(instance, include)
            return instance

        return None

    def delete(self, instance):
        # type: (object | list) -> None
        """Mark instance(s) for DELETE on next flush/commit.

        Accepts a single instance or a list.

        Example:
            session.delete(wo)
            session.delete([li1, li2, li3])
        """
        if self._closed:
            raise ValueError("Session is closed")
        if isinstance(instance, (list, tuple)):
            for item in instance:
                if item not in self._deleted:
                    self._deleted.append(item)
        else:
            if instance not in self._deleted:
                self._deleted.append(instance)

    def merge(self, instance):
        # type: (object | list) -> object | list
        """Merge instance(s) into the session.

        If the primary key exists in the database, update the existing row
        with the instance's current values. If not, insert it as new.

        Accepts a single instance or a list.

        Returns the managed instance(s).

        Example:
            session.merge(WorkOrder(id=42, title="Updated", status="complete"))
            session.merge([LineItem(id=1, desc="A"), LineItem(id=2, desc="B")])
        """
        if self._closed:
            raise ValueError("Session is closed")

        if isinstance(instance, (list, tuple)):
            return [self._merge_one(item) for item in instance]
        return self._merge_one(instance)

    def _merge_one(self, instance):
        """Merge a single instance."""
        pk_cols = _pk_columns(instance.__class__)
        pk = tuple(getattr(instance, col.key, None) for col in pk_cols)

        # If PK is incomplete, treat as new insert
        if None in pk:
            self.add(instance)
            return instance

        # Check identity map first
        key = (instance.__class__, pk)
        if key in self._identity_map:
            existing = self._identity_map[key]
            mapper = inspect(instance.__class__)
            for attr in mapper.column_attrs:
                col = attr.columns[0]
                if col.primary_key:
                    continue
                new_value = getattr(instance, attr.key, None)
                setattr(existing, attr.key, new_value)
            return existing

        # Try loading from database
        existing = self.get(instance.__class__, pk if len(pk) > 1 else pk[0])
        if existing is not None:
            mapper = inspect(instance.__class__)
            for attr in mapper.column_attrs:
                col = attr.columns[0]
                if col.primary_key:
                    continue
                new_value = getattr(instance, attr.key, None)
                setattr(existing, attr.key, new_value)
            return existing

        # Doesn't exist - insert
        self.add(instance)
        return instance

    # ------------------------------------------------------------------
    # Low-level query/execute (escape hatches)
    # ------------------------------------------------------------------

    def compile(self, stmt, params=None):
        # type: (object, dict) -> tuple
        """Compile a statement using this session's datasource dialect."""
        return compile(stmt, params=params, dialect=self.dialect)

    def query(self, stmt, params=None, model=None, as_dict=False, as_dataset=False, include=None):
        # type: (object, dict, type, bool, bool, list) -> list
        """Execute a SELECT statement.

        Args:
            stmt: A SQLAlchemy select statement.
            params: Optional bind params dict.
            model: Optional model class to materialize rows into.
            as_dict: Return rows as dictionaries.
            as_dataset: Return raw Ignition dataset.
            include: Optional list of relationships to eager-load on results.
        """
        self._ensure_open()
        sql, ordered_params = self.compile(stmt, params)

        if self.transactional:
            results = _run_prep_query_tx(sql, ordered_params, self.database, self.tx)
        else:
            results = _run_prep_query(sql, ordered_params, self.database)

        if as_dataset:
            return results

        rows = self._rows_as_dicts(results)

        if model is not None:
            instances = [self._materialize(model, row) for row in rows]
            if include:
                for instance in instances:
                    self.load(instance, include)
            return instances

        if not as_dict:
            inferred = self._infer_model(stmt)
            if inferred is not None:
                instances = [self._materialize(inferred, row) for row in rows]
                if include:
                    for instance in instances:
                        self.load(instance, include)
                return instances

        return rows

    def query_one(self, stmt, params=None, model=None, as_dict=False, as_dataset=False, include=None):
        # type: (object, dict, type, bool, bool, list) -> object | dict | None
        """Execute a SELECT and return the first row or None."""
        result = self.query(
            stmt.limit(1),
            params=params,
            model=model,
            as_dict=as_dict,
            as_dataset=as_dataset,
            include=include,
        )
        if as_dataset:
            return result
        return result[0] if result else None

    def execute(self, stmt, params=None):
        # type: (object, dict) -> int
        """Execute an INSERT/UPDATE/DELETE statement directly.

        Returns number of affected rows.
        """
        self._ensure_open()
        sql, ordered_params = self.compile(stmt, params)
        if self.transactional:
            return _run_prep_update_tx(sql, ordered_params, self.database, self.tx)
        return _run_prep_update(sql, ordered_params, self.database)

    def execute_many(self, stmt, param_list):
        # type: (object, list[dict]) -> int
        """Execute a bulk INSERT/UPDATE/DELETE in a single database call.

        Builds a single multi-row statement and sends all parameters at once.

        Args:
            stmt: A SQLAlchemy insert/update/delete statement with bindparams.
            param_list: List of parameter dicts.

        Returns:
            Number of affected rows.
        """
        if not param_list:
            return 0

        self._ensure_open()

        # Get column keys from first param set
        keys = list(param_list[0].keys())

        # Flatten all params into one ordered list
        all_params = []
        for params in param_list:
            for key in keys:
                all_params.append(params.get(key))

        # Build multi-row VALUES clause
        # e.g. INSERT INTO t (a, b) VALUES (?, ?), (?, ?), (?, ?)
        compiled = stmt.compile(
            dialect=self.dialect,
            compile_kwargs={"render_postcompile": True}
        )
        single_sql = str(compiled)

        # Detect if this is an INSERT with VALUES
        upper_sql = single_sql.upper()
        if "INSERT" in upper_sql and "VALUES" in upper_sql:
            # Extract the base INSERT ... VALUES portion
            values_idx = single_sql.upper().index("VALUES")
            base_sql = single_sql[:values_idx + 6]  # "INSERT INTO ... VALUES"

            # Build placeholder row from param count
            row_placeholder = "({})".format(", ".join(["?"] * len(keys)))
            multi_sql = "{} {}".format(
                base_sql,
                ", ".join([row_placeholder] * len(param_list))
            )

            if self.transactional:
                return _run_prep_update_tx(multi_sql, all_params, self.database, self.tx)
            return _run_prep_update(multi_sql, all_params, self.database)
        else:
            # For UPDATE/DELETE, fall back to individual executions (can't multi-row these)
            total = 0
            for params in param_list:
                total += self.execute(stmt, params)
            return total

    # ------------------------------------------------------------------
    # Flush and lifecycle
    # ------------------------------------------------------------------

    def flush(self):
        # type: () -> None
        """Flush pending inserts, updates, and deletes to the database.

        Called automatically by commit().
        """
        self._ensure_open()
        self._flush_inserts()
        self._flush_updates()
        self._flush_deletes()

    def commit(self):
        # type: () -> None
        """Flush all changes and commit the transaction."""
        self._ensure_open()
        self.flush()

        if not self.transactional:
            self._committed = True
            return

        try:
            system.db.commitTransaction(self.tx)
            self._committed = True
        except JavaException as error:
            _raise_java_cause(error)
        finally:
            try:
                system.db.closeTransaction(self.tx)
            except JavaException:
                pass
            self.tx = None

    def rollback(self):
        # type: () -> None
        """Rollback the transaction and discard all pending changes."""
        self._new = []
        self._deleted = []
        self._identity_map = {}
        self._snapshots = {}

        if not self.transactional:
            self._committed = False
            return
        if self.tx is None:
            return

        try:
            system.db.rollbackTransaction(self.tx)
            self._committed = False
        except JavaException as error:
            _raise_java_cause(error)
        finally:
            try:
                system.db.closeTransaction(self.tx)
            except JavaException:
                pass
            self.tx = None

    def close(self):
        # type: () -> None
        """Close the session. Rolls back if not committed."""
        if self._closed:
            return
        if self.transactional and self.tx is not None:
            try:
                system.db.rollbackTransaction(self.tx)
            except JavaException:
                pass
            try:
                system.db.closeTransaction(self.tx)
            except JavaException:
                pass
            self.tx = None
        self._new = []
        self._deleted = []
        self._identity_map = {}
        self._snapshots = {}
        self._committed = False
        self._closed = True

    # ------------------------------------------------------------------
    # Internal: flush logic
    # ------------------------------------------------------------------

    def _flush_inserts(self):
        pending = list(self._new)
        self._new = []

        for instance in pending:
            table = instance.__table__
            values = _columns_dict(instance, skip_none_pk=True)

            stmt = table.insert().values(**values)
            sql, ordered_params = self.compile(stmt)

            pk_cols = _pk_columns(instance.__class__)
            has_auto_pk = (
                len(pk_cols) == 1 and
                getattr(instance, pk_cols[0].key, None) is None
            )

            if has_auto_pk:
                if self.transactional:
                    generated = _run_prep_update_tx_key(
                        sql, ordered_params, self.database, self.tx)
                else:
                    generated = system.db.runPrepUpdate(
                        sql, ordered_params, self.database, getKey=1)
                setattr(instance, pk_cols[0].key, generated)
            else:
                if self.transactional:
                    _run_prep_update_tx(sql, ordered_params, self.database, self.tx)
                else:
                    _run_prep_update(sql, ordered_params, self.database)

            self._register(instance)

    def _flush_updates(self):
        for key, instance in list(self._identity_map.items()):
            if instance in self._deleted:
                continue

            original = self._snapshots.get(id(instance))
            if original is None:
                continue

            changed = _dirty_columns(instance, original)
            if not changed:
                continue

            table = instance.__table__
            pk_cols = _pk_columns(instance.__class__)

            # Remove PK from changed set
            for col in pk_cols:
                changed.pop(col.name, None)

            if not changed:
                continue

            stmt = table.update()
            for col in pk_cols:
                stmt = stmt.where(
                    getattr(table.c, col.name) == getattr(instance, col.key))
            stmt = stmt.values(**changed)

            sql, ordered_params = self.compile(stmt)
            if self.transactional:
                _run_prep_update_tx(sql, ordered_params, self.database, self.tx)
            else:
                _run_prep_update(sql, ordered_params, self.database)

            # Re-snapshot
            self._snapshots[id(instance)] = _snapshot(instance)

    def _flush_deletes(self):
        pending = list(self._deleted)
        self._deleted = []

        # Cascade: collect children that need deleting first
        children_to_delete = []
        for instance in pending:
            children_to_delete.extend(self._cascade_collect(instance))

        # Delete children first (FK ordering)
        for child in children_to_delete:
            self._delete_instance(child)

        # Delete parents
        for instance in pending:
            self._delete_instance(instance)

    def _delete_instance(self, instance):
        """Execute DELETE for a single instance."""
        table = instance.__table__
        pk_cols = _pk_columns(instance.__class__)

        stmt = table.delete()
        for col in pk_cols:
            stmt = stmt.where(
                getattr(table.c, col.name) == getattr(instance, col.key))

        sql, ordered_params = self.compile(stmt)
        if self.transactional:
            _run_prep_update_tx(sql, ordered_params, self.database, self.tx)
        else:
            _run_prep_update(sql, ordered_params, self.database)

        # Remove from identity map
        ident_key = _identity_key(instance)
        if ident_key is not None:
            self._identity_map.pop(ident_key, None)
        self._snapshots.pop(id(instance), None)

    def _cascade_collect(self, instance):
        """Collect child instances that should be cascade-deleted."""
        children = []
        mapper = inspect(instance.__class__)

        for rel in mapper.relationships:
            cascade = rel.cascade

            # SQLAlchemy 1.3 CascadeOptions has boolean attrs: .delete, .save_update, etc.
            should_cascade = False
            if hasattr(cascade, "delete"):
                should_cascade = cascade.delete
            elif hasattr(cascade, '__iter__'):
                cascade_set = set(cascade)
                should_cascade = "delete" in cascade_set or "all" in cascade_set

            if not should_cascade:
                continue

            # Only cascade ONETOMANY (parent -> children)
            from sqlalchemy.orm.relationships import ONETOMANY
            if rel.direction is not ONETOMANY:
                continue

            # Load children if not already attached
            attr_value = instance.__dict__.get(rel.key, None)
            if attr_value is None:
                self.load(instance, rel.key)
                attr_value = instance.__dict__.get(rel.key, None)

            if attr_value:
                for child in attr_value:
                    children.append(child)
                    # Recursively cascade
                    children.extend(self._cascade_collect(child))

        return children

    # ------------------------------------------------------------------
    # Relationship loading
    # ------------------------------------------------------------------

    def load(self, instance, relationships):
        # type: (object, object | str | list) -> None
        """Eagerly load one or more relationships on an instance.

        Args:
            instance: A model instance (must be loaded/registered).
            relationships: A string, descriptor, or list of strings/descriptors.

        Example:
            session.load(wo, WorkOrder.line_items)
            session.load(wo, [WorkOrder.line_items, WorkOrder.comments])
            session.load(wo, "line_items")
        """
        if not isinstance(relationships, (list, tuple)):
            relationships = [relationships]

        mapper = inspect(instance.__class__)

        for rel_ref in relationships:
            # Resolve to relationship name string
            rel_name = self._resolve_rel_name(rel_ref)

            if rel_name not in mapper.relationships:
                raise ValueError("{} has no relationship '{}'".format(
                    instance.__class__.__name__, rel_name))

            rel = mapper.relationships[rel_name]
            target_model = rel.mapper.class_

            # Build WHERE clause from local/remote column pairs
            pairs = rel.local_remote_pairs
            stmt = select([target_model])

            for local_col, remote_col in pairs:
                local_value = getattr(instance, local_col.key)
                stmt = stmt.where(remote_col == local_value)

            # Execute
            self._ensure_open()
            sql, ordered_params = self.compile(stmt)
            if self.transactional:
                results = _run_prep_query_tx(sql, ordered_params, self.database, self.tx)
            else:
                results = _run_prep_query(sql, ordered_params, self.database)

            rows = self._rows_as_dicts(results)
            instances = [self._materialize(target_model, row) for row in rows]

            # Determine if collection or scalar
            from sqlalchemy.orm.relationships import ONETOMANY, MANYTOMANY
            if rel.direction in (ONETOMANY, MANYTOMANY):
                instance.__dict__[rel_name] = instances
            else:
                instance.__dict__[rel_name] = instances[0] if instances else None

    def _resolve_rel_name(self, rel_ref):
        """Resolve a relationship reference to its string name.

        Accepts:
            "line_items"           -> "line_items"
            WorkOrder.line_items   -> "line_items"
        """
        if isinstance(rel_ref, str):
            return rel_ref

        # Class-level descriptor: WorkOrder.line_items
        # This is an InstrumentedAttribute with a .key property
        if hasattr(rel_ref, "key"):
            return rel_ref.key

        # Could also be a property object
        if hasattr(rel_ref, "property") and hasattr(rel_ref.property, "key"):
            return rel_ref.property.key

        raise ValueError("Cannot resolve relationship from: {}".format(rel_ref))

    # ------------------------------------------------------------------
    # Internal: helpers
    # ------------------------------------------------------------------

    def _register(self, instance):
        """Register an instance in the identity map and snapshot it."""
        key = _identity_key(instance)
        if key is not None:
            self._identity_map[key] = instance
        self._snapshots[id(instance)] = _snapshot(instance)

    def _materialize(self, model, row_dict):
        """Create or retrieve a model instance from a row dict.

        Maps DB column names back to Python attribute names before constructing.
        """
        # Build column name -> attr key mapping
        mapped = self._db_to_attr(model, row_dict)

        pk_cols = _pk_columns(model)
        pk = tuple(mapped.get(col.key) for col in pk_cols)

        if None not in pk:
            key = (model, pk)
            if key in self._identity_map:
                return self._identity_map[key]

        instance = model(**mapped)
        self._register(instance)
        return instance

    def _db_to_attr(self, model, row_dict):
        """Map a dict keyed by DB column names to Python attribute names."""
        mapper = inspect(model)
        col_name_to_attr_key = {}
        for attr in mapper.column_attrs:
            col_name = attr.columns[0].name
            col_name_to_attr_key[col_name] = attr.key

        mapped = {}
        for db_name, value in row_dict.items():
            attr_key = col_name_to_attr_key.get(db_name, db_name)
            mapped[attr_key] = value
        return mapped

    def _rows_as_dicts(self, results):
        cols = results.getColumnNames() if hasattr(results, 'getColumnNames') else []
        rows = []
        for row in results:
            if cols:
                rows.append({col: row[col] for col in cols})
            else:
                rows.append(dict(row))
        return rows

    def _infer_model(self, stmt):
        """Best-effort model inference from a select([Model]) statement."""
        try:
            raw_columns = getattr(stmt, "_raw_columns", None) or []
            if len(raw_columns) == 1:
                column = raw_columns[0]
                if hasattr(column, "__table__") and hasattr(column, "__mapper__"):
                    return column
        except Exception:
            pass
        return None


