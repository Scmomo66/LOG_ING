Windows 适配说明

已处理：
1. 移除 macOS 元数据目录/文件：__MACOSX、._*、.DS_Store。
2. 移除 Python 缓存目录：__pycache__。
3. 将 .py/.md/.txt/.json/.spec/.bat 文本文件换行统一为 Windows CRLF。
4. requirements.txt 中移除了 macOS 打包依赖 macholib。

运行：
- 已有可执行文件：dist\AndroidLogViewer.exe

重新构建：
- 双击 build.bat，或在当前目录运行：build.bat