"""Rule-based conversational intent: question, thanks, greeting, complaint.

Intent drives relationship metrics, e.g. "who answers questions" and "how fast are
newcomers welcomed". Rules cover English and Russian; extend the patterns for other
languages. Bump INTENT_VERSION whenever the rules change.
"""

import re

INTENT_VERSION = "1"

QUESTION, THANKS, GREETING, COMPLAINT = "question", "thanks", "greeting", "complaint"

_THANKS = re.compile(
    r"\b(thanks?|thank you|thx|ty|appreciate|спасибо|спс|благодар\w*|рахмет)\b", re.IGNORECASE
)
_GREETING = re.compile(
    r"^\W*(hi|hello|hey|good (morning|evening|afternoon)|welcome|привет\w*|здравствуй\w*|"
    r"добро пожаловать|доброе утро|добрый (день|вечер)|салем|сәлем)\b",
    re.IGNORECASE,
)
_QUESTION_WORDS = re.compile(
    r"^\W*(how|what|why|where|when|who|which|can|could|does|do|is|are|should|anyone|"
    r"как|что|почему|зачем|где|когда|кто|какой|какая|какие|можно|подскажите|есть ли)\b",
    re.IGNORECASE,
)
_COMPLAINT = re.compile(
    r"\b(doesn'?t work|not working|broken|bug|error|crash\w*|frustrat\w*|annoying|terrible|"
    r"awful|useless|nothing works|не работает|сломал\w*|ошибк\w*|бесит|ужас\w*|отстой)\b",
    re.IGNORECASE,
)


def detect_intent(text: str, sentiment_label: str | None = None) -> str | None:
    """Return the dominant intent of a cleaned message, or None.

    Precedence: thanks > complaint > question > greeting. A greeting followed by a
    question ("Hi all, how do I...") is a question: that is what needs an answer.
    """
    if not text:
        return None
    if _THANKS.search(text):
        return THANKS
    if _COMPLAINT.search(text) and sentiment_label != "positive":
        return COMPLAINT
    if "?" in text or _QUESTION_WORDS.search(_GREETING.sub("", text).lstrip(" ,!.")):
        return QUESTION
    if _GREETING.search(text):
        return GREETING
    return None
