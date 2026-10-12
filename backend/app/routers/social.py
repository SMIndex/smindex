import re
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, desc, func

from app.db.database import get_session_factory
from app.db.models import User
from app.db.social_models import Post, Comment, PostLike
from app.routers.auth import get_authenticated_user, get_optional_user
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/social", tags=["social"])

# Rate limiting (in-memory)
_rate_limits: dict[int, list[float]] = {}
POST_LIMIT = 5  # max posts per minute
COMMENT_LIMIT = 20  # max comments per minute


def _strip_html(text: str) -> str:
    """Strip HTML tags from user content."""
    return re.sub(r'<[^>]+>', '', text).strip()


def _check_rate(user_id: int, limit: int) -> bool:
    now = time.time()
    if user_id not in _rate_limits:
        _rate_limits[user_id] = []
    _rate_limits[user_id] = [t for t in _rate_limits[user_id] if now - t < 60]
    if len(_rate_limits[user_id]) >= limit:
        return False
    _rate_limits[user_id].append(now)
    return True


class CreatePostRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=500)
    market_id: int | None = None
    position_snapshot: dict | None = None


class CreateCommentRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=200)


@router.post("/posts")
async def create_post(
    req: CreatePostRequest,
    user: User = Depends(get_authenticated_user),
) -> dict:
    if not _check_rate(user.id, POST_LIMIT):
        raise HTTPException(status_code=429, detail="Rate limit: max 5 posts per minute")

    sanitized_content = _strip_html(req.content)
    if not sanitized_content:
        raise HTTPException(status_code=400, detail="Content cannot be empty after sanitization")

    async_session = get_session_factory()
    async with async_session() as session:
        post = Post(
            user_id=user.id,
            content=sanitized_content,
            market_id=req.market_id,
            position_snapshot=req.position_snapshot,
            created_at=datetime.utcnow(),
        )
        session.add(post)
        await session.commit()
        await session.refresh(post)

    logger.info("New post: user=%d post=%d", user.id, post.id)

    result = {
        "id": post.id,
        "user_id": post.user_id,
        "wallet_address": user.wallet_address,
        "content": post.content,
        "market_id": post.market_id,
        "position_snapshot": post.position_snapshot,
        "likes_count": 0,
        "comments_count": 0,
        "created_at": post.created_at.isoformat(),
        "liked_by_me": False,
    }

    # Broadcast to WS
    from app.ws.client_feed import client_manager
    await client_manager.broadcast("social_feed", {"type": "new_post", "post": result})

    return result


@router.get("/feed")
async def get_feed(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    market_id: int | None = None,
    user: User | None = Depends(get_optional_user),
) -> list[dict]:
    async_session = get_session_factory()
    async with async_session() as session:
        stmt = select(Post, User.wallet_address).join(User, Post.user_id == User.id)
        if market_id:
            stmt = stmt.where(Post.market_id == market_id)
        stmt = stmt.order_by(desc(Post.created_at)).offset(skip).limit(limit)
        result = await session.execute(stmt)
        rows = result.all()

        # Get liked post IDs for current user
        liked_post_ids: set[int] = set()
        if user:
            post_ids = [post.id for post, _ in rows]
            if post_ids:
                likes_result = await session.execute(
                    select(PostLike.post_id).where(
                        PostLike.user_id == user.id,
                        PostLike.post_id.in_(post_ids),
                    )
                )
                liked_post_ids = {row[0] for row in likes_result.all()}

    return [
        {
            "id": post.id,
            "user_id": post.user_id,
            "wallet_address": wallet,
            "content": post.content,
            "market_id": post.market_id,
            "position_snapshot": post.position_snapshot,
            "likes_count": post.likes_count,
            "comments_count": post.comments_count,
            "created_at": post.created_at.isoformat() if post.created_at else None,
            "liked_by_me": post.id in liked_post_ids,
        }
        for post, wallet in rows
    ]


@router.post("/posts/{post_id}/comment")
async def add_comment(
    post_id: int,
    req: CreateCommentRequest,
    user: User = Depends(get_authenticated_user),
) -> dict:
    if not _check_rate(user.id, COMMENT_LIMIT):
        raise HTTPException(status_code=429, detail="Rate limit: max 20 comments per minute")

    sanitized_content = _strip_html(req.content)
    if not sanitized_content:
        raise HTTPException(status_code=400, detail="Comment cannot be empty after sanitization")

    async_session = get_session_factory()
    async with async_session() as session:
        post = await session.get(Post, post_id)
        if not post:
            raise HTTPException(status_code=404, detail="Post not found")

        comment = Comment(
            post_id=post_id,
            user_id=user.id,
            content=sanitized_content,
            created_at=datetime.utcnow(),
        )
        session.add(comment)
        post.comments_count = (post.comments_count or 0) + 1
        await session.commit()
        await session.refresh(comment)

    return {
        "id": comment.id,
        "post_id": post_id,
        "user_id": user.id,
        "wallet_address": user.wallet_address,
        "content": comment.content,
        "created_at": comment.created_at.isoformat(),
    }


@router.get("/posts/{post_id}/comments")
async def get_comments(post_id: int) -> list[dict]:
    async_session = get_session_factory()
    async with async_session() as session:
        stmt = (
            select(Comment, User.wallet_address)
            .join(User, Comment.user_id == User.id)
            .where(Comment.post_id == post_id)
            .order_by(Comment.created_at)
        )
        result = await session.execute(stmt)
        rows = result.all()

    return [
        {
            "id": c.id,
            "post_id": c.post_id,
            "user_id": c.user_id,
            "wallet_address": wallet,
            "content": c.content,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c, wallet in rows
    ]


@router.post("/posts/{post_id}/like")
async def like_post(
    post_id: int,
    user: User = Depends(get_authenticated_user),
) -> dict:
    async_session = get_session_factory()
    async with async_session() as session:
        post = await session.get(Post, post_id)
        if not post:
            raise HTTPException(status_code=404, detail="Post not found")

        existing = await session.execute(
            select(PostLike).where(PostLike.post_id == post_id, PostLike.user_id == user.id)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Already liked")

        like = PostLike(post_id=post_id, user_id=user.id, created_at=datetime.utcnow())
        session.add(like)
        post.likes_count = (post.likes_count or 0) + 1
        try:
            await session.commit()
        except Exception:
            raise HTTPException(status_code=409, detail="Already liked")

    return {"success": True, "likes_count": post.likes_count}


@router.delete("/posts/{post_id}/like")
async def unlike_post(
    post_id: int,
    user: User = Depends(get_authenticated_user),
) -> dict:
    async_session = get_session_factory()
    async with async_session() as session:
        result = await session.execute(
            select(PostLike).where(PostLike.post_id == post_id, PostLike.user_id == user.id)
        )
        like = result.scalar_one_or_none()
        if not like:
            raise HTTPException(status_code=404, detail="Not liked")

        await session.delete(like)
        post = await session.get(Post, post_id)
        if post:
            post.likes_count = max((post.likes_count or 0) - 1, 0)
        await session.commit()

    return {"success": True, "likes_count": post.likes_count if post else 0}
