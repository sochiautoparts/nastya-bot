"""Inter-Bot Communication System v2.0 - GitHub-based state sync.

Настя ↔ Ася - межботовое взаимодействие через GitHub-hosted interbot_state.json.

Architecture:
  - Each bot has interbot_state.json in its own repo
  - Read other bot's state via raw.githubusercontent.com
  - Write own state via GitHub Contents API (PAT auth)
  - Настя as AI-Filter: reviews Ася's news candidates before publishing
  
Capabilities:
  1. AI-Filter: Настя reviews Ася's news candidates (approve/reject/improve)
  2. Inter-bot messaging: direct messages between bots
  3. Shared chat coordination: both bots in same chat discuss topics
"""
import json
import logging
import time
import asyncio
import httpx
from typing import List, Dict, Optional
from pathlib import Path

logger = logging.getLogger("nastya.interbot")

# GitHub repo details
NASTYA_REPO = "sochiautoparts/nastya-bot"
ASYA_REPO = "sochiautoparts/asya-bot"
INTERBOT_FILE = "interbot_state.json"

# URLs
NASTYA_RAW_URL = f"https://raw.githubusercontent.com/{NASTYA_REPO}/main/{INTERBOT_FILE}"
ASYA_RAW_URL = f"https://raw.githubusercontent.com/{ASYA_REPO}/main/{INTERBOT_FILE}"
NASTYA_API_URL = f"https://api.github.com/repos/{NASTYA_REPO}/contents/{INTERBOT_FILE}"

# Local cache
LOCAL_CACHE = Path("data/interbot_state.json")
REFRESH_INTERVAL = 30  # Check for updates every 30 seconds - faster reviews!


class InterbotManager:
    """Manages inter-bot communication between Настя and Ася."""

    def __init__(self):
        self._gh_pat: str = ""  # GitHub PAT, set during init
        self._own_state: Dict = {}
        self._other_state: Dict = {}
        self._last_own_sha: str = ""  # SHA for GitHub API update
        self._last_refresh: float = 0
        self._ai_router = None

    def configure(self, gh_pat: str = "", ai_router=None):
        """Configure with GitHub PAT and AI router."""
        self._gh_pat = gh_pat
        self._ai_router = ai_router
        logger.info(f"InterbotManager configured (PAT={'set' if gh_pat else 'missing'}, AI={'set' if ai_router else 'missing'})")

    async def init(self):
        """Initialize: load own state, fetch other bot's state."""
        # Try loading from GitHub first
        self._own_state = await self._fetch_state(NASTYA_RAW_URL)
        if not self._own_state:
            # Create initial state
            self._own_state = self._empty_state("nastya")
            await self._push_state()
        
        # Fetch Ася's state
        self._other_state = await self._fetch_state(ASYA_RAW_URL)
        if not self._other_state:
            self._other_state = self._empty_state("asya")
        
        logger.info(f"Interbot initialized: own_state has {len(self._own_state.get('pending_reviews', []))} pending reviews, "
                     f"other_state has {len(self._other_state.get('pending_reviews', []))} pending reviews")

    def _empty_state(self, bot_name: str) -> Dict:
        return {
            "bot": bot_name,
            "version": "1.0",
            "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "pending_reviews": [],
            "reviews": [],
            "shared_chats": {},
            "messages": [],
        }

    async def _fetch_state(self, url: str) -> Dict:
        """Fetch interbot state from URL."""
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.get(url, headers={"Cache-Control": "no-cache"})
                if response.status_code == 200:
                    data = response.json()
                    logger.debug(f"Fetched interbot state from {url[:60]}...")
                    return data
        except Exception as e:
            logger.warning(f"Failed to fetch interbot state from {url[:60]}: {e}")
        return {}

    async def _push_state(self) -> bool:
        """Push own state to GitHub via Contents API."""
        if not self._gh_pat:
            logger.warning("No GitHub PAT - cannot push interbot state")
            return False

        self._own_state["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {
                    "Authorization": f"token {self._gh_pat}",
                    "Accept": "application/vnd.github.v3+json",
                }
                
                # Get current SHA (required for update)
                response = await client.get(NASTYA_API_URL, headers=headers)
                if response.status_code == 200:
                    self._last_own_sha = response.json().get("sha", "")
                elif response.status_code == 404:
                    self._last_own_sha = ""  # File doesn't exist yet
                
                # Push content
                content = json.dumps(self._own_state, ensure_ascii=False, indent=2)
                import base64
                encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
                
                data = {
                    "message": f"interbot: update state ({time.strftime('%Y-%m-%d %H:%M')})",
                    "content": encoded,
                    "committer": {"name": "nastya-bot", "email": "bot@nastya.local"},
                }
                if self._last_own_sha:
                    data["sha"] = self._last_own_sha
                
                response = await client.put(NASTYA_API_URL, headers=headers, json=data)
                if response.status_code in (200, 201):
                    result = response.json()
                    self._last_own_sha = result.get("content", {}).get("sha", self._last_own_sha)
                    logger.info(f"Pushed interbot state to GitHub (sha={self._last_own_sha[:8]}...)")
                    # Also save local cache
                    await self._save_local_cache()
                    return True
                else:
                    logger.error(f"Failed to push interbot state: {response.status_code} {response.text[:200]}")
                    return False
        except Exception as e:
            logger.error(f"Error pushing interbot state: {e}")
            return False

    async def _save_local_cache(self):
        """Save a local copy of own state as backup cache."""
        try:
            LOCAL_CACHE.parent.mkdir(parents=True, exist_ok=True)
            with open(LOCAL_CACHE, "w", encoding="utf-8") as f:
                json.dump(self._own_state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"Failed to save local cache: {e}")

    async def maybe_refresh(self):
        """Refresh state from GitHub if enough time has passed."""
        now = time.time()
        if now - self._last_refresh < REFRESH_INTERVAL:
            return
        self._last_refresh = now
        
        # Refresh other bot's state
        other = await self._fetch_state(ASYA_RAW_URL)
        if other:
            self._other_state = other
        
        # Also refresh own state (in case it was updated elsewhere)
        own = await self._fetch_state(NASTYA_RAW_URL)
        if own:
            self._own_state = own

    # ── AI-Filter: Настя reviews Ася's news candidates ──

    async def check_pending_reviews(self) -> List[Dict]:
        """Check for pending news reviews from Ася.
        
        Returns list of unreviewed candidates.
        """
        await self.maybe_refresh()
        
        # Check Ася's pending_reviews (these are candidates FROM asya)
        pending = self._other_state.get("pending_reviews", [])
        
        # Check which ones we've already reviewed
        our_reviews = {r["candidate_id"] for r in self._own_state.get("reviews", [])}
        
        unreviewed = [p for p in pending if p.get("id") not in our_reviews and p.get("status") == "pending"]
        
        if unreviewed:
            logger.info(f"Found {len(unreviewed)} unreviewed news candidates from Ася")
        
        return unreviewed

    async def review_candidate(self, candidate: Dict) -> Dict:
        """Review a news candidate from Ася using AI.
        
        Returns review dict with verdict: approved, rejected, or improved.
        """
        if not self._ai_router:
            return {
                "id": f"rev_{int(time.time())}_{candidate.get('id', '')}",
                "candidate_id": candidate.get("id", ""),
                "reviewer": "nastya",
                "verdict": "approved",  # Default approve if no AI
                "comment": "Автоматическое одобрение (AI недоступен)",
                "improved_text": "",
                "timestamp": time.time(),
            }
        
        try:
            title = candidate.get("title", "")
            summary = candidate.get("summary", "")
            category = candidate.get("category", "general")
            
            prompt = f"Новость от Аси для канала @sochiautoparts:\n\nЗаголовок: {title}\n"
            if summary:
                prompt += f"Содержание: {summary[:500]}\n"
            prompt += f"\nКатегория: {category}\n"
            prompt += (
                "\nОцени эту новость для публикации в автоканале @sochiautoparts. "
                "Ответь СТРОГО в формате JSON:\n"
                '{"verdict": "approved|rejected|improved", "comment": "твой комментарий", "improved_text": "улучшенный текст поста если verdict=improved иначе пусто"}\n\n'
                "Критерии:\n"
                "- approved: новость интересная, актуальная, про автомобили, без политики\n"
                "- rejected: новость скучная, не про авто, политическая, устаревшая, дубликат\n"
                "- improved: новость хорошая, но нужно улучшить текст - дай улучшенную версию\n"
                "Важно: канал @sochiautoparts - АВТОМОБИЛЬНЫЙ, только про машины и автопром!"
            )
            
            result = await self._ai_router.chat(
                prompt=prompt,
                system_prompt=(
                    "Ты Настя - блогер и редактор. Ты помогаешь Асе отбирать новости для её автоканала. "
                    "Отвечай ТОЛЬКО JSON, без другого текста."
                ),
                max_tokens=400,
                priority="low",
                route_type="function",
            )
            
            if result and result.text:
                import re
                text = result.text.strip()
                # Extract JSON from response
                json_match = re.search(r'\{[^{}]+\}', text, re.DOTALL)
                if json_match:
                    review_data = json.loads(json_match.group())
                    verdict = review_data.get("verdict", "approved")
                    if verdict not in ("approved", "rejected", "improved"):
                        verdict = "approved"
                    
                    review = {
                        "id": f"rev_{int(time.time())}_{candidate.get('id', '')}",
                        "candidate_id": candidate.get("id", ""),
                        "reviewer": "nastya",
                        "verdict": verdict,
                        "comment": review_data.get("comment", ""),
                        "improved_text": review_data.get("improved_text", ""),
                        "timestamp": time.time(),
                    }
                    
                    # Save review to own state
                    self._own_state.setdefault("reviews", []).append(review)
                    # Keep only last 50 reviews
                    self._own_state["reviews"] = self._own_state["reviews"][-50:]
                    await self._push_state()
                    
                    logger.info(f"Reviewed candidate '{title[:40]}...': {verdict}")
                    return review
        
        except Exception as e:
            logger.error(f"AI review error: {e}")
        
        # Fallback: approve
        review = {
            "id": f"rev_{int(time.time())}_{candidate.get('id', '')}",
            "candidate_id": candidate.get("id", ""),
            "reviewer": "nastya",
            "verdict": "approved",
            "comment": "Одобрено (fallback)",
            "improved_text": "",
            "timestamp": time.time(),
        }
        self._own_state.setdefault("reviews", []).append(review)
        await self._push_state()
        return review

    async def run_review_cycle(self):
        """Run a full review cycle: check pending -> review -> push."""
        unreviewed = await self.check_pending_reviews()
        if not unreviewed:
            return 0
        
        reviewed = 0
        for candidate in unreviewed[:3]:  # Review max 3 per cycle
            try:
                await self.review_candidate(candidate)
                reviewed += 1
                await asyncio.sleep(2)  # Don't spam AI
            except Exception as e:
                logger.error(f"Review cycle error: {e}")
        
        return reviewed

    # ── Inter-bot messaging ──

    async def send_message(self, text: str, to: str = "asya") -> bool:
        """Send a message to the other bot via interbot state."""
        msg = {
            "from": "nastya",
            "to": to,
            "text": text,
            "timestamp": time.time(),
            "read": False,
        }
        self._own_state.setdefault("messages", []).append(msg)
        # Keep last 100 messages
        self._own_state["messages"] = self._own_state["messages"][-100:]
        return await self._push_state()

    async def check_messages(self) -> List[Dict]:
        """Check for unread messages from Ася."""
        await self.maybe_refresh()
        messages = self._other_state.get("messages", [])
        unread = [m for m in messages if m.get("to") == "nastya" and not m.get("read", False)]
        return unread

    async def mark_messages_read(self, message_texts: List[str]):
        """Mark messages as read by pushing updated state.
        
        Note: We can't directly modify Ася's state, so we store a read-receipt 
        in our own state and Ася checks it.
        """
        for text in message_texts:
            receipt = {"from": "asya", "text_hash": str(hash(text)), "read_at": time.time()}
            self._own_state.setdefault("read_receipts", []).append(receipt)
        await self._push_state()

    # ── Shared chat coordination ──

    def register_shared_chat(self, chat_id: int, chat_title: str = ""):
        """Register a chat where both bots are present."""
        chat_key = str(chat_id)
        if chat_key not in self._own_state.get("shared_chats", {}):
            self._own_state.setdefault("shared_chats", {})[chat_key] = {
                "title": chat_title,
                "topics": [],
                "last_discussion": 0,
                "nastya_active": True,
                "asya_active": True,
            }

    def is_shared_chat(self, chat_id: int) -> bool:
        """Check if a chat is a shared chat with Ася."""
        chat_key = str(chat_id)
        return chat_key in self._own_state.get("shared_chats", {})

    async def update_shared_chat_topic(self, chat_id: int, topic: str):
        """Update the current discussion topic in a shared chat."""
        chat_key = str(chat_id)
        chats = self._own_state.setdefault("shared_chats", {})
        if chat_key in chats:
            chats[chat_key]["topics"] = chats[chat_key].get("topics", [])[-4:] + [topic]
            chats[chat_key]["last_discussion"] = time.time()
            await self._push_state()

    # ── Status ──

    def get_status(self) -> Dict:
        return {
            "pending_reviews_for_us": len([p for p in self._other_state.get("pending_reviews", []) if p.get("status") == "pending"]),
            "our_reviews": len(self._own_state.get("reviews", [])),
            "unread_messages": len([m for m in self._other_state.get("messages", []) if m.get("to") == "nastya" and not m.get("read", False)]),
            "shared_chats": len(self._own_state.get("shared_chats", {})),
        }


# ── Global instance ──
interbot_manager = InterbotManager()


# ── Convenience functions (backward compatible) ──

async def send_to_asya(message: str, msg_type: str = "info") -> bool:
    """Send a message to Ася bot."""
    return await interbot_manager.send_message(f"[{msg_type}] {message}", to="asya")


async def check_messages() -> List[str]:
    """Check for unread messages from Ася."""
    msgs = await interbot_manager.check_messages()
    return [m.get("text", "") for m in msgs]


async def run_review_cycle():
    """Run AI-filter review cycle for Ася's news candidates."""
    return await interbot_manager.run_review_cycle()
