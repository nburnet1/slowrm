# slowrm

A lightweight ORM for Ignition. Uses SQLAlchemy's expression language to build dialect-aware SQL and executes through Ignition's `system.db` with object persistence and transaction management.

## Install

```bash
pip install --target ./site-packages slowrm
```

## Getting Started

```python
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, Integer, String, select
from slowrm import Session, sync_schema

Base = declarative_base()

class WorkOrder(Base):
    __tablename__ = "work_orders"
    id = Column(Integer, primary_key=True)
    title = Column(String(255))
    status = Column(String(50))

sync_schema([WorkOrder], "MESDB")

with Session("MESDB") as session:
    wo = WorkOrder(title="Replace pump", status="open")
    session.add(wo)
    session.commit()
```

## Documentation

[https://nburnet1.github.io/slowrm](https://nburnet1.github.io/slowrm)
