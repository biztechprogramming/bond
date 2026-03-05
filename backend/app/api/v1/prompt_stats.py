from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from bond.backend.app.db.models.prompt_usage import PromptUsage
from bond.backend.app.db.session import get_db
from sqlalchemy.orm import Session

router = APIRouter(prefix="/prompt-stats", tags=["prompts"])

@router.get("/usage")
async def get_prompt_usage_stats(db: Session = Depends(get_db)):
    try:
        # Group by prompt_path and count occurrences
        stmt = select(
            PromptUsage.prompt_path,
            func.count(PromptUsage.id).label("usage_count")
        ).group_by(PromptUsage.prompt_path).order_by(func.count(PromptUsage.id).desc())
        
        results = db.execute(stmt).all()
        return [{"prompt_path": r.prompt_path, "count": r.usage_count} for r in results]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
