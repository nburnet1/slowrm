from enum import Enum
from sqlalchemy import Column, Integer, Enum as SAEnum
from sqlalchemy.ext.declarative import declarative_base
from slowrm import Session, sync_schema

Base = declarative_base()

class Color(Enum):
    black = 'black'
    white = 'white'
    navy = 'navy'
    red = 'red'

class Size(Enum):
    small = 'S'
    medium = 'M'
    large = 'L'
    xlarge = 'XL'

class Shirt(Base):
    __tablename__ = "shirts"
    id = Column(Integer, primary_key=True)
    color = Column(SAEnum(Color), nullable=False)
    size = Column(SAEnum(Size), nullable=False)