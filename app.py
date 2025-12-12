# app.py
from flask import Flask, jsonify, request, send_from_directory, session
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim
import ssl
import secrets

app = Flask(__name__, static_folder='static')
app.secret_key = secrets.token_hex(32)  # Für Session-Management

def get_si(host, user, password):
    """Verbindung zum ESXi herstellen"""
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
    return vms

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/login', methods=['POST'])
def login():
    """Login und Credentials in Session speichern"""
    try:
        data = request.json
        host = data.get('host')
        user = data.get('user')
        password = data.get('password')
        
        if not all([host, user, password]):
            return jsonify({'success': False, 'error': 'Alle Felder müssen ausgefüllt sein'}), 400
        
        # Verbindung testen
        try:
            si = get_si(host, user, password)
            Disconnect(si)
        except Exception as e:
            return jsonify({'success': False, 'error': f'Verbindung fehlgeschlagen: {str(e)}'}), 401
        
        # Credentials in Session speichern
        session['host'] = host
        session['user'] = user
        session['password'] = password
        session['logged_in'] = True
        
        return jsonify({'success': True, 'message': 'Login erfolgreich'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/logout', methods=['POST'])
def logout():
    """Logout und Session löschen"""
    session.clear()
    return jsonify({'success': True, 'message': 'Logout erfolgreich'})

@app.route('/api/check-session', methods=['GET'])
def check_session():
    """Prüfen ob User eingeloggt ist"""
    if session.get('logged_in'):
        return jsonify({'success': True, 'logged_in': True, 'host': session.get('host')})
    return jsonify({'success': True, 'logged_in': False})

@app.route('/api/vms', methods=['GET'])
def list_vms():
    """Liste aller VMs zurückgeben"""
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
    """VMRC-Link generieren"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Nicht eingeloggt'}), 401
    
    host = session.get('host')
    user = session.get('user')
    # Format: vmrc://username@esxi-host/?moid=vm-123
    vmrc_url = f"vmrc://{user}@{host}/?moid={moid}"
    return jsonify({'success': True, 'vmrc_url': vmrc_url})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)