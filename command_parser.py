"""
Command parser for the Telegram bot.

Supports all commands including:
    send, stop, stopall, mainclick, cloneNclick, mainDclick,
    forward10(account) CHATID "trigger" for @user /delay send(main) "line N",
    forwardloop10(account) CHATID "trigger" for @user /delay send "line N" [-n],
    rep25(random) CHATID "trigger" send "text" /delay send(main) "LINE N",
    reploop25(random) CHATID "trigger" send(clone1) "LINE N",
    repall(1) CHATID "trigger" send "text" /delay send(m) "LINE N",
    stopclick, react, show/hide prof,
    prof status, prof hideall, set prof, save as, send vim/vom/mus/vid,
    del, list, Dclick inside send, direct click, react with link
"""

import re
import logging
from typing import Dict, List, Optional

logger = logging.getLogger("TG-Auto")






SEND_BASIC = re.compile(
    r"""^send(?:\((?P<clones>[^)]+)\))?\s+(?P<q>["'])(?P<text>.+?)(?P=q)(?P<rest>.*)""",
    re.IGNORECASE | re.DOTALL,
)

FORWARD_LINE_PATTERN = re.compile(
    r'''^(?P<mode>forwardloop|forward)(?P<batch>\d+)\((?P<account>main|clone\d+|random)\)\s+'''
    r'''(?P<source_id>-?\d+)\s+(?P<trigger_q>["'])(?P<trigger>.+?)(?P=trigger_q)\s+'''
    r'''for\s+(?P<user>@[A-Za-z][A-Za-z0-9_]{2,}|-?\d+)\s+'''
    r'''/(?P<delay>\d+)\s+send(?:\((?P<send_account>main|clone\d+|random)\))?\s+'''
    r'''(?P<line_q>["'])(?P<line>line\s+\d+)(?P=line_q)'''
    r'''(?P<no_trace>\s+-n)?\s*$''',
    re.IGNORECASE | re.DOTALL,
)

REP_COMMAND_PATTERN = re.compile(
    r'''^(?P<mode>repall|rep|reploop)(?P<batch>\d+)?\((?P<account>m|main|\d+|clone\d+|random)\)\s+'''
    r'''(?:(?P<chat_id>-?\d+)\s+)?'''
    r'''(?P<trigger_q>["'])(?P<trigger>.+?)(?P=trigger_q)\s+'''
    r'''send\s+(?P<first_q>["'])(?P<first>.+?)(?P=first_q)'''
    r'''(?:\s+(?:/(?P<delay>\d+)(?:-(?P<timeout>\d+))?\s+)?'''
    r'''(?P<reply_mode>sendrep|send)'''
    r'''(?:\((?P<reply_account>m|main|\d+|clone\d+|random)\))?\s+'''
    r'''(?:(?P<reply_chat_id>-?\d+)\s+)?'''
    r'''(?P<line_q>["'])(?P<line>line\s+\d+)(?P=line_q))?\s*$''',
    re.IGNORECASE | re.DOTALL,
)

STOP_PATTERN = re.compile(
    r"""^stop\s+(?P<q>["'])(?P<text>.+?)(?P=q)\s*$""",
    re.IGNORECASE | re.DOTALL,
)

STOP_CLICK_PATTERN = re.compile(
    r"""^stopclick(?:\s+(?P<q>["'])(?P<button>.+?)(?P=q))?\s*$""",
    re.IGNORECASE | re.DOTALL,
)

ME_FLAG_PATTERN   = re.compile(r'(?P<flag>[+-]me)', re.IGNORECASE)
DELAY_PATTERN     = re.compile(r'(?<![a-zA-Z])/(?P<delay>\d+)', re.IGNORECASE)
LOOP_PATTERN      = re.compile(r'loop(?P<loop>\d+)', re.IGNORECASE)
REP_PATTERN       = re.compile(r'(?<!\w)rep\s*=\s*(?P<chat_id>-?\d+)', re.IGNORECASE)
REPLY_TIMEOUT_PATTERN = re.compile(r'(?<!\w)t(?P<seconds>\d+)(?!\w)', re.IGNORECASE)
LOOP_FLAG_PATTERN = re.compile(r'\bloop\b(?!\d)', re.IGNORECASE)
NO_TRACE_PATTERN  = re.compile(r'-n\b', re.IGNORECASE)
EDIT_FLAG_PATTERN = re.compile(r'/edit\b', re.IGNORECASE)
NOWATER_FLAG_PATTERN = re.compile(r'-nowater\b', re.IGNORECASE)
NO_MAIN_FLAG = re.compile(r'-m\b', re.IGNORECASE)

CLICK_PATTERN = re.compile(
    r"""(?:--click|click)(?:\(\))?\s*(?:/(?P<chain_delay>\d+)\s+)?(?P<q>["'])(?P<button>.+?)(?P=q)""",
    re.IGNORECASE,
)

DCLICK_PATTERN = re.compile(
    r"""(?<!\w)D(?P<dclick_delay>\d+)click\s+(?P<q>["'])(?P<button>.+?)(?P=q)""",
    re.IGNORECASE,
)

REPLY_PATTERN = re.compile(
    r"""(?:--reply|reply)\s+(?P<q>["'])(?P<text>.+?)(?P=q)""",
    re.IGNORECASE,
)

HUNT_CLICK_PATTERN = re.compile(
    r"""^(?P<session_ref>main|clone\d+)(?P<disappear>D)?click(?P<times>\d+)?\s+(?P<q>["'])(?P<button>.+?)(?P=q)(?P<rest>.*)""",
    re.IGNORECASE | re.DOTALL,
)

REACT_PATTERN = re.compile(
    r"""^react(?:\((?P<clones>[^)]+)\))?\s+(?P<q>["'])(?P<emoji>.+?)(?P=q)\s*$""",
    re.IGNORECASE | re.DOTALL,
)

REACT_LINK_PATTERN = re.compile(
    r"""^react(?:\((?P<clones>[^)]+)\))?\s+(?P<q>["'])(?P<emoji>.+?)(?P=q)(?P<rest>.*)$""",
    re.IGNORECASE | re.DOTALL,
)

DIRECT_CLICK_PATTERN = re.compile(
    r"""^click(?:\(\))?\s*(?:/(?P<chain_delay>\d+)\s+)?(?P<q>["'])(?P<button>.+?)(?P=q)(?:\s+(?P<link>https?://t\.me/\S+))?\s*$""",
    re.IGNORECASE | re.DOTALL,
)

TG_MSG_LINK_PATTERN = re.compile(
    r"""https?://t\.me/(?:c/(?P<chat_id>\d+)|(?P<username>[a-zA-Z][a-zA-Z0-9_]{3,}))/(?P<msg_id>\d+)""",
    re.IGNORECASE,
)

SHOW_PROF_PATTERN = re.compile(
    r"""^(?P<action>show|hide)\s+(?:(?P<scope>main)\s+)?prof(?P<rest>.*)$""",
    re.IGNORECASE | re.DOTALL,
)

PROF_STATUS_PATTERN = re.compile(
    r"""^prof\s+status\s*$""",
    re.IGNORECASE | re.DOTALL,
)

PROF_HIDEALL_PATTERN = re.compile(
    r"""^prof\s+hideall\s*$""",
    re.IGNORECASE | re.DOTALL,
)

SET_PROF_PATTERN = re.compile(
    r"""^set\s+prof(?P<rest>.*)$""",
    re.IGNORECASE | re.DOTALL,
)

SAVE_MEDIA_PATTERN = re.compile(
    r"""^save\s+as\s+(?P<type>vim|vom|mus|vid)\s+(?P<q>["'])(?P<name>.+?)(?P=q)\s*$""",
    re.IGNORECASE | re.DOTALL,
)

SEND_MEDIA_PATTERN = re.compile(
    r"""^send(?:\((?P<clones>[^)]+)\))?\s+(?P<type>vim|vom|mus|vid)\s+(?P<q>["'])(?P<name>.+?)(?P=q)(?P<flags>.*)$""",
    re.IGNORECASE | re.DOTALL,
)

DEL_MEDIA_PATTERN = re.compile(
    r"""^del\s+(?P<type>vim|vom|mus|vid)\s+(?P<q>["'])(?P<name>.+?)(?P=q)\s*$""",
    re.IGNORECASE | re.DOTALL,
)

LIST_MEDIA_PATTERN = re.compile(
    r"""^list\s+(?P<type>vim|vom|mus|vid|all)\s*$""",
    re.IGNORECASE | re.DOTALL,
)

IF_INLINE_PATTERN = re.compile(
    r"""if\s+(?P<cond>(?:line\d+|line~["'][^"']+["']|any)\s+(?:["'][^"']+["']|[A-Z](?:==|!=|=|>=|<=|>|<)[A-Z]))""",
    re.IGNORECASE,
)

IF_NESTED_PATTERN = re.compile(
    r"""if\s*\(\s*(?P<inner>.+?)\s*\)""",
    re.IGNORECASE | re.DOTALL,
)

ELIF_PATTERN = re.compile(r'\belif\b', re.IGNORECASE)
ELSE_PATTERN = re.compile(r'\belse\b', re.IGNORECASE)

INVITE_LINK_PATTERN = re.compile(
    r'(?:https?://)?(?:t\.me/\+|t\.me/joinchat/)(?P<hash>[a-zA-Z0-9_-]+)'
)
USERNAME_LINK_PATTERN = re.compile(
    r'(?:(?:https?://)?t\.me/|@)(?P<username>[a-zA-Z][a-zA-Z0-9_]{3,})'
)
AT_USERNAME_PATTERN = re.compile(
    r'@(?P<username>[a-zA-Z][a-zA-Z0-9_]{3,})'
)






def parse_clone_selector(raw):
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    parts = re.split(r'[,\s]+', raw)
    indices = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        range_match = re.match(r'^(\d+)-(\d+)$', part)
        if range_match:
            start = int(range_match.group(1))
            end = int(range_match.group(2))
            if start <= end:
                indices.extend(range(start, end + 1))
            else:
                indices.extend(range(start, end - 1, -1))
        elif part.isdigit():
            indices.append(int(part))
    if not indices:
        return None
    seen = set()
    unique = []
    for idx in indices:
        if idx not in seen and idx >= 1:
            seen.add(idx)
            unique.append(idx)
    return unique if unique else None






def parse_actions(text):
    actions = {"click": None, "click_chain_delay": None, "reply": None}
    click_match = CLICK_PATTERN.search(text)
    if click_match:
        actions["click"] = click_match.group("button").strip()
        if click_match.group("chain_delay") is not None:
            actions["click_chain_delay"] = int(click_match.group("chain_delay"))
    reply_match = REPLY_PATTERN.search(text)
    if reply_match:
        actions["reply"] = reply_match.group("text").strip()
    return actions


def parse_if_structure(rest):
    rest = rest.strip()

    nested_match = IF_NESTED_PATTERN.search(rest)
    if nested_match:
        inner = nested_match.group("inner").strip()
        after_paren = rest[nested_match.end():].strip()

        cond_match = re.search(
            r'((?:line\d+|line~["\'][^"\']+["\']|any)\s+(?:["\'][^"\']+["\']|[A-Z](?:==|!=|=|>=|<=|>|<)[A-Z]))\s*$',
            inner,
            re.IGNORECASE,
        )
        if not cond_match:
            return None

        condition = cond_match.group(1)
        nested_cmd = inner[:cond_match.start()].strip()

        result = {
            "type": "nested",
            "condition": condition,
            "nested_command": nested_cmd,
            "then": None, "elif": None, "else": None,
        }

        elif_match = ELIF_PATTERN.search(after_paren)
        else_match = ELSE_PATTERN.search(after_paren)

        if elif_match:
            then_str = after_paren[:elif_match.start()].strip()
            after_elif = after_paren[elif_match.end():].strip()
            if else_match and else_match.start() > elif_match.end():
                elif_str = after_paren[elif_match.end():else_match.start()].strip()
                else_str = after_paren[else_match.end():].strip()
            else:
                elif_str = after_elif
                else_str = ""
            result["then"] = parse_actions(then_str)
            result["elif"] = parse_actions(elif_str)
            if else_str:
                result["else"] = parse_actions(else_str)
        elif else_match:
            then_str = after_paren[:else_match.start()].strip()
            else_str = after_paren[else_match.end():].strip()
            result["then"] = parse_actions(then_str)
            result["else"] = parse_actions(else_str)
        else:
            result["then"] = parse_actions(after_paren)
        return result

    inline_match = IF_INLINE_PATTERN.search(rest)
    if inline_match:
        condition = inline_match.group("cond")
        after_cond = rest[inline_match.end():].strip()
        result = {
            "type": "inline",
            "condition": condition,
            "nested_command": None,
            "then": None, "elif": None, "else": None,
        }
        elif_match = ELIF_PATTERN.search(after_cond)
        else_match = ELSE_PATTERN.search(after_cond)
        if elif_match:
            then_str = after_cond[:elif_match.start()].strip()
            after_elif = after_cond[elif_match.end():].strip()
            if else_match and else_match.start() > elif_match.end():
                elif_str = after_cond[elif_match.end():else_match.start()].strip()
                else_str = after_cond[else_match.end():].strip()
            else:
                elif_str = after_elif
                else_str = ""
            result["then"] = parse_actions(then_str)
            result["elif"] = parse_actions(elif_str)
            if else_str:
                result["else"] = parse_actions(else_str)
        elif else_match:
            then_str = after_cond[:else_match.start()].strip()
            else_str = after_cond[else_match.end():].strip()
            result["then"] = parse_actions(then_str)
            result["else"] = parse_actions(else_str)
        else:
            result["then"] = parse_actions(after_cond)
        return result

    return None




def parse_send_command(raw):
    m = SEND_BASIC.match(raw.strip())
    if not m:
        return None

    text = m.group("text")
    rest = (m.group("rest") or "").strip()
    clones_raw = m.group("clones")

    if_struct = parse_if_structure(rest)

    if if_struct:
        if_pos_match = re.search(r'\bif\b', rest, re.IGNORECASE)
        if if_pos_match:
            outer_flags = rest[:if_pos_match.start()].strip()
        else:
            outer_flags = rest
    else:
        outer_flags = rest

    include_me = False
    delay = 0
    loop_interval = None
    rep_chat_id = None
    reply_timeout = None
    button_text = None
    reply_text = None
    no_trace = False
    clone_indices = None
    dclick_text = None
    dclick_delay = 0
    click_chain_delay = None

    if clones_raw:
        clone_indices = parse_clone_selector(clones_raw)

    mf = ME_FLAG_PATTERN.search(outer_flags)
    if mf:
        include_me = mf.group("flag").lower() == "+me"

    
    
    
    click_for_delay = CLICK_PATTERN.search(outer_flags)
    for df in DELAY_PATTERN.finditer(outer_flags):
        if click_for_delay and (
            click_for_delay.start() <= df.start() < click_for_delay.end()
        ):
            continue
        delay = int(df.group("delay"))
        break

    lf = LOOP_PATTERN.search(outer_flags)
    if lf:
        loop_interval = int(lf.group("loop"))

    rep_match = REP_PATTERN.search(outer_flags)
    if rep_match:
        rep_chat_id = int(rep_match.group("chat_id"))

    timeout_match = REPLY_TIMEOUT_PATTERN.search(outer_flags)
    if timeout_match:
        reply_timeout = int(timeout_match.group("seconds"))

    if not if_struct:
        dcf = DCLICK_PATTERN.search(outer_flags)
        if dcf:
            dclick_text = dcf.group("button").strip()
            dclick_delay = int(dcf.group("dclick_delay"))
            logger.info(
                f'[PARSE] Dclick detected: D{dclick_delay}click "{dclick_text}"'
            )
        else:
            cf = CLICK_PATTERN.search(outer_flags)
            if cf:
                button_text = cf.group("button").strip()
                if cf.group("chain_delay") is not None:
                    click_chain_delay = int(cf.group("chain_delay"))

        rf = REPLY_PATTERN.search(outer_flags)
        if rf:
            reply_text = rf.group("text").strip()

    nf = NO_TRACE_PATTERN.search(outer_flags)
    if nf:
        no_trace = True

    return {
        "text": text,
        "include_me": include_me,
        "delay": delay,
        "loop_interval": loop_interval,
        "rep_chat_id": rep_chat_id,
        "reply_timeout": reply_timeout,
        "button_text": button_text,
        "reply_text": reply_text,
        "no_trace": no_trace,
        "clone_indices": clone_indices,
        "if_structure": if_struct,
        "dclick_text": dclick_text,
        "dclick_delay": dclick_delay,
        "click_chain_delay": click_chain_delay,
    }


def parse_forward_line_command(raw):
    """Parse a trigger-forward/copy-first-line command."""
    m = FORWARD_LINE_PATTERN.match(raw.strip())
    if not m:
        return None
    is_loop = m.group("mode").lower() == "forwardloop"
    send_account = m.group("send_account")
    if is_loop and send_account:
        return None
    if int(m.group("batch")) <= 0 or int(m.group("delay")) <= 0:
        return None
    return {
        "mode": m.group("mode").lower(),
        "is_loop": is_loop,
        "batch": int(m.group("batch")),
        "account": m.group("account").lower(),
        "source_id": int(m.group("source_id")),
        "trigger": m.group("trigger"),
        "user": m.group("user"),
        "delay": int(m.group("delay")),
        "line": int(re.search(r"\d+", m.group("line")).group()),
        "send_account": send_account.lower() if send_account else None,
        "no_trace": bool(m.group("no_trace")),
    }


def parse_rep_command(raw):
    """Parse a reply-trigger command for Main, a clone, or random account."""
    match = REP_COMMAND_PATTERN.match(raw.strip())
    if not match:
        return None
    mode = match.group("mode").lower()
    batch = match.group("batch")
    if batch is not None and int(batch) <= 0:
        return None
    delay = match.group("delay")
    timeout = match.group("timeout")
    reply_chat_id = match.group("reply_chat_id")
    has_reply_chain = match.group("reply_mode") is not None
    if delay is not None and not has_reply_chain:
        return None
    if timeout is not None and not has_reply_chain:
        return None
    if mode == "reploop" and batch is None:
        batch = "1"
    if mode == "repall":
        
        
        batch = batch or "1"

    def normalize_account(value):
        value = value.lower()
        if value in {"m", "main"}:
            return "main"
        if value.isdigit():
            return f"clone{int(value)}"
        return value

    delay_value = int(delay) if delay is not None else (0 if has_reply_chain else None)
    if timeout is not None:
        timeout_value = int(timeout)
        
        
    elif has_reply_chain and delay is not None:
        
        
        timeout_value = 10
    else:
        
        timeout_value = 0 if has_reply_chain else None

    return {
        "account": normalize_account(match.group("account")),
        "mode": mode,
        "batch": int(batch) if batch else None,
        "chat_id": (
            int(match.group("chat_id"))
            if match.group("chat_id") is not None else None
        ),
        "trigger_sender_id": (
            int(match.group("chat_id"))
            if match.group("chat_id") is not None else None
        ),
        "trigger": match.group("trigger"),
        "first_text": match.group("first"),
        "delay": delay_value,
        "response_timeout": timeout_value,
        "reply_mode": (match.group("reply_mode") or "send").lower()
        if has_reply_chain else None,
        "reply_account": (
            normalize_account(match.group("reply_account"))
            if match.group("reply_account") is not None else None
        ),
        
        
        "reply_sender_id": int(reply_chat_id) if reply_chat_id is not None else None,
        "line": int(re.search(r"\d+", match.group("line")).group())
        if match.group("line") else None,
    }






def parse_hunt_click_command(raw):
    m = HUNT_CLICK_PATTERN.match(raw.strip())
    if not m:
        return None

    session_ref = m.group("session_ref").lower()
    disappear = bool(m.group("disappear"))
    times_str = m.group("times")
    button_text = m.group("button").strip()
    rest = (m.group("rest") or "").strip()

    times = int(times_str) if times_str else 1

    if session_ref == "main":
        clone_index = 0
    else:
        clone_num_match = re.match(r'clone(\d+)', session_ref)
        if not clone_num_match:
            return None
        clone_index = int(clone_num_match.group(1))

    edit_mode = bool(EDIT_FLAG_PATTERN.search(rest))
    loop_mode = bool(LOOP_FLAG_PATTERN.search(rest))

    if (edit_mode or loop_mode or disappear) and not times_str:
        times = 0

    delay = 0
    df = DELAY_PATTERN.search(rest)
    if df:
        delay = int(df.group("delay"))

    return {
        "session_ref": session_ref,
        "clone_index": clone_index,
        "times": times,
        "button_text": button_text,
        "delay": delay,
        "edit_mode": edit_mode,
        "loop_mode": loop_mode,
        "disappear_mode": disappear,
    }


def parse_stop_click_command(raw):
    m = STOP_CLICK_PATTERN.match(raw.strip())
    if not m:
        return None
    button = m.group("button")
    return {"button_text": button.strip() if button else None}






def parse_direct_click_command(raw):
    m = DIRECT_CLICK_PATTERN.match(raw.strip())
    if not m:
        return None

    button_text = m.group("button").strip()
    link = m.group("link")

    chat_id = None
    username = None
    msg_id = None

    if link:
        link_match = TG_MSG_LINK_PATTERN.match(link.strip())
        if link_match:
            if link_match.group("chat_id"):
                chat_id = int(link_match.group("chat_id"))
            if link_match.group("username"):
                username = link_match.group("username")
            msg_id = int(link_match.group("msg_id"))

    return {
        "button_text": button_text,
        "chain_delay": (
            int(m.group("chain_delay"))
            if m.group("chain_delay") is not None else None
        ),
        "link": link,
        "chat_id": chat_id,
        "username": username,
        "msg_id": msg_id,
    }






def parse_react_command(raw):
    m = REACT_PATTERN.match(raw.strip())
    if not m:
        return None
    emoji = m.group("emoji").strip()
    clones_raw = m.group("clones")
    clone_indices = None
    if clones_raw:
        clone_indices = parse_clone_selector(clones_raw)
    return {
        "emoji": emoji,
        "clone_indices": clone_indices,
    }


def parse_react_with_link_command(raw):
    m = REACT_LINK_PATTERN.match(raw.strip())
    if not m:
        return None

    emoji = m.group("emoji").strip()
    clones_raw = m.group("clones")
    rest = (m.group("rest") or "").strip()
    links = []
    for match in TG_MSG_LINK_PATTERN.finditer(rest):
        link = match.group(0).rstrip(".,!?)]}>")
        if link not in links:
            links.append(link)
    link = links[0] if links else None

    clone_indices = None
    if clones_raw:
        if clones_raw.strip().lower() == "all":
            clone_indices = "all"
        else:
            clone_indices = parse_clone_selector(clones_raw)

    chat_id = None
    username = None
    msg_id = None

    if link:
        link_match = TG_MSG_LINK_PATTERN.match(link.strip())
        if link_match:
            if link_match.group("chat_id"):
                chat_id = int(link_match.group("chat_id"))
            if link_match.group("username"):
                username = link_match.group("username")
            msg_id = int(link_match.group("msg_id"))

    return {
        "emoji": emoji,
        "clone_indices": clone_indices,
        "link": link,
        "links": links,
        "chat_id": chat_id,
        "username": username,
        "msg_id": msg_id,
    }






def parse_show_prof_command(raw):
    m = SHOW_PROF_PATTERN.match(raw.strip())
    if not m:
        return None
    action = m.group("action").lower()
    scope = (m.group("scope") or "all").lower()
    rest = (m.group("rest") or "").strip()
    if scope == "all" and re.match(r"^main(?:\s|$)", rest, re.IGNORECASE):
        scope = "main"
        rest = re.sub(r"^main\s*", "", rest, count=1, flags=re.IGNORECASE)
    targets = []
    if rest:
        for token in re.findall(
            r"""https?://t\.me/[^\s]+|t\.me/[^\s]+|@[^\s]+|-?\d+""",
            rest,
            re.IGNORECASE,
        ):
            token = token.strip("[]()<>.,")
            if token:
                targets.append(token)
    return {
        "action": action,
        "scope": scope,
        "targets": targets,
        
        "usernames": [
            target.lstrip("@")
            for target in targets
            if target.startswith("@")
        ],
    }


def parse_prof_status_command(raw):
    return bool(PROF_STATUS_PATTERN.match(raw.strip()))


def parse_prof_hideall_command(raw):
    return bool(PROF_HIDEALL_PATTERN.match(raw.strip()))






def parse_set_prof_command(raw):
    m = SET_PROF_PATTERN.match(raw.strip())
    if not m:
        return None
    rest = (m.group("rest") or "").strip()
    no_watermark = bool(NOWATER_FLAG_PATTERN.search(rest))
    return {"no_watermark": no_watermark}






def parse_save_media_command(raw):
    m = SAVE_MEDIA_PATTERN.match(raw.strip())
    if not m:
        return None
    return {
        "media_type": m.group("type").lower(),
        "name": m.group("name").strip(),
    }


def parse_send_media_command(raw):
    m = SEND_MEDIA_PATTERN.match(raw.strip())
    if not m:
        return None

    media_type = m.group("type").lower()
    name = m.group("name").strip()
    clones_raw = m.group("clones")
    flags = (m.group("flags") or "").strip()

    clone_indices = None
    if clones_raw:
        if clones_raw.strip().lower() == "all":
            clone_indices = "all"
        else:
            clone_indices = parse_clone_selector(clones_raw)

    include_main = not bool(NO_MAIN_FLAG.search(flags))
    no_trace = bool(NO_TRACE_PATTERN.search(flags))

    return {
        "media_type": media_type,
        "name": name,
        "clone_indices": clone_indices,
        "include_main": include_main,
        "no_trace": no_trace,
    }


def parse_del_media_command(raw):
    m = DEL_MEDIA_PATTERN.match(raw.strip())
    if not m:
        return None
    return {
        "media_type": m.group("type").lower(),
        "name": m.group("name").strip(),
    }


def parse_list_media_command(raw):
    m = LIST_MEDIA_PATTERN.match(raw.strip())
    if not m:
        return None
    t = m.group("type").lower()
    return {"media_type": None if t == "all" else t}






def parse_stop_command(raw):
    m = STOP_PATTERN.match(raw.strip())
    return m.group("text") if m else None






def extract_join_target(text):
    m = INVITE_LINK_PATTERN.search(text)
    if m:
        return {"type": "invite", "hash": m.group("hash")}
    m = USERNAME_LINK_PATTERN.search(text)
    if m:
        uname = m.group("username")
        excluded = {"joinchat", "addstickers", "share", "proxy", "socks", "setlanguage"}
        if uname.lower() not in excluded:
            return {"type": "username", "username": f"@{uname}"}
    m = AT_USERNAME_PATTERN.search(text)
    if m:
        return {"type": "username", "username": f"@{m.group('username')}"}
    return None
