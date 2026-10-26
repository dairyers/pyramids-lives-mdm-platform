"""Help guide: a level-1 "how do I / where is" lookup with no AI and no network. Topics, synonyms, stopwords and every
tuning number are read from data/help_topics.json; the roles that may open a page come from config_rules.json.

Ranking is BM25 over each topic's title, trigger phrases and answer. Words are mapped through the synonym list and a light
stemmer, and a word that is not in any topic is matched to the closest known word with rapidfuzz, so small typos still
find the right topic. Nothing is generated: the answer returned is the text that was written for the topic.
"""
import functools
import json
import math
import pathlib
import re

from rapidfuzz import fuzz, process

HELP_FILE = pathlib.Path(__file__).parent / "data" / "help_topics.json"


def load_help(path=HELP_FILE):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def help_of(config):
    """The help topics this session runs on: the copy carried in the live settings when there is one (so an edit, Undo
    and Reset follow the same path as every other setting), otherwise the file."""
    return (config or {}).get("help") or load_help()


def page_choices(help_data):
    """[(page id or None, label)] for the 'opens page' dropdown, from the file's page list."""
    return [(pid, p["label"]) for pid, p in help_data["pages"].items()] + [(None, help_data["no_page_label"])]


def _slug(title, taken):
    base = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_") or "topic"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    return name


def clean_rows(rows, help_data):
    """Rows from the editor ({id, title, words, answer, page}) as topics, plus the problems found. A row that is entirely
    empty is ignored; ids are kept for existing topics and made from the title for new ones."""
    errors, topics, titles, taken = [], [], set(), {r.get("id") for r in rows if r.get("id")}
    for n, r in enumerate(rows, 1):
        title = (r.get("title") or "").strip()
        words = [w.strip() for w in (r.get("words") or []) if w and w.strip()]
        answer = (r.get("answer") or "").strip()
        page = r.get("page")
        if not (title or words or answer):
            continue
        label = f"'{title}'" if title else f"Row {n}"
        if not title:
            errors.append(f"{label} has no title.")
        elif title.lower() in titles:
            errors.append(f"Two topics are called '{title}'. Titles must be different.")
        titles.add(title.lower())
        if not answer:
            errors.append(f"{label} has no answer.")
        if not words:
            errors.append(f"{label} needs at least one phrase people might type.")
        if page is not None and page not in help_data["pages"]:
            errors.append(f"{label} opens a page that does not exist.")
        tid = r.get("id") or _slug(title, taken)
        taken.add(tid)
        topics.append({"id": tid, "title": title, "page": page,
                       "tab": help_data["pages"][page]["tab"] if page in help_data["pages"] else None,
                       "words": words, "answer": answer})
    if not topics:
        errors.append("The guide needs at least one topic.")
    return topics, errors


def clean_synonyms(rows):
    """Editor rows ({word, same_as}) as the similar-words dict, plus the problems found. Each side is one lower-case word
    (letters and digits). A word may not point to itself, be listed twice, or point at a word that is itself listed."""
    errors, out = [], {}
    for r in rows:
        word, same = (r.get("word") or "").strip().lower(), (r.get("same_as") or "").strip().lower()
        if not (word or same):
            continue
        if not re.fullmatch(r"[a-z0-9]+", word) or not re.fullmatch(r"[a-z0-9]+", same):
            errors.append(f"'{word}' to '{same}': use one plain word on each side.")
        elif word == same:
            errors.append(f"'{word}' cannot be treated as itself.")
        elif word in out:
            errors.append(f"'{word}' is listed twice.")
        else:
            out[word] = same
    errors += [f"'{w}' points to '{s}', which is itself listed. Point it at '{out[s]}' instead." for w, s in out.items() if s in out]
    return out, errors


def set_synonyms(config, rows):
    """Replace the similar-words list in `config` (a trial copy). Raises ValueError listing every problem, otherwise
    returns plain-language lines about what changed."""
    new, errors = clean_synonyms(rows)
    if errors:
        raise ValueError(" ".join(errors))
    old = config["help"]["synonyms"]
    out = [f"Added '{w}' = '{s}'" for w, s in new.items() if w not in old]
    out += [f"Removed '{w}' = '{s}'" for w, s in old.items() if w not in new]
    out += [f"Changed '{w}' to '{new[w]}'" for w in new if w in old and old[w] != new[w]]
    config["help"]["synonyms"] = new
    return out


def set_topics(config, rows):
    """Replace the help topics in `config` (a trial copy) with the edited rows. Raises ValueError with every problem found,
    otherwise returns plain-language lines describing what changed (empty when nothing did)."""
    data = config["help"]
    topics, errors = clean_rows(rows, data)
    if errors:
        raise ValueError(" ".join(errors))
    old = {t["id"]: t for t in data["topics"]}
    new = {t["id"]: t for t in topics}
    out = [f"Added '{t['title']}'" for i, t in new.items() if i not in old]
    out += [f"Removed '{t['title']}'" for i, t in old.items() if i not in new]
    out += [f"Edited '{t['title']}'" for i, t in new.items() if i in old and t != old[i]]
    if topics != data["topics"] and not out:
        out = ["Reordered the topics"]
    data["topics"] = topics
    return out


def _stem(word, help_data=None):
    """Light suffix stripping so that approve, approves, approved, approving and approver all become one word. The
    suffix list is in the help file. A suffix is only removed when at least four letters remain, and a final 'e' is
    dropped so that 'create' and 'creating' meet."""
    suffixes = (help_data or {}).get("suffixes", ["ing", "ed", "es", "s"])
    for suffix in suffixes:                                    # longest first
        if word.endswith(suffix) and len(word) - len(suffix) >= 4 and not (suffix == "s" and word.endswith("ss")):
            word = word[: -len(suffix)]
            break
    return word[:-1] if word.endswith("e") and len(word) > 4 else word


@functools.lru_cache(maxsize=32)
def _stemmed_synonyms(pairs, suffixes):
    """The similar-words list keyed by stem, so that a listed word also catches its other forms (escalation also
    catches escalations and escalating)."""
    data = {"suffixes": list(suffixes)}
    return {_stem(k, data): _stem(v, data) for k, v in pairs}


def tokens(text, help_data):
    """Normalised words: lower case, hyphens and punctuation split, stopwords dropped, each word replaced by its listed
    similar word (looked up as written, then by stem) and stemmed, so different forms of one idea share a token."""
    syn, stop = help_data["synonyms"], set(help_data["stopwords"])
    stemmed = _stemmed_synonyms(tuple(syn.items()), tuple(help_data.get("suffixes", ())))
    out = []
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if w in stop:
            continue
        s = _stem(w, help_data)
        out.append(_stem(syn[w], help_data) if w in syn else stemmed.get(s, s))
    return out


class Index:
    """BM25 index over the topics of one help file."""

    def __init__(self, help_data):
        self.data, st = help_data, help_data["settings"]
        self.topics = help_data["topics"]
        self.docs = []
        for t in self.topics:
            toks = tokens(t["title"], help_data) * st["title_weight"] + tokens(" ".join(t["words"]), help_data) * st["word_weight"] \
                + tokens(t["answer"], help_data)
            self.docs.append(toks)
        self.avg = sum(len(d) for d in self.docs) / max(len(self.docs), 1)
        self.df = {}
        for d in self.docs:
            for w in set(d):
                self.df[w] = self.df.get(w, 0) + 1
        self.vocab = sorted(self.df)

    def _idf(self, w):
        n = len(self.docs)
        return math.log(1 + (n - self.df[w] + 0.5) / (self.df[w] + 0.5))

    def _known(self, word):
        """The word itself when some topic uses it, else the closest known word (typo repair), else None."""
        st = self.data["settings"]
        if word in self.df:
            return word
        if len(word) < st["min_word_length"]:
            return None
        hit = process.extractOne(word, self.vocab, scorer=fuzz.ratio, score_cutoff=st["typo_cutoff"])
        return hit[0] if hit else None

    def search(self, query):
        """[(score, topic)] best first: only topics above min_score and within relative_cutoff of the best one."""
        st = self.data["settings"]
        words = [k for k in (self._known(w) for w in tokens(query, self.data)) if k]
        if not words:
            return []
        scored = []
        for topic, doc in zip(self.topics, self.docs):
            score = 0.0
            for w in set(words):
                f = doc.count(w)
                if f:
                    score += self._idf(w) * f * (st["k1"] + 1) / (f + st["k1"] * (1 - st["b"] + st["b"] * len(doc) / self.avg))
            scored.append((score, topic))
        scored.sort(key=lambda p: p[0], reverse=True)
        if not scored or scored[0][0] < st["min_score"]:
            return []
        best = scored[0][0]
        return [(s, t) for s, t in scored if s >= st["min_score"] and s >= best * st["relative_cutoff"]][: st["max_results"]]


def roles_with_tab(tab_id, config):
    """Labels of the roles that can open a configured page tab."""
    return [r["label"] for r in config["roles"].values() if tab_id in r["tabs"]]


def can_open(topic, role, config):
    """True when the topic has no page restriction or the role may open its page."""
    return not topic.get("tab") or topic["tab"] in config["roles"][role]["tabs"]
