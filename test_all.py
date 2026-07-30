"""batch-wos-download + pdf_server 单元/集成测试。

无需浏览器/bsk。覆盖：
- parse_input（DOI/标题/关键词/出版商前缀/注释）
- detect_publisher（DOI 前缀映射）
- _is_valid_pdf（%PDF magic + 最小体积）
- find_ref（snapshot 文本解析）
- 集成：pdf_server 落盘 + _is_valid_pdf 校验 → 证明空包/错误页不再被当有效 PDF
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent

_spec = importlib.util.spec_from_file_location("bd", HERE / "batch-wos-download.py")
bd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bd)


class TestParseInput(unittest.TestCase):
    def test_doi_bare(self):
        self.assertEqual(
            bd.parse_input(["10.1016/j.envres.2026.123905"]),
            [("doi", "10.1016/j.envres.2026.123905", None)])

    def test_doi_prefix(self):
        self.assertEqual(
            bd.parse_input(["doi: 10.1007/s11356-025-36116-w"]),
            [("doi", "10.1007/s11356-025-36116-w", None)])

    def test_title_quoted(self):
        self.assertEqual(
            bd.parse_input(['"exact title here"']),
            [("title", '"exact title here"', None)])

    def test_keyword(self):
        self.assertEqual(
            bd.parse_input(["goethite fenton hydroxyl"]),
            [("keyword", "goethite fenton hydroxyl", None)])

    def test_publisher_prefix_doi(self):
        self.assertEqual(
            bd.parse_input(["acs: 10.1021/acs.est.3c01379"]),
            [("doi", "10.1021/acs.est.3c01379", "acs")])

    def test_publisher_prefix_title(self):
        self.assertEqual(
            bd.parse_input(['elsevier: "some title"']),
            [("title", '"some title"', "elsevier")])

    def test_publisher_case_insensitive(self):
        self.assertEqual(
            bd.parse_input(["Elsevier: 10.1016/x"]),
            [("doi", "10.1016/x", "elsevier")])

    def test_comments_and_blanks_skipped(self):
        lines = ["# comment", "", "10.1016/x", "   ", "# another"]
        self.assertEqual(bd.parse_input(lines), [("doi", "10.1016/x", None)])


class TestDetectPublisher(unittest.TestCase):
    def test_elsevier(self):
        self.assertEqual(bd.detect_publisher("10.1016/j.x"), "elsevier")

    def test_springer(self):
        self.assertEqual(bd.detect_publisher("10.1007/s1"), "springer")

    def test_acs(self):
        self.assertEqual(bd.detect_publisher("10.1021/acs.x"), "acs")

    def test_wiley(self):
        self.assertEqual(bd.detect_publisher("10.1002/etc.1"), "wiley")

    def test_rsc(self):
        self.assertEqual(bd.detect_publisher("10.1039/x"), "rsc")

    def test_tandf(self):
        self.assertEqual(bd.detect_publisher("10.1080/x"), "tandf")

    def test_unknown_defaults_elsevier(self):
        self.assertEqual(bd.detect_publisher("10.9999/unknown"), "elsevier")


class TestIsValidPdf(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)

    def tearDown(self):
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def test_nonexistent(self):
        self.assertFalse(bd._is_valid_pdf(Path("/no/such/file.pdf")))

    def test_too_small(self):
        Path(self.path).write_bytes(b"%PDF-1.4 short")
        self.assertFalse(bd._is_valid_pdf(Path(self.path)))

    def test_wrong_magic(self):
        # 体积够大但不是 PDF 头（模拟错误页伪装）
        Path(self.path).write_bytes(b"<html>" + b"x" * 60000)
        self.assertFalse(bd._is_valid_pdf(Path(self.path)))

    def test_empty_file(self):
        # 模拟 P1 的空包场景：0 字节
        Path(self.path).write_bytes(b"")
        self.assertFalse(bd._is_valid_pdf(Path(self.path)))

    def test_valid_pdf(self):
        Path(self.path).write_bytes(b"%PDF-1.4\n" + b"\x00" * 60000)
        self.assertTrue(bd._is_valid_pdf(Path(self.path)))


class TestFindRef(unittest.TestCase):
    SNAP = ('@e1 link "CAS统一身份认证登录"\n'
            '@e2 textbox "账号"\n'
            '@e3 button "登录"')

    def test_found(self):
        self.assertEqual(bd.find_ref(self.SNAP, "CAS"), "@e1")

    def test_not_found(self):
        self.assertIsNone(bd.find_ref(self.SNAP, "nonexistent"))

    def test_tag_filter_match(self):
        self.assertEqual(bd.find_ref(self.SNAP, "账号", tag="textbox"), "@e2")

    def test_tag_filter_mismatch(self):
        self.assertIsNone(bd.find_ref(self.SNAP, "账号", tag="button"))

    def test_none_input(self):
        self.assertIsNone(bd.find_ref(None, "x"))

    def test_case_insensitive(self):
        self.assertEqual(bd.find_ref(self.SNAP, "cas"), "@e1")


class TestPdfServerPipeline(unittest.TestCase):
    """集成测试：pdf_server 落盘 + _is_valid_pdf 校验配合。

    这是 P1 修复的核心验证——证明空包和错误页（>50KB 非 PDF）落盘后
    会被 _is_valid_pdf 正确识别为无效，不再被永久跳过。
    """
    PORT = "19999"  # 避开默认 9999，防止与正在运行的服务冲突

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="pdftest_")
        env = dict(os.environ, PDF_SERVER_PORT=cls.PORT, PDF_SERVER_DEBUG="1")
        cls.proc = subprocess.Popen(
            [sys.executable, str(HERE / "pdf_server.py"), cls.tmpdir],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        cls._wait_ready()

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    @classmethod
    def _wait_ready(cls):
        url = f"http://127.0.0.1:{cls.PORT}/"
        for _ in range(50):
            time.sleep(0.1)
            try:
                urllib.request.urlopen(url, timeout=0.5)
            except urllib.error.HTTPError:
                return  # 服务器已在响应（对 GET 返回 501）
            except Exception:
                continue
        raise RuntimeError("pdf_server 未在 5s 内就绪")

    def _post(self, fname, data):
        url = f"http://127.0.0.1:{self.PORT}/{urllib.parse.quote(fname)}"
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read()

    def test_valid_pdf_roundtrip(self):
        data = b"%PDF-1.4\n" + b"\x00" * 60000
        self._post("valid.pdf", data)
        path = Path(self.tmpdir) / "valid.pdf"
        self.assertTrue(path.exists())
        self.assertEqual(path.stat().st_size, len(data))
        self.assertTrue(bd._is_valid_pdf(path))  # 落盘后校验通过

    def test_empty_blob_rejected(self):
        # P1 的真实 bug 场景：浏览器 fetch 到 0 字节空包
        self._post("empty.pdf", b"")
        path = Path(self.tmpdir) / "empty.pdf"
        self.assertTrue(path.exists())
        self.assertEqual(path.stat().st_size, 0)
        self.assertFalse(bd._is_valid_pdf(path))  # 空包被正确拒绝

    def test_error_page_rejected(self):
        # 模拟出版商返回 HTML 错误页（体积 >50KB 但非 PDF）
        html = b"<html><body>Access Denied</body></html>" + b" " * 60000
        self._post("error.pdf", html)
        path = Path(self.tmpdir) / "error.pdf"
        self.assertTrue(path.exists())
        self.assertGreater(path.stat().st_size, 50000)
        self.assertFalse(bd._is_valid_pdf(path))  # 错误页被正确拒绝

    def test_path_traversal_sanitized(self):
        # 路径遍历尝试应被 basename 化，不会写到 tmpdir 之外
        data = b"%PDF-1.4\n" + b"\x00" * 60000
        self._post("../../etc/evil.pdf", data)
        # 文件应落到 tmpdir 下的 evil.pdf，而非上级目录
        self.assertTrue((Path(self.tmpdir) / "evil.pdf").exists())
        self.assertFalse((Path(self.tmpdir).parent.parent.parent / "etc" / "evil.pdf").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
