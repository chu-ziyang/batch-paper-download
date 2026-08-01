# Batch Paper Downloader 使用教程

通过学校 VPN 或 CARSI + 浏览器自动化，从各大出版商批量下载论文 PDF。支持 DOI、精确标题、关键词三种输入方式，覆盖 Elsevier、ACS、Springer、Wiley 等 6 家主要出版商。多学校可配置，开箱即用。

---

## 目录

- [快速体验（30 秒）](#快速体验30-秒)
- [前置条件](#前置条件)
- [输入格式详解](#输入格式详解)
- [运行与输出](#运行与输出)
- [工作原理](#工作原理)
- [支持的出版商](#支持的出版商)
- [下载速度](#下载速度)
- [新设备配置指南](#新设备配置指南)
- [踩坑记录](#踩坑记录)

---

## 快速体验（30 秒）

```bash
# 0. 安装依赖（只需一次）
pip install -r requirements.txt

# 1. 首次运行：自动生成 config.yaml 模板
py batch-wos-download.py
# → 编辑 config.yaml，填入学校信息和认证方式

# 2. 创建输入文件（每行一篇论文）
cat > test.txt << 'EOF'
# DOI 方式
10.1016/j.envres.2026.123905
# 精确标题
"Molecular transformation of petroleum compounds by hydroxyl radicals"
EOF

# 3. 运行
py batch-wos-download.py test.txt ./papers
```

---

## 前置条件

### 软件要求

| 软件 | 用途 | 安装 |
|------|------|------|
| Python 3.12+ | 运行脚本 | [python.org](https://python.org) 下载 → 安装时勾选 "Add to PATH" |
| browser-skill | 驱动 Chrome 浏览器 | 安装 CLI + Chrome 扩展，见下方 |
| Chrome 浏览器 | 承载学校 VPN 登录态 | 已有即可 |

### browser-skill 安装与验证

```bash
# 安装 browser-skill CLI
# 参考: https://github.com/anthropics/browser-skill
# 安装后在 Chrome 中加载扩展

# 验证：应该看到 daemon running 和 browsers connected
bsk status
bsk browsers
```

输出示例：
```
INSTANCE  BROWSER           EXT
058c7104  chrome 150.0.0.0  0.1.4    ← 记下这个 ID
```

### 配置脚本

首次运行 `py batch-wos-download.py` 会自动生成 `config.yaml` 模板。编辑此文件：

**VPN 模式**（多数学校适用）：
```yaml
browser: "058c7104"              # bsk browsers 第一列
school:
  name: "合肥工业大学"
  english_name: "Hefei University of Technology"
auth:
  method: "vpn"
  vpn:
    url: "https://webvpn.hfut.edu.cn/"   # 学校 VPN 地址
    login_link: "CAS"                     # 登录入口的链接文字
```

**CARSI 模式**（无需 VPN，直连出版商）：
```yaml
browser: "058c7104"
school:
  name: "北京大学"
  english_name: "Peking University"
auth:
  method: "carsi"
  carsi:
    timeout: 300
    probe: ""   # 可选：填一篇本校订阅的论文页 URL，登录后自动验证权限是否生效
```

详见 `docs/` 目录下的设计与实现文档。

> **多学校切换**：`config.yaml` 路径可用环境变量 `BPD_CONFIG` 覆盖，例如
> `set BPD_CONFIG=D:\configs\hfut.yaml` 后运行，无需复制文件。

### 账号要求

- **VPN 模式**：学校 WebVPN 账号（学号 + 信息门户密码/验证码）
- **CARSI 模式**：学校统一身份认证账号（CARSI 联盟成员）
- 首次运行时会弹出浏览器窗口让你手动登录
- 登录一次后，cookie 保持数小时有效，后续运行自动跳过

---

## 输入格式详解

输入文件 `papers_to_download.txt`，每行一篇论文，`#` 开头为注释。

### 格式 1：DOI（推荐首选）

```
10.1016/j.envres.2026.123905
doi: 10.1007/s11356-025-36116-w
```

以 `10.` 开头自动识别。脚本根据 DOI 前缀自动选择出版商，直达 PDF 链接，无需搜索。

**DOI 前缀 → 出版商映射：**

| 前缀 | 出版商 | PDF 链接构造 |
|------|--------|-------------|
| `10.1016/` | Elsevier | `/pii/{PII}/pdfft` |
| `10.1007/` | Springer | `/content/pdf/{DOI}.pdf` |
| `10.1002/` | Wiley | `/doi/pdfdirect/{DOI}` |
| `10.1021/` | ACS | `/doi/pdf/{DOI}` |
| `10.1039/` | RSC | `/articlepdf/{DOI}` |
| `10.1080/` | Taylor & Francis | `/doi/pdf/{DOI}` |

### 格式 2：精确标题搜索（不知道 DOI 时使用）

```
"Catalytic activity of different iron oxides in heterogeneous Fenton-like systems"
"Molecular transformation of petroleum compounds by hydroxyl radicals"
```

以 `"` 开头。在出版商网站用引号做精确匹配搜索，命中率极高。

> **提示**：标题不需要 100% 完整，关键短语足够。搜索只取第一条结果。

### 格式 3：关键词搜索

```
goethite Fenton hydroxyl radical degradation
```

普通文本，做分词搜索。结果取第一条——**不保证精确匹配**，适合测试或论文标题非常独特时使用。

### 格式 4：手动指定出版商

```
acs: 10.1021/acs.est.3c01379
elsevier: "exact paper title here"
springer: goethite degradation mechanism
wiley: 10.1002/etc.5621
rsc: "nanoparticle synthesis review"
```

当 DOI 前缀检测不可靠，或想用特定出版商搜索时使用。支持的 key：`elsevier`, `acs`, `springer`, `wiley`, `rsc`, `tandf`。

### 完整示例

```
# === 我的论文列表 ===

# DOI 方式（最快最准）
10.1016/j.chemosphere.2024.141264
10.1016/j.envres.2026.123905

# 精确标题（不知道 DOI 时）
"Fenton oxidation of organic contaminants with aquifer sediment activated by ascorbic acid"
"Insights into the degradation process of phenol during in-situ thermal desorption"

# 指定出版商
acs: 10.1021/acs.est.3c01379
springer: 10.1007/s11356-025-36116-w

# 关键词（论文标题足够独特时可用）
Hydroxylamine Promoted Goethite Surface Fenton Degradation
```

---

## 运行与输出

```bash
py batch-wos-download.py <输入文件> <输出目录>
```

实时输出示例：
```
==================================================
Batch Download: 4 篇
输出: papers_download
==================================================
[*] 启动浏览器会话...
  [OK] xdma
VPN 登录检查
  [OK] 已登录

──────────────────────────────────────────────────
[1/4] DOI  10.1016/j.chemosphere.2024.141264
──────────────────────────────────────────────────
  [i] 出版商: Elsevier (ScienceDirect)
  [->] 搜索 DOI: 10.1016/j.chemosphere.2024.141264
  [OK] PII: S0045653524001577
  [->] PDF: .../pii/S0045653524001577/pdfft
  [i] 10.8 MB, 解码中...
  [OK] 10.1016_j.chemosphere.2024.141264.pdf (10.8 MB)

==================================================
结果: 4/4 成功
==================================================
```

**输出文件：**

```
papers_download/
├── 10.1016_j.chemosphere.2024.141264.pdf   # 论文 PDF
├── _Catalytic_activity_of_different.pdf     # 论文 PDF
├── ...
└── _log.json                                # 下载日志
```

日志 `_log.json` 记录每篇的状态（ok/fail）、错误原因、时间戳。

### 重复运行

脚本自动跳过已下载的论文（检测文件存在且 >50KB）：

```
[1/4] DOI  10.1016/j.chemosphere.2024.141264
  [OK] 已存在 (10.8 MB)，跳过
```

---

## 工作原理

```
用户输入 (DOI / 标题 / 关键词)
   │
   ├─ DOI 路径：
   │   检测出版商 → CrossRef API 查 PII（Elsevier）
   │      成功 → 直接构造 PDF URL → 跳至下载
   │      失败 → 在出版商搜索 DOI → 提取 PII → 构造 URL
   │
   └─ 搜索路径（标题/关键词）：
       出版商网站搜索（精确/关键词）
         → 提取文章 URL → 提取 PII/DOI → 构造 PDF URL
         → 精确搜索无结果时回退关键词搜索
   │
   ▼
导航到 PDF 页面
   │
   ├─ HTTP 直传（快）：fetch PDF → POST 到本地 :9999 → 直接存盘
   └─ base64 回退（兼容）：fetch → btoa → subprocess 传回 → 解码存盘
   │
   ▼
保存到输出目录 ✓
```

### 关键设计决策

**为什么不用 WOS（Web of Science）？**

WOS 只是一个检索入口，最终下载还是要跳转到出版商。学校 VPN 本身已经提供了所有出版商的访问权限。直接去出版商网站：
- 更快（少一次跳转）
- 更准（出版商自己的搜索引擎）
- 不依赖 WOS 索引覆盖度

**为什么用 base64 而不是直接下载？**

Chrome 阻止无用户手势的 `a.click()` 下载。base64 方式通过 `fetch()` 在页面内读取 PDF 数据，绕过限制。

**HTTP 直传模式**

为了加速，脚本启动一个本地 HTTP 服务器（`pdf_server.py`），浏览器直接 POST PDF 二进制到 `127.0.0.1:9999`，省去 base64 编码和 subprocess 文本传输。从 ~60 秒降到 ~5 秒。

---

## 支持的出版商

| Key | 名称 | DOI 前缀 | 测试状态 |
|-----|------|----------|:--------:|
| `elsevier` | ScienceDirect | `10.1016/` | ✅ 验证通过 |
| `acs` | ACS Publications | `10.1021/` | ✅ 验证通过 |
| `springer` | Springer Link | `10.1007/` | ✅ 验证通过 |
| `wiley` | Wiley Online | `10.1002/` | ✅ 验证通过 |
| `rsc` | RSC Publishing | `10.1039/` | ⚠️ 未充分测试 |
| `tandf` | Taylor & Francis | `10.1080/` | ⚠️ 未充分测试 |
| `ieee` | IEEE Xplore | `10.1109/` | 📋 待添加 |
| `nature` | Nature | `10.1038/` | 📋 待添加 |
| `oup` | Oxford Academic | `10.1093/` | 📋 待添加 |

### 添加新出版商

编辑 `batch-wos-download.py`，在 `PUBLISHERS` 字典中添加：

```python
"oup": {
    "name": "Oxford Academic",
    "domain": "academic.oup.com",
    "search_url": "/search?q={query}",
    "pdf_from_doi": "/pdf/{doi}",
    "doi_regex": r'/([^/?]+)',
    "result_selector": 'a[href*="/article/"]',
},
```

关键字段说明：
- `domain`：出版商域名（用于 VPN URL 转换）
- `search_url`：搜索 URL 模板，`{query}` 会被替换
- `pdf_from_doi`：DOI → PDF URL 模板（DOI 方式直接下载用）
- `result_selector`：CSS 选择器，找到搜索结果中的文章链接

然后在 `config.yaml` 的 `vpn_prefixes` 中添加 VPN 编码前缀（首次运行时会自动探测）。

---

## 下载速度

| PDF 大小 | HTTP 直传 | base64 回退 | 已跳过 |
|----------|:-------:|:---------:|:----:|
| 1 MB | ~1 秒 | ~10 秒 | 0 秒 |
| 5 MB | ~3 秒 | ~30 秒 | 0 秒 |
| 10 MB | ~5 秒 | ~60 秒 | 0 秒 |

**HTTP 直传模式**自动启用（`pdf_server.py` 在脚本目录下即可）。如果服务器未启动，自动回退到 base64。

---

## 新设备配置指南

从零开始在新电脑上配置，按顺序执行：

### Step 1：安装 Python

```bash
# 下载 Python 3.12+ → 安装 → 验证
py --version
# Python 3.14.4
```

### Step 2：安装 browser-skill

```bash
# 安装 CLI
# 在 Chrome 中加载扩展
# 验证
bsk status
# daemon version 0.1.8
# browsers connected  1
```

### Step 3：获取浏览器 ID

```bash
bsk browsers
# 058c7104  chrome 150.0.0.0  0.1.4  -  0
```

### Step 4：配置脚本

```bash
# 首次运行自动生成 config.yaml
py batch-wos-download.py
# → 编辑 config.yaml，填入浏览器 ID、学校信息、VPN 地址或选 CARSI 模式
```

### Step 5：首次运行测试

```bash
# 用一篇论文测试
echo '10.1016/j.envres.2026.123905' > test.txt
py batch-wos-download.py test.txt ./test_output
```

首次运行会弹出浏览器窗口要求 VPN 登录。完成一次后，后续运行自动跳过。

### Step 6：批量下载

```bash
# 把论文列表写入 papers_to_download.txt
py batch-wos-download.py papers_to_download.txt ./papers
```

### 必要文件

换设备时只需复制：

```
batch-wos-download.py    # 主脚本
config.yaml              # 用户配置（浏览器 ID、学校、VPN/CARSI）
pdf_server.py            # HTTP 加速服务器（不需要改，自动启动/停止）
requirements.txt         # Python 依赖（pyyaml）
```

---

## 踩坑记录

这些是开发过程中遇到的关键问题及解决方案，AI 代理和开发者都需要注意。

### 1. Windows 上不能用 `python`，必须用 `py`

原因：Windows Store 的 `python.exe` stub 会拦截调用，返回 exit code 49。

```bash
# ❌ 错误
python batch-wos-download.py ...

# ✅ 正确
py batch-wos-download.py ...
```

### 2. VPN 有时需要登录有时不需要

VPN cookie 有效期内自动登录，过期需要手动输入验证码。脚本的 `ensure_vpn()` 通过 URL 检测判断状态：
- 导航到 VPN 主页 → URL 如果不是 `/login` 则是已登录
- 如果重定向到 `/login`，弹出 `request-help` 让用户手动输入

### 3. Chrome 阻止程序化下载

`a.click()` 在无用户手势时被 Chrome 拦截。解决方案（脚本自动选择，无需干预）：
- **HTTP 模式（默认优先）**：浏览器 `fetch PDF → POST 到本地 pdf_server`（二进制传输，快；带随机 token 鉴权，防止陌生网页向本机写文件）
- **base64 模式（自动回退）**：`fetch PDF → btoa → subprocess stdout → Python 解码`（文本传输，慢但兼容；HTTP 模式不可用时使用）

### 4. bsk snapshot --json 不可靠

`bsk snapshot --json` 输出中文引号导致 JSON 解析崩溃。解决方案：不用 `--json`，直接解析纯文本 snapshot 的 `@eN role "name"` 行格式。

### 5. VPN 编码前缀：WebVPN 会把域名编码成 hex

点击 VPN 主页的出版商链接后，URL 形如 `https://webvpn.xxx.edu.cn/https/77726476706e69.../`——目标域名（如 sciencedirect）**不会出现在 URL 中**，而是被编码成 hex。脚本的 `get_vpn_prefix` 处理方式：
- 首选 `config.yaml` 中配置的 `vpn_prefixes`（已验证可用，最快）
- 回退自动探测：点击链接后检测 URL 是否出现 `vpn_host + /https/`（代理跳转标志），从 hex 重建前缀
- ⚠️ `config.yaml` 里 `vpn_prefixes:` 后面如果只有注释（没有值），YAML 会解析为 `None`，脚本已做兜底（`or {}`）

### 6. ScienceDirect pdfft URL 需要 PII

Elsevier 的 PDF 链接格式是 `/science/article/pii/{PII}/pdfft`。`PII` 是 S + 16 位数字（共 17 字符，如 `S0045653524001577`），不能直接从 DOI 推导。获取方式：
1. CrossRef API → `alternative-id` 字段
2. 回退：ScienceDirect 搜索 DOI → 从结果 URL 提取 PII

### 7. bsk evaluate 的 timeout 必须够大

PDF fetch + base64 编码对 10MB+ 文件需要 60-120 秒。默认 timeout 需要设 180 秒。

### 8. 标题搜索加引号命中率最高

在 ScienceDirect 搜索时，用 `"exact title phrase"` 比分词搜索命中率高得多。脚本的精确搜索模式自动给标题加引号。精确搜索无结果时自动回退关键词搜索。

### 9. 连续批量下载可能触发出版商反爬（403/400）

短时间内反复请求时，ScienceDirect 会返回 403 页（"There was a problem providing the content you requested" + reference number），Springer 可能间歇性 400。这是**服务端限流**，不是脚本 bug：
- 脚本对单篇失败自动重试 2 次（间隔 5s / 15s 退避）
- 若整批被拦，建议暂停 30-60 分钟再跑，或适当放慢速度
- Springer/Wiley 的 PDF 路径需要 DOI URL 编码（%2F）；fetch 构造 URL 失败时会自动回退到 `window.location.href`（WebVPN 规范化后的 URL，实测可下载）

---

## AI 代理使用指南

如果你让 AI 助手（Claude、ChatGPT、Cursor 等）帮你操作：

**给 AI 的第一步指令：**
> 阅读 batch-wos-download.py 和 README.md，了解如何批量下载论文。检查 bsk 是否可用，浏览器是否连接。

**然后告诉 AI 你要下载什么：**
> 下载以下论文到 papers_download 目录：
> [粘贴参考文献列表]
> 输入文件每行一个 DOI 或 `"title"` 格式。

**常见问题（AI 视角）：**

| 现象 | AI 应该做什么 |
|------|-------------|
| `bsk` 不存在 | 提示用户安装 browser-skill |
| `no browsers connected` | 提示用户打开 Chrome 确认扩展连接 |
| VPN 登录弹窗 | 告诉用户"请在浏览器中完成 VPN 登录，然后点 Continue" |
| `py` 命令报错 exit 49 | 使用 `py` 而不是 `python` |
| 某篇下载失败 | 检查出版商是否支持、标题是否正确 |
| session not registered | 浏览器窗口被关闭，重新运行脚本即可 |

---

**文件列表：**

```
📁 项目目录/
├── 📜 batch-wos-download.py   ← 主脚本
├── 📜 config.yaml             ← 用户配置
├── 📜 pdf_server.py           ← HTTP 加速服务器（自动启动）
├── 📜 test_all.py             ← 单元测试（92 个）
├── 📜 SKILL.md                ← AI 代理使用说明（可安装为 skill）
├── 📋 papers_to_download.txt  ← 输入文件
├── 📖 README.md              ← 本教程
└── 📂 papers_download/       ← 输出目录
    ├── 📄 *.pdf
    └── 📋 _log.json
```
