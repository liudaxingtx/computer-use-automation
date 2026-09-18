#!/usr/bin/env python3
"""
Legacy Member Services — a deliberately hostile mock of a bank back-office app.

This is the stand-in for the "no-API, drive-the-UI" reality the assignment
describes. It is intentionally hard to automate against:

  - table-based layout (no CSS grid/flex, no semantic <main>/<section>)
  - deprecated tags (<font>, bgcolor, HTML 4.01 doctype)
  - NO test IDs, NO meaningful ids/classes — only inline styles
  - ALL-CAPS headings, GET-parameter navigation (like a 1990s CGI app)

Routes (all GET, like a real legacy app):
  /                               search form
  /search?member_id=...           member detail  OR  "NO SUCH MEMBER"
  /deactivate?member_id=...       confirmation interstitial
  /do_deactivate?member_id=...    result (success | access denied)

Planted runtime states (the whole point — see DESIGN.md §6):
  - "NO SUCH MEMBER"   -> a legitimate BUSINESS OUTCOME, not a crash
  - confirmation page  -> a RECOVERABLE condition (dismiss or confirm)
  - "ACCESS DENIED"    -> a HARD FAILURE (member 1002 is RESTRICTED)

Run:  python3 server.py   (serves http://localhost:9000)
"""

from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
import base64

PORT = 9000

MEMBERS = {
    "1001": {"name": "JOHN SMITH",  "status": "ACTIVE",     "balance": "$4,250.00"},
    "1002": {"name": "JANE DOE",    "status": "RESTRICTED", "balance": "$12.80"},
    "1003": {"name": "ROBERT CHEN", "status": "ACTIVE",     "balance": "$18,900.00"},
}


def page(title, body):
    """Wrap content in the same deliberately-legacy chrome on every page."""
    return (
        '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">\n'
        "<html><head><title>" + title + "</title></head>\n"
        '<body bgcolor="#c0c0c0">\n'
        '<table width="760" border="0" cellpadding="0" cellspacing="0" align="center">\n'
        '<tr><td bgcolor="#000080" height="48">'
        '<font color="#ffffff" size="5"><b>&nbsp;&nbsp;LEGACY MEMBER SERVICES</b></font>'
        "</td></tr>\n"
        '<tr><td bgcolor="#ffffff" style="padding:16px">\n'
        + body +
        "\n</td></tr>\n"
        '<tr><td bgcolor="#000080" height="20"><font color="#ffffff" size="1">'
        "&nbsp;&copy; 1998 FISERV BANKING CORP</font></td></tr>\n"
        "</table></body></html>"
    )


def search_page():
    body = (
        '<font size="4"><b>MEMBER LOOKUP</b></font><br><br>\n'
        '<form action="/search" method="get">\n'
        '<table border="0" cellpadding="2" cellspacing="2">\n'
        '<tr><td><font size="2">MEMBER ID:</font></td>'
        '<td><input type="text" name="member_id" size="12"></td></tr>\n'
        '<tr><td>&nbsp;</td>'
        '<td><input type="submit" value="SEARCH"></td></tr>\n'
        "</table></form>"
    )
    return page("MEMBER LOOKUP", body)


def not_found_page(mid):
    body = (
        '<font size="4" color="#cc0000"><b>NO SUCH MEMBER</b></font><br><br>\n'
        '<font size="2">MEMBER ID ' + mid + " WAS NOT FOUND IN THE SYSTEM.</font><br><br>\n"
        '<a href="/"><font size="2">BACK TO SEARCH</font></a>'
    )
    return page("MEMBER LOOKUP", body)


def detail_page(mid):
    m = MEMBERS[mid]
    body = (
        '<font size="4"><b>MEMBER DETAIL</b></font><br><br>\n'
        '<table border="1" cellpadding="4" cellspacing="0">\n'
        '<tr><td bgcolor="#e0e0e0"><font size="2"><b>FIELD</b></font></td>'
        '<td bgcolor="#e0e0e0"><font size="2"><b>VALUE</b></font></td></tr>\n'
        '<tr><td><font size="2">MEMBER ID</font></td><td><font size="2">' + mid + "</font></td></tr>\n"
        '<tr><td><font size="2">NAME</font></td><td><font size="2">' + m["name"] + "</font></td></tr>\n"
        '<tr><td><font size="2">STATUS</font></td><td><font size="2">' + m["status"] + "</font></td></tr>\n"
        '<tr><td><font size="2">BALANCE</font></td><td><font size="2">' + m["balance"] + "</font></td></tr>\n"
        "</table><br>\n"
        '<a href="/deactivate?member_id=' + mid + '"><font size="3">DEACTIVATE ACCOUNT</font></a>'
    )
    return page("MEMBER DETAIL", body)


def confirm_page(mid):
    body = (
        '<font size="4"><b>CONFIRM DEACTIVATION</b></font><br><br>\n'
        '<font size="2">ARE YOU SURE YOU WANT TO DEACTIVATE MEMBER ' + mid + "?</font><br><br>\n"
        '<a href="/do_deactivate?member_id=' + mid + '"><font size="3">CONFIRM</font></a>'
        "&nbsp;&nbsp;&nbsp;"
        '<a href="/search?member_id=' + mid + '"><font size="3">CANCEL</font></a>'
    )
    return page("CONFIRM DEACTIVATION", body)


def result_success(mid):
    body = (
        '<font size="4" color="#008000"><b>SUCCESS</b></font><br><br>\n'
        '<font size="2">MEMBER ' + mid + " HAS BEEN DEACTIVATED.</font><br><br>\n"
        '<a href="/"><font size="2">BACK TO SEARCH</font></a>'
    )
    return page("RESULT", body)


def result_denied(mid):
    body = (
        '<font size="4" color="#cc0000"><b>ACCESS DENIED</b></font><br><br>\n'
        '<font size="2">YOU DO NOT HAVE PERMISSION TO DEACTIVATE MEMBER ' + mid + ".</font><br><br>\n"
        '<a href="/search?member_id=' + mid + '"><font size="2">BACK TO DETAIL</font></a>'
    )
    return page("RESULT", body)


def img_button_page():
    """A deliberately NON-SEMANTIC surface: the only control is an <img> 'button'
    with no alt text. The accessibility tree exposes no button/link here, so a
    competent agent must fall back to vision to understand and act on it."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="220" height="64">'
        '<rect width="220" height="64" rx="10" fill="#0000cc"/>'
        '<text x="110" y="42" font-size="26" fill="#ffffff" text-anchor="middle" '
        'font-family="Arial, sans-serif" font-weight="bold">CONTINUE</text>'
        "</svg>"
    )
    data = base64.b64encode(svg.encode("utf-8")).decode()
    body = (
        '<font size="4"><b>IMAGE GATE</b></font><br><br>\n'
        '<font size="2">CLICK THE BUTTON BELOW TO PROCEED.</font><br><br>\n'
        '<img src="data:image/svg+xml;base64,' + data + '" '
        'onclick="location.href=\'/imgbutton_done\'" style="cursor:pointer">'
    )
    return page("IMAGE GATE", body)


def img_button_done_page():
    body = (
        '<font size="4" color="#008000"><b>PROCEEDED</b></font><br><br>\n'
        '<font size="2">YOU CLICKED THE IMAGE BUTTON.</font><br><br>\n'
        '<a href="/"><font size="2">BACK TO SEARCH</font></a>'
    )
    return page("IMAGE GATE", body)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)
        mid = (q.get("member_id") or [""])[0].strip()

        if parsed.path == "/":
            html, code = search_page(), 200
        elif parsed.path == "/search":
            if mid in MEMBERS:
                html, code = detail_page(mid), 200
            else:
                html, code = not_found_page(mid), 200  # 200: business outcome, not an error
        elif parsed.path == "/deactivate":
            html, code = confirm_page(mid) if mid in MEMBERS else not_found_page(mid), 200
        elif parsed.path == "/do_deactivate":
            if mid not in MEMBERS:
                html, code = not_found_page(mid), 200
            elif MEMBERS[mid]["status"] == "RESTRICTED":
                html, code = result_denied(mid), 200  # hard failure surfaced as a page state
            else:
                html, code = result_success(mid), 200
        elif parsed.path == "/imgbutton":
            html, code = img_button_page(), 200
        elif parsed.path == "/imgbutton_done":
            html, code = img_button_done_page(), 200
        else:
            html, code = page("NOT FOUND", "<font size='4'>404</font>"), 404

        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print("[mock]", format % args)


if __name__ == "__main__":
    print(f"Legacy Member Services running on http://localhost:{PORT}")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
