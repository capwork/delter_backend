"""A tiny local website with every delete-confirmation style Delter must tell apart.

  /                 links to every page below
  /native           <button onclick="return confirm(..)"> and a link with data-confirm  -> native dialog
  /themed           button opening a role=dialog modal with Confirm/Cancel               -> custom modal
  /none             delete button that immediately fires fetch DELETE                    -> no confirmation
  /menu             delete hidden in a kebab ("more") dropdown, native confirm           -> found via menu
  /hover            delete shown only when a row is hovered, native confirm              -> aggressive only
  /plain            ordinary page without delete controls (also has a "Clear filters" button: negative text)
  /missing          404
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LAYOUT = "<!doctype html><html><head><meta charset=utf-8><title>{title}</title><style>{css}</style></head><body>{body}</body></html>"
CSS = """
body{font-family:sans-serif;margin:24px} button{padding:6px 12px;margin:4px} table{border-collapse:collapse}
td{padding:8px;border:1px solid #ccc} .dropdown-menu{display:none;border:1px solid #888;padding:6px;background:#fff;width:140px}
.dropdown-menu.open{display:block} .row .row-actions{visibility:hidden} .row:hover .row-actions{visibility:visible}
.backdrop{position:fixed;inset:0;background:#0006;display:none} .backdrop.open{display:block}
.dialog{position:fixed;top:30%;left:35%;width:30%;background:#fff;padding:20px;border-radius:8px;z-index:1000}
"""

PAGES: dict[str, tuple[str, str]] = {
    "/": ("Home", '<h1>Fixture</h1><nav><a href="/native">native</a> <a href="/themed">themed</a> <a href="/none">none</a> '
                  '<a href="/menu">menu</a> <a href="/hover">hover</a> <a href="/plain">plain</a> '
                  '<a href="/missing">broken</a> <a href="/file.pdf">pdf</a> <a href="https://example.org/">external</a></nav>'),
    "/native": ("Native", """<h1>Native confirm</h1>
<table><tr><td>Item 1</td><td><button id="del1" onclick="if(confirm('Delete item 1?')){fetch('/api/items/1',{method:'DELETE'})}">Delete</button></td></tr></table>
<a href="/items/2/delete" data-confirm="Really delete?" onclick="return confirm('Really delete?')">Remove item 2</a>"""),
    "/themed": ("Themed", """<h1>Themed modal</h1>
<table><tr><td>Item A</td><td><button id="delA" onclick="document.getElementById('m').classList.add('open')">Delete</button></td></tr></table>
<div id="m" class="backdrop" role="presentation"><div class="dialog" role="dialog" aria-modal="true">
<p>Are you sure you want to delete this item? This cannot be undone.</p>
<button onclick="document.getElementById('m').classList.remove('open')">Cancel</button>
<button onclick="fetch('/api/items/A',{method:'DELETE'})">Delete</button></div></div>"""),
    "/none": ("None", """<h1>No confirmation</h1>
<table class="t"><tr><td>Item X</td><td><button id="delX" onclick="fetch('/api/items/X',{method:'DELETE'}).then(()=>this.closest('tr').remove())">Delete</button></td></tr></table>"""),
    "/menu": ("Menu", """<h1>Kebab menu</h1>
<div><button id="kebab" aria-haspopup="true" aria-label="More actions" onclick="document.getElementById('dd').classList.toggle('open')">&#8942;</button>
<div id="dd" class="dropdown-menu"><div><a href="#" onclick="return false">Edit</a></div>
<div><a href="#" class="menu-item" onclick="if(confirm('Delete project?')){}; return false">Delete project</a></div></div></div>"""),
    "/hover": ("Hover", """<h1>Hover row</h1>
<ul><li class="row">Row one <span class="row-actions"><button class="icon-trash" aria-label="Delete row" onclick="if(confirm('Delete row?')){}">x</button></span></li></ul>"""),
    "/plain": ("Plain", '<h1>Nothing destructive</h1><button>Clear filters</button> <button>Save</button> <a href="/">home</a>'),
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # silence
        pass

    def handle(self) -> None:
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):  # Chrome drops idle sockets
            pass

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path in PAGES:
            title, body = PAGES[path]
            self.server.hits.append(("GET", path))  # type: ignore[attr-defined]
            self._send(200, LAYOUT.format(title=title, css=CSS, body=body).encode())
        elif path == "/file.pdf":
            self._send(200, b"%PDF-1.4", "application/pdf")
        else:
            self._send(404, b"<h1>not found</h1>")

    def _mutation(self) -> None:
        self.server.hits.append((self.command, self.path))  # type: ignore[attr-defined]
        self._send(200, b"{}", "application/json")

    do_DELETE = do_POST = do_PUT = _mutation  # noqa: N815


class FixtureSite:
    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.hits = []  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    @property
    def hits(self) -> list[tuple[str, str]]:
        return self.server.hits  # type: ignore[attr-defined]

    def mutations(self) -> list[tuple[str, str]]:
        return [h for h in self.hits if h[0] != "GET"]

    def start(self) -> "FixtureSite":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
