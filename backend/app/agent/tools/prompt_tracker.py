from bond.backend.app.db.session import SessionLocal
from bond.backend.app.db.models.prompt_usage import PromptUsage
import logging

logger = logging.getLogger(__name__)

def track_prompt_usage(prompt_path: str, category: str = None, conversation_id: str = None, usage_metadata: dict = None):
    """
    Logs the usage of a specific prompt fragment to the database.
    """
    db = SessionLocal()
    try:
        usage = PromptUsage(
            prompt_path=prompt_path,
            category=category,
            conversation_id=conversation_id,
            usage_metadata=usage_metadata
        )
        db.add(usage)
        db.commit()
    except Exception as e:
        logger.error(f"Failed to track prompt usage for {prompt_path}: {e}")
        db.rollback()
    finally:
        db.close()
