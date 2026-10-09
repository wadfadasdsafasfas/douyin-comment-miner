"""客户端全局配置 —— 打包前在这里改 SERVER_URL。

打包 exe 前，把 SERVER_URL 改成你的公网地址（如 ngrok 域名或云服务器地址），
然后跑 PyInstaller 即可。客户端不需要在用户机器上做任何配置。

示例：
    SERVER_URL = "https://your-random.ngrok-free.app"
    SERVER_URL = "https://api.your-domain.com"
    SERVER_URL = "http://123.45.67.89:8000"
"""

# ↓↓↓ 改成你的服务器公网地址 ↓↓↓
SERVER_URL = "https://tingchao.cengfengkeji.cn"
# ↑↑↑ 改成你的服务器公网地址 ↑↑↑
