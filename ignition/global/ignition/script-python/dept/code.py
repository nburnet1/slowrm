from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, Integer, String, Text, ForeignKey, select
from sqlalchemy.orm import relationship
from slowrm import Session, create_all, drop_all

Base = declarative_base()


class Department(Base):
    __tablename__ = "departments"

    id = Column(Integer, primary_key=True)
    name = Column(String(255))

    employees = relationship("Employee", cascade="all, delete-orphan")

    def __repr__(self):
        return "Department(id={}, name={!r})".format(self.id, self.name)


class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True)
    department_id = Column(Integer, ForeignKey(Department.id))
    name = Column(String(255))
    role = Column(String(100))

    tasks = relationship("Task", cascade="all, delete-orphan")

    def __repr__(self):
        return "Employee(id={}, name={!r}, role={!r})".format(self.id, self.name, self.role)


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey(Employee.id))
    title = Column(String(255))
    status = Column(String(50))

    def __repr__(self):
        return "Task(id={}, title={!r}, status={!r})".format(self.id, self.title, self.status)