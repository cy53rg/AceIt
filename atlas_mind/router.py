"""
atlas_mind/router.py — Groq tool-calling intent router (OpenClicky pattern).

Model tool-call wins over regex. Regex fallback when API unavailable.
Every write goes through atlas_policy.py.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from atlas_policy import PolicyEngine, PolicyOutcome, RiskClass

log = logging.getLogger("atlas_mind.router")

ROUTER_SYSTEM = """You are Atlas intent router. Choose at most ONE tool when the user clearly wants an action.

Use tools for:
- web_search: current events, facts needing the web, "search/look up/latest"
- schedule_job: recurring or one-time Atlas jobs (not calendar)
- calendar_list_events / calendar_create_event / calendar_delete_event: Google Calendar reminders
- github_list_issues / github_create_issue / github_list_repos / github_create_repo / github_push_folder
- find_file / open_path / read_file: search and open files on this PC
- run_task: multi-step screen automation ("do this for me", "click through", "handle this")
- guide_user: highlight/point on screen ("show me where", "guide me to")

Do NOT use tools for:
- greetings, opinions, explanations, coding help, general chat
- stop/focus/voice settings (handled elsewhere)
- simple time/math (handled by stack router)

If no tool fits, respond with a single short sentence — the main chat will take over."""

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information or documentation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_job",
            "description": "Schedule a one-off or recurring job.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short job label"},
                    "goal": {"type": "string", "description": "What Atlas should do when the job runs"},
                    "schedule_type": {
                        "type": "string",
                        "enum": ["one_off", "recurring"],
                        "description": "one_off for a specific time, recurring for cron",
                    },
                    "run_at": {
                        "type": "string",
                        "description": "ISO-8601 datetime for one_off (user local time ok)",
                    },
                    "day_of_week": {
                        "type": "string",
                        "description": "Cron day for recurring: mon,tue,... or mon-fri",
                    },
                    "hour": {"type": "integer", "description": "Hour 0-23 UTC for recurring"},
                    "minute": {"type": "integer", "description": "Minute 0-59 for recurring"},
                },
                "required": ["name", "goal", "schedule_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_list_events",
            "description": "List upcoming Google Calendar events.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_results": {"type": "integer", "description": "Max events (default 10)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_create_event",
            "description": "Create a Google Calendar reminder/event.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Event title"},
                    "start": {"type": "string", "description": "Start datetime ISO-8601 (local ok)"},
                    "end": {"type": "string", "description": "Optional end datetime ISO-8601"},
                    "description": {"type": "string"},
                },
                "required": ["title", "start"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_delete_event",
            "description": "Delete a calendar event by event ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string"},
                },
                "required": ["event_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_list_repos",
            "description": "List the user's GitHub repositories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "per_page": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_create_repo",
            "description": "Create a new GitHub repository under the authenticated user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "private": {"type": "boolean"},
                    "description": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_push_folder",
            "description": "Git add/commit/push a local folder to a GitHub repo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_path": {"type": "string", "description": "Local folder path"},
                    "repo": {"type": "string", "description": "owner/repo"},
                    "commit_message": {"type": "string"},
                    "branch": {"type": "string"},
                },
                "required": ["folder_path", "repo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_list_issues",
            "description": "List open issues in a GitHub repository (owner/repo).",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "owner/repo"},
                    "state": {"type": "string", "enum": ["open", "closed", "all"]},
                },
                "required": ["repo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_create_issue",
            "description": "Create a GitHub issue in a repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "owner/repo"},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["repo", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_list_messages",
            "description": "List recent Gmail inbox messages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_results": {"type": "integer"},
                    "query": {"type": "string", "description": "Gmail search query"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_send_message",
            "description": "Send an email via Gmail.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notion_search_pages",
            "description": "Search Notion pages shared with Atlas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notion_append_block",
            "description": "Append a paragraph to a Notion page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["page_id", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_file",
            "description": "Search the local file index by name or keywords.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Filename or description to find"},
                    "max_results": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_path",
            "description": "Open a file or folder, or open the best match for a search query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Full path to open"},
                    "query": {"type": "string", "description": "Search query if path unknown"},
                    "reveal_only": {
                        "type": "boolean",
                        "description": "Reveal in Explorer without launching the file",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read text from a file on disk (policy-gated).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "query": {"type": "string", "description": "Find file by name if path omitted"},
                    "max_chars": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_task",
            "description": "Start autonomous multi-step screen task automation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Goal to accomplish on screen"},
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "guide_user",
            "description": "Highlight a UI element on screen for the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "What to find on screen"},
                    "instruction": {"type": "string", "description": "Optional extra context"},
                },
                "required": ["target"],
            },
        },
    },
]

_SEARCH_RE = re.compile(
    r"(?i)(?:search(?:\s+the\s+web)?\s+for|look\s+up|google|what(?:'s| is) the latest on)\s+(.+)"
)
_GMAIL_LIST_RE = re.compile(
    r"(?i)(?:check|read|show|list)\s+(?:my\s+)?(?:email|inbox|gmail)(?:\s+for\s+(.+))?$"
)
_GMAIL_SEND_RE = re.compile(
    r"(?i)send (?:an?\s+)?email to (\S+@\S+)\s+(?:with subject|subject|re:?)\s+['\"]?([^'\"]+?)['\"]?\s+(?:saying|body|message)\s+(.+)$"
)
_NOTION_SEARCH_RE = re.compile(
    r"(?i)(?:search|find)\s+notion(?:\s+for)?\s+(.+)"
)
_GITHUB_ISSUE_CREATE_RE = re.compile(
    r"(?i)(?:create|open|file)\s+(?:a\s+)?(?:github\s+)?issue\s+(?:in|on|for)\s+(\S+/\S+)\s+(?:titled?|called?|named?)\s+['\"]?(.+?)['\"]?$"
)
_GITHUB_ISSUES_RE = re.compile(
    r"(?i)(?:list|show|check)\s+(?:the\s+)?(?:open\s+)?issues\s+(?:in|on|for)\s+(\S+/\S+)"
)
_SCHEDULE_RE = re.compile(
    r"(?i)(?:every\s+\w+|schedule)\s+(.+)"
)
_CALENDAR_REMIND_RE = re.compile(
    r"(?i)^remind me\s+(?:to\s+)?(.+?)\s+tomorrow\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*$"
)
_GITHUB_CREATE_REPO_RE = re.compile(
    r"(?i)(?:create|make)\s+(?:a\s+)?(?:new\s+)?(?:github\s+)?repo(?:sitory)?\s+(?:called|named)?\s*['\"]?(\S+?)['\"]?(?:\s+and\s+push)?\s*$"
)
_GITHUB_PUSH_RE = re.compile(
    r"(?i)push\s+(?:this\s+)?(?:folder|directory|project)\s+(?:to\s+)?(?:github\s+)?(?:repo\s+)?(\S+/\S+)?"
)
_FIND_FILE_RE = re.compile(
    r"(?i)(?:find|search for|locate|where is)\s+(?:my\s+)?(.+?)(?:\s+file)?\??$"
)
_OPEN_FILE_RE = re.compile(
    r"(?i)^open\s+(?:my\s+)?(.+?)[\.!?]?$"
)
_READ_FILE_RE = re.compile(
    r"(?i)(?:read|show(?:\s+me)?)\s+(?:the\s+)?(?:contents?\s+of\s+)?(?:my\s+)?(.+?)[\.!?]?$"
)


def _default_github_repo(engine: Any) -> str:
    prefs = {}
    try:
        prefs = engine.get_user_prefs() if hasattr(engine, "get_user_prefs") else {}
    except Exception:
        pass
    return str(
        prefs.get("github_default_repo")
        or os.environ.get("ATLAS_GITHUB_DEFAULT_REPO")
        or ""
    ).strip()


def _parse_reminder_calendar(raw: str) -> Optional[dict[str, Any]]:
    m = _CALENDAR_REMIND_RE.match((raw or "").strip())
    if not m:
        return None
    title = m.group(1).strip().rstrip(".")
    hour = int(m.group(2))
    minute = int(m.group(3) or 0)
    ampm = (m.group(4) or "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    start = (datetime.now() + timedelta(days=1)).replace(
        hour=hour, minute=minute, second=0, microsecond=0,
    )
    end = start + timedelta(minutes=30)
    return {
        "title": title,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }


def _router_model() -> str:
    return (
        os.environ.get("ATLAS_ROUTER_MODEL")
        or os.environ.get("ATLAS_FAST_MODEL")
        or "llama-3.1-8b-instant"
    ).strip()


def _policy_for_engine(engine: Any) -> PolicyEngine:
    return PolicyEngine(engine.memory.db_path, user_id=engine.user_id)


def _policy_ctx(engine: Any) -> dict[str, Any]:
    return {
        "safety_mode": str(getattr(engine, "safety_mode", "off")),
        "fs_access_active": bool(getattr(engine, "_fs_access_active", False)),
        "execution_blocked": bool(getattr(engine, "execution_blocked", False)),
    }


def classify_with_model(client: Any, text: str) -> Optional[tuple[str, dict[str, Any]]]:
    """Call Groq with tools. Returns (tool_name, args) or None."""
    if not (os.environ.get("GROQ_API_KEY") or "").strip():
        return None
    try:
        resp = client.chat.completions.create(
            model=_router_model(),
            messages=[
                {"role": "system", "content": ROUTER_SYSTEM},
                {"role": "user", "content": text},
            ],
            tools=TOOL_DEFINITIONS,
            tool_choice="auto",
            temperature=0,
            max_tokens=300,
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return None
        tc = msg.tool_calls[0]
        name = tc.function.name
        args = json.loads(tc.function.arguments or "{}")
        if not isinstance(args, dict):
            return None
        return name, args
    except Exception as exc:
        log.debug("router model classify failed: %s", exc)
        return None


def regex_tool_fallback(text: str) -> Optional[tuple[str, dict[str, Any]]]:
    """Heuristic fallback when Groq tools unavailable."""
    raw = (text or "").strip()
    if not raw:
        return None

    m = _SEARCH_RE.search(raw)
    if m:
        return "web_search", {"query": m.group(1).strip().rstrip("?.!")}

    m = _GITHUB_ISSUE_CREATE_RE.search(raw)
    if m:
        return "github_create_issue", {
            "repo": m.group(1).strip(),
            "title": m.group(2).strip().rstrip("?.!"),
            "body": "",
        }

    m = _GITHUB_ISSUES_RE.search(raw)
    if m:
        return "github_list_issues", {"repo": m.group(1).strip(), "state": "open"}

    cal = _parse_reminder_calendar(raw)
    if cal:
        return "calendar_create_event", cal

    m = _GITHUB_CREATE_REPO_RE.search(raw)
    if m:
        return "github_create_repo", {"name": m.group(1).strip(), "private": False}

    m = _GITHUB_PUSH_RE.search(raw)
    if m:
        repo = (m.group(1) or "").strip()
        return "github_push_folder", {
            "folder_path": ".",
            "repo": repo,
        }

    m = _OPEN_FILE_RE.match(raw)
    if m and len(m.group(1).strip()) > 2:
        return "open_path", {"query": m.group(1).strip()}

    m = _FIND_FILE_RE.match(raw)
    if m and len(m.group(1).strip()) > 2:
        return "find_file", {"query": m.group(1).strip()}

    m = _READ_FILE_RE.match(raw)
    if m and len(m.group(1).strip()) > 2:
        return "read_file", {"query": m.group(1).strip()}

    m = _GMAIL_LIST_RE.search(raw)
    if m:
        query = (m.group(1) or "").strip()
        return "gmail_list_messages", {"max_results": 10, "query": query}

    m = _GMAIL_SEND_RE.search(raw)
    if m:
        return "gmail_send_message", {
            "to": m.group(1).strip(),
            "subject": m.group(2).strip(),
            "body": m.group(3).strip(),
        }

    m = _NOTION_SEARCH_RE.search(raw)
    if m:
        return "notion_search_pages", {"query": m.group(1).strip().rstrip("?.!")}

    if _SCHEDULE_RE.search(raw):
        return "schedule_job", {
            "name": raw[:60],
            "goal": raw,
            "schedule_type": "recurring",
            "day_of_week": "mon",
            "hour": 8,
            "minute": 0,
        }

    task_m = re.match(
        r"(?i)^(?:please\s+)?(?:do this for me|handle this for me|automate this)(?:[:\s]+(.+))?$",
        raw,
    )
    if task_m:
        task = (task_m.group(1) or raw).strip()
        return "run_task", {"task": task}

    return None


def _tool_web_search(args: dict[str, Any]) -> str:
    from atlas_research import search_task_docs

    query = str(args.get("query") or "").strip()
    if not query:
        return "I need a search query."
    results = search_task_docs(query, max_results=5)
    if not results:
        return f"I couldn't find web results for \"{query}\"."
    lines = [f"Here's what I found for \"{query}\":"]
    for item in results[:5]:
        title = item.get("title") or "Untitled"
        snippet = (item.get("snippet") or "")[:220]
        url = item.get("url") or ""
        lines.append(f"• {title} — {snippet}")
        if url:
            lines.append(f"  {url}")
    return "\n".join(lines)


def _tool_connector(engine: Any, connector_id: str, method: str, args: dict[str, Any]) -> str:
    reg = getattr(engine, "connectors", None)
    if reg is None:
        return f"{connector_id} connector isn't available — connect in Settings."
    ctx = _policy_ctx(engine)
    result = reg.execute(
        connector_id,
        method,
        safety_mode=ctx["safety_mode"],
        fs_access_active=ctx["fs_access_active"],
        execution_blocked=ctx["execution_blocked"],
        **{k: v for k, v in args.items() if v is not None},
    )
    if result.get("ok"):
        return result.get("result", result)
    if result.get("denied"):
        return {"_error": f"{connector_id} action denied: {result.get('reason', 'policy')}"}
    return {"_error": f"{connector_id} error: {result.get('error') or result.get('reason', 'unknown')}"}


def _format_github_result(method: str, payload: Any, args: dict[str, Any]) -> str:
    if isinstance(payload, dict) and payload.get("_error"):
        return str(payload["_error"])
    if method == "list_issues":
        issues = payload.get("issues") if isinstance(payload, dict) else payload
        if isinstance(issues, list):
            if not issues:
                return f"No issues found in {args.get('repo', 'repo')}."
            lines = [f"Issues in {args.get('repo')}:"]
            for issue in issues[:10]:
                if not isinstance(issue, dict):
                    continue
                lines.append(f"  #{issue.get('number', '?')}: {issue.get('title', 'Untitled')}")
            return "\n".join(lines)
    if method == "create_issue":
        num = payload.get("number") if isinstance(payload, dict) else None
        url = payload.get("html_url") if isinstance(payload, dict) else ""
        return f"Created issue #{num} in {args.get('repo')}.{(' ' + url) if url else ''}"
    if method == "list_repos":
        repos = payload.get("repos") if isinstance(payload, dict) else []
        if not repos:
            return "No repositories found on your GitHub account."
        lines = ["Your repositories:"]
        for repo in repos[:15]:
            if isinstance(repo, dict):
                lines.append(f"  • {repo.get('full_name')}")
        return "\n".join(lines)
    if method == "create_repo":
        name = payload.get("full_name") or payload.get("name") if isinstance(payload, dict) else None
        url = payload.get("html_url") if isinstance(payload, dict) else ""
        return f"Created repository {name}.{(' ' + url) if url else ''}"
    if method == "push_folder":
        if isinstance(payload, dict):
            return (
                f"Pushed {payload.get('folder')} to {payload.get('repo')} "
                f"on branch {payload.get('branch', 'main')}."
            )
    return f"GitHub {method}: {json.dumps(payload)[:500]}"


def _tool_github(engine: Any, method: str, args: dict[str, Any]) -> str:
    if method in ("list_issues", "create_issue", "push_folder"):
        if not args.get("repo"):
            default_repo = _default_github_repo(engine)
            if default_repo:
                args = {**args, "repo": default_repo}
            else:
                return "Set a default GitHub repo in Settings → Connected Accounts (owner/repo)."
    payload = _tool_connector(engine, "github", method, args)
    return _format_github_result(method, payload, args)


def _tool_calendar(engine: Any, method: str, args: dict[str, Any]) -> str:
    payload = _tool_connector(engine, "google_calendar", method, args)
    if isinstance(payload, dict) and payload.get("_error"):
        return str(payload["_error"])
    if method == "list_events":
        events = payload.get("events") if isinstance(payload, dict) else []
        if not events:
            return "No upcoming calendar events."
        lines = ["Upcoming calendar events:"]
        for ev in events[:10]:
            if isinstance(ev, dict):
                lines.append(f"  • {ev.get('start', '?')}: {ev.get('summary', '(no title)')}")
        return "\n".join(lines)
    if method == "create_event":
        if isinstance(payload, dict):
            link = payload.get("htmlLink") or ""
            title = payload.get("summary") or args.get("title")
            start = payload.get("start") or args.get("start")
            msg = f"Calendar event created: \"{title}\" at {start}."
            return f"{msg}{(' ' + link) if link else ''}"
    if method == "delete_event":
        return "Calendar event deleted."
    return f"Calendar {method}: {json.dumps(payload)[:500]}"


def _tool_gmail(engine: Any, method: str, args: dict[str, Any]) -> str:
    payload = _tool_connector(engine, "gmail", method, args)
    if isinstance(payload, dict) and payload.get("_error"):
        return str(payload["_error"])
    if method == "list_messages":
        messages = payload.get("messages") if isinstance(payload, dict) else []
        if not messages:
            return "No recent Gmail messages found."
        lines = ["Recent email:"]
        for msg in messages[:10]:
            if isinstance(msg, dict):
                lines.append(
                    f"  • {msg.get('from', '?')}: {msg.get('subject', '(no subject)')}"
                )
        return "\n".join(lines)
    if method == "send_message":
        if isinstance(payload, dict):
            return f"Email sent to {payload.get('to')} — subject: {payload.get('subject')}"
    return f"Gmail {method}: {json.dumps(payload)[:500]}"


def _tool_notion(engine: Any, method: str, args: dict[str, Any]) -> str:
    payload = _tool_connector(engine, "notion", method, args)
    if isinstance(payload, dict) and payload.get("_error"):
        return str(payload["_error"])
    if method == "search_pages":
        pages = payload.get("pages") if isinstance(payload, dict) else []
        if not pages:
            return f"No Notion pages matched \"{args.get('query', '')}\"."
        lines = [f"Notion pages for \"{args.get('query', '')}\":"]
        for page in pages[:10]:
            if isinstance(page, dict):
                lines.append(f"  • {page.get('title', 'Untitled')}")
        return "\n".join(lines)
    if method == "append_block":
        return "Added text to the Notion page."
    return f"Notion {method}: {json.dumps(payload)[:500]}"


def _parse_run_at(value: str) -> Optional[datetime]:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _tool_schedule_job(engine: Any, args: dict[str, Any]) -> str:
    scheduler = getattr(engine, "apscheduler", None)
    if scheduler is None:
        return "Scheduler isn't ready yet — try again in a moment."

    name = str(args.get("name") or "Scheduled job").strip()[:120]
    goal = str(args.get("goal") or "").strip()
    if not goal:
        return "I need to know what the scheduled job should do."

    detail = f"{name}: {goal[:200]}"
    policy = _policy_for_engine(engine)
    auth = policy.authorize(
        "scheduler.create",
        detail,
        RiskClass.WRITE_SCOPED,
        **_policy_ctx(engine),
    )
    if auth.decision == PolicyOutcome.DENY:
        return f"Can't schedule: {auth.reason}"
    if auth.decision in (PolicyOutcome.ASK, PolicyOutcome.CONFIRM_TYPED):
        return f"Scheduling needs your confirmation ({auth.reason})."

    job_id = f"router-{uuid.uuid4().hex[:10]}"
    schedule_type = str(args.get("schedule_type") or "one_off").lower()
    payload = {"goal": goal}

    try:
        if schedule_type == "recurring":
            scheduler.schedule_recurring_cron(
                job_id,
                name=name,
                payload=payload,
                day_of_week=str(args.get("day_of_week") or "mon"),
                hour=int(args.get("hour", 8)),
                minute=int(args.get("minute", 0)),
            )
            return f"Scheduled recurring job \"{name}\" — {goal[:80]}"
        run_at = _parse_run_at(str(args.get("run_at") or ""))
        if run_at is None:
            run_at = datetime.now(timezone.utc).replace(
                hour=int(args.get("hour", 9)),
                minute=int(args.get("minute", 0)),
                second=0,
                microsecond=0,
            )
        scheduler.schedule_one_off(
            job_id,
            name=name,
            run_at=run_at,
            payload=payload,
        )
        return f"Scheduled \"{name}\" for {run_at.strftime('%Y-%m-%d %H:%M UTC')} — {goal[:80]}"
    except Exception as exc:
        log.warning("schedule_job failed: %s", exc)
        return f"Couldn't schedule that job: {exc}"


def _tool_run_task(engine: Any, args: dict[str, Any]) -> str:
    task = str(args.get("task") or "").strip()
    if not task:
        return "I need a task description to start."
    policy = _policy_for_engine(engine)
    auth = policy.authorize(
        "task.run",
        task,
        RiskClass.SHELL_DANGEROUS,
        **_policy_ctx(engine),
    )
    if auth.decision == PolicyOutcome.DENY:
        return f"Can't run that task: {auth.reason}"
    if auth.decision in (PolicyOutcome.ASK, PolicyOutcome.CONFIRM_TYPED):
        return f"That task needs confirmation first ({auth.reason})."
    engine.run_task(task)
    return f"Starting task: {task[:120]}"


def _tool_guide_user(engine: Any, args: dict[str, Any]) -> str:
    target = str(args.get("target") or "").strip()
    instruction = str(args.get("instruction") or "").strip()
    if not target:
        return "Tell me what on screen you'd like me to highlight."
    threading.Thread(
        target=engine._execute_guide,
        args=(target, instruction),
        daemon=True,
        name="atlas-router-guide",
    ).start()
    return f"Looking for \"{target}\" on your screen…"


def _file_indexer(engine: Any) -> Any:
    return getattr(engine, "file_indexer", None)


def _tool_find_file(engine: Any, args: dict[str, Any]) -> str:
    from atlas_files.access import find_file

    query = str(args.get("query") or "").strip()
    if not query:
        return "I need something to search for."
    indexer = _file_indexer(engine)
    if indexer is None:
        return "File search isn't ready — restart the Atlas daemon."
    matches = find_file(indexer, query, top_k=int(args.get("max_results") or 8))
    if not matches:
        return (
            f"No files matched \"{query}\". "
            "Add index roots or rebuild the index in Settings → Filesystem."
        )
    lines = [f"Files matching \"{query}\":"]
    for item in matches:
        lines.append(f"  • {item['name']} — {item['path']}")
    return "\n".join(lines)


def _tool_open_path(engine: Any, args: dict[str, Any]) -> str:
    from atlas_files.access import open_path

    ok, msg = open_path(
        str(args.get("path") or ""),
        query=str(args.get("query") or ""),
        indexer=_file_indexer(engine),
        engine=engine,
        reveal_only=bool(args.get("reveal_only")),
    )
    return msg if ok else msg


def _tool_read_file(engine: Any, args: dict[str, Any]) -> str:
    from atlas_files.access import find_file, read_file_path

    path = str(args.get("path") or "").strip()
    query = str(args.get("query") or "").strip()
    if not path and query:
        indexer = _file_indexer(engine)
        if indexer is None:
            return "File search isn't ready — restart the Atlas daemon."
        matches = find_file(indexer, query, top_k=1)
        if not matches:
            return f"I couldn't find a file matching \"{query}\"."
        path = matches[0]["path"]
    if not path:
        return "I need a file path or search query."
    ok, content = read_file_path(
        path,
        engine=engine,
        max_chars=int(args.get("max_chars") or 8000),
    )
    if not ok:
        return content
    preview = content.strip()
    if len(preview) > 1200:
        preview = preview[:1200] + "\n…"
    return f"Contents of {path}:\n{preview}"


def execute_tool(engine: Any, tool_name: str, args: dict[str, Any]) -> str:
    """Run one router tool; return user-facing message."""
    name = (tool_name or "").strip().lower()
    if name == "web_search":
        return _tool_web_search(args)
    if name == "github_list_issues":
        return _tool_github(engine, "list_issues", args)
    if name == "github_create_issue":
        return _tool_github(engine, "create_issue", args)
    if name == "github_list_repos":
        return _tool_github(engine, "list_repos", args)
    if name == "github_create_repo":
        return _tool_github(engine, "create_repo", args)
    if name == "github_push_folder":
        return _tool_github(engine, "push_folder", args)
    if name == "calendar_list_events":
        return _tool_calendar(engine, "list_events", args)
    if name == "calendar_create_event":
        return _tool_calendar(engine, "create_event", args)
    if name == "calendar_delete_event":
        return _tool_calendar(engine, "delete_event", args)
    if name == "gmail_list_messages":
        return _tool_gmail(engine, "list_messages", args)
    if name == "gmail_send_message":
        return _tool_gmail(engine, "send_message", args)
    if name == "notion_search_pages":
        return _tool_notion(engine, "search_pages", args)
    if name == "notion_append_block":
        return _tool_notion(engine, "append_block", args)
    if name == "find_file":
        return _tool_find_file(engine, args)
    if name == "open_path":
        return _tool_open_path(engine, args)
    if name == "read_file":
        return _tool_read_file(engine, args)
    if name == "schedule_job":
        return _tool_schedule_job(engine, args)
    if name == "run_task":
        return _tool_run_task(engine, args)
    if name == "guide_user":
        return _tool_guide_user(engine, args)
    return f"Unknown tool: {tool_name}"


def resolve_tool_call(
    client: Any,
    text: str,
    *,
    use_regex_fallback: bool = True,
) -> Optional[tuple[str, dict[str, Any]]]:
    """Model first, regex fallback second."""
    picked = classify_with_model(client, text)
    if picked:
        return picked
    if use_regex_fallback:
        return regex_tool_fallback(text)
    return None


def try_route_tools(engine: Any, text: str, *, client: Any = None) -> bool:
    """
    Classify + execute a tool route. Returns True if the turn is complete.
    """
    groq = client
    if groq is None:
        from atlas_core import groq_client as groq

    picked = resolve_tool_call(groq, text)
    if not picked:
        return False

    tool_name, args = picked
    message = execute_tool(engine, tool_name, args)
    engine._finish_router_response(text, message, tool=tool_name)
    return True
