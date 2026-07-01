# Repository Guidelines

## 项目结构与模块组织

本仓库是一个 Python/PyQt6 编写的 Android 日志查看工具。`main.py` 包含窗口组件、ADB 交互和日志展示流程；`app_core.py` 放置可测试的路径、配置和滚动日志缓冲逻辑；`tests/` 存放单元测试。`AndroidLogViewer.spec` 与 `build.bat` 用于 PyInstaller 打包。运行时数据保存在程序目录下的 `data/`，日志和工作区导出保存在 `output/`。

## 构建、测试与本地运行

- `python -m venv .venv`：创建本地虚拟环境。
- `.venv/bin/pip install -r requirements.txt`：安装运行、测试和打包依赖；Windows 使用 `.venv\Scripts\pip`。
- `.venv/bin/python main.py`：启动应用；需确保 `adb` 在 `PATH` 中。
- `.venv/bin/python -m unittest`：运行单元测试。
- `py -3 -m PyInstaller AndroidLogViewer.spec` 或 `build.bat`：生成 Windows 可执行文件。

## 编码风格与命名规范

使用 4 空格缩进，保持现有 PyQt 面向类的组织方式。类名使用 `PascalCase`，例如 `LogTextEdit`；函数、方法和变量使用 `snake_case`。用户可见文本以中文为主。可测试的纯逻辑优先放入 `app_core.py`，避免把路径计算、配置解析等逻辑散落在 UI 回调中。不要使用静默的 `except Exception: pass`，错误应反馈到状态栏、弹窗或测试断言中。

## 测试规范

测试使用 Python 标准库 `unittest`。新增非 UI 逻辑时，在 `tests/test_app_core.py` 添加覆盖；新增窗口行为时，在 `tests/test_main_window.py` 添加离屏测试。测试文件命名为 `test_<feature>.py`。提交前运行 `.venv/bin/python -m unittest`，涉及真实设备的功能还需手动验证 ADB 连接、日志抓取和保存路径。

## 提交与 Pull Request 规范

现有提交历史使用简短中文说明，例如 `移除构建文件，添加 .gitignore`。提交信息应简洁、动宾结构清晰，说明实际变更。PR 应包含用户可见变化、手动验证步骤、是否影响打包流程；涉及 UI 改动时，附截图或录屏更便于审查。

## 安全与配置提示

应用首次运行会生成 `data/config.json`，可配置 `package_name`、`output_dir`、`log_dir`、`workspace_dir`、`adb_connect_addresses` 和 `client_logging_remote_template`。刷新设备以 `adb devices -l` 为准；只有必须 TCP 手动连接的模拟器才写入 `adb_connect_addresses`。相对路径基于程序或 exe 所在目录解析。不要提交个人设备标识、日志文件、本地配置或打包生成的二进制文件；`data/`、`output/`、`build/` 和 `dist/` 应保持为本地产物。
