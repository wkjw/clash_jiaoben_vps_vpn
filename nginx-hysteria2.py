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
    """Get user home directory"""
    return str(Path.home())

def get_system_info():
    """Get system information"""
    system = platform.system().lower()
    machine = platform.machine().lower()
    
    # OS mapping
    os_map = {
        'linux': 'linux',
        'darwin': 'darwin',  # macOS
        'windows': 'windows'
    }
    
    # Architecture mapping
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
    """Ensure nginx user exists, create if not, uniformly use nginx user"""
    try:
        # Check if nginx user already exists
        try:
            result = subprocess.run(['id', 'nginx'], check=True, capture_output=True, text=True)
            if result.returncode == 0:
                print("nginx user already exists")
                return 'nginx'
        except:
            # nginx user does not exist, create it
            print("nginx user does not exist, creating...")
            
            # Create nginx system user (no login shell, no home directory)
            try:
                subprocess.run([
                    'sudo', 'useradd', 
                    '--system',           # System user
                    '--no-create-home',   # No home directory
                    '--shell', '/bin/false',  # No login shell
                    '--comment', 'nginx web server',  # Comment
                    'nginx'
                ], check=True, capture_output=True)
                print("nginx user created successfully")
                return 'nginx'
            except subprocess.CalledProcessError as e:
                # If creation fails, perhaps user already exists or other reasons
                print(f"Creating nginx user failed: {e}")
                
                # Check again if user exists (possible concurrent creation)
                try:
                    subprocess.run(['id', 'nginx'], check=True, capture_output=True)
                    print("nginx user actually exists")
                    return 'nginx'
                except:
                    # Creation truly failed, fallback to root user
                    print("Using root user as nginx runtime user")
                    return 'root'
        
    except Exception as e:
        print(f"Error handling nginx user: {e}")
        # Use root user on error
        return 'root'

def set_nginx_permissions(web_dir):
    """Set correct permissions for nginx directory"""
    try:
        nginx_user = ensure_nginx_user()
        print(f"Setting directory permissions: {web_dir}")
        print(f"Using user: {nginx_user}")
        
        # Set directory and file permissions
        subprocess.run(['sudo', 'chown', '-R', f'{nginx_user}:{nginx_user}', web_dir], check=True)
        subprocess.run(['sudo', 'chmod', '-R', '755', web_dir], check=True)
        subprocess.run(['sudo', 'find', web_dir, '-type', 'f', '-exec', 'chmod', '644', '{}', ';'], check=True)
        
        print(f"Permissions set successfully: {web_dir} (user: {nginx_user})")
        return True
    except Exception as e:
        print(f"Setting permissions failed: {e}")
        return False

def check_port_available(port):
    """Check if port is available (using socket only)"""
    try:
        # For Hysteria2, mainly check UDP port
        # nginx uses TCP port, hysteria uses UDP port, they can coexist
        
        # Check if UDP port is available (needed by hysteria2)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(1)
            try:
                s.bind(('', port))
                return True  # UDP port available
            except:
                # UDP port occupied, check if hysteria process
                return False
                
    except:
        # If any exception, conservatively return port unavailable
        return False

def is_port_listening(port):
    """Check if port is listening (if service is started)"""
    try:
        # Attempt to connect to port
        # Since Hysteria uses UDP, check UDP port
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1)
        
        # Try sending a packet to port
        # If port open, send won't throw exception
        try:
            sock.sendto(b"ping", ('127.0.0.1', port))
            try:
                sock.recvfrom(1024)  # Try receiving response
                return True
            except socket.timeout:
                # No response but no error, may still be listening
                return True
        except:
            pass
            
        # Another way: try binding port, if fails port is occupied
        try:
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            test_sock.bind(('', port))
            test_sock.close()
            return False  # Successful bind means port not occupied
        except:
            return True  # Cannot bind means port occupied
            
        return False
    except:
        return False
    finally:
        try:
            sock.close()
        except:
            pass

def check_process_running(pid_file):
    """Check if process is running"""
    if not os.path.exists(pid_file):
        return False
        
    try:
        with open(pid_file, 'r') as f:
            pid = f.read().strip()
            
        if not pid:
            return False
            
        # Try sending signal 0 to check process existence
        try:
            os.kill(int(pid), 0)
            return True
        except:
            return False
    except:
        return False

def create_directories():
    """Create necessary directories"""
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
    """Download file with retry mechanism"""
    for i in range(max_retries):
        try:
            print(f"Downloading... (attempt {i+1}/{max_retries})")
            urllib.request.urlretrieve(url, save_path)
            return True
        except Exception as e:
            print(f"Download failed: {e}")
            if i < max_retries - 1:
                time.sleep(2)  # Wait 2 seconds before retry
            continue
    return False

def get_latest_version():
    """Return fixed latest version v2.6.1"""
    return "v2.6.1"

def get_download_filename(os_name, arch):
    """Return correct filename based on OS and architecture"""
    # windows needs .exe
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
    """Verify if binary file is valid (simplified version)"""
    try:
        # Check if file exists
        if not os.path.exists(binary_path):
            return False
            
        # Check file size (at least 5MB - hysteria generally >10MB)
        if os.path.getsize(binary_path) < 5 * 1024 * 1024:
            return False
            
        # Set file executable
        os.chmod(binary_path, 0o755)
        
        # Return success
        return True
    except:
        return False

def download_hysteria2(base_dir):
    """Download Hysteria2 binary, using simplified link and verification"""
    try:
        version = get_latest_version()
        os_name, arch = get_system_info()
        filename = get_download_filename(os_name, arch)
        
        # Use only original GitHub link, avoid mirror issues
        url = f"https://github.com/apernet/hysteria/releases/download/app/{version}/{filename}"
        
        binary_path = f"{base_dir}/hysteria"
        if os_name == 'windows':
            binary_path += '.exe'
        
        print(f"Downloading Hysteria2 {version}...")
        print(f"System type: {os_name}, Architecture: {arch}, Filename: {filename}")
        print(f"Download link: {url}")
        
        # Use wget to download
        try:
            has_wget = shutil.which('wget') is not None
            has_curl = shutil.which('curl') is not None
            
            if has_wget:
                print("Using wget to download...")
                subprocess.run(['wget', '--tries=3', '--timeout=15', '-O', binary_path, url], check=True)
            elif has_curl:
                print("Using curl to download...")
                subprocess.run(['curl', '-L', '--connect-timeout', '15', '-o', binary_path, url], check=True)
            else:
                print("System has no wget/curl, trying Python download...")
                urllib.request.urlretrieve(url, binary_path)
                
            # Verify download
            if not verify_binary(binary_path):
                raise Exception("Downloaded file is invalid")
                
            print(f"Download successful: {binary_path}, Size: {os.path.getsize(binary_path)/1024/1024:.2f}MB")
            return binary_path, version
            
        except Exception as e:
            print(f"Automatic download failed: {e}")
            print("Please follow these steps to download manually:")
            print(f"1. Visit https://github.com/apernet/hysteria/releases/tag/app/{version}")
            print(f"2. Download {filename} file")
            print(f"3. Rename the file to hysteria (no extension) and move to {base_dir}/ directory")
            print(f"4. Execute: chmod +x {base_dir}/hysteria")
            
            # Ask if user has placed the file
            while True:
                user_input = input("Manual download and placement completed? (y/n): ").lower()
                if user_input == 'y':
                    # Check if file exists
                    if os.path.exists(binary_path) and verify_binary(binary_path):
                        print("File verification successful, continuing installation...")
                        return binary_path, version
                    else:
                        print(f"File does not exist or invalid, ensure placed at {binary_path}.")
                elif user_input == 'n':
                    print("Aborting installation.")
                    sys.exit(1)
    
    except Exception as e:
        print(f"Download error: {e}")
        sys.exit(1)

def get_ip_address():
    """Get machine IP address (prefer public IP, fallback to local IP)"""
    # First try to get public IP
    try:
        # Try from public API
        with urllib.request.urlopen('https://api.ipify.org', timeout=5) as response:
            public_ip = response.read().decode('utf-8')
            if public_ip and len(public_ip) > 0:
                return public_ip
    except:
        try:
            # Alternative API
            with urllib.request.urlopen('https://ifconfig.me', timeout=5) as response:
                public_ip = response.read().decode('utf-8')
                if public_ip and len(public_ip) > 0:
                    return public_ip
        except:
            pass

    # If getting public IP fails, try local IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # No need to actually connect, just get routing info
        s.connect(('8.8.8.8', 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except:
        # If all methods fail, return loopback address
        return '127.0.0.1'

def setup_nginx_smart_proxy(base_dir, domain, web_dir, cert_path, key_path, hysteria_port):
    """Set up nginx web masquerade: TCP port shows normal website, UDP port for Hysteria2"""
    print("Configuring nginx web masquerade...")
    
    try:
        # Check certificate files
        print(f"Checking certificate file paths:")
        print(f"Certificate file: {cert_path}")
        print(f"Key file: {key_path}")
        
        if not os.path.exists(cert_path):
            print(f"Certificate file does not exist: {cert_path}")
            cert_path, key_path = generate_self_signed_cert(base_dir, domain)
        
        if not os.path.exists(key_path):
            print(f"Key file does not exist: {key_path}")
            cert_path, key_path = generate_self_signed_cert(base_dir, domain)
        
        print(f"Final certificate paths used:")
        print(f"Certificate: {cert_path}")
        print(f"Key: {key_path}")
        
        # Ensure nginx user exists
        nginx_user = ensure_nginx_user()
        print(f"Using nginx user: {nginx_user}")
        
        # Create nginx standard web configuration
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
        listen 443 ssl http2;
        server_name _;
        
        ssl_certificate {os.path.abspath(cert_path)};
        ssl_certificate_key {os.path.abspath(key_path)};
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384;
        
        root {web_dir};
        index index.html index.htm;
        
        # Normal website access
        location / {{
            try_files $uri $uri/ /index.html;
        }}
        
        add_header X-Frame-Options DENY always;
        add_header X-Content-Type-Options nosniff always;
    }}
}}"""
        
        # Update nginx configuration
        print("Backing up current nginx configuration...")
        subprocess.run(['sudo', 'cp', '/etc/nginx/nginx.conf', '/etc/nginx/nginx.conf.backup'], check=True)
        
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.conf') as tmp:
            tmp.write(nginx_conf)
            tmp.flush()
            subprocess.run(['sudo', 'cp', tmp.name, '/etc/nginx/nginx.conf'], check=True)
            os.unlink(tmp.name)
        
        subprocess.run(['sudo', 'rm', '-f', '/etc/nginx/conf.d/*.conf'], check=True)
        
        # Test and restart
        print("Testing nginx configuration...")
        test_result = subprocess.run(['sudo', 'nginx', '-t'], capture_output=True, text=True)
        if test_result.returncode != 0:
            print(f"nginx configuration test failed:")
            print(f"Error message: {test_result.stderr}")
            subprocess.run(['sudo', 'cp', '/etc/nginx/nginx.conf.backup', '/etc/nginx/nginx.conf'], check=True)
            print("Restored nginx configuration backup")
            return False, None
        
        print("nginx configuration test passed")
        
        print("Restarting nginx service...")
        restart_result = subprocess.run(['sudo', 'systemctl', 'restart', 'nginx'], capture_output=True, text=True)
        if restart_result.returncode != 0:
            print(f"nginx restart failed:")
            print(f"Error message: {restart_result.stderr}")
            return False, None
        
        print("nginx web masquerade configuration successful!")
        print("TCP port: Standard HTTPS website")
        print("UDP port: Hysteria2 proxy service")
        
        return True, hysteria_port
        
    except Exception as e:
        print(f"Configuration failed: {e}")
        return False, None

def create_web_masquerade(base_dir):
    """Create web masquerade page"""
    web_dir = f"{base_dir}/web"
    os.makedirs(web_dir, exist_ok=True)
    
    return create_web_files_in_directory(web_dir)

def create_web_files_in_directory(web_dir):
    """Create web files in specified directory"""
    # Ensure directory exists
    if not os.path.exists(web_dir):
        try:
            subprocess.run(['sudo', 'mkdir', '-p', web_dir], check=True)
        except:
            os.makedirs(web_dir, exist_ok=True)
    
    # Create a more realistic enterprise website homepage
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
                     <div class="feature-icon"></div>
                     <h3>Security & Compliance</h3>
                     <p>Advanced security protocols and compliance standards including SOC 2, ISO 27001, and GDPR to protect your business data.</p>
                 </div>
                 <div class="feature">
                     <div class="feature-icon"></div>
                     <h3>High Performance</h3>
                     <p>Lightning-fast performance with our global CDN network and optimized infrastructure for maximum speed and reliability.</p>
                 </div>
                 <div class="feature">
                     <div class="feature-icon"></div>
                     <h3>Analytics & Monitoring</h3>
                     <p>Real-time monitoring and detailed analytics to help you optimize performance and make data-driven business decisions.</p>
                 </div>
                 <div class="feature">
                     <div class="feature-icon"></div>
                     <h3>Managed Services</h3>
                     <p>Full-stack managed services including database management, security updates, and performance optimization by our experts.</p>
                 </div>
                 <div class="feature">
                     <div class="feature-icon"></div>
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
    
    # Use sudo to write file (if needed)
    try:
        with open(f"{web_dir}/index.html", "w", encoding="utf-8") as f:
            f.write(index_html)
    except PermissionError:
        # Use sudo to write
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.html') as tmp:
            tmp.write(index_html)
            tmp.flush()
            subprocess.run(['sudo', 'cp', tmp.name, f"{web_dir}/index.html"], check=True)
            os.unlink(tmp.name)
    
    # Create robots.txt (to look more real)
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
    
    # Create sitemap.xml
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
    
    # Create favicon.ico (simple base64 encoded)
    # This is a simple blue circle icon
    favicon_data = """AAABAAEAEBAAAAEAIABoBAAAFgAAACgAAAAQAAAAIAAAAAEAIAAAAAAAAAQAABILAAASCwAAAAAAAAAAAAD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A2dnZ/9nZ2f/Z2dn/2dnZ/9nZ2f/Z2dn/2dnZ/9nZ2f/Z2dn/2dnZ/////wD///8A////AP///wD///8A2dnZ/1tbW/8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/1tbW//Z2dn/////AP///wD///8A2dnZ/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/2dnZ/////wD///8A2dnZ/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/2dnZ/////wD///8A2dnZ/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/2dnZ/////wD///8A2dnZ/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/2dnZ/////wD///8A2dnZ/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/2dnZ/////wD///8A2dnZ/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/2dnZ/////wD///8A2dnZ/1tbW/8AAAD/AAAA/wAAAP8AAAD/AAAA/wAAAP8AAAD/AAAA/1tbW//Z2dn/////AP///wD///8A////AP///wD///8A2dnZ/9nZ2f/Z2dn/2dnZ/9nZ2f/Z2dn/2dnZ/9nZ2f/Z2dn/2dnZ/////wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAA=="""
    
    import base64
    try:
        favicon_bytes = base64.b64decode(favicon_data)
        try:
            with open(f"{web_dir}/favicon.ico", "wb") as f:
                f.write(favicon_bytes)
        except PermissionError:
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix='.ico') as tmp:
                tmp.write(favicon_bytes)
                tmp.flush()
                subprocess.run(['sudo', 'cp', tmp.name, f"{web_dir}/favicon.ico"], check=True)
                os.unlink(tmp.name)
    except:
        pass  # Skip if favicon creation fails
    
    # Create about page
    about_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>About Us - Global Digital Solutions</title>
    <link rel="stylesheet" href="style.css">
</head>
<body>
    <div style="text-align: center; padding: 50px; font-family: Arial, sans-serif;">
        <h1>About Global Digital Solutions</h1>
        <p>We are a leading provider of enterprise cloud solutions, serving businesses worldwide since 2015.</p>
        <p>Our mission is to transform how businesses operate in the digital age through innovative cloud technologies.</p>
        <p><a href="/">← Back to Home</a></p>
    </div>
</body>
</html>"""
    try:
        with open(f"{web_dir}/about.html", "w", encoding="utf-8") as f:
            f.write(about_html)
    except PermissionError:
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.html') as tmp:
            tmp.write(about_html)
            tmp.flush()
            subprocess.run(['sudo', 'cp', tmp.name, f"{web_dir}/about.html"], check=True)
            os.unlink(tmp.name)
    
    # Create 404 page
    error_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>404 - Page Not Found</title>
    <style>
        body { font-family: Arial, sans-serif; text-align: center; padding: 50px; background: #f4f4f4; }
        .error-container { background: white; padding: 50px; border-radius: 10px; box-shadow: 0 0 20px rgba(0,0,0,0.1); max-width: 500px; margin: 0 auto; }
        h1 { color: #e74c3c; font-size: 4rem; margin-bottom: 1rem; }
        p { color: #666; font-size: 1.2rem; }
        a { color: #3498db; text-decoration: none; }
    </style>
</head>
<body>
    <div class="error-container">
        <h1>404</h1>
        <p>Sorry, the page you are looking for could not be found.</p>
        <p><a href="/">Return to Homepage</a></p>
    </div>
</body>
</html>"""
    
    try:
        with open(f"{web_dir}/404.html", "w", encoding="utf-8") as f:
            f.write(error_html)
    except PermissionError:
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.html') as tmp:
            tmp.write(error_html)
            tmp.flush()
            subprocess.run(['sudo', 'cp', tmp.name, f"{web_dir}/404.html"], check=True)
            os.unlink(tmp.name)
    
    return web_dir

def generate_self_signed_cert(base_dir, domain):
    """Generate self-signed certificate"""
    cert_dir = f"{base_dir}/cert"
    cert_path = f"{cert_dir}/server.crt"
    key_path = f"{cert_dir}/server.key"
    
    # Ensure domain is not empty, use default if empty
    if not domain or not domain.strip():
        domain = "localhost"
        print("Warning: Domain is empty, using localhost as certificate common name")
    
    try:
        # Generate more secure certificate
        subprocess.run([
            "openssl", "req", "-x509", "-nodes",
            "-newkey", "rsa:4096",  # Use 4096-bit key
            "-keyout", key_path,
            "-out", cert_path,
            "-subj", f"/CN={domain}",
            "-days", "36500",
            "-sha256"  # Use SHA256
        ], check=True)
        
        # Set appropriate permissions
        os.chmod(cert_path, 0o644)
        os.chmod(key_path, 0o600)
        
        return cert_path, key_path
    except Exception as e:
        print(f"Generating certificate failed: {e}")
        sys.exit(1)

def get_real_certificate(base_dir, domain, email="admin@example.com"):
    """Use certbot to obtain real Let's Encrypt certificate"""
    cert_dir = f"{base_dir}/cert"
    
    try:
        # Check if certbot is installed
        if not shutil.which('certbot'):
            print("Installing certbot...")
            if platform.system().lower() == 'linux':
                # Ubuntu/Debian
                if shutil.which('apt'):
                    subprocess.run(['sudo', 'apt', 'update'], check=True)
                    subprocess.run(['sudo', 'apt', 'install', '-y', 'certbot'], check=True)
                # CentOS/RHEL
                elif shutil.which('yum'):
                    subprocess.run(['sudo', 'yum', 'install', '-y', 'certbot'], check=True)
                elif shutil.which('dnf'):
                    subprocess.run(['sudo', 'dnf', 'install', '-y', 'certbot'], check=True)
                else:
                    print("Cannot automatically install certbot, please install manually")
                    return None, None
            else:
                print("Please install certbot manually")
                return None, None
        
        # Obtain certificate using standalone mode
        print(f"Obtaining Let's Encrypt certificate for domain {domain}...")
        subprocess.run([
            'sudo', 'certbot', 'certonly',
            '--standalone',
            '--agree-tos',
            '--non-interactive',
            '--email', email,
            '-d', domain
        ], check=True)
        
        # Copy certificate to our directory
        cert_source = f"/etc/letsencrypt/live/{domain}/fullchain.pem"
        key_source = f"/etc/letsencrypt/live/{domain}/privkey.pem"
        cert_path = f"{cert_dir}/server.crt"
        key_path = f"{cert_dir}/server.key"
        
        shutil.copy2(cert_source, cert_path)
        shutil.copy2(key_source, key_path)
        
        # Set permissions
        os.chmod(cert_path, 0o644)
        os.chmod(key_path, 0o600)
        
        print(f"Successfully obtained real certificate: {cert_path}")
        return cert_path, key_path
        
    except Exception as e:
        print(f"Obtaining real certificate failed: {e}")
        print("Will use self-signed certificate as fallback...")
        return None, None

def create_config(base_dir, port, password, cert_path, key_path, domain, enable_web_masquerade=True, custom_web_dir=None, enable_port_hopping=False, obfs_password=None, enable_http3_masquerade=False):
    """Create Hysteria2 configuration file (port hopping, obfuscation, HTTP/3 masquerade)"""
    
    # Base configuration
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
    
    # Port hopping configuration (Port Hopping)
    if enable_port_hopping:
        # Hysteria2 server listens on single port, port hopping via iptables DNAT
        port_start = max(1024, port - 25)  
        port_end = min(65535, port + 25)
        
        # Ensure reasonable range: if base port too small, use fixed range
        if port < 1049:  # 1024 + 25
            port_start = 1024
            port_end = 1074
        
        # Server still listens on single port
        config["listen"] = f":{port}"
        
        # Record port hopping info for subsequent iptables config
        config["_port_hopping"] = {
            "enabled": True,
            "range_start": port_start,
            "range_end": port_end,
            "listen_port": port
        }
        
        print(f"Enabled port hopping - Server listen: {port}, Client available range: {port_start}-{port_end}")
    
    # Traffic obfuscation configuration (Salamander Obfuscation)
    if obfs_password:
        config["obfs"] = {
            "type": "salamander",
            "salamander": {
                "password": obfs_password
            }
        }
        print(f"Enabled Salamander obfuscation - Password: {obfs_password}")
    
    # HTTP/3 masquerade configuration
    if enable_http3_masquerade:
        if enable_web_masquerade and custom_web_dir and os.path.exists(custom_web_dir):
            config["masquerade"] = {
                "type": "file",
                "file": {
                    "dir": custom_web_dir
                }
            }
        else:
            # Use HTTP/3 website masquerade
            config["masquerade"] = {
                "type": "proxy",
                "proxy": {
                    "url": "https://www.google.com",
                    "rewriteHost": True
                }
            }
        print("Enabled HTTP/3 masquerade - Traffic looks like normal HTTP/3")
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
    
    # QUIC/HTTP3 optimization configuration
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
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    
    return config_path

def create_service_script(base_dir, binary_path, config_path, port):
    """Create startup script"""
    os_name = platform.system().lower()
    pid_file = f"{base_dir}/hysteria.pid"
    log_file = f"{base_dir}/logs/hysteria.log"
    
    if os_name == 'windows':
        script_content = f"""@echo off
echo Starting Hysteria2 service...
start /b {binary_path} server -c {config_path} > {log_file} 2>&1
echo Startup command executed, check logs to confirm service status
"""
        script_path = f"{base_dir}/start.bat"
    else:
        script_content = f"""#!/bin/bash
echo "Starting Hysteria2 service..."

# Check if binary file exists
if [ ! -f "{binary_path}" ]; then
    echo "Error: Hysteria2 binary file does not exist"
    exit 1
fi

# Check if configuration file exists
if [ ! -f "{config_path}" ]; then
    echo "Error: Configuration file does not exist"
    exit 1
fi

# Start service
nohup {binary_path} server -c {config_path} > {log_file} 2>&1 &
echo $! > {pid_file}
echo "Hysteria2 service started, PID: $(cat {pid_file})"

# Give service some time to start
sleep 2
echo "Startup command executed, check logs to confirm service status"
"""
        script_path = f"{base_dir}/start.sh"
    
    with open(script_path, "w") as f:
        f.write(script_content)
    
    if os_name != 'windows':
        os.chmod(script_path, 0o755)
    
    return script_path

def create_stop_script(base_dir):
    """Create stop script"""
    os_name = platform.system().lower()
    
    if os_name == 'windows':
        script_content = f"""@echo off
for /f "tokens=*" %%a in ('type {base_dir}\\hysteria.pid') do (
    taskkill /F /PID %%a
)
del {base_dir}\\hysteria.pid
echo Hysteria2 service stopped
"""
        script_path = f"{base_dir}/stop.bat"
    else:
        script_content = f"""#!/bin/bash
if [ -f {base_dir}/hysteria.pid ]; then
    kill $(cat {base_dir}/hysteria.pid)
    rm {base_dir}/hysteria.pid
    echo "Hysteria2 service stopped"
else
    echo "Hysteria2 service not running"
fi
"""
        script_path = f"{base_dir}/stop.sh"
    
    with open(script_path, "w") as f:
        f.write(script_content)
    
    if os_name != 'windows':
        os.chmod(script_path, 0o755)
    
    return script_path

def delete_hysteria2():
    """Complete deletion of Hysteria2 installation in 5 steps"""
    print("Starting complete deletion of Hysteria2...")
    print("Deletion process: Stop service -> Clean iptables -> Clean nginx -> Delete directories -> Clean service")
    
    home = get_user_home()
    base_dir = f"{home}/.hysteria2"
    
    if not os.path.exists(base_dir):
        print("Hysteria2 not installed or already deleted")
        return True
    
    # 1. Stop Hysteria2 service
    print("\nStep 1: Stop Hysteria2 service")
    try:
        pid_file = f"{base_dir}/hysteria.pid"
        
        if os.path.exists(pid_file):
            try:
                with open(pid_file, 'r') as f:
                    pid = f.read().strip()
                if pid:
                    try:
                        os.kill(int(pid), 15)  # SIGTERM
                        time.sleep(2)
                        print(f"Stopped Hysteria2 process (PID: {pid})")
                    except ProcessLookupError:
                        print("Process does not exist")
                    except Exception as e:
                        print(f"Stopping process failed: {e}")
                        try:
                            os.kill(int(pid), 9)  # SIGKILL
                            print("Force terminated process successfully")
                        except:
                            pass
            except Exception as e:
                print(f"Reading PID file failed: {e}")
        
        # Find and stop all hysteria processes
        try:
            result = subprocess.run(['pgrep', '-f', 'hysteria'], capture_output=True, text=True)
            if result.stdout.strip():
                pids = result.stdout.strip().split('\n')
                for pid in pids:
                    try:
                        subprocess.run(['sudo', 'kill', '-15', pid], check=True)
                        print(f"Stopped hysteria process: {pid}")
                    except:
                        try:
                            subprocess.run(['sudo', 'kill', '-9', pid], check=True)
                        except:
                            pass
        except:
            pass
            
    except Exception as e:
        print(f"Stopping service failed: {e}")
    
    # 2. Clean iptables rules
    print("\nStep 2: Clean iptables rules")
    try:
        port_ranges = []
        
        # Read port info from configuration file
        config_path = f"{base_dir}/config/config.json"
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    config = json.load(f)
                listen_port = int(config.get('listen', ':443').replace(':', ''))
                
                # Calculate possible port range
                port_start = max(1024, listen_port - 25)
                port_end = min(65535, listen_port + 25)
                if listen_port < 1049:
                    port_start = 1024
                    port_end = 1074
                
                port_ranges.append((port_start, port_end, listen_port))
                print(f"Read port info from config file: {port_start}-{port_end} -> {listen_port}")
            except:
                pass
    
        # Add common port ranges to ensure complete cleanup
        common_ranges = [
            (1024, 1074, 443),
            (28888, 29999, 443),
            (10000, 10050, 443),
            (20000, 20050, 443)
        ]
        port_ranges.extend(common_ranges)
        
        # Clean iptables rules
        for port_start, port_end, listen_port in port_ranges:
            try:
                # Delete NAT rules
                subprocess.run([
                    'sudo', 'iptables', '-t', 'nat', '-D', 'PREROUTING',
                    '-p', 'udp', '--dport', f'{port_start}:{port_end}',
                    '-j', 'DNAT', '--to-destination', f'127.0.0.1:{listen_port}'
                ], check=False)
                
                # Delete other related rules
                subprocess.run([
                    'sudo', 'iptables', '-t', 'nat', '-D', 'OUTPUT',
                    '-p', 'udp', '--dport', f'{port_start}:{port_end}',
                    '-j', 'DNAT', '--to-destination', f'127.0.0.1:{listen_port}'
                ], check=False)
                
                # Delete INPUT rules
                subprocess.run([
                    'sudo', 'iptables', '-D', 'INPUT',
                    '-p', 'udp', '--dport', f'{port_start}:{port_end}',
                    '-j', 'ACCEPT'
                ], check=False)
                
                subprocess.run([
                    'sudo', 'iptables', '-D', 'INPUT',
                    '-p', 'udp', '--dport', f'{listen_port}',
                    '-j', 'ACCEPT'
                ], check=False)
                
            except Exception as e:
                print(f"Cleaning iptables for range {port_start}-{port_end} failed: {e}")
        
        # Save iptables rules
        try:
            subprocess.run(['sudo', 'iptables-save'], check=True, capture_output=True)
        except:
            pass
        
        print("iptables cleanup complete")
    except Exception as e:
        print(f"iptables cleanup failed: {e}")
    
    # 3. Clean nginx configuration
    print("\nStep 3: Clean nginx configuration")
    try:
        # Restore nginx backup if exists
        if os.path.exists('/etc/nginx/nginx.conf.backup'):
            subprocess.run(['sudo', 'cp', '/etc/nginx/nginx.conf.backup', '/etc/nginx/nginx.conf'], check=True)
            subprocess.run(['sudo', 'rm', '-f', '/etc/nginx/nginx.conf.backup'], check=True)
            print("Restored nginx configuration from backup")
        
        # Remove hysteria related conf files
        subprocess.run(['sudo', 'rm', '-f', '/etc/nginx/conf.d/hysteria*'], check=True)
        
        # Restart nginx
        subprocess.run(['sudo', 'systemctl', 'restart', 'nginx'], check=False)
        
        print("nginx cleanup complete")
    except Exception as e:
        print(f"nginx cleanup failed: {e}")
    
    # 4. Delete directories
    print("\nStep 4: Delete directories")
    try:
        if os.path.exists(base_dir):
            shutil.rmtree(base_dir)
            print("Deleted Hysteria2 directory")
    except Exception as e:
        print(f"Deleting directory failed: {e}")
    
    # 5. Clean service
    print("\nStep 5: Clean service")
    try:
        # Remove startup scripts if any
        for script in ['start.sh', 'stop.sh', 'start.bat', 'stop.bat']:
            script_path = f"{base_dir}/{script}"
            if os.path.exists(script_path):
                os.remove(script_path)
        
        print("Service cleanup complete")
    except Exception as e:
        print(f"Service cleanup failed: {e}")
    
    print("\nHysteria2 deletion complete")
    return True

def setup_port_hopping_iptables(port_start, port_end, listen_port):
    """Set up iptables for port hopping"""
    try:
        print(f"Setting up iptables port hopping: {port_start}-{port_end} -> {listen_port}")
        
        # Delete existing rules to avoid duplicates
        subprocess.run([
            'sudo', 'iptables', '-t', 'nat', '-D', 'PREROUTING',
            '-p', 'udp', '--dport', f'{port_start}:{port_end}',
            '-j', 'DNAT', '--to-destination', f'127.0.0.1:{listen_port}'
        ], check=False)
        
        subprocess.run([
            'sudo', 'iptables', '-t', 'nat', '-D', 'OUTPUT',
            '-p', 'udp', '--dport', f'{port_start}:{port_end}',
            '-j', 'DNAT', '--to-destination', f'127.0.0.1:{listen_port}'
        ], check=False)
        
        subprocess.run([
            'sudo', 'iptables', '-D', 'INPUT',
            '-p', 'udp', '--dport', f'{port_start}:{port_end}',
            '-j', 'ACCEPT'
        ], check=False)
        
        # Add NAT rules
        subprocess.run([
            'sudo', 'iptables', '-t', 'nat', '-A', 'PREROUTING',
            '-p', 'udp', '--dport', f'{port_start}:{port_end}',
            '-j', 'DNAT', '--to-destination', f'127.0.0.1:{listen_port}'
        ], check=True)
        
        subprocess.run([
            'sudo', 'iptables', '-t', 'nat', '-A', 'OUTPUT',
            '-p', 'udp', '--dport', f'{port_start}:{port_end}',
            '-j', 'DNAT', '--to-destination', f'127.0.0.1:{listen_port}'
        ], check=True)
        
        # Allow UDP ports in INPUT chain
        subprocess.run([
            'sudo', 'iptables', '-A', 'INPUT',
            '-p', 'udp', '--dport', f'{port_start}:{port_end}',
            '-j', 'ACCEPT'
        ], check=True)
        
        # Allow listen port
        subprocess.run([
            'sudo', 'iptables', '-A', 'INPUT',
            '-p', 'udp', '--dport', f'{listen_port}',
            '-j', 'ACCEPT'
        ], check=True)
        
        # Allow TCP 443 for nginx
        subprocess.run([
            'sudo', 'iptables', '-A', 'INPUT',
            '-p', 'tcp', '--dport', '443', '-j', 'ACCEPT'
        ], check=False)
        
        # Try to save iptables rules
        try:
            # Debian/Ubuntu
            subprocess.run(['sudo', 'iptables-save'], check=True, capture_output=True)
            subprocess.run(['sudo', 'netfilter-persistent', 'save'], check=False, capture_output=True)
        except:
            try:
                # CentOS/RHEL
                subprocess.run(['sudo', 'service', 'iptables', 'save'], check=False, capture_output=True)
            except:
                pass
        
        print("iptables port hopping configuration successful")
        print(f"Client connectable port range: {port_start}-{port_end}")
        print(f"Server actual listen port: {listen_port}")
        
        return True
        
    except Exception as e:
        print(f"iptables configuration failed: {e}")
        print("Port hopping function may not work properly")
        return False

def deploy_hysteria2_complete(server_address, port=443, password="123qwe!@#QWE", enable_real_cert=False, domain=None, email="admin@example.com", port_range=None, enable_bbr=False):
    """
    Hysteria2 complete one-click deployment: port hopping + obfuscation + nginx web masquerade
    """
    print("Starting Hysteria2 complete deployment...")
    print("Deployment content: port hopping + Salamander obfuscation + nginx web masquerade")
    
    # 1. Create directories
    base_dir = create_directories()
    print(f"Created directories: {base_dir}")
    
    # 2. Download Hysteria2
    binary_path, version = download_hysteria2(base_dir)
    print(f"Downloaded Hysteria2: {version}")
    
    # 3. Generate obfuscation password
    import random, string
    obfs_password = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
    print(f"Generated obfuscation password: {obfs_password}")
    
    # 4. Generate or obtain certificate
    if enable_real_cert and domain:
        cert_path, key_path = get_real_certificate(base_dir, domain, email)
        if not cert_path:
            cert_path, key_path = generate_self_signed_cert(base_dir, domain)
    else:
        cert_path, key_path = generate_self_signed_cert(base_dir, server_address)
    print(f"Certificate configuration: {cert_path}")
    
    # 5. Create web masquerade files
    web_dir = create_web_masquerade(base_dir)
    print(f"Created web masquerade: {web_dir}")
    
    # 6. Create Hysteria2 configuration (port hopping + obfuscation + HTTP/3 masquerade)
    hysteria_config = {
        "listen": f":{port}",
        "tls": {
            "cert": cert_path,
            "key": key_path
        },
        "auth": {
            "type": "password",
            "password": password
        },
        "obfs": {
            "type": "salamander",
            "salamander": {
                "password": obfs_password
            }
        },
        "masquerade": {
            "type": "proxy",
            "proxy": {
                "url": "https://www.microsoft.com",
                "rewriteHost": True
            }
        },
        "bandwidth": {
            "up": "1000 mbps",
            "down": "1000 mbps"
        },
        "log": {
            "level": "warn",
            "output": f"{base_dir}/logs/hysteria.log",
            "timestamp": True
        }
    }
    
    config_path = f"{base_dir}/config/config.json"
    with open(config_path, "w") as f:
        json.dump(hysteria_config, f, indent=2)
    print(f"Created configuration: {config_path}")
    
    # 7. Configure port hopping (iptables)
    if port_range:
        # Use user-specified port range
        port_start, port_end = parse_port_range(port_range)
        if port_start is None or port_end is None:
            print("Port range parsing failed, using default range")
            port_start = max(1024, port - 25)
            port_end = min(65535, port + 25)
            if port < 1049:
                port_start = 1024
                port_end = 1074
    else:
        # Use default port range
        port_start = max(1024, port - 25)
        port_end = min(65535, port + 25)
        if port < 1049:
            port_start = 1024
            port_end = 1074
    
    success = setup_port_hopping_iptables(port_start, port_end, port)
    if success:
        print(f"Port hopping: {port_start}-{port_end} -> {port}")
    
    # 8. BBR optimization (if enabled)
    if enable_bbr:
        bbr_success = enable_bbr_optimization()
        if bbr_success:
            print("BBR congestion control optimization enabled")
        else:
            print("BBR optimization failed, but does not affect main functionality")
    
    # 9. Create and start Hysteria2 service
    start_script = create_service_script(base_dir, binary_path, config_path, port)
    service_started = start_service(start_script, port, base_dir)
    if service_started:
        print("Hysteria2 service started successfully")
    
    # 10. Configure nginx web masquerade
    nginx_success = setup_nginx_web_masquerade(base_dir, server_address, web_dir, cert_path, key_path, port)
    if nginx_success:
        print("nginx web masquerade configuration successful")
    
    # 11. Generate client configuration
    insecure = "1" if not enable_real_cert else "0"
    params = [
        f"insecure={insecure}",
        f"sni={server_address}",
        f"obfs=salamander",
        f"obfs-password={urllib.parse.quote(obfs_password)}"
    ]
    
    # Generate standard single port configuration link (best compatibility)
    config_link = f"hysteria2://{urllib.parse.quote(password)}@{server_address}:{port}?{'&'.join(params)}"
    
    # If port hopping enabled, generate additional JSON configuration
    if port_range:
        port_hopping_config = {
            "server": server_address,
            "auth": password,
            "obfs": {
                "type": "salamander",
                "salamander": {
                    "password": obfs_password
                }
            },
            "tls": {
                "sni": server_address,
                "insecure": insecure == "1"
            },
            "transport": {
                "type": "udp",
                "udp": {
                    "hopPorts": f"{port_start}-{port_end}"
                }
            }
        }
    
    # 12. Output deployment results
    if port_range:
        # Prepare download links
        download_links = {
            "v2rayN multi-port subscription (recommended)": f"http://{server_address}:8080/v2rayn-subscription.txt",
            "Multi-port configuration plain text view": f"http://{server_address}:8080/multi-port-links.txt",
            "Clash multi-port configuration": f"http://{server_address}:8080/clash.yaml", 
            "Official client configuration": f"http://{server_address}:8080/hysteria-official.yaml",
            "JSON configuration (full functionality)": f"http://{server_address}:8080/hysteria2.json"
        }
        
        # Generate multi-port configurations
        print("\nGenerating multi-port configuration files...")
        
        # Calculate port range and select ports
        import random
        port_range_list = list(range(port_start, port_end + 1))
        num_configs = 100
        
        if len(port_range_list) > num_configs:
            selected_ports = random.sample(port_range_list, num_configs)
        else:
            selected_ports = port_range_list
        
        selected_ports.sort()  # Sort for viewing
        num_ports = len(selected_ports)
        
        # Generate v2rayN subscription file
        subscription_file, subscription_plain_file, _ = generate_multi_port_subscription(
            server_address, password, obfs_password, port_start, port_end, base_dir, num_configs=100
        )
        print(f"Generated {num_ports} port configuration nodes")
        
        # Use unified output function
        show_final_summary(
            server_address=server_address,
            port=port,
            port_range=f"{port_start}-{port_end}",
            password=password,
            obfs_password=obfs_password,
            config_link=config_link,
            enable_port_hopping=True,
            download_links=download_links,
            num_ports=num_ports
        )
        
        # Save JSON configuration file
        config_file = f"{base_dir}/client-config.json"
        with open(config_file, 'w') as f:
            json.dump(port_hopping_config, f, indent=2)
        print(f"Port hopping JSON configuration saved to: {config_file}")
        
        # Generate v2rayN compatible configuration (single port, since v2rayN does not support port hopping)
        v2rayn_config = f"""# Hysteria2 v2rayN compatible configuration - single port version
# Note: v2rayN does not support port hopping, can only use server's main listen port
# Usage: Import this configuration into v2rayN client

server: {server_address}:{port}
auth: {password}

obfs:
  type: salamander
  salamander:
    password: {obfs_password}

tls:
  sni: {server_address}
  insecure: true

bandwidth:
  up: 50 mbps
  down: 200 mbps

socks5:
  listen: 127.0.0.1:1080

http:
  listen: 127.0.0.1:8080
"""
        
        # Generate Hysteria2 official client YAML configuration (correct port hopping format)
        hysteria_official_config = f"""# Hysteria2 official client configuration - port hopping version
# Supports port hopping for better anti-blocking
# Usage: Save as config.yaml, then run hysteria client -c config.yaml

server: {server_address}:{port}
auth: {password}

transport:
  type: udp
  udp:
    hopInterval: 30s

obfs:
  type: salamander
  salamander:
    password: {obfs_password}

tls:
  sni: {server_address}
  insecure: true

bandwidth:
  up: 50 mbps
  down: 200 mbps

socks5:
  listen: 127.0.0.1:1080

http:
  listen: 127.0.0.1:8080

# Port hopping explanation:
# Hysteria2 port hopping has two implementations:
# 1. Server-side iptables DNAT: Forward {port_start}-{port_end} traffic to {port}
# 2. Client multi-port connection: Client randomly selects port in {port_start}-{port_end} range
# 
# Current configuration uses method 1, keeping client config simple
# For method 2, change server to: {server_address}:{port_start}-{port_end}
"""
        
        # Generate Clash multi-port configuration (same multi-node as v2rayN)
        clash_proxies = []
        clash_proxy_names = []
        
        # Generate multiple port Clash node configurations
        for i, port_num in enumerate(selected_ports, 1):
            node_name = f"Hysteria2-Port{port_num}-Node{i:02d}"
            clash_proxy_names.append(node_name)
            clash_proxies.append(f"""  - name: "{node_name}"
    type: hysteria2
    server: {server_address}
    port: {port_num}
    password: "{password}"
    obfs: salamander
    obfs-password: "{obfs_password}"
    sni: {server_address}
    skip-cert-verify: true
    fast-open: true""")
        
        clash_config = f"""# Clash Meta Hysteria2 multi-port configuration
# Contains {len(selected_ports)} nodes with different ports, supports manual switching
# Usage: Import to Clash Meta client, select different ports in node list

mixed-port: 7890
allow-lan: false
bind-address: '*'
mode: rule
log-level: info
external-controller: '127.0.0.1:9090'

proxies:
{chr(10).join(clash_proxies)}
    
proxy-groups:
  - name: "Node Selection"
    type: select
    proxies:
{chr(10).join([f'      - "{name}"' for name in clash_proxy_names])}
      - DIRECT
      
  - name: "Foreign Websites"
    type: select
    proxies:
      - "Node Selection"
      - DIRECT
      
rules:
  - DOMAIN-SUFFIX,google.com,Foreign Websites
  - DOMAIN-SUFFIX,youtube.com,Foreign Websites
  - DOMAIN-SUFFIX,github.com,Foreign Websites
  - DOMAIN-SUFFIX,openai.com,Foreign Websites
  - DOMAIN-SUFFIX,chatgpt.com,Foreign Websites
  - GEOIP,CN,DIRECT
  - MATCH,Node Selection
"""
        
        # Generate real client port hopping configuration (optional)
        hysteria_client_hopping_config = f"""# Hysteria2 client port hopping configuration
# This config lets client implement real port hopping (random port selection)
# Usage: Save as hopping.yaml, run hysteria client -c hopping.yaml

server: {server_address}:{port_start}-{port_end}
auth: {password}

transport:
  type: udp
  udp:
    hopInterval: 30s

obfs:
  type: salamander
  salamander:
    password: {obfs_password}

tls:
  sni: {server_address}
  insecure: true

bandwidth:
  up: 50 mbps
  down: 200 mbps

socks5:
  listen: 127.0.0.1:1080

http:
  listen: 127.0.0.1:8080

# This config requires server to open {port_start}-{port_end} port range
# Each port needs independent Hysteria2 service instance or load balancing config
"""

        # Save YAML configuration files
        v2rayn_file = f"{base_dir}/v2rayn-config.yaml"
        clash_file = f"{base_dir}/clash-config.yaml"
        hysteria_official_file = f"{base_dir}/hysteria-official-config.yaml"
        hysteria_client_hopping_file = f"{base_dir}/hysteria-client-hopping.yaml"
        
        with open(v2rayn_file, 'w', encoding='utf-8') as f:
            f.write(v2rayn_config)
        with open(clash_file, 'w', encoding='utf-8') as f:
            f.write(clash_config)
        with open(hysteria_official_file, 'w', encoding='utf-8') as f:
            f.write(hysteria_official_config)
        with open(hysteria_client_hopping_file, 'w', encoding='utf-8') as f:
            f.write(hysteria_client_hopping_config)
            
        print(f"v2rayN configuration saved to: {v2rayn_file}")
        print(f"Clash configuration saved to: {clash_file}")
        print(f"Official client configuration saved to: {hysteria_official_file}")
        print(f"Client port hopping configuration saved to: {hysteria_client_hopping_file}")
        
        # Copy configuration files to nginx web directory for download
        setup_config_download_service(server_address, v2rayn_file, clash_file, hysteria_official_file, hysteria_client_hopping_file, subscription_file, subscription_plain_file, config_file)
        
    else:
        # Use unified output function
        show_final_summary(
            server_address=server_address,
            port=port,
            port_range=None,
            password=password,
            obfs_password=obfs_password,
            config_link=config_link,
            enable_port_hopping=False,
            download_links=None
        )
    
    return {
        "server": server_address,
        "port": port,
        "port_range": f"{port_start}-{port_end}",
        "password": password,
        "obfs_password": obfs_password,
        "config_link": config_link,
        "nginx_success": nginx_success
    }

def setup_nginx_web_masquerade(base_dir, server_address, web_dir, cert_path, key_path, port):
    """
    Configure nginx web masquerade simplified version
    """
    try:
        print("Configuring nginx web masquerade...")
        
        # 1. Check if nginx is installed
        try:
            subprocess.run(['which', 'nginx'], check=True, capture_output=True)
        except:
            print("Installing nginx...")
            if shutil.which('apt'):
                subprocess.run(['sudo', 'apt', 'update'], check=True)
                subprocess.run(['sudo', 'apt', 'install', '-y', 'nginx'], check=True)
            elif shutil.which('yum'):
                subprocess.run(['sudo', 'yum', 'install', '-y', 'epel-release'], check=True)
                subprocess.run(['sudo', 'yum', 'install', '-y', 'nginx'], check=True)
            else:
                print("Cannot install nginx")
                return False
        
        # 2. Find nginx web directory
        nginx_web_dirs = ["/var/www/html", "/usr/share/nginx/html", "/var/www"]
        nginx_web_dir = None
        for dir_path in nginx_web_dirs:
            if os.path.exists(dir_path):
                nginx_web_dir = dir_path
                break
        
        if not nginx_web_dir:
            nginx_web_dir = "/var/www/html"
            subprocess.run(['sudo', 'mkdir', '-p', nginx_web_dir], check=True)
        
        # 3. Copy web files
        print("Deploying web masquerade files...")
        create_web_files_in_directory(nginx_web_dir)
        set_nginx_permissions(nginx_web_dir)
        
        # 4. Configure nginx SSL
        ssl_conf = f"""server {{
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name _;
    
    ssl_certificate {os.path.abspath(cert_path)};
    ssl_certificate_key {os.path.abspath(key_path)};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384;
    
    root {nginx_web_dir};
    index index.html;
    
    location / {{
        try_files $uri $uri/ /index.html;
    }}
    
    server_tokens off;
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;
}}

server {{
    listen 80;
    listen [::]:80;
    server_name _;
    return 301 https://$server_name$request_uri;
}}"""
        
        # 5. Write nginx configuration
        ssl_conf_file = "/etc/nginx/conf.d/hysteria2-ssl.conf"
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.conf') as tmp:
            tmp.write(ssl_conf)
            tmp.flush()
            subprocess.run(['sudo', 'cp', tmp.name, ssl_conf_file], check=True)
            os.unlink(tmp.name)
        
        # 6. Test and restart nginx
        test_result = subprocess.run(['sudo', 'nginx', '-t'], capture_output=True, text=True)
        if test_result.returncode != 0:
            print(f"nginx configuration error: {test_result.stderr}")
            return False
        
        subprocess.run(['sudo', 'systemctl', 'restart', 'nginx'], check=True)
        subprocess.run(['sudo', 'systemctl', 'enable', 'nginx'], check=True)
        
        print("nginx web masquerade configuration complete")
        return True
        
    except Exception as e:
        print(f"nginx configuration failed: {e}")
        return False

def enable_bbr_optimization():
    """Enable BBR congestion control algorithm to optimize network performance"""
    try:
        print("Enabling BBR congestion control algorithm...")
        
        # Check current congestion control algorithm
        try:
            with open('/proc/sys/net/ipv4/tcp_congestion_control', 'r') as f:
                current_cc = f.read().strip()
            print(f"Current congestion control algorithm: {current_cc}")
            
            if current_cc == 'bbr':
                print("BBR already enabled")
                return True
        except:
            pass
        
        # Check kernel version
        try:
            result = subprocess.run(['uname', '-r'], capture_output=True, text=True)
            kernel_version = result.stdout.strip()
            print(f"Kernel version: {kernel_version}")
            
            # BBR requires kernel version >= 4.9
            version_parts = kernel_version.split('.')
            major = int(version_parts[0])
            minor = int(version_parts[1].split('-')[0])
            
            if major < 4 or (major == 4 and minor < 9):
                print(f"BBR requires kernel version >= 4.9, current version: {kernel_version}")
                print("Suggest upgrading kernel or using other optimization schemes")
                return False
        except:
            print("Cannot detect kernel version")
        
        # Check if BBR module is available
        try:
            result = subprocess.run(['modprobe', 'tcp_bbr'], check=False, capture_output=True)
            if result.returncode == 0:
                print("BBR module loaded successfully")
            else:
                print("BBR module loading failed, may not support")
        except:
            pass
        
        # Configure BBR
        bbr_config = """# BBR congestion control optimization configuration
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr

# Network performance optimization
net.core.rmem_max = 134217728
net.core.wmem_max = 134217728
net.core.netdev_max_backlog = 5000
net.ipv4.tcp_rmem = 4096 87380 134217728
net.ipv4.tcp_wmem = 4096 65536 134217728
net.ipv4.tcp_mtu_probing = 1
net.ipv4.tcp_congestion_control = bbr

# UDP optimization (Hysteria2 uses UDP)
net.core.rmem_default = 262144
net.core.rmem_max = 16777216
net.core.wmem_default = 262144
net.core.wmem_max = 16777216
net.core.netdev_max_backlog = 5000
"""
        
        # Write sysctl configuration
        sysctl_file = "/etc/sysctl.d/99-hysteria2-bbr.conf"
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.conf') as tmp:
                tmp.write(bbr_config)
                tmp.flush()
                subprocess.run(['sudo', 'cp', tmp.name, sysctl_file], check=True)
                os.unlink(tmp.name)
            
            print(f"BBR configuration written to: {sysctl_file}")
        except Exception as e:
            print(f"Writing BBR configuration failed: {e}")
            return False
        
        # Apply configuration
        try:
            subprocess.run(['sudo', 'sysctl', '-p', sysctl_file], check=True)
            print("BBR configuration applied")
        except Exception as e:
            print(f"Applying BBR configuration failed: {e}")
        
        # Enable BBR immediately
        try:
            subprocess.run(['sudo', 'sysctl', '-w', 'net.core.default_qdisc=fq'], check=True)
            subprocess.run(['sudo', 'sysctl', '-w', 'net.ipv4.tcp_congestion_control=bbr'], check=True)
            print("BBR enabled immediately")
        except Exception as e:
            print(f"Enabling BBR immediately failed: {e}")
        
        # Verify if BBR is enabled
        try:
            with open('/proc/sys/net/ipv4/tcp_congestion_control', 'r') as f:
                current_cc = f.read().strip()
            
            if current_cc == 'bbr':
                print("BBR congestion control algorithm enabled successfully!")
                
                # Show available congestion control algorithms
                try:
                    with open('/proc/sys/net/ipv4/tcp_available_congestion_control', 'r') as f:
                        available_cc = f.read().strip()
                    print(f"Available algorithms: {available_cc}")
                except:
                    pass
                
                return True
            else:
                print(f"BBR enable failed, current algorithm: {current_cc}")
                return False
                
        except Exception as e:
            print(f"Verifying BBR status failed: {e}")
            return False
            
    except Exception as e:
        print(f"BBR optimization failed: {e}")
        return False

def setup_config_download_service(server_address, v2rayn_file, clash_file, hysteria_official_file, hysteria_client_hopping_file, subscription_file, subscription_plain_file, json_file):
    """Set up configuration file download service - fully automated"""
    try:
        print("Setting up configuration file download service...")
        
        # Get base_dir
        base_dir = os.path.expanduser("~/.hysteria2")
        
        # Create configuration directory
        config_dir = f"{base_dir}/configs"
        subprocess.run(['mkdir', '-p', config_dir], check=True)
        
        # Copy configuration files
        subprocess.run(['cp', v2rayn_file, f'{config_dir}/v2rayn.yaml'], check=True)
        subprocess.run(['cp', clash_file, f'{config_dir}/clash.yaml'], check=True)
        subprocess.run(['cp', hysteria_official_file, f'{config_dir}/hysteria-official.yaml'], check=True)
        subprocess.run(['cp', hysteria_client_hopping_file, f'{config_dir}/hysteria-client-hopping.yaml'], check=True)
        subprocess.run(['cp', subscription_file, f'{config_dir}/v2rayn-subscription.txt'], check=True)
        subprocess.run(['cp', subscription_plain_file, f'{config_dir}/multi-port-links.txt'], check=True)
        subprocess.run(['cp', json_file, f'{config_dir}/hysteria2.json'], check=True)
        
        # Start Python HTTP server (without systemd)
        print("Starting Python HTTP server...")
        
        # Create HTTP server script
        server_script = f'''#!/usr/bin/env python3
import os
import http.server
import socketserver
from urllib.parse import urlparse

class ConfigHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory="{config_dir}", **kwargs)
    
    def end_headers(self):
        if self.path.endswith(('.yaml', '.yml', '.json')):
            filename = os.path.basename(self.path)
            self.send_header('Content-Disposition', f'attachment; filename="{{filename}}"')
            self.send_header('Content-Type', 'application/octet-stream')
        super().end_headers()
    
    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    PORT = 8080
    try:
        with socketserver.TCPServer(("", PORT), ConfigHandler) as httpd:
            print(f"HTTP server started, port: {{PORT}}")
            httpd.serve_forever()
    except Exception as e:
        print(f"Server start failed: {{e}}")
        exit(1)
'''
        
        # Save and start server
        server_file = f"{base_dir}/config_server.py"
        with open(server_file, 'w', encoding='utf-8') as f:
            f.write(server_script)
        subprocess.run(['chmod', '+x', server_file], check=True)
        
        # Open firewall port (8080 for config download)
        subprocess.run(['sudo', 'iptables', '-A', 'INPUT', '-p', 'tcp', '--dport', '8080', '-j', 'ACCEPT'], check=False)
        
        # Start HTTP server in background
        subprocess.Popen(['python3', server_file], cwd=base_dir)
        
        # Wait for service start
        time.sleep(3)
        
        # Verify if service started
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(('127.0.0.1', 8080))
            sock.close()
            if result == 0:
                print("Python HTTP server started successfully")
                return True
            else:
                print("HTTP server start failed")
                return False
        except Exception as e:
            print(f"Verifying HTTP server failed: {e}")
            return False
        
    except Exception as e:
        print(f"Setting config download service failed: {e}")
        return False

def parse_port_range(port_range_str):
    """Parse port range string"""
    try:
        if not port_range_str:
            return None, None
        
        if '-' not in port_range_str:
            print(f"Port range format error: {port_range_str}")
            print("Correct format: start-port-end, e.g.: 28888-29999")
            return None, None
        
        start_str, end_str = port_range_str.split('-', 1)
        start_port = int(start_str.strip())
        end_port = int(end_str.strip())
        
        # Verify port range
        if start_port < 1024 or end_port > 65535:
            print(f"Port range out of valid range (1024-65535): {start_port}-{end_port}")
            return None, None
        
        if start_port >= end_port:
            print(f"Start port must be less than end port: {start_port}-{end_port}")
            return None, None
        
        if end_port - start_port > 10000:
            print(f"Port range too large ({end_port - start_port} ports), suggest controlling within 10000")
            user_input = input("Continue? (y/n): ").lower()
            if user_input != 'y':
                return None, None
        
        print(f"Port range parsed successfully: {start_port}-{end_port} (total {end_port - start_port + 1} ports)")
        return start_port, end_port
        
    except ValueError:
        print(f"Port range format error: {port_range_str}")
        print("Correct format: start-port-end, e.g.: 28888-29999")
        return None, None
    except Exception as e:
        print(f"Parsing port range failed: {e}")
        return None, None

def show_final_summary(server_address, port, port_range, password, obfs_password, config_link, enable_port_hopping=False, download_links=None, num_ports=None):
    import urllib.parse
    """Display final complete summary information - including download links, client links and author info"""
    
    print("\n" + "="*80)
    print("┌──────────────────────────────────────────────────────────────────────────────┐")
    print("│                            Hysteria2 Deployment Complete!                    │")
    print("└──────────────────────────────────────────────────────────────────────────────┘")
    
    # Server information
    print("\nServer Information:")
    print(f"   - Server address: {server_address}")
    print(f"   - Listen port: {port} (UDP)")
    if enable_port_hopping and port_range:
        print(f"   - Client port range: {port_range}")
    print(f"   - Connection password: {password}")
    if obfs_password:
        print(f"   - Obfuscation password: {obfs_password}")
    
    # One-click import link
    print(f"\nOne-click Import Link:")
    print(f"   {config_link}")
    
    # Configuration file download links (if any)
    if download_links:
        print(f"\nConfiguration File Downloads:")
        for name, url in download_links.items():
            print(f"   - {name}: {url}")
        
        print(f"\nClient Configuration Guide:")
        print("   v2rayN users:")
        print("     - Multi-port subscription: Download v2rayN multi-port subscription -> Add subscription link")
        print("     - Manual import: Download multi-port configuration plain text -> Copy links to v2rayN")
        print("     - Single port: Download v2rayN single port configuration")
        print("   Clash Meta users:")
        print("     - Multi-port configuration: Download Clash multi-port configuration, contains multiple port nodes")
        print("   Official client users:")
        print("     - Use official client configuration")
        print(f"   Multi-port explanation: Contains {num_ports} different port nodes, manual switch for anti-blocking effect")
    
    # Protection features
    print(f"\nProtection Features:")
    if enable_port_hopping:
        print(f"   Port hopping: {port_range} -> {port} (server-side DNAT implementation)")
    if obfs_password:
        print(f"   Salamander obfuscation: {obfs_password}")
    print("   HTTP/3 masquerade: Simulate normal HTTP/3 traffic")
    print("   nginx web masquerade: TCP port shows normal website")
    print("   UDP protocol: Based on QUIC/HTTP3, strong anti-blocking capability")
    
    # Usage reminder
    print(f"\nUsage Reminder:")
    print("   - Hysteria2 uses UDP protocol, ensure firewall opens UDP ports")
    if enable_port_hopping and port_range:
        print(f"   - Port hopping mode: Need to open UDP port range {port_range}")
    else:
        print(f"   - Need to open UDP port {port}")
    print(f"   - nginx web masquerade needs to open TCP port {port}")
    
    # 443 port address and 10 random v2ray addresses
    print(f"\n443 Port Connection Address:")
    hysteria_443_url = f"hysteria2://{urllib.parse.quote(password)}@{server_address}:443?insecure=1&sni={server_address}&obfs=salamander&obfs-password={urllib.parse.quote(obfs_password)}#Hysteria2-443"
    print(f"   {hysteria_443_url}")
    
    print(f"\n10 Random v2ray Addresses (can copy directly):")
    random_ports = []
    random_urls = []
    if port_range and '-' in str(port_range):
        # Select 10 from generated multi-port config
        import random
        port_start, port_end = port_range.split('-')
        port_list = list(range(int(port_start), int(port_end) + 1))
        random_ports = random.sample(port_list, min(10, len(port_list)))
        random_ports.sort()
        
        for i, random_port in enumerate(random_ports, 1):
            random_url = f"hysteria2://{urllib.parse.quote(password)}@{server_address}:{random_port}?insecure=1&sni={server_address}&obfs=salamander&obfs-password={urllib.parse.quote(obfs_password)}#V2Ray-{random_port}-{i:02d}"
            random_urls.append(random_url)
            print(f"   {random_url}")
        
        # Generate Base64 subscription format
        subscription_content = "\n".join(random_urls)
        subscription_base64 = base64.b64encode(subscription_content.encode('utf-8')).decode('utf-8')
        print(f"\nBase64 Subscription for 10 Random Addresses:")
        print(f"   {subscription_base64}")
    else:
        print("   (Need to enable multi-port configuration to generate random addresses)")
    
    # Author information
    print("\n" + "="*80)
    print("┌──────────────────────────────────────────────────────────────────────────────┐")
    print("│                                  Author Information                          │")
    print("├──────────────────────────────────────────────────────────────────────────────┤")
    print("│ Author: KangKang                                                             │")
    print("│ Github: https://github.com/zhumengkang/                                      │")
    print("│ YouTube: https://www.youtube.com/@康康的V2Ray与Clash                           │")
    print("│ Telegram: https://t.me/+WibQp7Mww1k5MmZl                                     │")
    print("└──────────────────────────────────────────────────────────────────────────────┘")
    print("="*80)
    
    # Save configuration info to global file
    save_global_config(server_address, port, port_range, password, obfs_password, hysteria_443_url, random_ports)
    
    # Prominent success message
    print("\n" + "="*80)
    print("║" + " "*78 + "║")
    print("║" + "Deployment complete! After successful connection, enjoy high-speed stable network experience!".center(76) + "║")
    print("║" + " "*78 + "║")
    print("║" + "Created global management command, enter 'kk' to access management menu".center(74) + "║")
    print("║" + " "*78 + "║")
    print("║" + "Menu functions: 1-View nodes 2-View config 3-Service status 4-Restart service 5-View logs 6-Delete service".center(66) + "║")
    print("║" + " "*78 + "║")
    print("║" + "If problems, contact author for technical support".center(70) + "║")
    print("║" + " "*78 + "║")
    print("="*80 + "\n")

def save_global_config(server_address, port, port_range, password, obfs_password, hysteria_443_url, random_ports):
    """Save configuration info to global file, and create kk command"""
    try:
        home = get_user_home()
        config_dir = f"{home}/.hysteria2"
        
        # Save configuration info
        global_config = {
            "server_address": server_address,
            "port": port,
            "port_range": port_range,
            "password": password,
            "obfs_password": obfs_password,
            "hysteria_443_url": hysteria_443_url,
            "random_ports": random_ports,
            "timestamp": time.time()
        }
        
        config_file = f"{config_dir}/global_config.json"
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(global_config, f, indent=2, ensure_ascii=False)
        
        # Create kk command script
        kk_script_content = f'''#!/bin/bash
# Hysteria2 management tool
# Author: KangKang

CONFIG_FILE="{config_file}"
BASE_DIR="$HOME/.hysteria2"

# Check if config file exists
if [ ! -f "$CONFIG_FILE" ]; then
    echo "Config file does not exist: $CONFIG_FILE"
    echo "Please run Hysteria2 deployment script first"
    exit 1
fi

# Load config function
load_config() {{
    CONFIG=$(cat "$CONFIG_FILE")
    SERVER_ADDRESS=$(echo "$CONFIG" | python3 -c "import sys, json; print(json.load(sys.stdin)['server_address'])" 2>/dev/null || echo "N/A")
    PORT=$(echo "$CONFIG" | python3 -c "import sys, json; print(json.load(sys.stdin)['port'])" 2>/dev/null || echo "N/A")
    PORT_RANGE=$(echo "$CONFIG" | python3 -c "import sys, json; print(json.load(sys.stdin).get('port_range', 'N/A'))" 2>/dev/null || echo "N/A")
    PASSWORD=$(echo "$CONFIG" | python3 -c "import sys, json; print(json.load(sys.stdin)['password'])" 2>/dev/null || echo "N/A")
    OBFS_PASSWORD=$(echo "$CONFIG" | python3 -c "import sys, json; print(json.load(sys.stdin)['obfs_password'])" 2>/dev/null || echo "N/A")
    HYSTERIA_443_URL=$(echo "$CONFIG" | python3 -c "import sys, json; print(json.load(sys.stdin)['hysteria_443_url'])" 2>/dev/null || echo "N/A")
    RANDOM_PORTS=$(echo "$CONFIG" | python3 -c "import sys, json; print(' '.join(map(str, json.load(sys.stdin)['random_ports'])))" 2>/dev/null || echo "")
}}

# Show node info
show_node_info() {{
    load_config
    echo "╔══════════════════════════════════════════════════════════════════════════════╗"
    echo "║                           Hysteria2 Node Information                          ║"
    echo "╠══════════════════════════════════════════════════════════════════════════════╣"
    echo "║ Server: $SERVER_ADDRESS"
    echo "║ Port: $PORT (UDP)"
    echo "║ Port Range: $PORT_RANGE"
    echo "║ Password: $PASSWORD"
    echo "║ Obfuscation Password: $OBFS_PASSWORD"
    echo "╚══════════════════════════════════════════════════════════════════════════════╝"
    
    echo ""
    echo "443 Port Connection Address:"
    echo "$HYSTERIA_443_URL"
    
    echo ""
    echo "10 Random v2ray Addresses (can copy directly):"
    if [ -n "$RANDOM_PORTS" ]; then
        URLS=""
        for port in $RANDOM_PORTS; do
            url="hysteria2://$(python3 -c "import urllib.parse; print(urllib.parse.quote('$PASSWORD'))")@$SERVER_ADDRESS:$port?insecure=1&sni=$SERVER_ADDRESS&obfs=salamander&obfs-password=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$OBFS_PASSWORD'))")#V2Ray-$port"
            echo "$url"
            if [ -z "$URLS" ]; then
                URLS="$url"
            else
                URLS="$URLS\\n$url"
            fi
        done
        
        echo ""
        echo "Base64 Subscription Format (can add directly to v2rayN):"
        echo -e "$URLS" | python3 -c "import sys, base64; print(base64.b64encode(sys.stdin.read().encode()).decode())"
    else
        echo "(Need to enable multi-port configuration to generate random addresses)"
    fi
}}

# Show config info
show_config_info() {{
    load_config
    echo "╔══════════════════════════════════════════════════════════════════════════════╗"
    echo "║                           Configuration File Information                     ║"
    echo "╚══════════════════════════════════════════════════════════════════════════════╝"
    
    echo ""
    echo "Configuration File Download Addresses:"
    echo "• v2rayN multi-port subscription: http://$SERVER_ADDRESS:8080/v2rayn-subscription.txt"
    echo "• Multi-port configuration plain text: http://$SERVER_ADDRESS:8080/multi-port-links.txt"
    echo "• Clash multi-port configuration: http://$SERVER_ADDRESS:8080/clash.yaml"
    echo "• Official client configuration: http://$SERVER_ADDRESS:8080/hysteria2.json"
    
    echo ""
    echo "Local Configuration Files:"
    if [ -f "$BASE_DIR/config/config.json" ]; then
        echo "Hysteria2 config: $BASE_DIR/config/config.json"
    else
        echo "Hysteria2 config: File does not exist"
    fi
    
    if [ -f "$BASE_DIR/cert/cert.pem" ]; then
        echo "SSL certificate: $BASE_DIR/cert/cert.pem"
    else
        echo "SSL certificate: File does not exist"
    fi
    
    if [ -f "$BASE_DIR/logs/hysteria.log" ]; then
        echo "Log file: $BASE_DIR/logs/hysteria.log"
    else
        echo "Log file: File does not exist"
    fi
}}

# Show service status
show_service_status() {{
    echo "╔══════════════════════════════════════════════════════════════════════════════╗"
    echo "║                           Service Status                                     ║"
    echo "╚══════════════════════════════════════════════════════════════════════════════╝"
    
    # Check Hysteria2 process
    if pgrep -f "hysteria" > /dev/null; then
        echo "Hysteria2 service: Running"
        echo "   Process ID: $(pgrep -f hysteria)"
    else
        echo "Hysteria2 service: Not running"
    fi
    
    # Check nginx process
    if pgrep -f "nginx" > /dev/null; then
        echo "nginx service: Running"
    else
        echo "nginx service: Not running"
    fi
    
    # Check port listening
    load_config
    if [ "$PORT" != "N/A" ]; then
        if netstat -ulnp 2>/dev/null | grep ":$PORT " > /dev/null; then
            echo "UDP port $PORT: Listening"
        else
            echo "UDP port $PORT: Not listening"
        fi
    fi
    
    if netstat -tlnp 2>/dev/null | grep ":443 " > /dev/null; then
        echo "TCP port 443: Listening (nginx)"
    else
        echo "TCP port 443: Not listening"
    fi
    
    if netstat -tlnp 2>/dev/null | grep ":8080 " > /dev/null; then
        echo "TCP port 8080: Listening (config download)"
    else
        echo "TCP port 8080: Not listening"
    fi
}}

# Restart service
restart_service() {{
    echo "Restarting Hysteria2 service..."
    
    # Stop service
    if [ -f "$BASE_DIR/stop.sh" ]; then
        echo "Stopping current service..."
        bash "$BASE_DIR/stop.sh"
        sleep 2
    fi
    
    # Start service
    if [ -f "$BASE_DIR/start.sh" ]; then
        echo "Starting service..."
        bash "$BASE_DIR/start.sh"
        sleep 3
        
        # Check service status
        if pgrep -f "hysteria" > /dev/null; then
            echo "Service restart successful"
        else
            echo "Service restart failed"
        fi
    else
        echo "Start script does not exist: $BASE_DIR/start.sh"
    fi
}}

# Show logs
show_logs() {{
    echo "╔══════════════════════════════════════════════════════════════════════════════╗"
    echo "║                           View Logs                                          ║"
    echo "╚══════════════════════════════════════════════════════════════════════════════╝"
    
    if [ -f "$BASE_DIR/logs/hysteria.log" ]; then
        echo "Showing latest 50 log lines:"
        echo "----------------------------------------"
        tail -n 50 "$BASE_DIR/logs/hysteria.log"
        echo "----------------------------------------"
        echo "Real-time log view: tail -f $BASE_DIR/logs/hysteria.log"
    else
        echo "Log file does not exist: $BASE_DIR/logs/hysteria.log"
    fi
}}

# Delete service
delete_service() {{
    echo "Confirm deletion of Hysteria2 service? This will delete all configurations and files!"
    echo "Enter 'yes' to confirm deletion, any other key to cancel:"
    read -r confirm
    
    if [ "$confirm" = "yes" ]; then
        echo "Deleting Hysteria2 service..."
        
        # Stop service
        if [ -f "$BASE_DIR/stop.sh" ]; then
            bash "$BASE_DIR/stop.sh"
        fi
        
        # Delete files
        if [ -d "$BASE_DIR" ]; then
            rm -rf "$BASE_DIR"
            echo "Deleted config directory: $BASE_DIR"
        fi
        
        # Delete config file
        if [ -f "$CONFIG_FILE" ]; then
            rm -f "$CONFIG_FILE"
            echo "Deleted global config: $CONFIG_FILE"
        fi
        
        echo "Hysteria2 service completely deleted"
    else
        echo "Deletion operation canceled"
    fi
}}

# Main menu
show_menu() {{
    clear
    echo "╔══════════════════════════════════════════════════════════════════════════════╗"
    echo "║                         Hysteria2 Management Tool                            ║"
    echo "╠══════════════════════════════════════════════════════════════════════════════╣"
    echo "║                              Author: KangKang                                ║"
    echo "╚══════════════════════════════════════════════════════════════════════════════╝"
    echo ""
    echo "Select operation:"
    echo "1  View node information"
    echo "2  View configuration files"
    echo "3  View service status"
    echo "4  Restart service"
    echo "5  View logs"
    echo "6  Delete service"
    echo "0  Exit"
    echo ""
    echo "GitHub: https://github.com/zhumengkang/"
    echo "YouTube: https://www.youtube.com/@康康的V2Ray与Clash"
    echo "Telegram: https://t.me/+WibQp7Mww1k5MmZl"
    echo ""
}}

# Main program
while true; do
    show_menu
    echo -n "Enter option (0-6): "
    read -r choice
    echo ""
    
    case $choice in
        1)
            show_node_info
            echo ""
            echo "Press any key to return to main menu..."
            read -r
            ;;
        2)
            show_config_info
            echo ""
            echo "Press any key to return to main menu..."
            read -r
            ;;
        3)
            show_service_status
            echo ""
            echo "Press any key to return to main menu..."
            read -r
            ;;
        4)
            restart_service
            echo ""
            echo "Press any key to return to main menu..."
            read -r
            ;;
        5)
            show_logs
            echo ""
            echo "Press any key to return to main menu..."
            read -r
            ;;
        6)
            delete_service
            echo ""
            echo "Press any key to return to main menu..."
            read -r
            ;;
        0)
            echo "Thank you for using Hysteria2 management tool!"
            exit 0
            ;;
        *)
            echo "Invalid option, please enter 0-6"
            echo ""
            echo "Press any key to continue..."
            read -r
            ;;
    esac
done
'''
        
        # Create kk command file
        kk_script_path = "/usr/local/bin/kk"
        try:
            with open(kk_script_path, 'w', encoding='utf-8') as f:
                f.write(kk_script_content)
            os.chmod(kk_script_path, 0o755)
            print(f"Created global command: {kk_script_path}")
        except PermissionError:
            # If no permission to write /usr/local/bin, try user directory
            user_bin = f"{home}/bin"
            os.makedirs(user_bin, exist_ok=True)
            kk_script_path = f"{user_bin}/kk"
            with open(kk_script_path, 'w', encoding='utf-8') as f:
                f.write(kk_script_content)
            os.chmod(kk_script_path, 0o755)
            print(f"Created user command: {kk_script_path}")
            print(f"Ensure {user_bin} is in PATH environment variable")
        
        return True
        
    except Exception as e:
        print(f"Saving global config failed: {e}")
        return False

def generate_multi_port_subscription(server_address, password, obfs_password, port_start, port_end, base_dir, num_configs=100):
    """
    Generate multi-port v2rayN subscription file
    Generate multiple hysteria2 configurations for ports in port hopping range
    """
    # Calculate port range
    port_range = list(range(port_start, port_end + 1))
    
    # If port count exceeds configs to generate, random select
    if len(port_range) > num_configs:
        selected_ports = random.sample(port_range, num_configs)
    else:
        selected_ports = port_range
    
    selected_ports.sort()  # Sort for viewing
    
    # Generate multiple hysteria2 links
    hysteria2_links = []
    
    for i, port in enumerate(selected_ports, 1):
        # Generate node name
        node_name = f"Hysteria2-Port{port}-Node{i:02d}"
        
        # URL encode password and obfuscation password
        import urllib.parse
        encoded_password = urllib.parse.quote(password, safe='')
        encoded_obfs_password = urllib.parse.quote(obfs_password, safe='')
        encoded_node_name = urllib.parse.quote(node_name, safe='')
        
        # Generate hysteria2 link
        hysteria2_url = f"hysteria2://{encoded_password}@{server_address}:{port}?insecure=1&sni={server_address}&obfs=salamander&obfs-password={encoded_obfs_password}#{encoded_node_name}"
        hysteria2_links.append(hysteria2_url)
    
    # Create v2rayN subscription content (Base64 encoded)
    subscription_content = "\n".join(hysteria2_links)
    subscription_base64 = base64.b64encode(subscription_content.encode('utf-8')).decode('utf-8')
    
    # Save subscription file
    subscription_file = f"{base_dir}/hysteria2-multi-port-subscription.txt"
    with open(subscription_file, 'w', encoding='utf-8') as f:
        f.write(subscription_base64)
    
    # Save plain text version (for viewing)
    subscription_plain_file = f"{base_dir}/hysteria2-multi-port-links.txt"
    with open(subscription_plain_file, 'w', encoding='utf-8') as f:
        f.write("# Hysteria2 multi-port configuration file\n")
        f.write(f"# Server: {server_address}\n")
        f.write(f"# Port range: {port_start}-{port_end}\n")
        f.write(f"# Generated nodes: {len(selected_ports)}\n")
        f.write(f"# Password: {password}\n")
        f.write(f"# Obfuscation password: {obfs_password}\n")
        f.write("\n# ===== Configuration Links =====\n\n")
        for link in hysteria2_links:
            f.write(link + "\n")
    
    return subscription_file, subscription_plain_file, len(selected_ports)

def start_service(start_script, port, base_dir):
    """Start Hysteria2 service"""
    os_name = platform.system().lower()
    
    try:
        if os_name == 'windows':
            subprocess.run(start_script, check=True)
        else:
            subprocess.run(['bash', start_script], check=True)
        
        # Wait for startup
        time.sleep(5)
        
        # Check if running
        if check_process_running(f"{base_dir}/hysteria.pid") or is_port_listening(port):
            return True
        else:
            print("Service startup failed")
            return False
    except Exception as e:
        print(f"Starting service failed: {e}")
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hysteria2 Deployment Script")
    parser.add_argument('--server', default=get_ip_address(), help="Server address")
    parser.add_argument('--port', type=int, default=443, help="Port")
    parser.add_argument('--password', default="123qwe!@#QWE", help="Password")
    parser.add_argument('--domain', help="Domain for certificate")
    parser.add_argument('--email', default="admin@example.com", help="Email for certificate")
    parser.add_argument('--real-cert', action='store_true', help="Use real certificate")
    parser.add_argument('--port-range', help="Port range for hopping, e.g. 28888-29999")
    parser.add_argument('--bbr', action='store_true', help="Enable BBR optimization")
    parser.add_argument('--delete', action='store_true', help="Delete Hysteria2")
    
    args = parser.parse_args()
    
    if args.delete:
        delete_hysteria2()
    else:
        deploy_hysteria2_complete(
            server_address=args.server,
            port=args.port,
            password=args.password,
            enable_real_cert=args.real_cert,
            domain=args.domain,
            email=args.email,
            port_range=args.port_range,
            enable_bbr=args.bbr
        )
