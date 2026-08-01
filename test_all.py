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

    def test_unknown_prefix_returns_none(self):
        # 未知前缀不再静默回退 elsevier，由 resolve_publisher 用 CrossRef 兜底
        self.assertIsNone(bd.detect_publisher("10.9999/unknown"))


class TestPublisherNameToKey(unittest.TestCase):
    def test_elsevier(self):
        self.assertEqual(bd.publisher_name_to_key("Elsevier BV"), "elsevier")

    def test_springer(self):
        self.assertEqual(bd.publisher_name_to_key("Springer Science and Business Media LLC"), "springer")

    def test_acs(self):
        self.assertEqual(bd.publisher_name_to_key("American Chemical Society (ACS)"), "acs")

    def test_wiley(self):
        self.assertEqual(bd.publisher_name_to_key("Wiley"), "wiley")

    def test_rsc(self):
        self.assertEqual(bd.publisher_name_to_key("Royal Society of Chemistry (RSC)"), "rsc")

    def test_tandf(self):
        self.assertEqual(bd.publisher_name_to_key("Informa UK Limited"), "tandf")

    def test_unknown(self):
        self.assertIsNone(bd.publisher_name_to_key("Some Unknown Publisher"))

    def test_none_input(self):
        self.assertIsNone(bd.publisher_name_to_key(None))


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


class TestFindRefAny(unittest.TestCase):
    SNAP = ('@e1 button "Log in"\n'
            '@e2 link "Find my institution"\n'
            '@e3 link "Log in via Shibboleth or Athens"')

    def test_first_candidate_match(self):
        self.assertEqual(bd.find_ref_any(self.SNAP, ["Log in", "Find my institution"]), "@e1")

    def test_second_candidate_match(self):
        self.assertEqual(bd.find_ref_any(self.SNAP, ["Register", "Find my institution"]), "@e2")

    def test_none_match(self):
        self.assertIsNone(bd.find_ref_any(self.SNAP, ["Register", "Subscribe"]))

    def test_empty_list(self):
        self.assertIsNone(bd.find_ref_any(self.SNAP, []))


class TestCarsiLoginDone(unittest.TestCase):
    """CARSI 登录成功判定（纯函数）。"""
    COND = {
        "url_contains": "sciencedirect.com",
        "url_not_contains": ["/login", "/shibboleth", "wayf.", "idp."],
    }

    def test_happy_path(self):
        # 入口是机构登录页，登录后回到主页且 URL 已变化 → 成功
        self.assertTrue(bd.carsi_login_done(
            "https://www.sciencedirect.com/", self.COND,
            "https://www.sciencedirect.com/user/institution/login?targetURL=%2F"))

    def test_still_on_login_page(self):
        self.assertFalse(bd.carsi_login_done(
            "https://www.sciencedirect.com/login", self.COND, "entry"))

    def test_on_idp_domain(self):
        self.assertFalse(bd.carsi_login_done(
            "https://idp.hfut.edu.cn/auth", self.COND, "entry"))

    def test_url_unchanged_from_entry(self):
        entry = "https://www.sciencedirect.com/user/institution/login?targetURL=%2F"
        self.assertFalse(bd.carsi_login_done(entry, self.COND, entry))

    def test_home_page_entry_without_auth_hop(self):
        # 入口就是出版商首页（ACS 模式）：URL 没离开过首页 → 不能判定登录
        entry = "https://pubs.acs.org/"
        acs_cond = {"url_contains": "pubs.acs.org",
                    "url_not_contains": ["/login", "/shibboleth", "wayf.", "idp."]}
        self.assertFalse(bd.carsi_login_done(entry, acs_cond, entry))

    def test_home_page_entry_with_auth_hop(self):
        # 发生过认证跳转（去过 IdP）后回到首页 → 判定成功
        entry = "https://pubs.acs.org/"
        acs_cond = {"url_contains": "pubs.acs.org",
                    "url_not_contains": ["/login", "/shibboleth", "wayf.", "idp."]}
        self.assertTrue(bd.carsi_login_done(
            entry, acs_cond, entry, seen_auth_hop=True))

    def test_hop_tracked_when_url_left_publisher(self):
        self.assertTrue(bd.carsi_login_done(
            "https://idp.hfut.edu.cn/auth", self.COND, "entry", seen_auth_hop=True) is False)

    def test_case_insensitive(self):
        self.assertTrue(bd.carsi_login_done(
            "HTTPS://WWW.SCIENCEDIRECT.COM/", self.COND, "entry"))

    def test_progress_without_not_cond(self):
        # 无 url_not_contains 时，只要 URL 已离开入口页即可判定
        cond = {"url_contains": "sciencedirect.com"}
        self.assertTrue(bd.carsi_login_done(
            "https://www.sciencedirect.com/login", cond, "entry"))


class TestAccessProbeVerdict(unittest.TestCase):
    ALLOW = ["download pdf", "pdf"]
    DENY = ["get access", "purchase", "access denied", "no access"]

    def test_allow_marker(self):
        self.assertEqual(
            bd.access_probe_verdict("Article content ... Download PDF", self.ALLOW, self.DENY),
            "allowed")

    def test_deny_marker(self):
        self.assertEqual(
            bd.access_probe_verdict("Get access to this article", self.ALLOW, self.DENY),
            "denied")

    def test_deny_takes_priority(self):
        self.assertEqual(
            bd.access_probe_verdict("Get access ... Download PDF", self.ALLOW, self.DENY),
            "denied")

    def test_unknown(self):
        self.assertEqual(
            bd.access_probe_verdict("Something completely different", self.ALLOW, self.DENY),
            "unknown")

    def test_empty_text(self):
        self.assertEqual(bd.access_probe_verdict("", self.ALLOW, self.DENY), "unknown")


class TestCarsiFailureHint(unittest.TestCase):
    def test_http_403_hint(self):
        self.assertIsNotNone(bd.carsi_failure_hint("ERR:HTTP 403"))

    def test_http_401_hint(self):
        self.assertIsNotNone(bd.carsi_failure_hint("ERR:HTTP 401"))

    def test_http_500_no_hint(self):
        self.assertIsNone(bd.carsi_failure_hint("ERR:HTTP 500"))

    def test_notpdf_hint(self):
        self.assertIsNotNone(bd.carsi_failure_hint("NOTPDF:text/html"))

    def test_other_no_hint(self):
        self.assertIsNone(bd.carsi_failure_hint("ERR:timeout"))


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


class TestPdfServerTokenAuth(unittest.TestCase):
    """token 鉴权：设置 PDF_SERVER_TOKEN 后必须携带正确 token 才能写入。"""
    PORT = "20000"  # 避开默认 9999 和集成测试的 19999
    TOKEN = "test-secret-token"

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="pdftoken_")
        env = dict(os.environ, PDF_SERVER_PORT=cls.PORT, PDF_SERVER_TOKEN=cls.TOKEN)
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
                return
            except Exception:
                continue
        raise RuntimeError("pdf_server 未在 5s 内就绪")

    def _post(self, fname, data, token=None):
        url = f"http://127.0.0.1:{self.PORT}/{urllib.parse.quote(fname)}"
        headers = {"X-Auth-Token": token} if token else {}
        req = urllib.request.Request(url, data=data, method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read()

    def test_without_token_rejected(self):
        data = b"%PDF-1.4\n" + b"\x00" * 60000
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post("no_token.pdf", data)
        self.assertEqual(ctx.exception.code, 403)
        self.assertFalse((Path(self.tmpdir) / "no_token.pdf").exists())

    def test_with_token_accepted(self):
        data = b"%PDF-1.4\n" + b"\x00" * 60000
        self.assertEqual(self._post("with_token.pdf", data, token=self.TOKEN), b"OK")
        self.assertTrue((Path(self.tmpdir) / "with_token.pdf").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
