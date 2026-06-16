"""Visitor tracking: log every request, classify bot vs human, and push a
phone alert (via ntfy.sh) the moment a real person engages with the demo.

The point is to catch a recruiter visiting. Bots crawl static HTML constantly;
a human is betrayed by a real browser UA AND engagement (running an analysis,
opening the 3D viewer, the partner page, the letter). We alert loudest on
engagement, quieter on a first human pageview.
"""

import json
import re
import threading
import time
from pathlib import Path

import requests

# PRECISE bot/tool markers — every token below is something that NEVER appears
# in a real human browser User-Agent, so blocking on them cannot catch HR.
# (Deliberately NOT matching vague substrings like "google"/"bing"/"scan" that
#  could appear in an in-app or mobile browser UA.)
_BOT_UA = re.compile(
    r"googlebot|bingbot|yandexbot|baiduspider|duckduckbot|slurp|sogou|exabot|"
    r"seznambot|petalbot|bytespider|gptbot|ccbot|claudebot|amazonbot|"
    r"semrushbot|ahrefsbot|mj12bot|dotbot|blexbot|dataforseo|crawler|spider|"
    r"curl/|wget/|python-requests|python-urllib|go-http-client|okhttp|libwww|"
    r"\bjava/|apache-httpclient|scrapy|httpx|aiohttp|node-fetch|axios/|guzzle|"
    r"nmap|nuclei|masscan|zgrab|wpscan|nikto|sqlmap|dirbuster|gobuster|"
    r"feroxbuster|censys|shodan|httprobe|headlesschrome|phantomjs|"
    r"uptimerobot|pingdom|statuscake|monitis", re.I)
_SCANNER_PATH = re.compile(
    r"\.env|\.git|wp-|/wp/|\.php|/admin|/phpmyadmin|/vendor/|/\.well-known/(?!$)|"
    r"/shell|/config|/owa/|/autodiscover|/cgi-bin|/boaform|/\.aws|/actuator", re.I)

# link-preview / messenger bots — ALLOWED so shared links show a preview card
_PREVIEW_UA = re.compile(
    r"linkedinbot|slackbot|twitterbot|facebookexternalhit|whatsapp|discordbot|"
    r"telegrambot|redditbot|applebot|pinterest|skypeuripreview|vkshare|"
    r"google-inspectiontool|embedly|ogtag|preview", re.I)

# requests that mean a human is USING the tool, not just looking
_ENGAGED = re.compile(r"^/api/analyze|^/private/|^/partner|/letters/|/viewers/")


class Tracker:
    def __init__(self, base: Path, ntfy_topic: str = "", ntfy_server: str = "https://ntfy.sh"):
        self.log_path = base / "visits.jsonl"
        self.ntfy_url = f"{ntfy_server.rstrip('/')}/{ntfy_topic}" if ntfy_topic else ""
        self._lock = threading.Lock()
        self._last_any = {}            # ip -> ts of any alert
        self._last_eng = {}            # ip -> ts of last ENGAGED (loud) alert
        self._seen_recent = {}         # ip -> ts, to detect first-in-a-while

    @staticmethod
    def classify(ua: str, path: str) -> str:
        ua = ua or ""
        if _SCANNER_PATH.search(path):
            return "scanner"
        if _PREVIEW_UA.search(ua):
            return "preview"            # allowed (link-preview cards)
        if _BOT_UA.search(ua):
            return "bot"
        if not ua.strip():
            # empty UA: usually a bot, but could be a human behind a proxy that
            # strips it — log it, but do NOT block (never risk blocking HR).
            return "unknown"
        return "human"

    # only block what we are CERTAIN is automated; "unknown" is left alone
    BLOCK_KINDS = {"bot", "scanner"}

    def handle(self, ip: str, country: str, ua: str, path: str, ref: str) -> bool:
        """Log + alert; return True if the request should be BLOCKED (403)."""
        kind = self.classify(ua, path)
        self.record(ip, country, ua, path, ref, kind)
        return kind in self.BLOCK_KINDS

    def record(self, ip: str, country: str, ua: str, path: str, ref: str,
               kind: str = None):
        if kind is None:
            kind = self.classify(ua, path)
        blocked = kind in self.BLOCK_KINDS
        now = time.time()
        entry = {"ts": round(now), "ip": ip, "cc": country, "kind": kind,
                 "blocked": blocked, "path": path, "ref": ref,
                 "ua": (ua or "")[:180]}
        try:
            with self._lock, self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

        if kind not in ("human",) or not self.ntfy_url:
            return
        # engagement = loud alert; first pageview from a fresh IP = quiet alert
        engaged = bool(_ENGAGED.match(path))
        first_seen = now - self._seen_recent.get(ip, 0) > 6 * 3600
        self._seen_recent[ip] = now
        # loud engaged alert fires independently of the quiet one (once/10min/IP)
        if engaged and now - self._last_eng.get(ip, 0) > 600:
            self._last_eng[ip] = now; self._last_any[ip] = now
            self._push(f"🔥 Someone is USING your demo — {self._where(country, ip)} "
                       f"({path})", title="solar-scout: live visitor", prio="high")
        elif not engaged and first_seen and now - self._last_any.get(ip, 0) > 1800:
            self._last_any[ip] = now
            self._push(f"👀 Demo opened — {self._where(country, ip)}",
                       title="solar-scout: visitor", prio="default")

    @staticmethod
    def _where(cc: str, ip: str) -> str:
        return f"{cc or '??'} · {ip}"

    def _push(self, msg: str, title: str, prio: str):
        try:
            requests.post(self.ntfy_url, data=msg.encode("utf-8"),
                          headers={"Title": title, "Priority": prio, "Tags": "sunny"},
                          timeout=5)
        except Exception:
            pass

    def stats(self, hours: int = 48) -> dict:
        cut = time.time() - hours * 3600
        humans, bots, scanners, engaged, blocked = [], 0, 0, [], 0
        if self.log_path.is_file():
            for line in self.log_path.read_text(encoding="utf-8").splitlines()[-20000:]:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e["ts"] < cut:
                    continue
                if e.get("blocked"):
                    blocked += 1
                if e["kind"] == "human":
                    humans.append(e)
                    if _ENGAGED.match(e["path"]):
                        engaged.append(e)
                elif e["kind"] == "preview":
                    pass                       # allowed, not counted as a visitor
                elif e["kind"] == "scanner":
                    scanners += 1
                else:                          # bot, unknown
                    bots += 1
        # unique human IPs, most recent first
        seen, uniq = set(), []
        for e in reversed(humans):
            if e["ip"] not in seen:
                seen.add(e["ip"]); uniq.append(e)
        return {"window_h": hours, "human_hits": len(humans),
                "unique_humans": len(seen), "bot_hits": bots,
                "scanner_hits": scanners, "blocked_hits": blocked,
                "engaged_hits": len(engaged),
                "recent_humans": uniq[:60],
                "recent_engaged": list(reversed(engaged))[:40]}
