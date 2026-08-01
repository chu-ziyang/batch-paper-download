#!/usr/bin/env python3
"""
batch-wos-download.py — 批量下载论文 PDF（多出版商，VPN/CARSI 模式）

用法:
    py batch-wos-download.py input.txt ./downloads

input.txt 格式:
    # ---- DOI 模式（以 10. 开头，自动识别出版商）----
    10.1016/j.envres.2026.123905
    doi: 10.1007/s11356-025-36116-w

    # ---- 精确标题搜索（引号包裹，在出版商搜精确匹配）----
    "Molecular transformation of petroleum compounds by hydroxyl radicals"

    # ---- 关键词搜索（普通文本，分词搜索）----
    goethite Fenton hydroxyl radical

    # ---- 手动指定出版商 ----
    acs: 10.1021/acs.est.3c01379
    elsevier: "exact title here"
    springer: 10.1007/s11356-025-36116-w

支持的出版商:
    elsevier (ScienceDirect)    acs (ACS Publications)
    springer (Springer Link)    wiley (Wiley Online Library)
"""

import subprocess, json, base64, sys, os, time, urllib.parse, re, hashlib, secrets, socket
from pathlib import Path

# ── YAML 配置（如未安装 pyyaml，运行: pip install pyyaml）──
try:
    import yaml
except ImportError:
    sys.exit("[!!] 需要 pyyaml 库。请运行: pip install pyyaml")

# ═══════════════════════════════════════════════════
#  配置加载
# ═══════════════════════════════════════════════════

CONFIG_PATH = Path(__file__).parent / "config.yaml"
CONFIG_TEMPLATE = """\
# ═══════════════════════════════════════════════════
#  Batch Paper Download — 用户配置
# ═══════════════════════════════════════════════════
browser: "058c7104"               # bsk browsers 查看

school:
  name: "合肥工业大学"
  english_name: "Hefei University of Technology"

auth:
  method: "vpn"                   # vpn | carsi

  vpn:
    url: "https://webvpn.hfut.edu.cn/"
    login_link: "CAS"
    timeout: 300

  carsi:
    timeout: 300               # 等待手动登录超时（秒）
    probe: ""                  # 可选：本校订阅的一篇论文页 URL，登录后自动验证权限

vpn_prefixes: {}                  # 可选，已知 VPN 前缀（加速启动）

download:
  output_dir: "./downloads"
  skip_existing: true          # 已存在有效 PDF 时跳过
"""


def validate_config(cfg):
    """递归校验配置结构，返回错误信息字符串；通过返回 None（纯函数）。"""
    if not isinstance(cfg, dict):
        return f"config.yaml 应为字典（YAML 映射），当前是 {type(cfg).__name__}"

    for key in ("browser", "school", "auth"):
        if key not in cfg:
            return f"缺少必填字段: {key}"

    if not isinstance(cfg["browser"], str) or not cfg["browser"].strip():
        return "browser 应为非空字符串（bsk browsers 第一列 ID）"

    if not isinstance(cfg["school"], dict):
        return ("school 应为字典格式，例如:\n"
                '  school:\n    name: "合肥工业大学"\n'
                '    english_name: "Hefei University of Technology"')

    if not isinstance(cfg["auth"], dict):
        return ("auth 应为字典格式，不能是字符串。\n"
                "  正确: auth:\n    method: vpn\n"
                "  错误: auth: vpn")

    method = cfg["auth"].get("method", "vpn")
    if method not in ("vpn", "carsi"):
        return f"auth.method 必须是 vpn 或 carsi，当前: {method}"

    if method == "vpn":
        vpn_cfg = cfg["auth"].get("vpn")
        if not isinstance(vpn_cfg, dict) or not vpn_cfg.get("url"):
            return "VPN 模式下 auth.vpn.url 不能为空，请填写学校 VPN 地址"
    else:
        carsi_cfg = cfg["auth"].get("carsi")
        if not isinstance(carsi_cfg, dict):
            return "CARSI 模式下需要 auth.carsi 配置（至少包含 timeout），请参考生成的模板"
        school = cfg["school"]
        if not school.get("english_name"):
            return ("CARSI 模式下 school.english_name 不能为空"
                    "（用于在出版商机构列表中搜索学校）")
    return None


def load_config():
    """加载 config.yaml，不存在则生成模板并退出。"""
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        print(f"[i] 已生成配置模板: {CONFIG_PATH}")
        print("[i] 请编辑此文件，填入你的学校信息和认证方式后重新运行")
        sys.exit(0)

    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except yaml.YAMLError as e:
        sys.exit(f"[!!] config.yaml 格式错误: {e}")

    err = validate_config(cfg)
    if err:
        sys.exit(f"[!!] config.yaml 配置错误: {err}")

    return cfg

config = load_config()
BROWSER = config["browser"]

# 已存在有效 PDF 时跳过（可由 config.yaml download.skip_existing 关闭）
SKIP_EXISTING = bool(config.get("download", {}).get("skip_existing", True))

# PDF 加速服务器状态（HTTP 模式，main 中启动，失败自动回退 base64 模式）
PDF_SERVER = {"port": None, "token": None, "proc": None}

# ═══════════════════════════════════════════════════
#  出版商定义
# ═══════════════════════════════════════════════════

DOI_PREFIX_MAP = {
    "10.1016/": "elsevier", "10.1007/": "springer", "10.1002/": "wiley",
    "10.1021/": "acs",     "10.1039/": "rsc",      "10.1080/": "tandf",
}

PUBLISHERS = {
    "elsevier": {
        "name": "Elsevier (ScienceDirect)",
        "domain": "sciencedirect.com",
        "search_url": "/search?qs={query}",
        # 从文章 URL 提取 PII → 构造 pdfft
        "pii_regex": r'/pii/([^/?]+)',
        "pdf_from_pii": "/science/article/pii/{pii}/pdfft",
        # 搜索结果选择器
        "result_selector": 'a[href*="/pii/"]',
        # CARSI 机构登录
        "carsi_login": {
            "entry_url": "https://www.sciencedirect.com/user/institution/login?targetURL=%2F",
            "steps": [
                {"type": "{school_name}"},
                {"select": "{school_name}"},
            ],
            "success": {
                "url_contains": "sciencedirect.com",
                "url_not_contains": ["/login", "/shibboleth", "wayf.", "idp."],
            },
        },
    },
    "acs": {
        "name": "ACS Publications",
        "domain": "pubs.acs.org",
        "search_url": "/action/doSearch?text={query}",
        # DOI → 直接 PDF
        "pdf_from_doi": "/doi/pdf/{doi}",
        "doi_regex": r'/doi/([^/?]+)',
        "result_selector": 'a[href*="/doi/"]',
        "carsi_login": {
            "entry_url": "https://pubs.acs.org/",
            "steps": [
                {"click_any": ["Find my institution", "Log in"]},
                {"click_any": ["China CERNET Federation", "CARSI Federation", "CERNET"]},
                {"type": "{school_name}"},
                {"select": "{school_name}"},
            ],
            "success": {
                "url_contains": "pubs.acs.org",
                "url_not_contains": ["/login", "/shibboleth", "wayf.", "idp."],
            },
        },
    },
    "springer": {
        "name": "Springer Link",
        "domain": "link.springer.com",
        "search_url": "/search?query={query}",
        "pdf_from_doi": "/content/pdf/{doi}.pdf",
        "doi_regex": r'/(?:chapter|article|book)/([^/?]+)',
        "result_selector": 'a[href*="/article/"],a[href*="/chapter/"]',
        "carsi_login": {
            "entry_url": "https://link.springer.com/",
            "steps": [
                {"click_any": ["Sign up / Log in", "Log in"]},
                {"click_any": ["Log in via Shibboleth or Athens", "Log in via Shibboleth", "Shibboleth"]},
                {"click": "Select your institution"},
                {"type": "{school_name}"},
                {"select": "{school_name}"},
            ],
            "success": {
                "url_contains": "link.springer.com",
                "url_not_contains": ["/login", "/shibboleth", "wayf.", "idp."],
            },
        },
    },
    "wiley": {
        "name": "Wiley Online Library",
        "domain": "onlinelibrary.wiley.com",
        "search_url": "/action/doSearch?AllField={query}",
        "pdf_from_doi": "/doi/pdfdirect/{doi}",
        "doi_regex": r'/doi/([^/?]+)',
        "result_selector": 'a[href*="/doi/"]',
        "carsi_login": {
            "entry_url": "https://onlinelibrary.wiley.com/",
            "steps": [
                {"click_any": ["Login / Register", "Login", "Log in"]},
                {"click": "Institutional"},
                {"type": "{school_name}"},
                {"select": "{school_name}"},
            ],
            "success": {
                "url_contains": "onlinelibrary.wiley.com",
                "url_not_contains": ["/login", "/shibboleth", "wayf.", "idp."],
            },
        },
    },
    "rsc": {
        "name": "RSC Publishing",
        "domain": "pubs.rsc.org",
        "search_url": "/en/search?q={query}",
        "pdf_from_doi": "/en/content/articlepdf/{doi}",
        "doi_regex": r'/article[^/]*/([^/?]+)',
        "result_selector": 'a[href*="/article"]',
        "carsi_login": {
            "entry_url": "https://pubs.rsc.org/",
            "steps": [
                {"click_any": ["Log in", "Login"]},
                {"click_any": ["Find my institution", "Log in via your home institution", "institution"]},
                {"click_any": ["China CERNET Federation", "CARSI Federation", "CERNET"]},
                {"type": "{school_name}"},
                {"select": "{school_name}"},
            ],
            "success": {
                "url_contains": "pubs.rsc.org",
                "url_not_contains": ["/login", "/shibboleth", "wayf.", "idp."],
            },
        },
    },
    "tandf": {
        "name": "Taylor & Francis",
        "domain": "tandfonline.com",
        "search_url": "/search?q={query}",
        "pdf_from_doi": "/doi/pdf/{doi}",
        "doi_regex": r'/doi/([^/?]+)',
        "result_selector": 'a[href*="/doi/"]',
        "carsi_login": {
            "entry_url": "https://www.tandfonline.com/",
            "steps": [
                {"click_any": ["Log in", "Login"]},
                {"click": "Log in via your institution"},
                {"click_any": ["Shibboleth", "China CERNET Federation", "CERNET"]},
                {"type": "{school_name}"},
                {"select": "{school_name}"},
            ],
            "success": {
                "url_contains": "tandfonline.com",
                "url_not_contains": ["/login", "/shibboleth", "wayf.", "idp."],
            },
        },
    },
}

vpn_cache = {}

# ═══════════════════════════════════════════════════
#  bsk CLI 封装
# ═══════════════════════════════════════════════════

def _bsk(*args, timeout=120):
    """底层：调用 bsk CLI，返回 stdout 字符串。"""
    cmd = ["bsk"] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=timeout)
    out = r.stdout.strip()
    if not out and r.stderr.strip():
        # bsk 把错误写到 stderr，stdout 为空时把 stderr 当作返回值
        return "ERR:bsk:" + r.stderr.strip()[:200]
    return out

def bsk_eval(js, sid, timeout=60):
    """执行 JS，timeout 传给 bsk CLI 的 --timeout 参数。
    subprocess 超时多加 15s buffer，避免 subprocess 抢在 bsk 内部超时前杀进程。"""
    return _bsk("evaluate", js, "--session", sid, "--quiet",
                f"--timeout={timeout}s", timeout=timeout + 15)

def bsk_nav(url, sid, timeout=60):
    # bsk 的 --timeout 接受 30s/1m/1500ms 格式，统一用秒（与 bsk_eval 一致）
    return _bsk("navigate", url, "--session", sid,
                "--wait-until", "domcontentloaded", f"--timeout={timeout}s")

def bsk_url(sid):
    return _bsk("evaluate", "window.location.href", "--session", sid, "--quiet", timeout=30)

def bsk_snap(sid):
    return _bsk("snapshot", "--session", sid, timeout=30)

def bsk_json(*args, timeout=30):
    raw = _bsk(*args, "--json", timeout=timeout)
    try: return json.loads(raw) if raw else None
    except: return None

# ═══════════════════════════════════════════════════
#  snapshot 解析
# ═══════════════════════════════════════════════════

def find_ref(snap, text, tag=None):
    """在纯文本 snapshot 中查找包含 text 的元素，返回 @eN ref。"""
    if not isinstance(snap, str):
        return None
    t = text.lower()
    for line in snap.split("\n"):
        m = re.match(r'^(@\w+)\s+(\w+)\s+"(.+)"', line.strip())
        if not m: continue
        ref, role, name = m.group(1), m.group(2), m.group(3)
        if tag and role != tag: continue
        if t in name.lower(): return ref
    return None


def find_ref_any(snap, texts):
    """按候选文本顺序查找第一个匹配元素（支持 click_any 多候选）。"""
    for t in texts or []:
        if not t:
            continue
        ref = find_ref(snap, t)
        if ref:
            return ref
    return None

# ═══════════════════════════════════════════════════
#  会话管理
# ═══════════════════════════════════════════════════

def start_session():
    print("[*] 启动浏览器会话...")
    # 不清理其它会话，避免误杀用户正在运行的其它自动化任务
    time.sleep(1)
    for _ in range(3):
        # 不用 --json（bsk --json 输出含中文引号，解析不可靠），直接读纯文本 session_id
        raw = _bsk("session", "start", "--browser", BROWSER)
        sid = raw.strip().split("\n")[0].strip() if raw else ""
        if sid:
            print(f"  [OK] {sid}")
            return sid
        time.sleep(3)
    print("[!!] 无法启动会话，检查浏览器是否打开且扩展已连接")
    return None

def stop_session(sid):
    if sid:
        _bsk("session", "stop", sid, "--quiet")

# ═══════════════════════════════════════════════════
#  认证入口（VPN / CARSI 分发）
# ═══════════════════════════════════════════════════

def ensure_auth(sid):
    """根据 config 选择 VPN 或 CARSI 认证流程。"""
    method = config["auth"]["method"]
    if method == "vpn":
        return ensure_vpn(sid)
    elif method == "carsi":
        return ensure_carsi(sid)
    return False

# ═══════════════════════════════════════════════════
#  CARSI 认证
# ═══════════════════════════════════════════════════

_carsi_authed = set()  # 已通过 CARSI 认证的出版商


def ensure_carsi(sid):
    """CARSI 模式入口：验证配置，打印提示。实际登录在各出版商首次访问时触发。"""
    school = config.get("school", {})
    print("=" * 50)
    print("CARSI 认证模式")
    print("=" * 50)
    print(f"  [i] 学校: {school.get('name', '未知')} / {school.get('english_name', '未知')}")
    print(f"  [i] 认证将在首次访问各出版商时自动触发")
    return True


def _dismiss_cookie_banner(sid):
    """关闭常见的 Cookie/隐私弹窗。"""
    js = (
        "(function(){"
        "var btns=document.querySelectorAll('button');"
        "for(var i=0;i<btns.length;i++){"
        "var t=btns[i].textContent||'';"
        "if(t.indexOf('Accept')>-1||t.indexOf('接受')>-1"
        "||t.indexOf('同意')>-1||t.indexOf('Allow')>-1"
        "||t.indexOf('全部')>-1){"
        "btns[i].click();return'clicked:'+t.slice(0,30);"
        "}"
        "}"
        "return'none';"
        "})()"
    )
    try:
        r = bsk_eval(js, sid, timeout=5)
        if r and r.startswith("clicked:"):
            time.sleep(2)
    except Exception:
        pass


def carsi_login_done(url, success_cond, entry_url="", seen_auth_hop=False):
    """CARSI 登录成功判定（纯函数，便于测试）。

    - url 必须在出版商域（url_contains）
    - url 不能停留在登录/IdP 中间页（url_not_contains）
    - 若从未观察到认证跳转（seen_auth_hop=False），还要求 URL 已离开入口页
      （防止入口就是出版商首页时，未登录状态被误判为成功）
    """
    if not url:
        return False
    u = url.lower()
    cond = success_cond or {}
    url_contains = (cond.get("url_contains") or "").lower()
    url_not = [x.lower() for x in cond.get("url_not_contains", [])]
    if url_contains and url_contains not in u:
        return False
    if any(x in u for x in url_not):
        return False
    if not seen_auth_hop and entry_url and u == entry_url.lower():
        return False
    return True


# 权限探针通用标记（启发式，只用于提示不阻塞下载）
ACCESS_PROBE_ALLOW = ["download pdf"]
ACCESS_PROBE_DENY = [
    "get access", "purchase", "buy this article", "subscribe",
    "access denied", "pay per view", "no access", "access options",
    "获取访问", "购买", "订阅", "无权限", "机构登录",
]


def access_probe_verdict(text, allow_markers, deny_markers):
    """根据文章页文本判断权限状态：allowed / denied / unknown（纯函数）。"""
    t = (text or "").lower()
    for m in deny_markers or []:
        if m and m.lower() in t:
            return "denied"
    for m in allow_markers or []:
        if m and m.lower() in t:
            return "allowed"
    return "unknown"


def carsi_failure_hint(result):
    """把下载失败结果映射为 CARSI 权限提示；无关问题返回 None（纯函数）。"""
    if not result:
        return None
    if result.startswith("ERR:HTTP"):
        status = result[len("ERR:HTTP"):].strip()
        if status in ("401", "403"):
            return "可能是 CARSI 机构权限未生效：请检查浏览器中是否已通过学校统一身份认证，并确认学校订阅了该期刊"
    if result.startswith("NOTPDF:"):
        return "返回的不是 PDF（可能是付费墙/错误页）：请确认 CARSI 登录后该文章有下载权限"
    return None


def probe_access(sid, pub_key, probe_url=""):
    """CARSI 登录成功后，若配置了探针 URL（一篇本校订阅的文章页），验证权限。

    只输出提示/警告，不阻塞下载——最终以实际 PDF 下载结果为准。
    """
    if not probe_url:
        return None
    pub = PUBLISHERS[pub_key]
    print(f"  [->] 权限探针: {pub['name']}")
    try:
        bsk_nav(probe_url, sid, timeout=45)
    except Exception:
        print("  [i] 探针页面导航未完全加载，继续...")
    time.sleep(5)
    try:
        text = bsk_eval(
            "(function(){return document.body?document.body.innerText.slice(0,8000):'';})()",
            sid, timeout=20) or ""
    except Exception:
        text = ""
    verdict = access_probe_verdict(text, ACCESS_PROBE_ALLOW, ACCESS_PROBE_DENY)
    if verdict == "denied":
        print("  [!] 探针提示: 文章页显示付费/无权限标记，请确认机构订阅（仍会尝试下载）")
    elif verdict == "allowed":
        print("  [i] 探针提示: 文章页显示可下载标记，权限正常")
    else:
        print("  [i] 探针提示: 无法判定权限状态（页面无明确标记）")
    return verdict


def carsi_authenticate(sid, pub_key):
    """对指定出版商执行 CARSI 机构登录。

    流程：
    1. 导航到出版商的机构登录入口页
    2. 关闭 Cookie 弹窗
    3. 尝试自动点击/输入（如 carsi_login.steps 定义）
    4. 等待用户在浏览器中完成学校 IdP 登录
    5. 检测 URL 回到出版商域 → 登录成功
    """
    global _carsi_authed
    if pub_key in _carsi_authed:
        return True

    pub = PUBLISHERS[pub_key]
    carsi_cfg = pub.get("carsi_login")
    if not carsi_cfg:
        print(f"  [!!] {pub['name']} 暂不支持 CARSI 登录，请使用 VPN 模式")
        return False

    school_name = config["school"].get("english_name", "")
    if not school_name:
        print(f"  [!!] school.english_name 为空，请在 config.yaml 中填写学校英文名")
        return False
    carsi_timeout = config["auth"]["carsi"].get("timeout", 300)

    print(f"  [->] CARSI 认证: {pub['name']}")

    # Step 1: 导航到机构登录入口，记录初始 URL
    entry = carsi_cfg.get("entry_url", f"https://{pub['domain']}/")
    try:
        bsk_nav(entry, sid, timeout=30)
    except Exception:
        print(f"  [i] 导航未完全加载，继续...")
    time.sleep(3)
    # 关闭可能弹出的 Cookie 弹窗
    _dismiss_cookie_banner(sid)
    time.sleep(1)
    try:
        entry_url = (bsk_url(sid) or entry).lower()
    except Exception:
        entry_url = entry.lower()

    # Step 2: 执行自动化步骤（点击 / 输入）
    steps = carsi_cfg.get("steps", [])
    for s in steps:
        # click_any 支持多个候选文本，按顺序尝试（出版商 UI 文案会变）
        texts = list(s.get("click_any") or [])
        if not texts and s.get("click"):
            texts = [s["click"]]
        if texts:
            texts = [t.replace("{school_name}", school_name) for t in texts]
            snap = bsk_snap(sid)
            ref = find_ref_any(snap, texts)
            if ref:
                try:
                    _bsk("click", ref, "--session", sid)
                except Exception:
                    pass
                time.sleep(3)
            else:
                print(f"  [i] 未找到按钮（尝试: {', '.join(t[:40] for t in texts)}），继续...")

        if "type" in s:
            type_text = s["type"].replace("{school_name}", school_name)
            js = (
                "(function(){"
                "var ins=document.querySelectorAll("
                "'input[type=\"text\"],input[type=\"search\"],input:not([type])');"
                "for(var i=0;i<ins.length;i++){"
                "if(ins[i].offsetParent!==null){"
                "ins[i].focus();"
                "var setter=Object.getOwnPropertyDescriptor("
                "window.HTMLInputElement.prototype,'value').set;"
                "if(setter){setter.call(ins[i]," + json.dumps(type_text) + ");}"
                "else{ins[i].value=" + json.dumps(type_text) + ";}"
                "ins[i].dispatchEvent(new Event('input',{bubbles:true}));"
                "ins[i].dispatchEvent(new Event('keydown',{bubbles:true,key:'Enter'}));"
                "ins[i].dispatchEvent(new Event('keyup',{bubbles:true,key:'Enter'}));"
                "ins[i].dispatchEvent(new Event('change',{bubbles:true}));"
                "return ins[i].name||ins[i].id||'ok';"
                "}"
                "}"
                "return '';"
                "})()"
            )
            try:
                r = bsk_eval(js, sid, timeout=10)
                if r:
                    print(f"  [i] 已输入学校名到搜索框")
                time.sleep(2)
            except Exception:
                pass

        if "select" in s:
            select_text = s["select"].replace("{school_name}", school_name)
            snap = bsk_snap(sid)
            ref = find_ref(snap, select_text)
            if ref:
                try:
                    _bsk("click", ref, "--session", sid)
                except Exception:
                    pass
                time.sleep(4)

    # Step 3: 等待用户在浏览器中完成 IdP 登录
    # 检查自动化步骤是否生效（URL 应已离开入口页）
    try:
        post_steps_url = (bsk_url(sid) or "").lower()
    except Exception:
        post_steps_url = ""
    if post_steps_url == entry_url:
        print(f"  [i] 自动化步骤未能跳转，请在浏览器中手动完成机构登录")
        print(f"  [i] （如: Log in / Sign in → Institutional / Find my institution → 选择学校）")

    print(f"  [i] 等待登录完成...")
    success_cond = {
        "url_contains": carsi_cfg.get("success", {}).get("url_contains", pub["domain"]),
        "url_not_contains": carsi_cfg.get("success", {}).get(
            "url_not_contains", ["/login", "/shibboleth", "wayf.", "idp."]),
    }
    probe_url = config.get("auth", {}).get("carsi", {}).get("probe", "")

    # seen_auth_hop：是否观察到 URL 离开过出版商域（去过 IdP/登录页）。
    # 对入口=首页的出版商（ACS 等），必须有跳转证据才能判定登录成功。
    seen_auth_hop = False
    deadline = time.time() + carsi_timeout
    while time.time() < deadline:
        try:
            url = (bsk_url(sid) or "").lower()
        except Exception:
            url = ""
        if url:
            cond_contains = (success_cond.get("url_contains") or "").lower()
            if url != entry_url.lower() or (cond_contains and cond_contains not in url):
                seen_auth_hop = True
        if carsi_login_done(url, success_cond, entry_url, seen_auth_hop):
            _carsi_authed.add(pub_key)
            print(f"\n  [OK] {pub['name']} CARSI 认证成功")
            probe_access(sid, pub_key, probe_url)
            return True
        remaining = int(deadline - time.time())
        print(f"  ... 等待登录（剩余 {remaining}s）     ", end="\r")
        time.sleep(5)

    # 超时兜底：用户可能在其它标签页完成了 IdP 登录（cookie 已写入）。
    # 重新加载入口页/出版商首页，用页面状态做最后一次检查。
    print(f"\n  [!] 登录等待超时，重新加载入口页做最后检查...")
    try:
        bsk_nav(entry, sid, timeout=30)
    except Exception:
        pass
    time.sleep(4)
    try:
        url = (bsk_url(sid) or "").lower()
    except Exception:
        url = ""
    # 重检不再要求 URL 离开入口：cookies 已在其它标签页生效时，
    # Elsevier 式入口会自动重定向回主页，首页式入口则靠探针确认。
    if carsi_login_done(url, success_cond, "", seen_auth_hop=True):
        _carsi_authed.add(pub_key)
        print(f"  [OK] {pub['name']} CARSI 认证成功（重检）")
        probe_access(sid, pub_key, probe_url)
        return True

    print(f"  [!!] {pub['name']} CARSI 登录超时")
    if probe_url:
        print(f"  [i] 提示: 可设置 auth.carsi.probe 为学校订阅的一篇文章页 URL，"
              f"重检时若页面无权限标记可自动判定成功")
    return False

# ═══════════════════════════════════════════════════
#  VPN 登录
# ═══════════════════════════════════════════════════

def _is_logged_in(sid):
    """检查 VPN 是否已登录：URL 不在 /login 路径且仍在 VPN 域名内。"""
    url = bsk_url(sid) or ""
    if not url:
        return False
    u = url.lower()
    try:
        vpn_url = config["auth"]["vpn"]["url"]
        vpn_host = vpn_url.split("://")[1].split("/")[0]
        # 提取域名核心部分（如 webvpn.hfut.edu.cn → webvpn）
        vpn_domain_core = vpn_host.split(".")[0]
    except (IndexError, KeyError):
        return False
    return "/login" not in u and vpn_domain_core in u

def ensure_vpn(sid, timeout=None):
    """检测 VPN 登录状态，未登录则提示用户手动登录并轮询等待。

    不依赖 bsk request-help（该命令在部分版本不弹窗）。改为点击登录
    链接后，每 5 秒轮询当前 URL，检测到离开 /login 即视为登录成功。
    """
    if timeout is None:
        timeout = config["auth"]["vpn"].get("timeout", 300)
    vpn_url = config["auth"]["vpn"]["url"]

    print("=" * 50)
    print("VPN 登录检查")
    print("=" * 50)
    bsk_nav(vpn_url, sid); time.sleep(3)
    if _is_logged_in(sid):
        print("  [OK] 已登录")
        return True

    # 需要登录：点击登录链接，然后轮询等待用户在浏览器完成登录
    login_link_text = config["auth"]["vpn"].get("login_link", "CAS")
    print(f"  [!] 需要登录。请在浏览器中完成 {login_link_text} 登录（账号+密码+验证码）")
    print(f"  [i] 等待登录完成，最长 {timeout} 秒（完成后自动继续）...")
    snap = bsk_snap(sid)
    login_ref = find_ref(snap, login_link_text, tag="link")
    if login_ref:
        _bsk("click", login_ref, "--session", sid)
        time.sleep(3)

    deadline = time.time() + timeout
    while time.time() < deadline:
        if _is_logged_in(sid):
            # 登录后会跳转回 vpn 域，确认一下
            bsk_nav(vpn_url, sid); time.sleep(3)
            if _is_logged_in(sid):
                print(f"\n  [OK] 检测到登录成功")
                return True
        remaining = int(deadline - time.time())
        print(f"  ... 等待登录（剩余 {remaining}s）", flush=True)
        time.sleep(5)
    print("\n  [!!] 登录超时，请确认 VPN 是否正常")
    return False

# ═══════════════════════════════════════════════════
#  VPN 前缀（出版商 → VPN 编码 URL）
# ═══════════════════════════════════════════════════

def get_vpn_prefix(sid, pub_key):
    """获取出版商的 VPN 编码前缀。优先缓存 → 配置 → 自动探测。"""
    global vpn_cache
    if pub_key in vpn_cache:
        return vpn_cache[pub_key]

    # 快速通道：从 config.yaml 读取已知前缀
    # 注意：vpn_prefixes 只有注释时会解析为 None，需兜底为空字典
    vpn_prefixes = config.get("vpn_prefixes") or {}
    if pub_key in vpn_prefixes and vpn_prefixes[pub_key]:
        vpn_cache[pub_key] = vpn_prefixes[pub_key]
        return vpn_prefixes[pub_key]

    # 自动探测：从 VPN 主页点击出版商链接
    vpn_url = config["auth"]["vpn"]["url"]
    pub = PUBLISHERS[pub_key]
    bsk_nav(vpn_url, sid); time.sleep(3)
    snap = bsk_snap(sid)
    ref = find_ref(snap, pub["domain"].split(".")[0], tag="link")
    if not ref:
        ref = find_ref(snap, pub_key, tag="link")
    if not ref:
        print(f"  [!!] 未找到 {pub['name']} 链接，请添加硬编码前缀")
        return None

    _bsk("click", ref, "--session", sid)
    vpn_host = vpn_url.rstrip("/").split("://")[1]
    for _ in range(15):
        time.sleep(2)
        url = bsk_url(sid)
        # WebVPN 会把目标域名编码成 hex，URL 中不再出现原始域名，
        # 因此改为检测 VPN 代理跳转（vpn_host + /https/）并提取 hex 前缀
        if url and vpn_host in url and "/https/" in url:
            m = re.search(rf'https://{re.escape(vpn_host)}/https/([0-9a-fA-F]+)', url)
            if m:
                # 注意：前缀不带尾部斜杠！_handle_doi/_handle_search 用
                # f"{prefix}{path}" 拼接（path 以 / 开头），带斜杠会变 // 导致 400
                prefix = f"https://{vpn_host}/https/{m.group(1)}"
                vpn_cache[pub_key] = prefix
                print(f"  [OK] {pub['name']}: {prefix[:50]}...")
                return prefix
    print(f"  [!!] {pub['name']} 连接失败，请检查 VPN 是否正常")
    return None


def get_access_url(sid, pub_key):
    """获取出版商访问地址（前缀或原生域名）。

    VPN 模式：返回 VPN 编码前缀
    CARSI 模式：返回原生域名（如 https://www.sciencedirect.com），
               触发 CARSI 认证（如需要），之后直连下载
    """
    if config["auth"]["method"] == "carsi":
        if not carsi_authenticate(sid, pub_key):
            return None
        # CARSI 下直连出版商，不需要 URL 编码
        return f"https://{PUBLISHERS[pub_key]['domain']}"
    else:
        return get_vpn_prefix(sid, pub_key)

# ═══════════════════════════════════════════════════
#  出版商搜索
# ═══════════════════════════════════════════════════

def _search_publisher(sid, pub_key, query, exact=True):
    """
    在出版商网站搜索文章。
    返回 (article_url, publisher_key) 或 None。
    """
    pub = PUBLISHERS[pub_key]
    prefix = get_access_url(sid, pub_key)
    if not prefix:
        return None

    # 精确搜索时加引号（去掉已有的外层引号）
    q = query.strip().strip('"')
    if exact:
        q = f'"{q}"'
    qs = urllib.parse.quote(q, safe='"/:?=&')
    search_url = f"{prefix}{pub['search_url'].format(query=qs)}"

    mode = "精确" if exact else "关键词"
    print(f"  [->] {mode}搜索: {q[:80]}")
    bsk_nav(search_url, sid); time.sleep(5)

    # 提取第一个匹配结果
    js = f"""(function(){{var as=document.querySelectorAll('{pub["result_selector"]}');for(var i=0;i<as.length;i++){{var t=as[i].textContent.trim();if(t.length>20)return JSON.stringify({{text:t.slice(0,80),href:as[i].href}});}}return'{{}}';}})()"""
    r = bsk_eval(js, sid)
    if r and r != '{}':
        try:
            d = json.loads(r)
            print(f"  [OK] {d['text'][:70]}")
            return d["href"]
        except: pass

    # 如果精确搜索无结果，关键词搜索可以回退
    if not exact:
        print(f"  [!] 无搜索结果")
    return None

# ═══════════════════════════════════════════════════
#  DOI 辅助
# ═══════════════════════════════════════════════════

def detect_publisher(doi):
    """按 DOI 前缀识别出版商；无法识别返回 None（不再静默回退 elsevier）。"""
    for prefix, pk in sorted(DOI_PREFIX_MAP.items(), key=lambda x: -len(x[0])):
        if doi.startswith(prefix):
            return pk
    return None

def doi_to_pii(doi):
    """用 CrossRef API 查 DOI → 返回 (publisher_key, PII)。"""
    import urllib.request
    try:
        req = urllib.request.Request(
            f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='')}",
            headers={"User-Agent": "batch-download/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            msg = json.loads(resp.read())["message"]
        pk = detect_publisher(doi)
        pii = None
        for alt in msg.get("alternative-id", []):
            # Elsevier PII 格式：S + 16 位数字（共 17 字符），
            # 旧代码 len==16 会漏掉正确 PII（如 S0045653524001577）
            if alt.startswith("S") and len(alt) >= 16 and alt[1:].isdigit():
                pii = alt; break
        return pk, pii
    except Exception as e:
        print(f"  [i] CrossRef 查询失败: {e}")
        return detect_publisher(doi), None


# CrossRef publisher 字段 → 本脚本支持的出版商 key
PUBLISHER_NAME_KEYWORDS = [
    ("elsevier", "elsevier"),
    ("springer", "springer"),
    ("wiley", "wiley"),
    ("american chemical society", "acs"),
    ("royal society of chemistry", "rsc"),
    ("taylor & francis", "tandf"),
    ("informa uk", "tandf"),   # T&F 在 CrossRef 的注册名
]


def publisher_name_to_key(name):
    """把 CrossRef 的 publisher 字段映射到本脚本的出版商 key，未知返回 None。"""
    n = (name or "").lower()
    for kw, key in PUBLISHER_NAME_KEYWORDS:
        if kw in n:
            return key
    return None


def _urlencode_doi(doi):
    """DOI 用于 URL 路径时统一编码（先解码防止重复编码）。"""
    return urllib.parse.quote(urllib.parse.unquote(doi), safe='')


def resolve_publisher(doi):
    """DOI → 出版商 key：前缀识别 → CrossRef 兜底 → None（无法识别）。"""
    pk = detect_publisher(doi)
    if pk:
        return pk
    import urllib.request
    try:
        req = urllib.request.Request(
            f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='')}",
            headers={"User-Agent": "batch-download/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            msg = json.loads(resp.read())["message"]
        return publisher_name_to_key(msg.get("publisher"))
    except Exception as e:
        print(f"  [i] CrossRef 出版商查询失败: {e}")
        return None

# ═══════════════════════════════════════════════════
#  PDF 下载（HTTP 优先 / base64 回退，magic 校验）
# ═══════════════════════════════════════════════════

PDF_MAGIC = b'%PDF-'          # PDF 文件头
MIN_PDF_SIZE = 2048           # 小于此值视为损坏（错误页/空包）；2KB 已足以区分合法短文献

def _is_valid_pdf(path):
    """判断文件是否为有效 PDF：>=MIN_PDF_SIZE 且以 %PDF- 开头。"""
    try:
        if not path.exists() or path.stat().st_size < MIN_PDF_SIZE:
            return False
        with open(path, 'rb') as f:
            return f.read(5) == PDF_MAGIC
    except OSError:
        return False

# 分块传输参数
BASE64_CHUNK = 1048576   # 每轮 bsk_eval 传输 1MB（nature-downloader 同款）
CHUNK_TIMEOUT = 60       # 单块超时（秒）


def start_pdf_server(out_dir):
    """启动本地 PDF 接收服务器（HTTP 模式，带随机 token 鉴权）。

    失败（端口占用/环境异常）时返回 None，主流程自动回退 base64 模式。
    """
    global PDF_SERVER
    if PDF_SERVER["port"]:
        return PDF_SERVER["port"], PDF_SERVER["token"]
    try:
        import urllib.request
        # 随机空闲端口，避免与其它服务冲突
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        token = secrets.token_hex(16)
        env = dict(os.environ, PDF_SERVER_PORT=str(port), PDF_SERVER_TOKEN=token)
        proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).parent / "pdf_server.py"), str(out_dir)],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # 等待就绪（GET 返回 501 即视为已监听）
        for _ in range(50):
            time.sleep(0.1)
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=0.5)
            except urllib.error.HTTPError:
                break
            except Exception:
                continue
        else:
            proc.terminate()
            raise RuntimeError("server not ready")
        PDF_SERVER.update(port=port, token=token, proc=proc)
        print(f"  [i] PDF 加速服务器: http://127.0.0.1:{port}（HTTP 模式，带 token 鉴权）")
        return port, token
    except Exception as e:
        print(f"  [i] 启动 PDF 服务器失败，将使用 base64 模式: {e}")
        return None


def stop_pdf_server():
    """结束 PDF 服务器子进程。"""
    proc = PDF_SERVER.get("proc")
    if proc:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def _download_pdf_http(sid, out_path, total_size):
    """HTTP 模式：浏览器把 window.__pdf_buf 一次性 POST 到本地 pdf_server。"""
    port = PDF_SERVER["port"]
    token = PDF_SERVER["token"]
    url = f"http://127.0.0.1:{port}/{urllib.parse.quote(out_path.name)}"
    js = (
        "(async()=>{try{"
        "const r=await fetch(" + json.dumps(url) + ",{method:'POST',"
        "headers:{'Content-Type':'application/octet-stream',"
        "'X-Auth-Token':" + json.dumps(token) + "},"
        "body:window.__pdf_buf});"
        "const t=await r.text();"
        "return r.status+':'+t;"
        "}catch(e){return'ERR:'+e.message;}})()"
    )
    r = bsk_eval(js, sid, timeout=120)
    if not r or not r.startswith("200:"):
        print(f"  [!] HTTP 模式失败: {(r or '无响应')[:100]}")
        return False
    try:
        if _is_valid_pdf(out_path) and out_path.stat().st_size == total_size:
            print(f"  [OK] {out_path.name} ({total_size/1048576:.1f} MB, HTTP)")
            return True
    except OSError:
        pass
    print("  [!] HTTP 落盘校验失败")
    return False


def _download_pdf_base64(sid, out_path, total_size):
    """base64 分块模式：逐块 btoa 回传 → Python 写盘（兼容性兜底）。"""
    total_mb = total_size / 1048576
    total_chunks = (total_size + BASE64_CHUNK - 1) // BASE64_CHUNK
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    try:
        with open(tmp_path, 'wb') as f:
            for chunk_idx, start in enumerate(range(0, total_size, BASE64_CHUNK)):
                end = min(start + BASE64_CHUNK, total_size)
                # 参考 nature-downloader streamToDisk: btoa(32KB 子块拼成的 binary string)
                chunk_js = (
                    "(async()=>{try{"
                    "const b=window.__pdf_buf.slice(" + str(start) + "," + str(end) + ");"
                    "let x='';"
                    "for(let i=0;i<b.length;i+=0x8000){"
                    "x+=String.fromCharCode.apply(null,b.subarray(i,Math.min(i+0x8000,b.length)));"
                    "}"
                    "return btoa(x);"
                    "}catch(e){return'ERR:'+e.message;}})()"
                )
                b64 = bsk_eval(chunk_js, sid, timeout=CHUNK_TIMEOUT)
                if not b64 or b64.startswith("ERR:"):
                    print(f"\n  [!] 块 {chunk_idx+1}/{total_chunks} 传输失败: "
                          f"{b64[:80] if b64 else '无响应'}")
                    return False
                # Padding（btoa 输出只需补到 4 的倍数）
                rem = len(b64) % 4
                if rem:
                    b64 += "=" * (4 - rem)
                f.write(base64.b64decode(b64))
                pct = min(100, end * 100 // total_size)
                print(f"  [i] {end/1048576:.1f}/{total_mb:.1f} MB ({pct}%)  "
                      f"块{chunk_idx+1}/{total_chunks}", end='\r')

        print()  # 换行
        # 原子 rename
        tmp_path.replace(out_path)

        if _is_valid_pdf(out_path):
            size_mb = out_path.stat().st_size / 1048576
            print(f"  [OK] {out_path.name} ({size_mb:.1f} MB)")
            return True
        print("  [!] 落盘后校验失败")
        return False
    except Exception as e:
        print(f"\n  [!!] 写入失败: {e}")
        return False
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def download_pdf(sid, out_path, pdf_url=None, eval_timeout=90):
    """下载 PDF：浏览器 fetch → HTTP POST 本地服务器（优先）→ base64 分块回退。

    借鉴 nature-downloader 的分块策略（pdf-utils.mjs）：
    1. fetch PDF → Uint8Array 存入 window 变量
    2a. HTTP 模式：二进制 POST 到本地 pdf_server（快；带 token 鉴权）
    2b. base64 模式：多轮 bsk_eval 逐块 btoa 回传（每块 1MB），Python 解码写盘
    避免了单次巨型 base64 字符串导致的超时、内存压力和传输失败。
    """
    time.sleep(3)
    # fetch 目标：优先 pdf_url；失败时回退到 window.location.href。
    # WebVPN 会把编码 URL 规范化（如 %2F → /），直接 fetch 构造的 URL 可能 400，
    # 而页面当前地址（location.href）通常可正常下载（Springer/Wiley 实测）。
    if pdf_url:
        targets_js = "[" + json.dumps(pdf_url) + ",window.location.href]"
    else:
        targets_js = "[window.location.href]"

    # ── Step 1: fetch PDF → window.__pdf_buf，校验 %PDF- head ──
    fetch_js = (
        "(async()=>{"
        "const targets=" + targets_js + ";"
        "let lastErr='ERR:NO-TARGET';"
        "for(let i=0;i<targets.length;i++){"
        "try{"
        "const r=await fetch(targets[i],{credentials:'include',redirect:'follow'});"
        "if(!r.ok){lastErr='ERR:HTTP '+r.status;continue;}"
        "const ab=await r.arrayBuffer();"
        "const u=new Uint8Array(ab);"
        "let m='';for(let j=0;j<5&&j<u.length;j++)m+=String.fromCharCode(u[j]);"
        "if(m!=='%PDF-'){lastErr='NOTPDF:'+(r.headers.get('content-type')||'');continue;}"
        "window.__pdf_buf=u;"
        "return'OK:'+u.length;"
        "}catch(e){lastErr='ERR:'+e.message;}"
        "}"
        "return lastErr;"
        "})()"
    )
    r = bsk_eval(fetch_js, sid, timeout=eval_timeout)
    if not r:
        print("  [!] PDF fetch 失败（无响应）")
        return False
    if r.startswith("ERR:HTTP"):
        print(f"  [!] fetch 返回 HTTP 错误: {r[9:]}")
        hint = carsi_failure_hint(r)
        if hint and config.get("auth", {}).get("method") == "carsi":
            print(f"  [!] {hint}")
        return False
    if r.startswith("NOTPDF:"):
        print(f"  [!] 非 PDF 内容: {r[8:]}")
        hint = carsi_failure_hint(r)
        if hint and config.get("auth", {}).get("method") == "carsi":
            print(f"  [!] {hint}")
        return False
    if not r.startswith("OK:"):
        print(f"  [!] PDF fetch 异常: {r[:80]}")
        return False

    total_size = int(r.split(":")[1])
    print(f"  [i] PDF {total_size/1048576:.1f} MB")

    try:
        # ── Step 2a: HTTP 模式（二进制 POST 本地服务器，快；失败自动回退）──
        if PDF_SERVER["port"] and _download_pdf_http(sid, out_path, total_size):
            return True
        print("  [i] HTTP 模式不可用或失败，回退 base64 分块模式...")

        # ── Step 2b: 分块 base64 传输 → 逐块写盘 ──
        return _download_pdf_base64(sid, out_path, total_size)
    finally:
        # 清理 window 变量（best-effort）
        try:
            bsk_eval("delete window.__pdf_buf", sid, timeout=10)
        except Exception:
            pass

# ═══════════════════════════════════════════════════
#  单篇处理核心
# ═══════════════════════════════════════════════════

def process_one(sid, typ, query, pub_override, out_dir):
    raw_safe = re.sub(r'[\\/:*?"<>|]+', '_', query)
    # 截断可能导致不同论文同名互相覆盖：超长时附加短哈希保证唯一
    if len(raw_safe) > 80:
        safe = raw_safe[:80] + "-" + hashlib.md5(query.encode("utf-8")).hexdigest()[:8]
    else:
        safe = raw_safe
    res = {"query": query, "status": "fail"}

    try:
        pub_key = pub_override or (resolve_publisher(query) if typ == "doi" else "elsevier")
        if not pub_key:
            print("  [!!] 无法识别 DOI 的出版商（暂不支持该前缀），可手动指定: "
                  "elsevier: / acs: / springer: / wiley: / rsc: / tandf:")
            res["status"] = "fail"
            res["error"] = "Unknown publisher"
            return res
        pub = PUBLISHERS.get(pub_key, PUBLISHERS["elsevier"])
        print(f"  [i] 出版商: {pub['name']}")

        # 跳过已下载（有效 PDF 视为已存在；可用 config download.skip_existing 关闭）
        out_path = out_dir / f"{safe}.pdf"
        if SKIP_EXISTING and _is_valid_pdf(out_path):
            print(f"  [OK] 已存在 ({out_path.stat().st_size/1024/1024:.1f} MB)，跳过")
            res["status"] = "ok"
            res["file"] = str(out_path)
            return res

        prefix = get_access_url(sid, pub_key)
        if not prefix:
            return res

        pdf_url = None

        # ── 路径 1: DOI ──
        if typ == "doi":
            pdf_url = _handle_doi(sid, query, pub_key, pub, prefix)

        # ── 路径 2: 标题 / 关键词搜索 ──
        elif typ in ("title", "keyword"):
            pdf_url = _handle_search(sid, query, pub_key, pub, prefix,
                                     exact=(typ == "title"))

        if not pdf_url:
            print("  [!!] 无法获取 PDF 链接")
            print("  提示: 1) 检查论文标题是否正确 2) 手动指定出版商 3) 检查 VPN 权限")
            return res

        # ── 下载 ──
        if pdf_url:
            print(f"  [->] PDF: {pdf_url[:100]}...")

            # ACS 特殊处理：PDF 页面（/doi/pdf/...）通过 VPN 加载极慢（>120s），
            # 改为先打开文章页（HTML，加载快）建立会话/cookie，再从文章页提取
            # 真实 PDF 链接（可能含 token），最后用 fetch 直接下载。
            if pub_key == "acs":
                # 构造文章页 URL：{prefix}/doi/{doi_suffix}
                # 复用 pdf_url 的格式，把 /doi/pdf/ 替换为 /doi/
                article_url = pdf_url.replace('/doi/pdf/', '/doi/')
                print("  [i] ACS: 打开文章页建立会话...")
                try:
                    bsk_nav(article_url, sid, timeout=60)
                except Exception:
                    pass
                time.sleep(5)
                # 尝试从文章页获取真实 PDF 链接
                # ACS 文章页的 PDF 链接格式为 /article-pdf/...，不是 /doi/pdf/
                actual = _get_href_containing(sid, 'article-pdf')
                if not actual:
                    actual = _get_href_containing(sid, '/doi/pdf/')
                if actual:
                    # ACS 直接 URL（pubs.acs.org）配合文章页 cookie 可快速下载，
                    # 无需走 VPN 代理（VPN 代理下 ACS PDF 页面极慢 >120s）
                    pdf_url = actual
                    print(f"  [i] 真实 PDF: {pdf_url[:120]}")
                else:
                    print("  [i] 未找到 PDF 链接，使用构造 URL")
            else:
                # 导航容错：PDF 页面可能不触发 domcontentloaded，超时不中断
                try:
                    bsk_nav(pdf_url, sid, timeout=45)
                except Exception:
                    print("  [i] 导航未完成，尝试直接 fetch（页面可能已加载）")
                time.sleep(3)
                cur = bsk_url(sid) or ""
                print(f"  [i] 当前页面: {cur[:90]}")

            dl_timeout = 120 if pub_key == "acs" else 90
            if download_pdf(sid, out_path, pdf_url, eval_timeout=dl_timeout):
                res["status"] = "ok"
                res["file"] = str(out_path)
            else:
                res["status"] = "fail"
                res["error"] = "PDF download failed"
        else:
            res["status"] = "fail"
            res["error"] = "No PDF URL"

    except Exception as e:
        print(f"  [!!] 异常: {e}")
        res["error"] = str(e)
    return res


def _get_href_containing(sid, substring, timeout=15):
    """在页面中查找 href 包含指定子串的第一个链接，返回完整 URL。"""
    js = f"""(function(){{var as=document.querySelectorAll('a');for(var i=0;i<as.length;i++){{var h=as[i].href||'';if(h.indexOf('{substring}')!==-1)return h;}}return'';}})()"""
    r = bsk_eval(js, sid, timeout=timeout)
    return r if r else None


def _handle_doi(sid, query, pub_key, pub, prefix):
    """DOI 路径：根据出版商构造 PDF URL。"""
    doi_enc = urllib.parse.quote(query, safe='')

    # 有 pdf_from_doi 配置的出版商：直接构造 PDF URL
    # （DOI 需 URL 编码，否则 Springer/Wiley 的 /content/pdf/ 路径会 400）
    if "pdf_from_doi" in pub:
        path = pub["pdf_from_doi"].format(doi=doi_enc, pii="")
        return f"{prefix}{path}"

    # Elsevier：需要 PII → /pdfft
    if pub_key == "elsevier":
        _, pii = doi_to_pii(query)
        if pii:
            print(f"  [->] CrossRef PII: {pii}")
            return f"{prefix}/science/article/pii/{pii}/pdfft"
        # 回退：搜索 DOI 提取 PII
        print(f"  [->] 搜索 DOI: {query}")
        bsk_nav(f"{prefix}/search?qs={urllib.parse.quote(query)}", sid)
        time.sleep(5)
        r = bsk_eval("""(function(){var as=document.querySelectorAll('a[href*="/pii/"]');for(var i=0;i<as.length;i++){var t=as[i].textContent.trim();if(t.length>15)return as[i].href;}return'';})()""", sid)
        m = re.search(r'/pii/(S\d+)', r or "")
        if m:
            print(f"  [OK] PII: {m.group(1)}")
            return f"{prefix}/science/article/pii/{m.group(1)}/pdfft"
        return None

    # 通用回退：搜文章 → 提取标识符 → 构造 PDF
    article_url = _search_publisher(sid, pub_key, query, exact=True)
    if not article_url:
        return None
    doi_regex = pub.get("doi_regex", r'/([^/?]+)')
    m = re.search(doi_regex, article_url)
    if m and "pdf_from_doi" in pub:
        return f"{prefix}{pub['pdf_from_doi'].format(doi=m.group(1), pii='')}"
    return None


def _handle_search(sid, query, pub_key, pub, prefix, exact=True):
    """搜索路径。"""
    article_url = _search_publisher(sid, pub_key, query, exact=exact)
    if not article_url:
        # 精确搜索失败 → 回退关键词搜索
        if exact:
            print("  [i] 精确搜索无结果，回退关键词搜索...")
            article_url = _search_publisher(sid, pub_key, query, exact=False)
    if not article_url:
        return None

    # Elsevier：从文章 URL 提取 PII → pdfft
    if pub_key == "elsevier":
        m = re.search(r'/pii/([^/?]+)', article_url)
        if m:
            return f"{prefix}/science/article/pii/{m.group(1)}/pdfft"

    # ACS / Springer / Wiley / RSC / T&F：提取 DOI → 构造 PDF URL
    doi_regex = pub.get("doi_regex", r'/doi/([^/?]+)')
    m = re.search(doi_regex, article_url)
    if m and "pdf_from_doi" in pub:
        return f"{prefix}{pub['pdf_from_doi'].format(doi=_urlencode_doi(m.group(1)), pii='')}"
    if m and "pdf_from_pii" in pub:
        return f"{prefix}{pub['pdf_from_pii'].format(pii=m.group(1), doi='')}"

    # 最终回退：导航到文章页，用 JS 找 PDF 链接
    domain = pub["domain"]
    if "webvpn" not in article_url:
        idx = article_url.find(domain)
        if idx >= 0:
            article_url = prefix + article_url[idx + len(domain):]
    bsk_nav(article_url, sid); time.sleep(4)
    js = """(function(){var as=document.querySelectorAll('a');for(var i=0;i<as.length;i++){var h=as[i].href;if(h&&(h.includes('/pdf')||h.includes('.pdf')))return h;}return'';})()"""
    pdf = bsk_eval(js, sid)
    if pdf and "http" in pdf and "webvpn" not in pdf:
        idx = pdf.find(domain)
        if idx >= 0:
            pdf = prefix + pdf[idx + len(domain):]
    return pdf if pdf else None

# ═══════════════════════════════════════════════════
#  输入解析
# ═══════════════════════════════════════════════════

def parse_input(lines):
    entries = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        l = line.lower()
        # 手动指定出版商: "elsevier: query"
        for pk in PUBLISHERS:
            if l.startswith(f"{pk}:"):
                q = line[len(pk) + 1:].strip()
                t = "doi" if q.startswith("10.") else ("title" if q.startswith('"') else "keyword")
                entries.append((t, q, pk))
                break
        else:
            if l.startswith("doi:"):
                entries.append(("doi", line[4:].strip(), None))
            elif line.startswith('"'):
                entries.append(("title", line.strip(), None))
            elif line.startswith("10."):
                entries.append(("doi", line, None))
            else:
                entries.append(("keyword", line, None))
    return entries

# ═══════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    inp = Path(sys.argv[1])
    # 输出目录：命令行参数优先，缺省用 config.yaml 的 download.output_dir
    if len(sys.argv) >= 3:
        out = Path(sys.argv[2])
    else:
        out = Path(config.get("download", {}).get("output_dir", "./downloads"))
        print(f"[i] 未指定输出目录，使用 config.yaml 的 download.output_dir: {out}")

    if not inp.exists():
        print(f"[!!] 输入文件不存在: {inp}")
        sys.exit(1)

    out.mkdir(parents=True, exist_ok=True)

    entries = parse_input(inp.read_text(encoding="utf-8").splitlines())
    if not entries:
        print("[!!] 输入为空")
        sys.exit(1)

    print(f"\n{'='*50}")
    print(f"Batch Download: {len(entries)} 篇")
    print(f"输出: {out}")
    print(f"{'='*50}")

    sid = start_session()
    if not sid:
        return

    log = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "results": []}

    try:
        if not ensure_auth(sid):
            return

        # 启动本地 PDF 加速服务器（HTTP 模式；失败自动回退 base64）
        start_pdf_server(out)

        ok = 0
        for idx, (typ, query, pub) in enumerate(entries, 1):
            print(f"\n{'─'*50}")
            label = {"doi": "DOI", "title": "TITLE", "keyword": "KW"}[typ]
            print(f"[{idx}/{len(entries)}] {label}  {query[:80]}")
            print(f"{'─'*50}")

            res = None
            for attempt in range(3):
                res = process_one(sid, typ, query, pub, out)
                if res and res["status"] == "ok":
                    break
                if attempt < 2:
                    wait = 5 if attempt == 0 else 15
                    print(f"  [i] 失败，{wait} 秒后重试（还剩 {2 - attempt} 次）...")
                    time.sleep(wait)
            log["results"].append(res)
            if res and res["status"] == "ok":
                ok += 1
            time.sleep(2)

        print(f"\n{'='*50}")
        print(f"结果: {ok}/{len(entries)} 成功")
        # 打印失败详情
        failed = [r for r in log["results"] if r["status"] != "ok"]
        if failed:
            print("失败列表:")
            for f in failed:
                print(f"  - {f['query'][:70]}")
        print(f"{'='*50}")

    except KeyboardInterrupt:
        print("\n[!] 中断")
    finally:
        log["ended"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        (out / "_log.json").write_text(
            json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        stop_pdf_server()
        stop_session(sid)

if __name__ == "__main__":
    main()
