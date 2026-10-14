"""Keyword extraction for topic analytics: tokenize, drop stopwords, stem.

Chats mix Russian, English and French, so Cyrillic words use the Russian Snowball
stemmer and Latin words are accent-folded and use the English one. Stemming merges
inflections ("виза", "визу", "визы" -> "виз"); the most common surface form is used as
the topic's label. Pure functions, no I/O.
"""

import re
import unicodedata
from functools import lru_cache

import snowballstemmer

from tg_analyst.enrich.clean import clean_text

WORD = re.compile(r"[^\W\d_]{3,}")
_CYRILLIC = re.compile(r"[а-яё]")


def _words(block: str) -> frozenset[str]:
    return frozenset(block.split())


# Function words plus chat filler that would otherwise top every topic list.
STOPWORDS_RU = _words(
    """
    без более больше будет будем будете будешь буду будут бы был была были было быть вам вас
    весь во вот все всё всего всех всю вся вы где да даже для до его ее её ей ему если есть еще
    ещё же за здесь из или им их как какая какие какой каким когда кого кое кто куда ли либо
    между меня мне мной много может можете можно мой моя мои мое моё мы на над надо наш наша
    наше наши не него нее неё нет ни них но ну об однако он она они оно опять от очень по под
    после потом потому почему при про раз разве с сам сама сами само свой свою своя свои себе
    себя сейчас со совсем так также такая такие такой там тебе тебя тем теперь то тогда того
    тоже той только том тот ту тут ты уж уже хоть хотя чего чем через что чтоб чтобы чье чья
    эта эти это этого этой этом этот эту я
    вообще вроде типа короче кстати просто прям прямо ага угу ок окей ладно норм нормально
    давай давайте спасибо спс пожалуйста привет здравствуйте пока блин вдруг всегда никогда
    сегодня завтра вчера сколько какое какую каких чей где-то кто-то что-то тоже ведь значит
    наверное конечно точно правда реально пусть итак например около сразу снова больше меньше
    лучше хорошо плохо поэтому будто тех этих этим этими тому таким такое такую всем всеми
    своих своим ним нему ней нею них ими мой моего моих твой твоя твои вашу ваш ваша ваши
    один одна одно одни два три есть нету был могу может могут хочу хочет знаю знаешь думаю
    кажется нужно надо нужен нужна сделать делать будь бывает стоит стало было
    """
)
STOPWORDS_EN = _words(
    """
    about above after again all also and any are because been before being below between both
    but can could did does doing down during each few for from further had has have having her
    here hers herself him himself his how into its itself just more most myself nor not now off
    once only other our ours out over own same she should some such than that the their theirs
    them then there these they this those through too under until very was were what when where
    which while who whom why will with would you your yours yourself yes yeah okay thanks thank
    please hello hey lol get got like one know think really want need make good well much
    """
)
STOPWORDS_FR = _words(
    """
    alors aussi avec avoir bon car ce cela ces cet cette chez comme comment dans des donc dont
    elle elles entre est été être eux fait faire ici ils les leur leurs mais mes moi mon même
    nos notre nous oui par pas peu peut plus pour pourquoi quand que quel quelle quels qui sans
    ses son sont sous sur tes toi ton tous tout toute très une vos votre vous merci salut bonjour
    """
)
STOPWORDS = STOPWORDS_RU | STOPWORDS_EN | STOPWORDS_FR

# Common words that carry no topic, matched by stem so one form covers its inflections.
GENERIC_WORDS = _words(
    """
    который другой нибудь первый новый самый любой каждый своей одного такого целом случае
    время год лет день неделя месяц час минута раз люди человек ребята народ вопрос ответ
    место штука часть вариант смысл итог тема момент сторона вещь дело чат
    делать делал сделать сделал получить получается писать написать написано говорить сказать
    сказали говорят понять понял знать видеть смотреть думать хотеть давать брать идти пойти
    прийти ехать работает работать начать начал стать остаться спросить ждать узнать помочь
    искать найти взять дать нужный должно должен возможно обычно скорее интересно странно
    наверно реально нормальный понятно похоже вполне именно почти совсем вообще лишь больший
    какой-то ничего никто ничто нечто всякий иначе многие немного несколько сразу пару
    ребят кому нас нам знает подскажите понимаю можешь второй прошлый последний следующий
    никаких помню лично хороший приходит туда сюда назад мало типо вместе давно скоро
    year time day week month thing people question answer work make take use need
"""
)

_RU = snowballstemmer.stemmer("russian")
_EN = snowballstemmer.stemmer("english")


def _fold_accents(word: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", word) if not unicodedata.combining(c))


@lru_cache(maxsize=200_000)
def stem(word: str) -> str:
    """The stem of a lowercase word (Russian or accent-folded English/French)."""
    if _CYRILLIC.search(word):
        return _RU.stemWord(word.replace("ё", "е"))
    return _EN.stemWord(_fold_accents(word))


GENERIC_STEMS = frozenset(stem(w) for w in GENERIC_WORDS)


def keywords(text: str | None) -> dict[str, str]:
    """Distinct content words of a message as {stem: surface form}.

    URLs, @mentions, numbers, words shorter than three letters and stopwords are dropped.
    """
    found: dict[str, str] = {}
    for word in WORD.findall(clean_text(text).lower()):
        if word in STOPWORDS or word in ("http", "user"):
            continue
        key = stem(word)
        if len(key) >= 3 and key not in GENERIC_STEMS and key not in found:
            found[key] = word
    return found
