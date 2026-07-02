from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
import base

class LineItem(base.Base):
    __tablename__ = "line_items"
    id = Column(Integer, primary_key=True)
    work_order_id = Column(Integer, ForeignKey("work_orders.id"))
    description = Column(String(255))

    work_order = relationship("WorkOrder")