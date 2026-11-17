import hashlib
import json
import sqlite3
from contextlib import contextmanager
from itertools import count
from pathlib import Path

_SAVEPOINTS = count(1)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def connect(root, schema):
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(directory / "catalog.sqlite3", timeout=10)
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA foreign_keys=ON")
    database.executescript(schema)
    return database


@contextmanager
def atomic(database):
    """写事务。嵌套调用使用保存点，不会提交或回滚外层尚未完成的工作。"""
    if database.in_transaction:
        name = f"schoolmart_savepoint_{next(_SAVEPOINTS)}"
        database.execute(f"SAVEPOINT {name}")
        try:
            yield database
        except BaseException:
            database.execute(f"ROLLBACK TO {name}")
            database.execute(f"RELEASE {name}")
            raise
        else:
            database.execute(f"RELEASE {name}")
            return
    database.execute("BEGIN IMMEDIATE")
    try:
        yield database
        database.commit()
    except BaseException:
        database.rollback()
        raise


@contextmanager
def reading(database):
    """只读事务：一次查询内的多条语句看到同一版本，且不阻塞其他读者。"""
    if database.in_transaction:
        yield database
        return
    database.execute("BEGIN")
    try:
        yield database
    finally:
        database.rollback()


def records(database, sql, parameters=()):
    return [dict(row) for row in database.execute(sql, parameters)]


def require_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}需要非空字符串")
    return value
