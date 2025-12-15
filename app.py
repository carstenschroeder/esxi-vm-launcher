"""
ESXi VM Launcher
Eine Web-Anwendung zum Starten von VMs auf einem ESXi Server
"""

import os
from flask import Flask, render_template, jsonify, request, session
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim
import ssl
import requests

app = Flask(__name__, static_folder='static', static_url_path='')

# SECRET_KEY: Aus Umgebungsvariable (Docker) oder generiert (lokal)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24))

# Session-Konfiguration
app.config['SESSION_COOKIE_SECURE'] = False  # True nur bei HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = 3600  # 1 Stunde

def get_si(host, user, password):
    """Verbindung zum ESXi Server herstellen"""
    context = ssl._create_unverified_context()
    si = SmartConnect(
        host=host,
        user=user,
        pwd=password,
        sslContext=context
    )
    return si

def get_all_vms(host, user, password):
    """Alle VMs vom ESXi abrufen"""
    si = get_si(host, user, password)
    content = si.RetrieveContent()
    container = content.viewManager.CreateContainerView(
        content.rootFolder, [vim.VirtualMachine], True
    )
    vms = []
    for vm in container.view:
        # IP-Adresse ermitteln
        ip_address = 'N/A'
        if vm.guest and vm.guest.ipAddress:
            ip_address = vm.guest.ipAddress
        elif vm.guest and vm.guest.net:
            for nic in vm.guest.net:
                if nic.ipAddress:
                    # Erste IPv4-Adresse nehmen
                    for ip in nic.ipAddress:
                        if ':' not in ip:  # IPv4 (keine IPv6)
                            ip_address = ip
                            break
                if ip_address != 'N/A':
                    break
        
        # CPU und RAM
        num_cpu = vm.config.hardware.numCPU if vm.config else 0
        memory_mb = vm.config.hardware.memoryMB if vm.config else 0
        memory_gb = round(memory_mb / 1024, 1) if memory_mb > 0 else 0
        
        vms.append({
            'name': vm.name,
            'moid': vm._moId,
            'power_state': vm.runtime.powerState,
            'guest_os': vm.config.guestFullName if vm.config else 'Unknown',
            'num_cpu': num_cpu,
            'memory_gb': memory_gb,
            'ip_address': ip_address
        })
    container.Destroy()
    Disconnect(si)
    vms.sort(key=lambda x: x['name'].lower())
    return vms

@app.route('/')
def index():
    """Hauptseite"""
    return app.send_static_file('index.html')

@app.route('/api/login', methods=['POST'])
def login():
    """Login-Endpoint"""
    data = request.json
    host = data.get('host')
    user = data.get('user')
    password = data.get('password')
    
    try:
        # Verbindung testen
        si = get_si(host, user, password)
        Disconnect(si)
        
        # Session speichern
        session['logged_in'] = True
        session['host'] = host
        session['user'] = user
        session['password'] = password
        session.permanent = True
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    """Logout-Endpoint"""
    session.clear()
    return jsonify({'success': True})

@app.route('/api/check-session', methods=['GET'])
def check_session():
    """Session-Status prüfen"""
    if session.get('logged_in'):
        return jsonify({
            'logged_in': True,
            'host': session.get('host')
        })
    return jsonify({'logged_in': False})

@app.route('/api/vms', methods=['GET'])
def list_vms():
    """Liste aller VMs"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Nicht eingeloggt'}), 401
    
    try:
        vms = get_all_vms(
            session.get('host'),
            session.get('user'),
            session.get('password')
        )
        return jsonify({'success': True, 'vms': vms})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/vm/<moid>/start', methods=['POST'])
def start_vm(moid):
    """VM starten"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Nicht eingeloggt'}), 401
    
    try:
        si = get_si(
            session.get('host'),
            session.get('user'),
            session.get('password')
        )
        content = si.RetrieveContent()
        vm = None
        
        # VM anhand MoID finden
        container = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.VirtualMachine], True
        )
        for v in container.view:
            if v._moId == moid:
                vm = v
                break
        container.Destroy()
        
        if not vm:
            Disconnect(si)
            return jsonify({'success': False, 'error': 'VM nicht gefunden'}), 404
        
        if vm.runtime.powerState == 'poweredOn':
            Disconnect(si)
            return jsonify({'success': True, 'message': 'VM läuft bereits'})
        
        task = vm.PowerOn()
        Disconnect(si)
        return jsonify({'success': True, 'message': 'VM wird gestartet'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/vm/<moid>/shutdown', methods=['POST'])
def shutdown_vm(moid):
    """VM herunterfahren (Graceful Shutdown)"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Nicht eingeloggt'}), 401
    
    try:
        si = get_si(
            session.get('host'),
            session.get('user'),
            session.get('password')
        )
        content = si.RetrieveContent()
        vm = None
        
        # VM anhand MoID finden
        container = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.VirtualMachine], True
        )
        for v in container.view:
            if v._moId == moid:
                vm = v
                break
        container.Destroy()
        
        if not vm:
            Disconnect(si)
            return jsonify({'success': False, 'error': 'VM nicht gefunden'}), 404
        
        if vm.runtime.powerState == 'poweredOff':
            Disconnect(si)
            return jsonify({'success': True, 'message': 'VM ist bereits ausgeschaltet'})
        
        # Prüfen ob VMware Tools läuft
        if vm.guest.toolsRunningStatus == 'guestToolsRunning':
            # Graceful Shutdown
            vm.ShutdownGuest()
            Disconnect(si)
            return jsonify({'success': True, 'message': 'VM wird heruntergefahren'})
        else:
            # Fallback: Hard Power Off wenn Tools nicht laufen
            task = vm.PowerOff()
            Disconnect(si)
            return jsonify({'success': True, 'message': 'VM wird gestoppt (VMware Tools nicht verfügbar)'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/vm/<moid>/vmrc', methods=['GET'])
def get_vmrc_link(moid):
    """VMRC-Link für VM generieren"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Nicht eingeloggt'}), 401
    
    try:
        host = session.get('host')
        user = session.get('user')
        password = session.get('password')
        
        si = get_si(host, user, password)
        content = si.RetrieveContent()
        
        # VM finden
        container = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.VirtualMachine], True
        )
        vm = None
        for v in container.view:
            if v._moId == moid:
                vm = v
                break
        container.Destroy()
        
        if not vm:
            Disconnect(si)
            return jsonify({'success': False, 'error': 'VM nicht gefunden'}), 404
        
        # VMRC-URL generieren
        vmrc_url = f"vmrc://clone:{user}@{host}/?moid={moid}"
        
        Disconnect(si)
        return jsonify({'success': True, 'vmrc_url': vmrc_url})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    print("🚀 ESXi VM Launcher startet auf http://localhost:5000")
    app.run(debug=False, host='0.0.0.0', port=5000)