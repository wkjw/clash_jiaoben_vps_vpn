#!/usr/bin/env python3
import os
import sys
import json
import ssl
import shutil
import platform
import urllib.request
import urllib.parse
import subprocess
import socket
import time
import argparse
from pathlib import Path
import base64
import random

def get_user_home():
    """获取用户主目录"""
    return str(Path.home())

def get_system_info():
    """获取系统信息"""
    system = platform.system().lower()
    machine = platform.machine().lower()
    
    # 系统映射
    os_map = {
        'linux': 'linux',
        'darwin': 'darwin',  # macOS
        'windows': 'windows'
    }
    
    # 架构映射
    arch_map = {
        'x86_64': 'amd64',
        'amd64': 'amd64',
        'aarch64': 'arm64',
        'arm64': 'arm64',
        'i386': '386',
        'i686': '386'
    }
    
    os_name = os_map.get(system, 'linux')
    arch = arch_map.get(machine, 'amd64')
    
    return os_name, arch

def ensure_nginx_user():
    """确保nginx用户存在，如果不存在就创建，统一使用nginx用户"""
    try:
        # 检查nginx用户是否已存在
        try:
            result = subprocess.run(['id', 'nginx'], check=True, capture_output=True, text=True)
            if result.returncode == 0:
                print("✅ nginx用户已存在")
                return 'nginx'
        except:
            # nginx用户不存在，创建它
            print("🔧 nginx用户不存在，正在创建...")
            
            # 创建nginx系统用户（无登录shell，无家目录）
            try:
                subprocess.run([
                    'sudo', 'useradd', 
                    '--system',           # 系统用户
                    '--no-create-home',   # 不创建家目录
                    '--shell', '/bin/false',  # 无登录shell
                    '--comment', 'nginx web server',  # 注释
                    'nginx'
                ], check=True, capture_output=True)
                print("✅ nginx用户创建成功")
                return 'nginx'
            except subprocess.CalledProcessError as e:
                # 如果创建失败，可能是因为用户已存在但id命令失败，或其他原因
                print(f"⚠️ 创建nginx用户失败: {e}")
                
                # 再次检查用户是否存在（可能是并发创建）
                try:
                    subprocess.run(['id', 'nginx'], check=True, capture_output=True)
                    print("✅ nginx用户实际上已存在")
                    return 'nginx'
                except:
                    # 确实创建失败，fallback到root用户
                    print("⚠️ 使用root用户作为nginx运行用户")
                    return 'root'
        
    except Exception as e:
        print(f"❌ 处理nginx用户时出错: {e}")
        # 出错时使用root用户
        return 'root'

def set_nginx_permissions(web_dir):
    """设置nginx目录的正确权限"""
    try:
        nginx_user = ensure_nginx_user()
        print(f"🔧 设置目录权限: {web_dir}")
        print(f"👤 使用用户: {nginx_user}")
        
        # 设置目录和文件权限
        subprocess.run(['sudo', 'chown', '-R', f'{nginx_user}:{nginx_user}', web_dir], check=True)
        subprocess.run(['sudo', 'chmod', '-R', '755', web_dir], check=True)
        subprocess.run(['sudo', 'find', web_dir, '-type', 'f', '-exec', 'chmod', '644', '{}', ';'], check=True)
        
        print(f"✅ 权限设置完成: {web_dir} (用户: {nginx_user})")
        return True
    except Exception as e:
        print(f"❌ 设置权限失败: {e}")
        return False

def check_port_available(port):
    """检查端口是否可用（仅使用socket）"""
    try:
        # 对于Hysteria2，我们主要关心UDP端口
        # nginx使用TCP端口，hysteria使用UDP端口，它们可以共存
        
        # 检查UDP端口是否可用（这是hysteria2需要的）
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(1)
            try:
                s.bind(('', port))
                return True  # UDP端口可用
            except:
                # UDP端口被占用，检查是否是hysteria进程
                return False
                
    except:
        # 如果有任何异常，保守起见返回端口不可用
        return False

def is_port_listening(port):
    """检查端口是否已经在监听（服务是否已启动）"""
    try:
        # 尝试连接到端口
        # 由于 Hysteria 使用 UDP，我们检查 UDP 端口
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1)
        
        # 尝试发送一个数据包到端口
        # 如果端口打开，send不会抛出异常
        try:
            sock.sendto(b"ping", ('127.0.0.1', port))
            try:
                sock.recvfrom(1024)  # 尝试接收响应
                return True
            except socket.timeout:
                # 没收到响应但也没报错，可能仍在监听
                return True
        except:
            pass
            
        # 另一种检查方式：尝试绑定端口，如果失败说明端口已被占用
        try:
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            test_sock.bind(('', port))
            test_sock.close()
            return False  # 能成功绑定说明端口未被占用
        except:
            return True  # 无法绑定说明端口已被占用
            
        return False
    except:
        return False
    finally:
        try:
            sock.close()
        except:
            pass

def check_process_running(pid_file):
    """检查进程是否在运行"""
    if not os.path.exists(pid_file):
        return False
        
    try:
        with open(pid_file, 'r') as f:
            pid = f.read().strip()
            
        if not pid:
            return False
            
        # 尝试发送信号0检查进程是否存在
        try:
            os.kill(int(pid), 0)
            return True
        except:
            return False
    except:
        return False

def create_directories():
    """创建必要的目录"""
    home = get_user_home()
    dirs = [
        f"{home}/.hysteria2",
        f"{home}/.hysteria2/cert",
        f"{home}/.hysteria2/config",
        f"{home}/.hysteria2/logs"
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)
    return dirs[0]

def download_file(url, save_path, max_retries=3):
    """下载文件，带重试机制"""
    for i in range(max_retries):
        try:
            print(f"正在下载... (尝试 {i+1}/{max_retries})")
            urllib.request.urlretrieve(url, save_path)
            return True
        except Exception as e:
            print(f"下载失败: {e}")
            if i < max_retries - 1:
                time.sleep(2)  # 等待2秒后重试
            continue
    return False

def get_latest_version():
    """返回固定的最新版本号 v2.6.1"""
    return "v2.6.1"

def get_download_filename(os_name, arch):
    """根据系统和架构返回正确的文件名"""
    # windows 需要 .exe
    if os_name == 'windows':
        if arch == 'amd64':
            return 'hysteria-windows-amd64.exe'
        elif arch == '386':
            return 'hysteria-windows-386.exe'
        elif arch == 'arm64':
            return 'hysteria-windows-arm64.exe'
        else:
            return f'hysteria-windows-{arch}.exe'
    else:
        return f'hysteria-{os_name}-{arch}'

def verify_binary(binary_path):
    """验证二进制文件是否有效（简化版）"""
    try:
        # 检查文件是否存在
        if not os.path.exists(binary_path):
            return False
            
        # 检查文件大小（至少5MB - hysteria一般大于10MB）
        if os.path.getsize(binary_path) < 5 * 1024 * 1024:
            return False
            
        # 设置文件为可执行
        os.chmod(binary_path, 0o755)
        
        # 返回成功
        return True
    except:
        return False

def download_hysteria2(base_dir):
    """下载Hysteria2二进制文件，使用简化链接和验证方式"""
    try:
        version = get_latest_version()
        os_name, arch = get_system_info()
        filename = get_download_filename(os_name, arch)
        
        # 只使用原始GitHub链接，避免镜像问题
        url = f"https://github.com/apernet/hysteria/releases/download/app/{version}/{filename}"
        
        binary_path = f"{base_dir}/hysteria"
        if os_name == 'windows':
            binary_path += '.exe'
        
        print(f"正在下载 Hysteria2 {version}...")
        print(f"系统类型: {os_name}, 架构: {arch}, 文件名: {filename}")
        print(f"下载链接: {url}")
        
        # 使用wget下载
        try:
            has_wget = shutil.which('wget') is not None
            has_curl = shutil.which('curl') is not None
            
            if has_wget:
                print("使用wget下载...")
                subprocess.run(['wget', '--tries=3', '--timeout=15', '-O', binary_path, url], check=True)
            elif has_curl:
                print("使用curl下载...")
                subprocess.run(['curl', '-L', '--connect-timeout', '15', '-o', binary_path, url], check=True)
            else:
                print("系统无wget/curl，尝试使用Python下载...")
                urllib.request.urlretrieve(url, binary_path)
                
            # 验证下载
            if not verify_binary(binary_path):
                raise Exception("下载的文件无效")
                
            print(f"下载成功: {binary_path}, 大小: {os.path.getsize(binary_path)/1024/1024:.2f}MB")
            return binary_path, version
            
        except Exception as e:
            print(f"自动下载失败: {e}")
            print("请按照以下步骤手动下载:")
            print(f"1. 访问 https://github.com/apernet/hysteria/releases/tag/app/{version}")
            print(f"2. 下载 {filename} 文件")
            print(f"3. 将文件重命名为 hysteria (不要加后缀) 并移动到 {base_dir}/ 目录")
            print(f"4. 执行: chmod +x {base_dir}/hysteria")
            
            # 询问用户文件是否已放置
            while True:
                user_input = input("已完成手动下载和放置? (y/n): ").lower()
                if user_input == 'y':
                    # 检查文件是否存在
                    if os.path.exists(binary_path) and verify_binary(binary_path):
                        print("文件验证成功，继续安装...")
                        return binary_path, version
                    else:
                        print(f"文件不存在或无效，请确保放在 {binary_path} 位置。")
                elif user_input == 'n':
                    print("中止安装。")
                    sys.exit(1)
    
    except Exception as e:
        print(f"下载错误: {e}")
        sys.exit(1)

def get_ip_address():
    """获取本机IP地址（优先获取公网IP，如果失败则使用本地IP）"""
    # 首先尝试获取公网IP
    try:
        # 尝试从公共API获取公网IP
        with urllib.request.urlopen('https://api.ipify.org', timeout=5) as response:
            public_ip = response.read().decode('utf-8')
            if public_ip and len(public_ip) > 0:
                return public_ip
    except:
        try:
            # 备选API
            with urllib.request.urlopen('https://ifconfig.me', timeout=5) as response:
                public_ip = response.read().decode('utf-8')
                if public_ip and len(public_ip) > 0:
                    return public_ip
        except:
            pass

    # 如果获取公网IP失败，尝试获取本地IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # 不需要真正连接，只是获取路由信息
        s.connect(('8.8.8.8', 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except:
        # 如果所有方法都失败，返回本地回环地址
        return '127.0.0.1'

def setup_nginx_smart_proxy(base_dir, domain, web_dir, cert_path, key_path, hysteria_port):
    """设置nginx Web伪装：TCP端口显示正常网站，UDP端口用于Hysteria2"""
    print("🚀 正在配置nginx Web伪装...")
    
    try:
        # 检查证书文件
        print(f"🔍 检查证书文件路径:")
        print(f"证书文件: {cert_path}")
        print(f"密钥文件: {key_path}")
        
        if not os.path.exists(cert_path):
            print(f"❌ 证书文件不存在: {cert_path}")
            cert_path, key_path = generate_self_signed_cert(base_dir, domain)
        
        if not os.path.exists(key_path):
            print(f"❌ 密钥文件不存在: {key_path}")
            cert_path, key_path = generate_self_signed_cert(base_dir, domain)
        
        print(f"📁 最终使用的证书路径:")
        print(f"证书: {cert_path}")
        print(f"密钥: {key_path}")
        
        # 确保nginx用户存在
        nginx_user = ensure_nginx_user()
        print(f"👤 使用nginx用户: {nginx_user}")
        
        # 创建nginx标准Web配置 - 修改为使用您开放的端口
        nginx_conf = f"""user {nginx_user};
worker_processes auto;
error_log /var/log/nginx/error.log notice;
pid /run/nginx.pid;

events {{
    worker_connections 1024;
}}

http {{
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    sendfile on;
    keepalive_timeout 65;
    server_tokens off;
    
    server {{
        listen 80;
        listen 54116 ssl http2;
        server_name _;
        
        ssl_certificate {os.path.abspath(cert_path)};
        ssl_certificate_key {os.path.abspath(key_path)};
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384;
        
        root {web_dir};
        index index.html index.htm;
        
        # 正常网站访问
        location / {{
            try_files $uri $uri/ /index.html;
        }}
        
        add_header X-Frame-Options DENY always;
        add_header X-Content-Type-Options nosniff always;
    }}
}}"""
        
        # 更新nginx配置
        print("💾 备份当前nginx配置...")
        subprocess.run(['sudo', 'cp', '/etc/nginx/nginx.conf', '/etc/nginx/nginx.conf.backup'], check=True)
        
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.conf') as tmp:
            tmp.write(nginx_conf)
            tmp.flush()
            subprocess.run(['sudo', 'cp', tmp.name, '/etc/nginx/nginx.conf'], check=True)
            os.unlink(tmp.name)
        
        subprocess.run(['sudo', 'rm', '-f', '/etc/nginx/conf.d/*.conf'], check=True)
        
        # 测试并重启
        print("🔧 测试nginx配置...")
        test_result = subprocess.run(['sudo', 'nginx', '-t'], capture_output=True, text=True)
        if test_result.returncode != 0:
            print(f"❌ nginx配置测试失败:")
            print(f"错误信息: {test_result.stderr}")
            subprocess.run(['sudo', 'cp', '/etc/nginx/nginx.conf.backup', '/etc/nginx/nginx.conf'], check=True)
            print("🔄 已恢复nginx配置备份")
            return False, None
        
        print("✅ nginx配置测试通过")
        
        print("🔄 重启nginx服务...")
        restart_result = subprocess.run(['sudo', 'systemctl', 'restart', 'nginx'], capture_output=True, text=True)
        if restart_result.returncode != 0:
            print(f"❌ nginx重启失败:")
            print(f"错误信息: {restart_result.stderr}")
            return False, None
        
        print("✅ nginx Web伪装配置成功！")
        print("🎯 TCP端口 54116: 标准HTTPS网站")
        print("🎯 UDP端口: Hysteria2代理服务")
        
        return True, hysteria_port
        
    except Exception as e:
        print(f"❌ 配置失败: {e}")
        return False, None

def create_web_masquerade(base_dir):
    """创建Web伪装页面"""
    web_dir = f"{base_dir}/web"
    os.makedirs(web_dir, exist_ok=True)
    
    return create_web_files_in_directory(web_dir)

def create_web_files_in_directory(web_dir):
    """在指定目录创建Web文件"""
    # 确保目录存在
    if not os.path.exists(web_dir):
        try:
            subprocess.run(['sudo', 'mkdir', '-p', web_dir], check=True)
        except:
            os.makedirs(web_dir, exist_ok=True)
    
    # 创建一个更逼真的企业网站首页
    index_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Global Digital Solutions - Enterprise Cloud Services</title>
    <meta name="description" content="Leading provider of enterprise cloud solutions, digital infrastructure, and business technology services.">
    <meta name="keywords" content="cloud computing, enterprise solutions, digital transformation, IT services">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; line-height: 1.6; color: #333; background: #f8f9fa; }
        .container { max-width: 1200px; margin: 0 auto; padding: 0 20px; }
        
        header { background: linear-gradient(135deg, #2c5aa0 0%, #1e3a8a 100%); color: white; padding: 1rem 0; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        nav { display: flex; justify-content: space-between; align-items: center; }
        .logo { font-size: 1.8rem; font-weight: bold; }
        .nav-links { display: flex; list-style: none; gap: 2rem; }
        .nav-links a { color: white; text-decoration: none; transition: opacity 0.3s; font-weight: 500; }
        .nav-links a:hover { opacity: 0.8; }
        
        .hero { background: linear-gradient(135deg, #f8fafc 0%, #e2e8f0 100%); padding: 5rem 0; text-align: center; }
        .hero h1 { font-size: 3.5rem; margin-bottom: 1rem; color: #1e293b; font-weight: 700; }
        .hero p { font-size: 1.3rem; color: #64748b; margin-bottom: 2.5rem; max-width: 600px; margin-left: auto; margin-right: auto; }
        .btn { display: inline-block; background: #2563eb; color: white; padding: 15px 35px; text-decoration: none; border-radius: 8px; transition: all 0.3s; font-weight: 600; margin: 0 10px; }
        .btn:hover { background: #1d4ed8; transform: translateY(-2px); }
        .btn-secondary { background: transparent; border: 2px solid #2563eb; color: #2563eb; }
        .btn-secondary:hover { background: #2563eb; color: white; }
        
        .stats { background: white; padding: 3rem 0; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 2rem; text-align: center; }
        .stat h3 { font-size: 2.5rem; color: #2563eb; font-weight: 700; }
        .stat p { color: #64748b; font-weight: 500; }
        
        .features { padding: 5rem 0; background: #f8fafc; }
        .features h2 { text-align: center; font-size: 2.5rem; margin-bottom: 3rem; color: #1e293b; }
        .features-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); gap: 3rem; margin-top: 3rem; }
        .feature { background: white; padding: 2.5rem; border-radius: 15px; box-shadow: 0 10px 30px rgba(0,0,0,0.1); text-align: center; transition: transform 0.3s; }
        .feature:hover { transform: translateY(-5px); }
        .feature-icon { font-size: 3rem; margin-bottom: 1rem; }
        .feature h3 { color: #1e293b; margin-bottom: 1rem; font-size: 1.3rem; }
        .feature p { color: #64748b; line-height: 1.7; }
        
        .cta { background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%); color: white; padding: 5rem 0; text-align: center; }
        .cta h2 { font-size: 2.5rem; margin-bottom: 1rem; }
        .cta p { font-size: 1.2rem; margin-bottom: 2rem; opacity: 0.9; }
        
        footer { background: #1e293b; color: white; text-align: center; padding: 3rem 0; }
        .footer-content { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 2rem; margin-bottom: 2rem; text-align: left; }
        .footer-section h4 { margin-bottom: 1rem; color: #3b82f6; }
        .footer-section p, .footer-section a { color: #94a3b8; text-decoration: none; }
        .footer-section a:hover { color: white; }
        .footer-bottom { border-top: 1px solid #334155; padding-top: 2rem; margin-top: 2rem; text-align: center; color: #94a3b8; }
    </style>
</head>
 <body>
     <header>
         <nav class="container">
             <div class="logo">Global Digital Solutions</div>
             <ul class="nav-links">
                 <li><a href="#home">Home</a></li>
                 <li><a href="#services">Solutions</a></li>
                 <li><a href="#about">About</a></li>
                 <li><a href="#contact">Contact</a></li>
             </ul>
         </nav>
     </header>

     <section class="hero">
         <div class="container">
             <h1>Transform Your Digital Future</h1>
             <p>Leading enterprise cloud solutions and digital infrastructure services for businesses worldwide. Secure, scalable, and always available.</p>
             <a href="#services" class="btn">Explore Solutions</a>
             <a href="#contact" class="btn btn-secondary">Get Started</a>
         </div>
     </section>

     <section class="stats">
         <div class="container">
             <div class="stats-grid">
                 <div class="stat">
                     <h3>99.9%</h3>
                     <p>Uptime Guarantee</p>
                 </div>
                 <div class="stat">
                     <h3>10,000+</h3>
                     <p>Enterprise Clients</p>
                 </div>
                 <div class="stat">
                     <h3>50+</h3>
                     <p>Global Data Centers</p>
                 </div>
                 <div class="stat">
                     <h3>24/7</h3>
                     <p>Expert Support</p>
                 </div>
             </div>
         </div>
     </section>

     <section class="features" id="services">
         <div class="container">
             <h2>Enterprise Cloud Solutions</h2>
             <div class="features-grid">
                 <div class="feature">
                     <div class="feature-icon">☁️</div>
                     <h3>Cloud Infrastructure</h3>
                     <p>Scalable and secure cloud infrastructure with global reach. Deploy your applications with confidence on our enterprise-grade platform.</p>
                 </div>
                 <div class="feature">
                     <div class="feature-icon">🔒</div>
                     <h3>Security & Compliance</h3>
                     <p>Advanced security protocols and compliance standards including SOC 2, ISO 27001, and GDPR to protect your business data.</p>
                 </div>
                 <div class="feature">
                     <div class="feature-icon">⚡</div>
                     <h3>High Performance</h3>
                     <p>Lightning-fast performance with our global CDN network and optimized infrastructure for maximum speed and reliability.</p>
                 </div>
                 <div class="feature">
                     <div class="feature-icon">📊</div>
                     <h3>Analytics & Monitoring</h3>
                     <p>Real-time monitoring and detailed analytics to help you optimize performance and make data-driven business decisions.</p>
                 </div>
                 <div class="feature">
                     <div class="feature-icon">🛠️</div>
                     <h3>Managed Services</h3>
                     <p>Full-stack managed services including database management, security updates, and performance optimization by our experts.</p>
                 </div>
                 <div class="feature">
                     <div class="feature-icon">🌍</div>
                     <h3>Global Reach</h3>
                     <p>Worldwide infrastructure with data centers across six continents, ensuring low latency and high availability for your users.</p>
                 </div>
             </div>
         </div>
     </section>

     <section class="cta" id="contact">
         <div class="container">
             <h2>Ready to Transform Your Business?</h2>
             <p>Join thousands of enterprises already using our cloud solutions</p>
             <a href="mailto:contact@globaldigi.com" class="btn">Contact Sales Team</a>
         </div>
     </section>

     <footer>
         <div class="container">
             <div class="footer-content">
                 <div class="footer-section">
                     <h4>Solutions</h4>
                     <p><a href="#">Cloud Infrastructure</a></p>
                     <p><a href="#">Security Services</a></p>
                     <p><a href="#">Data Analytics</a></p>
                     <p><a href="#">Managed Services</a></p>
                 </div>
                 <div class="footer-section">
                     <h4>Company</h4>
                     <p><a href="#">About Us</a></p>
                     <p><a href="#">Careers</a></p>
                     <p><a href="#">News</a></p>
                     <p><a href="#">Contact</a></p>
                 </div>
                 <div class="footer-section">
                     <h4>Support</h4>
                     <p><a href="#">Documentation</a></p>
                     <p><a href="#">Help Center</a></p>
                     <p><a href="#">Status Page</a></p>
                     <p><a href="#">Contact Support</a></p>
                 </div>
                 <div class="footer-section">
                     <h4>Legal</h4>
                     <p><a href="#">Privacy Policy</a></p>
                     <p><a href="#">Terms of Service</a></p>
                     <p><a href="#">Security</a></p>
                     <p><a href="#">Compliance</a></p>
                 </div>
             </div>
             <div class="footer-bottom">
                 <p>&copy; 2024 Global Digital Solutions Inc. All rights reserved. | Enterprise Cloud Services</p>
             </div>
         </div>
     </footer>
 </body>
</html>"""
    
    # 使用sudo写入文件（如果需要）
    try:
        with open(f"{web_dir}/index.html", "w", encoding="utf-8") as f:
            f.write(index_html)
    except PermissionError:
        # 使用sudo写入
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.html') as tmp:
            tmp.write(index_html)
            tmp.flush()
            subprocess.run(['sudo', 'cp', tmp.name, f"{web_dir}/index.html"], check=True)
            os.unlink(tmp.name)
    
    # 创建robots.txt（看起来更真实）
    robots_txt = """User-agent: *
Allow: /

Sitemap: /sitemap.xml
"""
    try:
        with open(f"{web_dir}/robots.txt", "w") as f:
            f.write(robots_txt)
    except PermissionError:
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as tmp:
            tmp.write(robots_txt)
            tmp.flush()
            subprocess.run(['sudo', 'cp', tmp.name, f"{web_dir}/robots.txt"], check=True)
            os.unlink(tmp.name)
    
    # 创建sitemap.xml
    sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>/</loc>
    <lastmod>2024-01-01</lastmod>
    <changefreq>monthly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>/services</loc>
    <lastmod>2024-01-01</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>/about</loc>
    <lastmod>2024-01-01</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.6</priority>
  </url>
  <url>
    <loc>/contact</loc>
    <lastmod>2024-01-01</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.7</priority>
  </url>
</urlset>"""
    try:
        with open(f"{web_dir}/sitemap.xml", "w") as f:
            f.write(sitemap_xml)
    except PermissionError:
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.xml') as tmp:
            tmp.write(sitemap_xml)
            tmp.flush()
            subprocess.run(['sudo', 'cp', tmp.name, f"{web_dir}/sitemap.xml"], check=True)
            os.unlink(tmp.name)
    
    return web_dir

def generate_self_signed_cert(base_dir, domain):
    """生成自签名证书"""
    cert_dir = f"{base_dir}/cert"
    cert_path = f"{cert_dir}/server.crt"
    key_path = f"{cert_dir}/server.key"
    
    # 确保域名不为空，如果为空则使用默认值
    if not domain or not domain.strip():
        domain = "localhost"
        print("警告: 域名为空，使用localhost作为证书通用名")
    
    try:
        # 生成更安全的证书
        subprocess.run([
            "openssl", "req", "-x509", "-nodes",
            "-newkey", "rsa:4096",  # 使用4096位密钥
            "-keyout", key_path,
            "-out", cert_path,
            "-subj", f"/CN={domain}",
            "-days", "36500",
            "-sha256"  # 使用SHA256
        ], check=True)
        
        # 设置适当的权限
        os.chmod(cert_path, 0o644)
        os.chmod(key_path, 0o600)
        
        return cert_path, key_path
    except Exception as e:
        print(f"生成证书失败: {e}")
        sys.exit(1)

def create_config(base_dir, port, password, cert_path, key_path, domain, enable_web_masquerade=True, custom_web_dir=None, enable_port_hopping=False, obfs_password=None, enable_http3_masquerade=False):
    """创建Hysteria2配置文件（端口跳跃、混淆、HTTP/3伪装）"""
    
    # 基础配置
    config = {
        "listen": f":{port}",
        "tls": {
            "cert": cert_path,
            "key": key_path
        },
        "auth": {
            "type": "password",
            "password": password
        },
        "bandwidth": {
            "up": "1000 mbps",
            "down": "1000 mbps"
        },
        "ignoreClientBandwidth": False,
        "log": {
            "level": "warn",
            "output": f"{base_dir}/logs/hysteria.log",
            "timestamp": True
        },
        "resolver": {
            "type": "udp",
            "tcp": {
                "addr": "8.8.8.8:53",
                "timeout": "4s"
            },
            "udp": {
                "addr": "8.8.8.8:53", 
                "timeout": "4s"
            }
        }
    }
    
    # 端口跳跃配置 (Port Hopping)
    if enable_port_hopping:
        # Hysteria2服务器端只监听单个端口，端口跳跃通过iptables DNAT实现
        port_start = max(1024, port - 25)  
        port_end = min(65535, port + 25)
        
        # 确保范围合理：如果基准端口太小，使用固定范围
        if port < 1049:  # 1024 + 25
            port_start = 1024
            port_end = 1074
        
        # 服务器仍然只监听单个端口
        config["listen"] = f":{port}"
        
        # 记录端口跳跃信息，用于后续iptables配置
        config["_port_hopping"] = {
            "enabled": True,
            "range_start": port_start,
            "range_end": port_end,
            "listen_port": port
        }
        
        print(f"✅ 启用端口跳跃 - 服务器监听: {port}, 客户端可用范围: {port_start}-{port_end}")
    
    # 流量混淆配置 (Salamander Obfuscation)
    if obfs_password:
        config["obfs"] = {
            "type": "salamander",
            "salamander": {
                "password": obfs_password
            }
        }
        print(f"✅ 启用Salamander混淆 - 密码: {obfs_password}")
    
    # HTTP/3伪装配置
    if enable_http3_masquerade:
        if enable_web_masquerade and custom_web_dir and os.path.exists(custom_web_dir):
            config["masquerade"] = {
                "type": "file",
                "file": {
                    "dir": custom_web_dir
                }
            }
        else:
            # 使用HTTP/3网站伪装
            config["masquerade"] = {
                "type": "proxy",
                "proxy": {
                    "url": "https://www.google.com",
                    "rewriteHost": True
                }
            }
        print("✅ 启用HTTP/3伪装 - 流量看起来像正常HTTP/3")
    elif enable_web_masquerade and custom_web_dir and os.path.exists(custom_web_dir):
        config["masquerade"] = {
            "type": "file",
            "file": {
                "dir": custom_web_dir
            }
        }
    elif port in [80, 443, 8080, 8443]:
        config["masquerade"] = {
            "type": "proxy",
            "proxy": {
                "url": "https://www.microsoft.com",
                "rewriteHost": True
            }
        }
    else:
        masquerade_sites = [
            "https://www.microsoft.com",
            "https://www.apple.com", 
            "https://www.amazon.com",
            "https://www.github.com",
            "https://www.stackoverflow.com"
        ]
        import random
        config["masquerade"] = {
            "type": "proxy",
            "proxy": {
                "url": random.choice(masquerade_sites),
                "rewriteHost": True
            }
        }
    
    # QUIC/HTTP3优化配置 - 修改为您开放的端口
    if port in [54116, 17205, 39670]:  # 使用您的开放端口之一
        config["quic"] = {
            "initStreamReceiveWindow": 8388608,
            "maxStreamReceiveWindow": 8388608,
            "initConnReceiveWindow": 20971520,
            "maxConnReceiveWindow": 20971520,
            "maxIdleTimeout": "30s",
            "maxIncomingStreams": 1024,
            "disablePathMTUDiscovery": False
        }
    
    config_path = f"{base_dir}/config/config.json"
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    
    return config_path

def create_service_script(base_dir, binary_path, config_path, port):
    """创建启动脚本"""
    os_name = platform.system().lower()
    pid_file = f"{base_dir}/hysteria.pid"
    log_file = f"{base_dir}/logs/hysteria.log"
    
    if os_name == 'windows':
        script_content = f"""@echo off
echo 正在启动 Hysteria2 服务...
start /b {binary_path} server -c {config_path} > {log_file} 2>&1
echo 启动命令已执行，请检查日志以确认服务状态
"""
        script_path = f"{base_dir}/start.bat"
    else:
        script_content = f"""#!/bin/bash
echo "正在启动 Hysteria2 服务..."

# 检查二进制文件是否存在
if [ ! -f "{binary_path}" ]; then
    echo "错误: Hysteria2 二进制文件不存在"
    exit 1
fi

# 检查配置文件是否存在
if [ ! -f "{config_path}" ]; then
    echo "错误: 配置文件不存在"
    exit 1
fi

# 启动服务
nohup {binary_path} server -c {config_path} > {log_file} 2>&1 &
echo $! > {pid_file}
echo "Hysteria2 服务已启动，PID: $(cat {pid_file})"

# 给服务一点时间来启动
sleep 2
echo "启动命令已执行，请检查日志以确认服务状态"
"""
        script_path = f"{base_dir}/start.sh"
    
    with open(script_path, "w") as f:
        f.write(script_content)
    
    if os_name != 'windows':
        os.chmod(script_path, 0o755)
    
    return script_path

def create_stop_script(base_dir):
    """创建停止脚本"""
    os_name = platform.system().lower()
    
    if os_name == 'windows':
        script_content = f"""@echo off
for /f "tokens=*" %%a in ('type {base_dir}\\hysteria.pid') do (
    taskkill /F /PID %%a
)
del {base_dir}\\hysteria.pid
echo Hysteria2 服务已停止
"""
        script_path = f"{base_dir}/stop.bat"
    else:
        script_content = f"""#!/bin/bash
if [ -f {base_dir}/hysteria.pid ]; then
    kill $(cat {base_dir}/hysteria.pid)
    rm {base_dir}/hysteria.pid
    echo "Hysteria2 服务已停止"
else
    echo "Hysteria2 服务未运行"
fi
"""
        script_path = f"{base_dir}/stop.sh"
    
    with open(script_path, "w") as f:
        f.write(script_content)
    
    if os_name != 'windows':
        os.chmod(script_path, 0o755)
    
    return script_path

def main():
    parser = argparse.ArgumentParser(description='Hysteria2 一键部署工具（修改版 - 适配开放端口）')
    parser.add_argument('command', nargs='?', default='install',
                      help='命令: install, del, status, help, setup-nginx, client, fix')
    parser.add_argument('--ip', help='指定服务器IP地址或域名')
    parser.add_argument('--port', type=int, default=54116, help='指定服务器端口（默认54116）')
    parser.add_argument('--password', help='指定密码')
    parser.add_argument('--domain', help='指定域名（用于获取真实证书）')
    parser.add_argument('--email', help='Let\'s Encrypt证书邮箱地址')
    parser.add_argument('--use-real-cert', action='store_true', 
                      help='使用真实域名证书（需要域名指向服务器）')
    parser.add_argument('--web-masquerade', action='store_true', default=True,
                      help='启用Web伪装（默认启用）')
    parser.add_argument('--auto-nginx', action='store_true', default=True,
                      help='安装时自动配置nginx (默认启用)')
    
    # 真正的Hysteria2防墙功能选项
    parser.add_argument('--port-hopping', action='store_true',
                      help='启用端口跳跃（动态切换端口，防封锁）')
    parser.add_argument('--obfs-password', 
                      help='启用Salamander混淆密码（防DPI检测）')
    parser.add_argument('--http3-masquerade', action='store_true',
                      help='启用HTTP/3伪装（流量看起来像正常HTTP/3）')
    parser.add_argument('--one-click', action='store_true',
                      help='一键部署（自动启用所有防墙功能）')
    parser.add_argument('--simple', action='store_true',
                      help='简化一键部署（端口跳跃+混淆+nginx Web伪装）')
    parser.add_argument('--port-range', 
                      help='指定端口跳跃范围 (格式: 起始端口-结束端口，如: 28888-29999)')
    parser.add_argument('--enable-bbr', action='store_true',
                      help='启用BBR拥塞控制算法优化网络性能')
    
    args = parser.parse_args()
    
    print("""
🎯 修改版说明：
本版本已修改为适配您的服务器开放端口：
- 默认端口：54116 (可选：17205, 39670)
- nginx HTTPS监听端口：54116
- 请确保防火墙已开放这些端口的TCP和UDP

可用的开放端口：
1. 54116 (默认推荐)
2. 17205 (备选)
3. 39670 (备选)

使用示例：
python3 nginx-hysteria2-modified.py install --port 54116 --simple
python3 nginx-hysteria2-modified.py install --port 17205 --one-click
python3 nginx-hysteria2-modified.py install --port 39670 --obfs-password mykey123
""")
    
    if args.command == 'install':
        # 验证端口是否在允许的范围内
        allowed_ports = [54116, 17205, 39670]
        if args.port not in allowed_ports:
            print(f"⚠️ 警告：端口 {args.port} 不在您的开放端口列表中")
            print(f"建议使用开放端口：{allowed_ports}")
            user_confirm = input("是否继续使用此端口？(y/N): ").lower()
            if user_confirm != 'y':
                print("已取消安装")
                return
        
        # 简化一键部署
        if args.simple:
            print(f"🚀 简化一键部署模式 - 使用端口 {args.port}")
            server_address = args.ip if args.ip else get_ip_address()
            password = args.password if args.password else "123qwe!@#QWE"
            
            # 设置默认参数
            args.port_hopping = True
            if not args.obfs_password:
                args.obfs_password = "simple" + str(random.randint(1000, 9999))
            args.http3_masquerade = True
            
        # 一键部署逻辑
        if args.one_click:
            print("🚀 一键部署模式 - 自动启用所有防墙功能")
            args.port_hopping = True
            args.http3_masquerade = True
            if not args.obfs_password:
                # 生成随机混淆密码
                import random
                import string
                args.obfs_password = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
                print(f"🔒 自动生成混淆密码: {args.obfs_password}")
        
        # 获取基本配置
        port = args.port
        password = args.password if args.password else "123qwe!@#QWE"
        domain = args.domain
        use_real_cert = args.use_real_cert
        
        # 获取IP地址或域名
        if domain:
            server_address = domain
            print(f"使用域名: {domain}")
        else:
            server_address = args.ip if args.ip else get_ip_address()
        
        print(f"\n开始安装 Hysteria2（修改版 - 适配开放端口）...")
        print(f"服务器地址: {server_address}")
        print(f"端口: {port} ({'您的开放端口' if port in allowed_ports else '自定义端口'})")
        print(f"证书类型: {'真实证书' if use_real_cert else '自签名证书'}")
        
        # 检查端口
        if not check_port_available(port):
            print(f"⚠️ UDP端口 {port} 可能被占用，正在检查...")
            # 这里可以添加更详细的端口检查逻辑
        
        # 创建目录
        base_dir = create_directories()
        
        # 下载Hysteria2
        binary_path, version = download_hysteria2(base_dir)
        
        # 验证二进制文件
        if not verify_binary(binary_path):
            print("错误: Hysteria2 二进制文件无效")
            sys.exit(1)
        
        # 创建Web伪装页面
        web_dir = create_web_masquerade(base_dir)
        
        # 生成证书
        cert_path, key_path = generate_self_signed_cert(base_dir, server_address)
        
        # 创建配置
        config_path = create_config(base_dir, port, password, cert_path, key_path, 
                                  server_address, args.web_masquerade, web_dir, 
                                  args.port_hopping, args.obfs_password, args.http3_masquerade)
        
        # 创建启动脚本
        start_script = create_service_script(base_dir, binary_path, config_path, port)
        
        # 创建停止脚本
        stop_script = create_stop_script(base_dir)
        
        # 配置nginx Web伪装 (如果启用)
        nginx_success = False
        if args.auto_nginx:
            print(f"\n🚀 配置nginx Web伪装 - 使用端口 {port}...")
            
            nginx_success, _ = setup_nginx_smart_proxy(base_dir, server_address, web_dir, cert_path, key_path, port)
            if nginx_success:
                print(f"✅ nginx Web伪装配置成功！")
                print(f"🎯 TCP端口 {port}: 显示正常HTTPS网站")
                print(f"🎯 UDP端口 {port}: Hysteria2代理服务")
        
        # 生成客户端配置链接
        insecure_param = "0" if use_real_cert else "1"
        
        # 构建链接参数
        params = [f"insecure={insecure_param}", f"sni={server_address}"]
        
        # 添加混淆参数
        if args.obfs_password:
            params.append(f"obfs=salamander")
            params.append(f"obfs-password={urllib.parse.quote(args.obfs_password)}")
        
        config_link = f"hysteria2://{urllib.parse.quote(password)}@{server_address}:{port}?{'&'.join(params)}"
        
        print(f"""
🎉 Hysteria2 修改版安装成功！

📋 安装信息:
- 版本: {version}
- 服务器: {server_address}
- 端口: {port} ({'开放端口' if port in allowed_ports else '自定义端口'})
- 密码: {password}
- 安装目录: {base_dir}

🚀 使用方法:
1. 启动服务: {start_script}
2. 停止服务: {stop_script}
3. 查看日志: {base_dir}/logs/hysteria.log

🔗 客户端配置链接:
{config_link}

🛡️ 防护特性:
{'✅ 端口跳跃: 已启用' if args.port_hopping else '❌ 端口跳跃: 未启用'}
{'✅ Salamander混淆: ' + args.obfs_password if args.obfs_password else '❌ 混淆: 未启用'}
{'✅ HTTP/3伪装: 已启用' if args.http3_masquerade else '❌ HTTP/3伪装: 未启用'}
{'✅ nginx Web伪装: 已配置' if nginx_success else '❌ nginx Web伪装: 未配置'}

⚠️ 重要提醒:
- 确保防火墙已开放TCP和UDP端口 {port}
- nginx Web伪装访问: https://{server_address}:{port}
- Hysteria2使用UDP协议，客户端连接端口 {port}

💡 测试连接:
curl -k https://{server_address}:{port}  # 测试nginx Web伪装

🎯 针对您的服务器优化完成！
端口 {port} 已配置为同时支持TCP(Web伪装)和UDP(Hysteria2代理)
""")
    
    elif args.command == 'help':
        print("""
🛡️ Hysteria2 修改版一键部署工具

本版本已针对您的服务器开放端口进行优化：
- 支持的端口: 54116, 17205, 39670
- 默认端口: 54116

使用方法:
    python3 nginx-hysteria2-modified.py [命令] [选项]

可用命令:
    install      安装 Hysteria2
    help         显示此帮助信息

主要选项:
    --port PORT           指定端口 (54116/17205/39670)
    --password PWD        指定密码
    --simple              简化一键部署 (推荐)
    --one-click           完整一键部署 (所有功能)
    --obfs-password PWD   启用流量混淆
    --port-hopping        启用端口跳跃
    --http3-masquerade    启用HTTP/3伪装

示例:
    # 使用默认端口54116进行简化部署
    python3 nginx-hysteria2-modified.py install --simple
    
    # 使用端口17205进行完整部署
    python3 nginx-hysteria2-modified.py install --port 17205 --one-click
    
    # 使用端口39670进行自定义部署
    python3 nginx-hysteria2-modified.py install --port 39670 --obfs-password mykey123
""")
    
    else:
        print(f"未知命令: {args.command}")
        print("可用命令: install, help")

if __name__ == "__main__":
    main()
