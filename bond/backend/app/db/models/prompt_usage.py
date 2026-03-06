from sqlalchemy import Column, String, DateTime, Integer, JSON
from bond.backend.app.db.session import Base
from datetime import datetime

class PromptUsage(Base):
    __tablename__ = "prompt_usage"
    
    id = Column(Integer, primary_key=True, index=True)
    prompt_path = Column(String, index=True)
    category = Column(String, index=True)
    conversation_id = Column(String, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    usage_metadata = Column(JSON, nullable=True)
