from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship
import base

class WorkOrder(base.Base):
    __tablename__ = "work_orders"
    id = Column(Integer, primary_key=True)
    title = Column(String(255))
    status = Column(String(50))
    assignedTo = Column("assigned_to", String(255))

    line_items = relationship(lineitem.LineItem, cascade="all, delete-orphan")