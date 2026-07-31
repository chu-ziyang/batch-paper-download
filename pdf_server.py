"""微型 HTTP 服务器——接收浏览器 POST 的 PDF 二进制，直接存盘。

改进点：
- ThreadingHTTPServer：并发 POST 不互相阻塞
- 写盘 try/except：磁盘满/权限错误时返回 500，浏览器收到响应不会挂起
- 路径遍历防护：os.path.basename + URL 解码后规范化
- 调试日志默认关闭，PDF_SERVER_DEBUG=1 时才写 _debug.log（避免无限增长）
- 端口可经 PDF_SERVER_PORT 环境变量覆盖（默认 9999）
- 鉴权：设置 PDF_SERVER_TOKEN 后，POST 必须携带相同 X-Auth-Token，
  否则返回 403 —— 防止陌生网页向本机写入任意文件
"""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import sys, os, urllib.parse

DIR = sys.argv[1] if len(sys.argv) > 1 else "."
PORT = int(os.environ.get("PDF_SERVER_PORT", "9999"))
DEBUG = os.environ.get("PDF_SERVER_DEBUG") == "1"
# 鉴权 token：设置后必须匹配，未设置则保持向后兼容（不鉴权）
TOKEN = os.environ.get("PDF_SERVER_TOKEN", "")


class Handler(BaseHTTPRequestHandler):
    def _read_body(self):
        """可靠读取完整请求体：按 Content-Length 循环读，兼容 chunked。"""
        length = int(self.headers.get('Content-Length', 0))
        if length > 0:
            data = b''
            while len(data) < length:
                chunk = self.rfile.read(min(65536, length - len(data)))
                if not chunk:
                    break
                data += chunk
            return data
        if (self.headers.get('Transfer-Encoding', '') or '').lower() == 'chunked':
            return self._read_chunked()
        return b''

    def _read_chunked(self):
        data = b''
        while True:
            line = self.rfile.readline().strip()
            try:
                size = int(line.split(b';')[0], 16)
            except ValueError:
                break
            if size == 0:
                break
            data += self.rfile.read(size)
            self.rfile.readline()  # 丢弃块后的 \r\n
        return data

    def _safe_filename(self):
        """从 URL path 提取文件名，防路径遍历（../etc/passwd 之类）。"""
        raw = self.path.lstrip('/') or 'download.pdf'
        raw = urllib.parse.unquote(raw)
        # 统一斜杠后取 basename，丢弃任何目录成分
        fname = os.path.basename(raw.replace('\\', '/'))
        if not fname or fname in ('.', '..'):
            fname = 'download.pdf'
        return fname

    def _cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Auth-Token')

    def do_POST(self):
        if TOKEN and self.headers.get('X-Auth-Token', '') != TOKEN:
            self.send_response(403)
            self.send_header('Content-Type', 'text/plain')
            self._cors_headers()
            self.end_headers()
            self.wfile.write(b'ERR:auth')
            return
        length = int(self.headers.get('Content-Length', 0))
        data = self._read_body()
        if DEBUG:
            with open(os.path.join(DIR, "_debug.log"), "a") as dbg:
                dbg.write(f"cl={length} got={len(data)} te={self.headers.get('Transfer-Encoding','')} ct={self.headers.get('Content-Type','')}\n")
        fname = self._safe_filename()
        path = os.path.join(DIR, fname)
        try:
            with open(path, 'wb') as f:
                f.write(data)
        except OSError as e:
            print(f"FAIL: {path} ({e})")
            self.send_response(500)
            self.send_header('Content-Type', 'text/plain')
            self._cors_headers()
            self.end_headers()
            self.wfile.write(f'ERR:{e}'.encode())
            return
        print(f"SAVED: {path} ({len(data)/1024/1024:.1f} MB) [cl={length}]")
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self._cors_headers()
        self.end_headers()
        self.wfile.write(b'OK')

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def log_message(self, format, *args):
        pass  # 静默


os.makedirs(DIR, exist_ok=True)
print(f"PDF server on :{PORT} -> {os.path.abspath(DIR)} (debug={'on' if DEBUG else 'off'})")
ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
