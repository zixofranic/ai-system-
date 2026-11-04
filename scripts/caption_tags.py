"""Shared captions, hashtags and title cleanup for TikTok, Facebook, Instagram.

Each video gets at most MAX_HASHTAGS tags from a curated list per channel. AI
tags can only choose a topic tag from an approved list, so generic or
off-brand tags ("#philosophy" on a recovery video) can't reach a caption.

Recovery hashtags must stay compliant: no NA or AA program names and no
endorsement claims. Review these lists against TikTok Creative Center and our
own view data (generation_params.platform_stats) as results come in.
"""
import json
import re

MAX_HASHTAGS = 5
RECOVERY_CHANNELS = ("na", "aa")

_CORE_HASHTAGS = {
    "wisdom": ["#Philosophy", "#Wisdom"],
    "gibran": ["#KhalilGibran", "#TheProphet", "#Poetry"],
    "na": ["#Recovery", "#RecoveryTok", "#OneDayAtATime", "#CleanTime"],
    "aa": ["#Sobriety", "#SoberTok", "#EasyDoesIt", "#SoberLife"],
}

_PHILOSOPHER_HASHTAGS = {
    "marcus aurelius": ["#MarcusAurelius", "#Stoicism"],
    "seneca": ["#Seneca", "#Stoicism"],
    "epictetus": ["#Epictetus", "#Stoicism"],
    "rumi": ["#Rumi", "#Sufism"],
    "lao tzu": ["#LaoTzu", "#Taoism"],
    "nietzsche": ["#Nietzsche", "#Existentialism"],
    "dostoevsky": ["#Dostoevsky", "#Literature"],
    "musashi": ["#Musashi", "#Samurai"],
    "sun tzu": ["#SunTzu", "#TheArtOfWar"],
    "emerson": ["#Emerson", "#Transcendentalism"],
    "thoreau": ["#Thoreau", "#Walden"],
    "wilde": ["#OscarWilde", "#Literature"],
    "vivekananda": ["#Vivekananda", "#Vedanta"],
    "confucius": ["#Confucius"],
    "franklin": ["#BenFranklin"],
    "tesla": ["#NikolaTesla"],
    "da vinci": ["#DaVinci"],
}

_RECOVERY_TOPIC_HASHTAGS = {
    "amends": "#Amends", "gratitude": "#Gratitude",
    "forgiveness": "#Forgiveness", "resentment": "#Resentment",
    "resentments": "#Resentment", "shame": "#Shame", "relapse": "#RelapsePrevention",
    "relapseprevention": "#RelapsePrevention", "sponsor": "#Sponsorship",
    "sponsorship": "#Sponsorship", "mentalhealth": "#MentalHealth",
    "anxiety": "#Anxiety", "loneliness": "#Loneliness", "grief": "#Grief",
    "honesty": "#Honesty", "acceptance": "#Acceptance", "surrender": "#Surrender",
    "addictionrecovery": "#AddictionRecovery",
}

_REFLECTIVE_TOPIC_HASHTAGS = {
    "mindfulness": "#Mindfulness", "lettinggo": "#LettingGo",
    "innerpeace": "#InnerPeace", "grief": "#Grief", "sorrow": "#Grief",
    "love": "#Love", "courage": "#Courage", "selfdiscipline": "#SelfDiscipline",
    "discipline": "#SelfDiscipline", "meaningoflife": "#MeaningOfLife",
    "solitude": "#Solitude", "forgiveness": "#Forgiveness",
}
# Schools of thought (#Stoicism, #Taoism) come only from _PHILOSOPHER_HASHTAGS.
# The AI's tags can't be trusted for them: it tagged a Lao Tzu video "stoicism".


def _normalize(tag) -> str:
    return re.sub(r"[^a-z0-9]", "", str(tag or "").lower())


def _persona_hashtags(philosopher: str) -> list:
    name = (philosopher or "").lower()
    for key, tags in _PHILOSOPHER_HASHTAGS.items():
        if key in name:
            return tags
    cleaned = re.sub(r"[^A-Za-z0-9]", "", philosopher or "")
    return [f"#{cleaned}"] if cleaned else []


def select_hashtags(channel_slug: str, philosopher: str, ai_tags) -> list:
    """Curated hashtags for one video, most specific first."""
    allowed_topics = (_RECOVERY_TOPIC_HASHTAGS if channel_slug in RECOVERY_CHANNELS
                      else _REFLECTIVE_TOPIC_HASHTAGS)
    topic_tag = next((allowed_topics[_normalize(tag)] for tag in (ai_tags or [])
                      if _normalize(tag) in allowed_topics), None)

    # Philosopher tags are for the philosopher channel only. Recovery persona
    # names ("The Old-Timer") aren't terms anyone searches for.
    specific = _persona_hashtags(philosopher) if channel_slug == "wisdom" else []
    candidates = specific + _CORE_HASHTAGS.get(channel_slug, [])
    if topic_tag:
        # Reserve room so the topic tag always survives the cap.
        candidates = candidates[:MAX_HASHTAGS - 1] + [topic_tag]

    selected, seen = [], set()
    for tag in candidates:
        if tag.lower() not in seen:
            seen.add(tag.lower())
            selected.append(tag)
    return selected[:MAX_HASHTAGS]


def clean_title(title: str, channel_slug: str) -> str:
    """Remove philosophy branding from recovery titles. Others are unchanged."""
    title = title or ""
    if channel_slug not in RECOVERY_CHANNELS or "philosoph" not in title.lower():
        return title
    segments = [s.strip() for s in title.split("|")]
    kept = [s for s in segments if "philosoph" not in s.lower()]
    if kept:
        return " | ".join(kept)
    cleaned = re.sub(r"\b(?:the\s+)?philosophy\s+of\s+", "", title, flags=re.I)
    cleaned = re.sub(r"\bphilosoph\w*\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" :|-")
    return cleaned[:1].upper() + cleaned[1:] if cleaned else "Recovery"


def filter_platform_tags(tags, channel_slug: str) -> list:
    """Drop philosophy search tags from recovery uploads."""
    tags = list(tags or [])
    if channel_slug not in RECOVERY_CHANNELS:
        return tags
    return [t for t in tags if "philosoph" not in str(t).lower()]


def _shorten(text: str, limit: int = 200) -> str:
    """Cut at the last full sentence inside the limit, else at the last word."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > 0 else cut.rsplit(" ", 1)[0] + "..."


def build_caption(content: dict, channel_slug: str) -> str:
    """Caption text: title, first description line, curated hashtags."""
    params = content.get("generation_params") or {}
    if isinstance(params, str):
        params = json.loads(params)

    caption = clean_title(content.get("title") or "", channel_slug)
    first_line = _shorten((content.get("description") or "").split("\n")[0])
    off_brand = (channel_slug in RECOVERY_CHANNELS
                 and "philosoph" in first_line.lower())
    if first_line and not off_brand:
        caption = f"{caption}\n\n{first_line}" if caption else first_line

    hashtags = select_hashtags(channel_slug, content.get("philosopher") or "",
                               params.get("tags"))
    if hashtags:
        caption = f"{caption}\n\n{' '.join(hashtags)}" if caption else " ".join(hashtags)
    return caption[:2200]
