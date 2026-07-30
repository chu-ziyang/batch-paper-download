# Multi-Auth (VPN + CARSI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make batch-wos-download.py shareable — users configure their school (VPN or CARSI) in config.yaml and the script works without code changes.

**Architecture:** Add a YAML config layer between user and script. Replace hardcoded HFUT constants with config reads. Add CARSI authentication as an alternative to VPN — CARSI authenticates directly at each publisher (institutional login → school IdP) and downloads without VPN URL encoding.

**Tech Stack:** Python 3, bsk CLI, PyYAML

## Global Constraints

- `pdf_server.py` — zero changes
- Input parsing, search logic, chunked base64 download, PDF magic validation — zero changes
- Backward compatible: existing VPN users only need to fill config.yaml, nothing else breaks
- New dependency: `pyyaml`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `config.yaml` | **Create** | User-editable school + auth config |
| `batch-wos-download.py` | **Modify** | Read config, dispatch VPN/CARSI, add `carsi_login` steps to PUBLISHERS |
| `skills/SKILL.md` | **Modify** | Update docs to reflect multi-auth |

---

### Task 1: Create config.yaml template

**Files:**
- Create: `config.yaml`

**Interfaces:**
- Produces: `config.yaml` with all fields needed by Task 2+

- [ ] **Step 1: Create config.yaml**

```bash
cat > config.yaml << 'YAMLEOF'
# ═══════════════════════════════════════════════════
#  Batch Paper Download — 用户配置
#  复制此文件到脚本同目录，修改后运行
# ═══════════════════════════════════════════════════

# ── 浏览器 ID（bsk browsers 查看）──
browser: "058c7104"

# ── 学校/机构信息 ──
school:
  name: "合肥工业大学"                           # 中文名
  english_name: "Hefei University of Technology"  # 英文名（CARSI 搜索用）

# ── 认证方式：vpn 或 carsi ──
auth:
  method: "vpn"       # 可选: vpn | carsi

  vpn:
    url: "https://webvpn.hfut.edu.cn/"
    login_link: "CAS"          # VPN 首页的登录入口链接文本
    timeout: 300               # 等待手动登录超时（秒）

  carsi:
    timeout: 300               # 等待手动登录超时（秒）

# ── 已知 VPN 编码前缀（可选，加速启动，不填则自动探测）──
vpn_prefixes:
  # elsevier: "https://webvpn.hfut.edu.cn/https/77726476706e69737468656265737421e7e056d234336155700b8ca891472636a6d29e640e"
  # acs: "https://webvpn.hfut.edu.cn/https/77726476706e69737468656265737421e0e2438f69316b4330079bab"
  # springer: "https://webvpn.hfut.edu.cn/https/77726476706e69737468656265737421fcfe4f976923784277068ea98a1b203a54"
  # wiley: "https://webvpn.hfut.edu.cn/https/77726476706e69737468656265737421fff94d95293564597c1a88be811b343cb55cc5e3193677"

# ── 下载设置 ──
download:
  output_dir: "./downloads"
YAMLEOF

echo "config.yaml created"
```

- [ ] **Step 2: Verify the file**

```bash
python -c "import yaml; c=yaml.safe_load(open('config.yaml',encoding='utf-8')); print('OK:', c['auth']['method'])"
```

Expected: `OK: vpn`

---

### Task 2: Add config loading and replace hardcoded constants

**Files:**
- Modify: `batch-wos-download.py:29-38`

**Interfaces:**
- Consumes: `config.yaml` (Task 1)
- Produces: `load_config()` → `dict`, global `config` variable

- [ ] **Step 1: Add yaml import with friendly error**

Replace line 29 (`import subprocess, json, base64, sys, os, time, urllib.parse, re`):

```python
import subprocess, json, base64, sys, os, time, urllib.parse, re
from pathlib import Path

# ── YAML 配置（如未安装 pyyaml，运行: pip install pyyaml）──
try:
    import yaml
except ImportError:
    sys.exit("[!!] 需要 pyyaml 库。请运行: pip install pyyaml")
```

- [ ] **Step 2: Add load_config() function and replace constants**

Replace lines 32-37 (the config comment block + BROWSER + VPN_HOME):

```python
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
    timeout: 300

vpn_prefixes: {}                  # 可选，已知 VPN 前缀（加速启动）

download:
  output_dir: "./downloads"
"""

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

    # 校验必填字段
    required = ["browser", "school", "auth"]
    missing = [k for k in required if k not in cfg]
    if missing:
        sys.exit(f"[!!] config.yaml 缺少必填字段: {', '.join(missing)}")

    method = cfg["auth"].get("method", "vpn")
    if method not in ("vpn", "carsi"):
        sys.exit(f"[!!] auth.method 必须是 vpn 或 carsi，当前: {method}")

    return cfg

config = load_config()
BROWSER = config["browser"]
```

- [ ] **Step 3: Run script to verify config loading**

```bash
py batch-wos-download.py 2>&1 | head -5
```

Expected: script shows help text (from main), not config error. If config.yaml exists and is valid, script proceeds to help/docstring because no input args provided.

---

### Task 3: Add ensure_auth() dispatcher

**Files:**
- Modify: `batch-wos-download.py` — add `ensure_auth()` before `ensure_vpn()`

**Interfaces:**
- Consumes: `config` global (Task 2)
- Produces: `ensure_auth(sid) -> bool`

- [ ] **Step 1: Insert ensure_auth() before ensure_vpn() definition**

Insert after the `stop_session()` function (line 189) and before the VPN section comment (line 190):

```python
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
```

- [ ] **Step 2: Update ensure_vpn() to read from config**

In `ensure_vpn()` (line 202), replace the hardcoded reference. Change:

```python
bsk_nav(VPN_HOME, sid); time.sleep(3)
```

to:

```python
vpn_url = config["auth"]["vpn"]["url"]
bsk_nav(vpn_url, sid); time.sleep(3)
```

And change the `timeout` default from `300` to read from config:

```python
def ensure_vpn(sid, timeout=None):
```

Add at the top of the function body:

```python
    if timeout is None:
        timeout = config["auth"]["vpn"].get("timeout", 300)
```

Also, in the login link click logic (line 219-222), replace the hardcoded `"CAS"`:

```python
    login_link_text = config["auth"]["vpn"].get("login_link", "CAS")
    print(f"  [i] 点击 '{login_link_text}' 登录链接...")
    snap = bsk_snap(sid)
    cas_ref = find_ref(snap, login_link_text, tag="link")
```

And update the `_is_logged_in` check to use the configured VPN domain:

Change line 200:
```python
    return "/login" not in u and "webvpn" in u
```

to:

```python
    vpn_domain = config["auth"]["vpn"]["url"].split("://")[1].split("/")[0].split(".")[-2]
    return "/login" not in u and vpn_domain in u
```

- [ ] **Step 3: Update get_vpn_prefix() to use config prefixes**

In `get_vpn_prefix()` (line 243), change line 250:

```python
    if pub_key in _KNOWN_PREFIXES:
```

to:

```python
    vpn_prefixes = config.get("vpn_prefixes", {})
    if pub_key in vpn_prefixes and vpn_prefixes[pub_key]:
        vpn_cache[pub_key] = vpn_prefixes[pub_key]
        return vpn_prefixes[pub_key]
```

And in the regex on line 271, change the hardcoded `webvpn` to use the configured VPN domain:

```python
    vpn_host = config["auth"]["vpn"]["url"].rstrip("/").split("://")[1]
    escaped_host = re.escape(vpn_host)
    m = re.search(fr'({escaped_host}/https/[0-9a-f]+)/', url)
```

And update the URL extraction regex (line 271):
```python
        vpn_host = config["auth"]["vpn"]["url"].rstrip("/").split("://")[1]
        if url and pub["domain"].split(".")[0] in url:
            # 提取 VPN 编码前缀: https://webvpn.xxx.edu.cn/https/HEX.../
            m = re.search(rf'(https://{re.escape(vpn_host)}/https/[0-9a-f]+)/', url)
            if m:
```

- [ ] **Step 4: Remove _KNOWN_PREFIXES constant**

Delete lines 104-109 (the `_KNOWN_PREFIXES` dict) since config replaces it.

- [ ] **Step 5: Update main() to call ensure_auth instead of ensure_vpn**

In `main()` (line 708), change:

```python
if not ensure_vpn(sid):
```

to:

```python
if not ensure_auth(sid):
```

- [ ] **Step 6: Verify VPN mode still works with config**

```bash
py batch-wos-download.py 2>&1 | head -10
```

Expected: Script starts normally (provided config.yaml has valid VPN settings).

---

### Task 4: Add CARSI authentication functions

**Files:**
- Modify: `batch-wos-download.py` — insert `ensure_carsi()` and `carsi_authenticate()` after `ensure_auth()`

**Interfaces:**
- Consumes: `config`, `PUBLISHERS` (existing + Task 5), `bsk_*` helpers
- Produces: `ensure_carsi(sid) -> bool`, `carsi_authenticate(sid, pub_key) -> bool`

- [ ] **Step 1: Insert ensure_carsi() and carsi_authenticate()**

Insert after `ensure_auth()` definition (after Task 3 Step 1) and before `ensure_vpn()`:

```python
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


def carsi_authenticate(sid, pub_key):
    """对指定出版商执行 CARSI 机构登录。

    流程：
    1. 导航到出版商的机构登录入口页
    2. 尝试自动点击/输入（如 carsi_login.steps 定义）
    3. 等待用户在浏览器中完成学校 IdP 登录
    4. 检测 URL 回到出版商域 → 登录成功
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
    carsi_timeout = config["auth"]["carsi"].get("timeout", 300)

    print(f"  [->] CARSI 认证: {pub['name']}")

    # Step 1: 导航到机构登录入口
    entry = carsi_cfg.get("entry_url", f"https://{pub['domain']}/")
    try:
        bsk_nav(entry, sid, timeout=30)
    except Exception:
        print(f"  [i] 导航未完全加载，继续...")
    time.sleep(4)

    # Step 2: 执行自动化步骤（点击 / 输入）
    steps = carsi_cfg.get("steps", [])
    for s in steps:
        text = s.get("click", "").replace("{school_name}", school_name)
        if text:
            snap = bsk_snap(sid)
            ref = find_ref(snap, text)
            if ref:
                try:
                    _bsk("click", ref, "--session", sid)
                except Exception:
                    pass
                time.sleep(3)
            else:
                print(f"  [i] 未找到按钮 '{text[:40]}'，尝试继续...")

        if "type" in s:
            type_text = s["type"].replace("{school_name}", school_name)
            js = (
                "(function(){"
                "var ins=document.querySelectorAll("
                "'input[type=\"text\"],input[type=\"search\"],input:not([type])');"
                "for(var i=0;i<ins.length;i++){"
                "if(ins[i].offsetParent!==null){"
                "ins[i].value=" + json.dumps(type_text) + ";"
                "ins[i].dispatchEvent(new Event('input',{bubbles:true}));"
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
    print(f"  [i] 请在浏览器中完成学校 IdP 登录...")
    success_cond = carsi_cfg.get("success", {})
    url_contains = success_cond.get("url_contains", pub["domain"])
    url_not = success_cond.get("url_not_contains", ["/login", "/shibboleth", "wayf.", "idp."])

    deadline = time.time() + carsi_timeout
    while time.time() < deadline:
        try:
            url = (bsk_url(sid) or "").lower()
        except Exception:
            url = ""
        ok = (url_contains in url and
              not any(x in url for x in url_not))
        if ok:
            _carsi_authed.add(pub_key)
            print(f"\n  [OK] {pub['name']} CARSI 认证成功")
            return True
        remaining = int(deadline - time.time())
        print(f"  ... 等待登录（剩余 {remaining}s）     ", end="\r")
        time.sleep(5)

    print(f"\n  [!!] {pub['name']} CARSI 登录超时")
    return False
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "import py_compile; py_compile.compile('batch-wos-download.py', doraise=True); print('Syntax OK')"
```

---

### Task 5: Add carsi_login config to all 6 publishers

**Files:**
- Modify: `batch-wos-download.py` — `PUBLISHERS` dict

**Interfaces:**
- Produces: `PUBLISHERS[pk]["carsi_login"]` consumed by `carsi_authenticate()` (Task 4)

- [ ] **Step 1: Add carsi_login to each publisher in PUBLISHERS**

For each publisher entry, add a `"carsi_login"` key. Edit the PUBLISHERS dict (lines 49-101):

**elsevier** — add after `"result_selector"` line:
```python
        "carsi_login": {
            "entry_url": "https://www.sciencedirect.com/",
            "steps": [
                {"click": "Sign in"},
                {"click": "your institution"},
                {"type": "{school_name}"},
                {"select": "{school_name}"},
            ],
            "success": {
                "url_contains": "sciencedirect.com",
                "url_not_contains": ["/login", "/shibboleth", "wayf.", "idp.", "elsevier.com"],
            },
        },
```

**acs** — add after its `"result_selector"` line:
```python
        "carsi_login": {
            "entry_url": "https://pubs.acs.org/",
            "steps": [
                {"click": "Log in"},
                {"click": "your institution"},
                {"type": "{school_name}"},
                {"select": "{school_name}"},
            ],
            "success": {
                "url_contains": "pubs.acs.org",
                "url_not_contains": ["/login", "/shibboleth", "wayf.", "idp."],
            },
        },
```

**springer** — add after its `"result_selector"` line:
```python
        "carsi_login": {
            "entry_url": "https://link.springer.com/",
            "steps": [
                {"click": "Log in"},
                {"click": "institution"},
                {"type": "{school_name}"},
                {"select": "{school_name}"},
            ],
            "success": {
                "url_contains": "link.springer.com",
                "url_not_contains": ["/login", "/shibboleth", "wayf.", "idp."],
            },
        },
```

**wiley** — add after its `"result_selector"` line:
```python
        "carsi_login": {
            "entry_url": "https://onlinelibrary.wiley.com/",
            "steps": [
                {"click": "Login"},
                {"click": "Institutional"},
                {"type": "{school_name}"},
                {"select": "{school_name}"},
            ],
            "success": {
                "url_contains": "onlinelibrary.wiley.com",
                "url_not_contains": ["/login", "/shibboleth", "wayf.", "idp."],
            },
        },
```

**rsc** — add after its `"result_selector"` line:
```python
        "carsi_login": {
            "entry_url": "https://pubs.rsc.org/",
            "steps": [
                {"click": "Log in"},
                {"click": "institution"},
                {"type": "{school_name}"},
                {"select": "{school_name}"},
            ],
            "success": {
                "url_contains": "pubs.rsc.org",
                "url_not_contains": ["/login", "/shibboleth", "wayf.", "idp."],
            },
        },
```

**tandf** — add after its `"result_selector"` line:
```python
        "carsi_login": {
            "entry_url": "https://www.tandfonline.com/",
            "steps": [
                {"click": "Log in"},
                {"click": "Institution"},
                {"type": "{school_name}"},
                {"select": "{school_name}"},
            ],
            "success": {
                "url_contains": "tandfonline.com",
                "url_not_contains": ["/login", "/shibboleth", "wayf.", "idp."],
            },
        },
```

- [ ] **Step 2: Verify PUBLISHERS dict syntax**

```bash
python -c "exec(open('batch-wos-download.py',encoding='utf-8').read().split('# ══')[0]); print('OK')"
```

Actually, simpler:
```bash
python -c "import ast; ast.parse(open('batch-wos-download.py',encoding='utf-8').read()); print('Syntax OK')"
```

---

### Task 6: Add get_access_url() and wire CARSI into download flow

**Files:**
- Modify: `batch-wos-download.py` — rename `get_vpn_prefix` usage, add CARSI path in `process_one()`

**Interfaces:**
- Produces: `get_access_url(sid, pub_key) -> str|None`
- Modifies: `process_one()` to use `get_access_url()` and trigger CARSI auth

- [ ] **Step 1: Wrap get_vpn_prefix with get_access_url()**

Insert after `get_vpn_prefix()` (after line 276):

```python
def get_access_url(sid, pub_key):
    """获取出版商访问地址（前缀或原生域名）。

    VPN 模式：返回 VPN 编码前缀
    CARSI 模式：返回原生域名（如 https://www.sciencedirect.com），
               触发 CARSI 认证（如需要）
    """
    if config["auth"]["method"] == "carsi":
        if not carsi_authenticate(sid, pub_key):
            return None
        # CARSI 下直连出版商，不需要 URL 编码
        return f"https://{PUBLISHERS[pub_key]['domain']}"
    else:
        return get_vpn_prefix(sid, pub_key)
```

- [ ] **Step 2: Update process_one() to use get_access_url**

In `process_one()` (line 489-490), change:

```python
        prefix = get_vpn_prefix(sid, pub_key)
        if not prefix:
            return res
```

to:

```python
        prefix = get_access_url(sid, pub_key)
        if not prefix:
            return res
```

- [ ] **Step 3: Also update _handle_search() and _handle_doi()**

These functions receive `prefix` as a parameter from `process_one()` — no change needed since `prefix` is passed in. But `_handle_doi` also calls `doi_to_pii` for Elsevier — that function uses CrossRef API (external), unrelated to VPN/CARSI, so no change.

- [ ] **Step 4: Verify syntax again**

```bash
python -c "import ast; ast.parse(open('batch-wos-download.py',encoding='utf-8').read()); print('Syntax OK')"
```

---

### Task 7: Update SKILL.md

**Files:**
- Modify: `C:\Users\chuzi\.claude\skills\batch-paper-download\SKILL.md`

**Interfaces:**
- None (documentation only)

- [ ] **Step 1: Rewrite SKILL.md to reflect multi-auth**

Read current SKILL.md, rewrite the top section and add config/CARSI sections. Key changes:

- Title/description: remove "HFUT VPN" → "multi-school VPN or CARSI"
- Prerequisites: update to mention config.yaml
- Quick Start: add config setup step
- New section: "Configuring Your School (config.yaml)" 
- New section: "VPN vs CARSI"
- Troubleshooting: add CARSI entries
- Keep: Input format, supported publishers, architecture details, ACS handling, design decisions

---

### Task 8: End-to-end dry run test

**Files:**
- (no code changes, verification only)

- [ ] **Step 1: Verify config loading works**

```bash
py batch-wos-download.py
```

Expected: help text displayed (since no input args). Script successfully loaded config.yaml without errors.

- [ ] **Step 2: Delete config.yaml and verify template generation**

```bash
mv config.yaml config.yaml.bak
py batch-wos-download.py
```

Expected: `[i] 已生成配置模板: ...config.yaml` message, then exits. New config.yaml created.

- [ ] **Step 3: Restore config**

```bash
mv config.yaml.bak config.yaml
```

- [ ] **Step 4: Verify VPN mode unchanged (spot-check function calls)**

```bash
grep -n "VPN_HOME\|_KNOWN_PREFIXES\|ensure_vpn(" batch-wos-download.py
```

Expected: `VPN_HOME` no longer exists. `_KNOWN_PREFIXES` no longer exists. `ensure_vpn` still exists but reads from config.

---

## Verification Checklist

After all tasks complete:

1. [ ] `python -c "import ast; ast.parse(open('batch-wos-download.py',encoding='utf-8').read()); print('OK')"` — syntax OK
2. [ ] `grep -c "VPN_HOME" batch-wos-download.py` — returns 0 (no hardcoded VPN URL)
3. [ ] `grep -c "_KNOWN_PREFIXES" batch-wos-download.py` — returns 0 (moved to config)
4. [ ] `grep -c "ensure_auth" batch-wos-download.py` — >= 2 (function def + call site)
5. [ ] `grep -c "ensure_carsi" batch-wos-download.py` — >= 2
6. [ ] `grep -c "carsi_authenticate" batch-wos-download.py` — >= 2
7. [ ] `grep -c "carsi_login" batch-wos-download.py` — == 6 (one per publisher)
8. [ ] `grep -c "get_access_url" batch-wos-download.py` — >= 2
9. [ ] `config.yaml` exists and is valid YAML
10. [ ] SKILL.md updated with multi-auth content
