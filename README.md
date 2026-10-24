# UESTC Check-in Actions

电子科技大学成电智慧学工打卡流程的 Python / GitHub Actions 工具。
使用自己的会话完成 `微信 code → Ticket → 打卡`，运行端采用 HTTP 请求。
Windows 初始化工具用于从自己的已登录微信提取会话，定时运行端无需微信进程。

**这是独立的社区项目，与学校或微信无隶属关系。**
仓库只包含程序、字段模板和合成测试；每位使用者都需要准备自己的会话、Cookie 和位置配置。
初始状态关闭真实提交，配置完成后由使用者明确启用。

## 开始前

- GitHub 账号，以及自己的学校小程序账号。
- 首次初始化需要 Windows、Python 3.11 和已登录的 Windows 微信。
- 初始化脚本目前绑定微信 **4.1.15.56 / RadiumWMPF 25799**；其他构建需要重新适配原生偏移。
- 会话和 Cookie 的有效期取决于服务端。项目提供缓存会话重放，未实现账号密码登录和自动续期。
- 云端能否持续使用受会话状态、IP 和服务端规则影响；离线校验通过不代表实际登录仍有效。

## 1. 创建自己的仓库

点击本仓库的 **Use this template → Create a new repository**，建议选择 **Private**。
这样可以获得独立的项目历史。也可以 Fork 后自行配置，Secrets 需要在自己的仓库重新填写。
在自己的仓库进入 **Actions**，按页面提示启用工作流。

下载自己的项目后，在项目根目录打开 PowerShell：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-windows.txt
```

后续命令都在项目根目录运行；不需要激活虚拟环境。

## 2. 获取自己的会话与 Cookie

1. 登录自己的 Windows 微信，打开成电智慧学工小程序。
2. 尽量关闭其他小程序，保持目标小程序打开。
3. 执行会话采集与离线转换：

```powershell
.\.venv\Scripts\python.exe tools/windows/trace_login_request.py
.\.venv\Scripts\python.exe tools/windows/prepare_pure_login_state.py
```

生成 `tools/windows/pure-login-state.json`。
第一个脚本主动触发一次原生登录；跟踪记录位于同目录的 `login-request-trace/`。
如果等待回调迟迟不结束，按 Ctrl+C 停止，重新打开目标小程序后再试。

4. 在小程序进入打卡地图页面，然后执行：

```powershell
.\.venv\Scripts\python.exe tools/windows/get_checkin_cookie.py
```

生成 `tools/windows/checkin-cookie.txt`。若检测到多个小程序宿主，脚本会给出 PID，
按提示使用 `--pid PID` 选择目标宿主。换账号时要重新生成整组材料。

**这些文件含登录材料，请保留在本机；不要发到 Issue、提交到 Git 或加入截图。**

## 3. 填写自己的位置配置

```powershell
Copy-Item checkin-request-profile.example.json checkin-request-profile.json
notepad checkin-request-profile.json
```

修改 `payload` 内各字段，最后把顶层 `configured` 设为 `true`。
示例中的零坐标和诊断数值只展示类型，使用时填写自己的实际数据。
地址字段和 UA 中所有 `REPLACE_WITH_...` 占位符都要替换。

| 字段 | 类型及含义 |
| --- | --- |
| `latitude` / `longitude` | 数字，实际纬度 / 经度 |
| `reverseGeocode` | 字符串，实际位置对应的完整地址 |
| `userAgent` | 字符串，自己的微信页面 UA |
| `coordinateSystemCode` | 字符串，坐标系标识；示例 `gcj02` |
| `accuracy` | 非负数，定位精度，单位米 |
| `locationDurationMs` | 非负数，本次定位耗时，单位毫秒 |
| `networkTypeCode` | 字符串，页面记录的网络类型 |
| `locationEnabled` | JSON 布尔值 `true` 或 `false` |
| `locationAuthorized` / `locationAccuracy` | 字符串，页面记录的定位授权及精度授权状态 |
| `errorMessage` | 字符串，定位错误信息；正常时为空字符串 |

`clientTime` 在提交前按 UTC+8 自动生成，配置中不需要添加。
顶层 `configured` 和 `description` 仅供本地检查，不进入请求体。
本地配置已被 `.gitignore` 排除；云端通过 Secret 读取，位置无需提交到公开仓库。

## 4. 导出并配置三个 Secrets

```powershell
.\.venv\Scripts\python.exe tools/export_secrets.py
```

导出前会离线校验会话、Cookie 和配置。终端只显示文件路径，不显示凭据内容。
打开自己的仓库 **Settings → Secrets and variables → Actions → Secrets → New repository secret**，
按下表逐个添加，值取对应文件的完整内容：

| 名称 | 本机文件 |
| --- | --- |
| `WECHAT_LOGIN_STATE_B64` | `.local/github-secrets/WECHAT_LOGIN_STATE_B64.txt` |
| `CHECKIN_COOKIE` | `.local/github-secrets/CHECKIN_COOKIE.txt` |
| `CHECKIN_PROFILE_JSON` | `.local/github-secrets/CHECKIN_PROFILE_JSON.txt` |

有额外 Bearer token 的会话可选填 `CHECKIN_BEARER_TOKEN`。
Base64 是编码，不是加密。请通过 GitHub 的 Secrets 页面保存，避免放入 Variables、工作流 YAML 或 README。
导出工具也支持 `--state PATH`、`--cookie-file PATH` 和 `--profile PATH`。

## 5. 先离线校验，再启用真实提交

1. **Actions → Daily check-in → Run workflow**，保持 `mode: validate`。
2. 结果应为 `stage: validated`，三个阶段的请求数均为 `0`。
   这一步只检查配置与保存响应的解密，不验证会话的在线有效期。
3. 在 **Settings → Secrets and variables → Actions → Variables → New repository variable**
   添加 `CHECKIN_ENABLED`，值为小写 `true`。
4. 在可用打卡时间段手动选择 `mode: submit`，执行一次真实流程。
5. 查看任务 Summary 的 `stage`、HTTP 状态、业务码，再在小程序核对当日记录。

只有默认分支可以运行。`CHECKIN_ENABLED` 未设置或不等于 `true` 时，
真实提交和定时任务的作业会跳过；手动 `validate` 仍可运行。
要暂停自动提交，将变量设为 `false`，或在 Actions 页面停用该工作流。

## 6. 每天定时运行

默认计划在 **北京时间每天 21:17** 触发。文件 `.github/workflows/checkin.yml`：

```yaml
on:
  schedule:
    - cron: "17 21 * * *"
      timezone: Asia/Shanghai
```

修改 cron 中的分钟、小时，并提交到默认分支即可调整时间。
定时调度可能延迟，并非精确到分钟；工作流需存在于默认分支。
公开仓库连续 60 天无活动时，GitHub 会自动停用定时工作流，需要手动重新启用。
完成前述配置并设置 `CHECKIN_ENABLED=true` 后，自己的仓库才会实际执行定时提交。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| `checkin` 作业显示 Skipped | 检查默认分支和 `CHECKIN_ENABLED` 变量；首次先手动 `validate` |
| `error` / `ValueError` | 检查三个 Secrets、完整 JSON、`configured: true` 和占位符 |
| `error` / `FileNotFoundError` | 云端填写 `CHECKIN_PROFILE_JSON`；本机创建私有配置文件 |
| `error` / `InvalidTag` | 会话失效或协议变化，重新采集会话并更新 Secret |
| `ticket_rejected` | 检查微信会话、学校 Cookie 是否属于同一账号、是否有效 |
| `checkin_rejected` 且业务码 `4006` | 当前不在打卡时间段内 |
| `checkin_result_unknown` | 提交时连接中断，先在小程序确认当日记录，再决定是否重试 |
| Windows 采集时报进程或偏移错误 | 先核对微信及小程序宿主构建版本，保持小程序打开 |

更新会话：重复第 2 步、第 4 步，并覆盖对应 Secrets。
修改位置：编辑本机私有配置后重新导出，仅更新 `CHECKIN_PROFILE_JSON`。
本项目没有为 code、Ticket 或缓存会话承诺固定有效期。

## 日志与隐私

- 公开项目使用全新 Git 历史，不包含任何部署账号的会话、Cookie、真实位置、抓包或私人运行记录。
- Windows 采集结果、导出文件、私有配置和运行结果都在 Git 忽略列表中。
- Actions 将 Secrets 写入临时目录，结束后清理；只公开阶段、请求计数、HTTP 状态和整数业务码。
- 不输出服务端自由文本、code、Ticket、Cookie、位置或原始响应，不上传运行数据为 artifacts。
- 公共仓库的 Actions 日志可被他人查看；部署到自己的私有仓库更有利于保护运行元数据。
- 每次各阶段最多请求一次，无自动重试；并发锁只覆盖同一仓库，不保证跨运行每日仅提交一次。
- 如误提交过凭据，先使其失效并重新生成；仅删除最新文件并不能清除 Git 历史中的内容。

## 开发与离线测试

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试使用合成加密会话与模拟响应，不读取真实账号，不调用学校或微信接口。
`Offline tests` 在 push / PR 时运行 Ubuntu 和 Windows 测试，不读取 Secrets。
第三方 Actions 使用固定提交，工作流权限为 `contents: read`。

```text
.github/workflows/       定时运行和离线测试
tools/windows/          Windows 本机会话初始化工具
tools/export_secrets.py 离线导出三个 Secrets
run_actions.py          云端入口及脱敏状态输出
profile_config.py       私有位置配置加载与校验
pure_request_login.py   缓存会话下的 HTTP code 获取
pure_checkin_flow.py    code → Ticket → 打卡
tests/                  无真实账号的离线测试
```

## 参考与许可

- GitHub 定时工作流：https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
- GitHub Secrets：https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets
- MIT License，详见 `LICENSE`。
