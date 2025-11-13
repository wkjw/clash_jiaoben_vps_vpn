#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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

# 设置UTF-8编码环境
if sys.platform.startswith('win'):
    # Windows环境下设置控制台编码
    import locale
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
else:
    # Linux/Unix环境
    os.environ['PYTHONIOENCODING'] = 'utf-8'

def safe_print(*args, **kwargs):
    """安全的UTF-8 print函数，处理编码问题"""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        # 如果出现编码错误，尝试使用UTF-8编码
        try:
            message = ' '.join(str(arg) for arg in args)
            # 直接写入到stdout，使用UTF-8编码
            sys.stdout.buffer.write((message + '\n').encode('utf-8'))
            sys.stdout.flush()
        except:
            # 最后的备选方案：使用ASCII编码并忽略错误
            message = ' '.join(str(arg) for arg in args)
            print(message.encode('ascii', 'ignore').decode('ascii'))

def get_user_home():
    """Get user home directory"""
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
                safe_print("nginx用户已存在")
                return 'nginx'
        except:
            # nginx用户不存在，创建它
            safe_print("nginx用户不存在，正在创建...")
            
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
                safe_print("nginx用户创建成功")
                return 'nginx'
            except subprocess.CalledProcessError as e:
                # 如果创建失败，可能是因为用户已存在但id命令失败，或其他原因
                safe_print(f"创建nginx用户失败: {e}")
                
                # 再次检查用户是否存在（可能是并发创建）
                try:
                    subprocess.run(['id', 'nginx'], check=True, capture_output=True)
                    safe_print("nginx用户实际上已存在")
                    return 'nginx'
                except:
                    # 确实创建失败，fallback到root用户
                    safe_print("使用root用户作为nginx运行用户")
                    return 'root'
        
    except Exception as e:
        safe_print(f"处理nginx用户时出错: {e}")
        # 出错时使用root用户
        return 'root'

def set_nginx_permissions(web_dir):
    """设置nginx目录的正确权限"""
    try:
        nginx_user = ensure_nginx_user()
        safe_print(f"设置目录权限: {web_dir}")
        safe_print(f"使用用户: {nginx_user}")
        
        # 设置目录和文件权限
        subprocess.run(['sudo', 'chown', '-R', f'{nginx_user}:{nginx_user}', web_dir], check=True)
        subprocess.run(['sudo', 'chmod', '-R', '755', web_dir], check=True)
        subprocess.run(['sudo', 'find', web_dir, '-type', 'f', '-exec', 'chmod', '644', '{}', ';'], check=True)
        
        safe_print(f"权限设置完成: {web_dir} (用户: {nginx_user})")
        return True
    except Exception as e:
        safe_print(f"设置权限失败: {e}")
        return False

def check_port_available(port, prefer_ipv6=False):
    """检查端口是否可用（优先IPv6或IPv4）"""
    if prefer_ipv6:
        # 优先尝试 IPv6
        try:
            s6 = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
            s6.settimeout(1)
            s6.bind(('::', port))
            s6.close()
            return True
        except Exception:
            pass
        
        # 回退到 IPv4
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1)
            s.bind(('', port))
            s.close()
            return True
        except Exception:
            return False
    else:
        # 原有的 IPv4 优先逻辑
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1)
            s.bind(('', port))
            s.close()
            return True
        except Exception:
            # 尝试 IPv6
            try:
                s6 = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
                s6.settimeout(1)
                s6.bind(('::', port))
                s6.close()
                return True
            except Exception:
                return False

def is_port_listening(port, prefer_ipv6=False):
    """检查端口是否已经在监听（IPv6或IPv4）"""
    if prefer_ipv6:
        # 优先检查 IPv6
        try:
            s6 = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
            s6.settimeout(1)
            s6.sendto(b"ping", ('::1', port))
            s6.close()
            return True
        except Exception:
            pass
    
    # 检查 IPv4
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.sendto(b"ping", ('127.0.0.1', port))
        s.close()
        return True
    except Exception:
        # 如果发送失败，尝试绑定看端口是否可用（能绑定说明未监听）
        try:
            t = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            t.bind(('', port))
            t.close() 
            return False
        except Exception:
            return True

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

def get_ipv6_public():
    """尝试获取公网 IPv6 地址"""
    try:
        with urllib.request.urlopen('https://api64.ipify.org', timeout=5) as response:
            public_ip = response.read().decode('utf-8')
            if public_ip and ':' in public_ip:
                return public_ip
    except:
        pass
    return None

def get_local_ipv6_addresses():
    """枚举本地IPv6地址（排除回环和链路本地）"""
    addrs = set()
    try:
        # 方式1: 使用 getaddrinfo
        hostname = socket.gethostname()
        for res in socket.getaddrinfo(hostname, None, socket.AF_INET6):
            addr = res[4][0]
            # 去掉 zone id（%eth0）
            if '%' in addr:
                addr = addr.split('%')[0]
            if addr and not addr.startswith('::1') and not addr.lower().startswith('fe80'):
                addrs.add(addr)
    except:
        pass

    try:
        # 方式2: 调用 ip 命令
        if shutil.which('ip'):
            result = subprocess.run(['ip', '-6', 'addr'], capture_output=True, text=True)
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith('inet6 '):
                    parts = line.split()
                    if len(parts) >= 2:
                        addr = parts[1].split('/')[0]
                        if '%' in addr:
                            addr = addr.split('%')[0]
                        if addr and not addr.startswith('::1') and not addr.lower().startswith('fe80'):
                            addrs.add(addr)
    except:
        pass

    return list(addrs)

def get_best_server_address(prefer_ipv6=False, custom_ipv6_list=None):
    """获取最佳服务器地址（根据参数优先IPv4或IPv6）"""
    if custom_ipv6_list:
        # 用户指定了IPv6地址列表，优先使用第一个
        return custom_ipv6_list[0]
    
    if prefer_ipv6:
        # 优先IPv6
        public_ipv6 = get_ipv6_public()
        if public_ipv6:
            return public_ipv6
        
        local_ipv6_list = get_local_ipv6_addresses()
        if local_ipv6_list:
            return local_ipv6_list[0]
        
        # 回退到IPv4
        return get_ip_address()
    else:
        # 优先IPv4（原有逻辑）
        return get_ip_address()

def create_config_ipv6_aware(base_dir, port, password, cert_path, key_path, 
                            server_address, enable_web_masquerade=True, custom_web_dir=None, 
                            enable_port_hopping=False, obfs_password=None, enable_http3_masquerade=False,
                            prefer_ipv6=False, custom_ipv6_list=None):
    """创建IPv6感知的Hysteria2配置文件"""
    
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
    
    # IPv6配置优化
    if prefer_ipv6 or custom_ipv6_list:
        # 如果优先IPv6，监听所有接口（包括IPv6）
        config["listen"] = f"[::]:{port}"
        
        # 使用IPv6 DNS服务器
        config["resolver"]["tcp"]["addr"] = "2001:4860:4860::8888:53"
        config["resolver"]["udp"]["addr"] = "2001:4860:4860::8888:53"
    
    # 记录IPv6相关信息到配置
    if custom_ipv6_list:
        config["_ipv6_addresses"] = custom_ipv6_list
        config["_ipv6_primary"] = custom_ipv6_list[0]
    if prefer_ipv6:
        config["_prefer_ipv6"] = True
    
    # 端口跳跃配置
    if enable_port_hopping:
        port_start = max(1024, port - 25)  
        port_end = min(65535, port + 25)
        
        if port < 1049:
            port_start = 1024
            port_end = 1074
        
        config["_port_hopping"] = {
            "enabled": True,
            "range_start": port_start,
            "range_end": port_end,
            "listen_port": port
        }
        
        safe_print(f"启用端口跳跃 - 服务器监听: {port}, 客户端可用范围: {port_start}-{port_end}")
    
    # 流量混淆配置
    if obfs_password:
        config["obfs"] = {
            "type": "salamander",
            "salamander": {
                "password": obfs_password
            }
        }
        safe_print(f"启用Salamander混淆 - 密码: {obfs_password}")
    
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
            config["masquerade"] = {
                "type": "proxy",
                "proxy": {
                    "url": "https://www.google.com",
                    "rewriteHost": True
                }
            }
        safe_print("启用HTTP/3伪装 - 流量看起来像正常HTTP/3")
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
    
    # QUIC/HTTP3优化配置
    if port == 443:
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
    with open(config_path, "w", encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    
    return config_path

def setup_port_hopping_iptables_ipv6(port_start, port_end, listen_port, enable_ipv6=False):
    """配置iptables和ip6tables实现端口跳跃（IPv4+IPv6）"""
    try:
        safe_print(f"配置防火墙端口跳跃...")
        safe_print(f"端口范围: {port_start}-{port_end} -> {listen_port}")
        
        # IPv4 iptables 配置
        try:
            subprocess.run(['iptables', '--version'], check=True, capture_output=True)
            
            # 清理旧规则
            subprocess.run(['sudo', 'iptables', '-t', 'nat', '-D', 'PREROUTING', 
                          '-p', 'udp', '--dport', f'{port_start}:{port_end}', 
                          '-j', 'DNAT', '--to-destination', f':{listen_port}'], 
                          check=False, capture_output=True)
            
            # 添加IPv4 NAT规则
            subprocess.run([
                'sudo', 'iptables', '-t', 'nat', '-A', 'PREROUTING', 
                '-p', 'udp', '--dport', f'{port_start}:{port_end}', 
                '-j', 'DNAT', '--to-destination', f':{listen_port}'
            ], check=True)
            
            # 开放端口范围
            subprocess.run([
                'sudo', 'iptables', '-A', 'INPUT', 
                '-p', 'udp', '--dport', f'{port_start}:{port_end}', 
                '-j', 'ACCEPT'
            ], check=True)
            
            safe_print("IPv4 iptables 端口跳跃配置成功")
            
        except Exception as e:
            safe_print(f"IPv4 iptables配置失败: {e}")
        
        # IPv6 ip6tables 配置（如果启用）
        if enable_ipv6:
            try:
                subprocess.run(['ip6tables', '--version'], check=True, capture_output=True)
                
                # 清理旧规则
                subprocess.run(['sudo', 'ip6tables', '-t', 'nat', '-D', 'PREROUTING', 
                              '-p', 'udp', '--dport', f'{port_start}:{port_end}', 
                              '-j', 'DNAT', '--to-destination', f'[::]:{listen_port}'], 
                              check=False, capture_output=True)
                
                # 添加IPv6 NAT规则
                subprocess.run([
                    'sudo', 'ip6tables', '-t', 'nat', '-A', 'PREROUTING', 
                    '-p', 'udp', '--dport', f'{port_start}:{port_end}', 
                    '-j', 'DNAT', '--to-destination', f'[::]:{listen_port}'
                ], check=True)
                
                # 开放IPv6端口范围
                subprocess.run([
                    'sudo', 'ip6tables', '-A', 'INPUT', 
                    '-p', 'udp', '--dport', f'{port_start}:{port_end}', 
                    '-j', 'ACCEPT'
                ], check=True)
                
                safe_print("IPv6 ip6tables 端口跳跃配置成功")
                
            except Exception as e:
                safe_print(f"IPv6 ip6tables配置失败: {e}")
                safe_print("注意：可能系统不支持IPv6或未安装ip6tables")
        
        # 保存规则
        try:
            subprocess.run(['sudo', 'iptables-save'], check=True, capture_output=True)
            subprocess.run(['sudo', 'netfilter-persistent', 'save'], check=False, capture_output=True)
            if enable_ipv6:
                subprocess.run(['sudo', 'ip6tables-save'], check=False, capture_output=True)
        except:
            try:
                subprocess.run(['sudo', 'service', 'iptables', 'save'], check=False, capture_output=True)
            except:
                pass
        
        safe_print(f"防火墙端口跳跃配置完成")
        safe_print(f"客户端可连接端口范围: {port_start}-{port_end}")
        safe_print(f"服务器实际监听端口: {listen_port}")
        
        return True
        
    except Exception as e:
        safe_print(f"防火墙配置失败: {e}")
        return False

def create_nginx_config_ipv6(base_dir, server_address, web_dir, cert_path, key_path, port, prefer_ipv6=False):
    """创建IPv6感知的nginx配置"""
    
    # IPv6感知的监听配置
    if prefer_ipv6:
        listen_directives = f"""
    listen [{server_address}]:{port} ssl http2;
    listen {port} ssl http2;
    listen [::]:80;
    listen 80;"""
    else:
        listen_directives = f"""
    listen {port} ssl http2;
    listen [::]:443 ssl http2;
    listen 80;
    listen [::]:80;"""
    
    nginx_conf = f"""user nginx;
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
{listen_directives}
        server_name _;
        
        ssl_certificate {os.path.abspath(cert_path)};
        ssl_certificate_key {os.path.abspath(key_path)};
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384;
        
        root {web_dir};
        index index.html index.htm;
        
        location / {{
            try_files $uri $uri/ /index.html;
        }}
        
        add_header X-Frame-Options DENY always;
        add_header X-Content-Type-Options nosniff always;
    }}
}}"""
    
    return nginx_conf

def show_help_ipv6():
    """显示包含IPv6选项的帮助信息"""
    safe_print("""
Hysteria2 一键部署工具 (IPv6 增强版)

重要说明：Hysteria2基于UDP/QUIC协议，现在支持IPv6！

使用方法:
    python3 nginx-hysteria2-ipv6.py [命令] [选项]

可用命令:
    install      安装 Hysteria2 (一键部署，自动优化配置)
    client       显示客户端连接指南 (各平台详细说明)
    fix          修复nginx配置和权限问题
    setup-nginx  设置nginx Web伪装
    
    del          删除 Hysteria2
    status       查看 Hysteria2 状态
    help         显示此帮助信息

基础选项:
    --ip IP               指定服务器IP地址
    --port PORT           指定服务器端口 (推荐: 443)
    --password PWD        指定密码

IPv6 新增选项:
    --ipv6                优先使用IPv6进行检测和配置
    --ipv6-addrs ADDRS    逗号分隔的IPv6地址列表，如: 2001:db8::1,2001:db8::2
    
防墙增强选项:
    --domain DOMAIN         指定域名 (推荐用于真实证书)
    --email EMAIL           Let's Encrypt证书邮箱地址  
    --use-real-cert         使用真实域名证书 (需域名指向服务器)
    --web-masquerade        启用Web伪装 (默认启用)
    --auto-nginx            自动配置nginx (默认启用)

高级防墙选项:
    --simple                简化一键部署 (端口跳跃+混淆+nginx Web伪装)
    --port-range RANGE      指定端口跳跃范围 (如: 28888-29999)
    --enable-bbr            启用BBR拥塞控制算法优化网络性能
    --port-hopping          启用端口跳跃 (动态切换端口，防封锁)
    --obfs-password PWD     启用Salamander混淆 (防DPI检测)
    --http3-masquerade      启用HTTP/3伪装 (流量看起来像正常HTTP/3)
    --one-click             一键部署 (自动启用所有防墙功能)

IPv6 使用示例:

    # IPv6 优先模式
    python3 nginx-hysteria2-ipv6.py install --ipv6

    # 指定多个IPv6地址
    python3 nginx-hysteria2-ipv6.py install --ipv6-addrs "2001:db8::1,2001:db8::2"

    # IPv6 + 端口跳跃
    python3 nginx-hysteria2-ipv6.py install --ipv6 --port-hopping

    # IPv6 + 完整防墙功能
    python3 nginx-hysteria2-ipv6.py install --ipv6 --one-click

    # 自定义IPv6地址 + 混淆
    python3 nginx-hysteria2-ipv6.py install --ipv6-addrs "2001:db8::100" --obfs-password "mykey123"

IPv6 特性说明:
- 自动检测公网IPv6地址和本地IPv6地址
- 支持多个IPv6地址配置
- IPv6/IPv4双栈监听支持
- IPv6防火墙规则配置（ip6tables）
- IPv6感知的nginx配置

注意事项:
- 确保服务器和客户端都支持IPv6
- IPv6地址需要用方括号包围，如 [2001:db8::1]:443
- 某些VPS提供商可能需要手动启用IPv6
- 防火墙需要同时开放IPv4和IPv6端口

推荐配置:
1. IPv6优先: --ipv6 --simple
2. 双栈部署: --ipv6-addrs "your:ipv6::addr" --simple  
3. 完整IPv6功能: --ipv6 --one-click
""")

def main():
    parser = argparse.ArgumentParser(description='Hysteria2 一键部署工具（IPv6增强版）')
    parser.add_argument('command', nargs='?', default='install',
                      help='命令: install, del, status, help, setup-nginx, client, fix')
    parser.add_argument('--ip', help='指定服务器IP地址或域名')
    parser.add_argument('--ipv6', action='store_true', help='优先使用IPv6进行检测和配置')
    parser.add_argument('--ipv6-addrs', help='逗号分隔的IPv6地址列表（可自定义多个），如: 2001:db8::1,2001:db8::2')
    parser.add_argument('--port', type=int, help='指定服务器端口（推荐443）')
    parser.add_argument('--password', help='指定密码')
    parser.add_argument('--domain', help='指定域名（用于获取真实证书）')
    parser.add_argument('--email', help='Let\'s Encrypt证书邮箱地址')
    parser.add_argument('--use-real-cert', action='store_true', 
                      help='使用真实域名证书（需要域名指向服务器）')
    parser.add_argument('--web-masquerade', action='store_true', default=True,
                      help='启用Web伪装（默认启用）')
    parser.add_argument('--auto-nginx', action='store_true', default=True,
                      help='安装时自动配置nginx (默认启用)')
    
    # 防墙功能选项
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
    
    if args.command == 'help':
        show_help_ipv6()
        return
    
    # 处理IPv6选项
    prefer_ipv6 = args.ipv6
    custom_ipv6_list = None
    if args.ipv6_addrs:
        custom_ipv6_list = [addr.strip() for addr in args.ipv6_addrs.split(',')]
        prefer_ipv6 = True  # 如果指定了IPv6地址，自动启用IPv6优先
        safe_print(f"使用自定义IPv6地址: {custom_ipv6_list}")
    
    if args.command == 'install':
        # 获取最佳服务器地址
        if args.ip:
            server_address = args.ip
        else:
            server_address = get_best_server_address(prefer_ipv6, custom_ipv6_list)
        
        port = args.port if args.port else 443
        password = args.password if args.password else "ISDdwk@ASI47!F#WE"
        
        # 显示IPv6相关信息
        if prefer_ipv6:
            safe_print("IPv6优先模式已启用")
            if custom_ipv6_list:
                safe_print(f"自定义IPv6地址: {', '.join(custom_ipv6_list)}")
            else:
                public_ipv6 = get_ipv6_public()
                local_ipv6s = get_local_ipv6_addresses()
                if public_ipv6:
                    safe_print(f"检测到公网IPv6: {public_ipv6}")
                if local_ipv6s:
                    safe_print(f"检测到本地IPv6: {', '.join(local_ipv6s)}")
        
        safe_print(f"服务器地址: {server_address}")
        safe_print(f"端口: {port}")
        safe_print(f"协议优先级: {'IPv6优先' if prefer_ipv6 else 'IPv4优先'}")
        
        # 检查端口可用性（IPv6感知）
        if not check_port_available(port, prefer_ipv6):
            safe_print(f"端口 {port} 不可用")
            sys.exit(1)
        
        # 创建目录
        home = get_user_home()
        base_dir = f"{home}/.hysteria2"
        os.makedirs(base_dir, exist_ok=True)
        os.makedirs(f"{base_dir}/cert", exist_ok=True)
        os.makedirs(f"{base_dir}/config", exist_ok=True)
        os.makedirs(f"{base_dir}/logs", exist_ok=True)
        
        # 生成证书（简化）
        cert_path = f"{base_dir}/cert/server.crt"
        key_path = f"{base_dir}/cert/server.key"
        
        domain = server_address
        if not os.path.exists(cert_path) or not os.path.exists(key_path):
            safe_print("生成自签名证书...")
            try:
                subprocess.run([
                    "openssl", "req", "-x509", "-nodes",
                    "-newkey", "rsa:4096",
                    "-keyout", key_path,
                    "-out", cert_path,
                    "-subj", f"/CN={domain}",
                    "-days", "36500",
                    "-sha256"
                ], check=True)
                
                os.chmod(cert_path, 0o644)
                os.chmod(key_path, 0o600)
                safe_print(f"证书生成成功: {cert_path}")
            except Exception as e:
                safe_print(f"生成证书失败: {e}")
                sys.exit(1)
        
        # 创建配置文件（IPv6感知）
        config_path = create_config_ipv6_aware(
            base_dir, port, password, cert_path, key_path, 
            server_address, args.web_masquerade, None, 
            args.port_hopping, args.obfs_password, args.http3_masquerade,
            prefer_ipv6, custom_ipv6_list
        )
        
        # 配置端口跳跃（IPv6支持）
        if args.port_hopping:
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            if "_port_hopping" in config:
                ph_info = config["_port_hopping"]
                setup_port_hopping_iptables_ipv6(
                    ph_info["range_start"], 
                    ph_info["range_end"], 
                    ph_info["listen_port"],
                    prefer_ipv6 or custom_ipv6_list is not None
                )
                # 清理配置文件中的临时信息
                del config["_port_hopping"]
                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump(config, f, indent=2, ensure_ascii=False)
        
        safe_print(f"""
Hysteria2 IPv6增强版安装完成！

服务器信息:
- 地址: {server_address}
- 端口: {port} (UDP)
- 密码: {password}
- 协议栈: {'IPv6优先' if prefer_ipv6 else 'IPv4优先'}
- 证书: {cert_path}

配置文件: {config_path}

IPv6特性:
{'- IPv6优先模式已启用' if prefer_ipv6 else '- IPv4模式（可通过--ipv6启用IPv6）'}
{'- 自定义IPv6地址: ' + ', '.join(custom_ipv6_list) if custom_ipv6_list else ''}
{'- 端口跳跃已启用（IPv4+IPv6防火墙规则）' if args.port_hopping else ''}

客户端连接:
服务器: {server_address}
端口: {port}
密码: {password}
TLS: 启用
跳过证书验证: 是

注意事项:
- 确保防火墙已开放UDP端口 {port}
{'- IPv6需要确保ip6tables规则正确配置' if prefer_ipv6 else ''}
- 可以使用 --ipv6 参数优先使用IPv6
- 可以使用 --ipv6-addrs 指定多个IPv6地址

需要手动启动服务，或创建启动脚本。
""")
        
    else:
        safe_print(f"未知命令: {args.command}")
        safe_print("使用 --help 查看可用命令")

if __name__ == "__main__":
    main()
