# actuator_agent_pi.py
import socket
import json
import time
import threading
from gpsdclient import GPSDClient
import board
import busio
import adafruit_mcp4725

# --- Configuration ---
LAPTOP_IP = "YOUR_LAPTOP_IP_ADDRESS"  # <--- IMPORTANT: SET YOUR LAPTOP's IP
LAPTOP_PORT = 65431
AGENT_ID = "actuator_pi"
V_REF = 3.3

# --- Global State ---
latest_gps_coords = None
dac = None

def setup_dac():
    global dac
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        for addr in [0x60, 0x62, 0x63]:
            try:
                dac = adafruit_mcp4725.MCP4725(i2c, address=addr)
                print(f"✅ Adafruit MCP4725 DAC initialized at 0x{addr:02X}.")
                return
            except (ValueError, OSError):
                continue
        print("❌ ERROR: Could not find MCP4725 DAC.")
        dac = None
    except Exception as e:
        print(f"❌ ERROR during DAC setup: {e}")
        dac = None

def control_vehicle(voltage, status):
    print(f"  -> Command received [Status: {status}, Voltage: {voltage:.2f}V]")
    if not dac:
        return 0.0
    
    if status == "Proper":
        output_value = int((voltage / V_REF) * 65535)
        dac.value = max(0, min(65535, output_value))
        return voltage
    else:
        dac.value = 0
        return 0.0

def get_gps_coords():
    try:
        with GPSDClient() as client:
            for result_str in client.json_stream():
                result = json.loads(result_str)
                if result.get("class") == "TPV" and result.get("mode") == 3 and "lat" in result:
                    coords = (result['lat'], result['lon'])
                    print(f"🛰  Actuator Pi GPS Lock: {coords}")
                    return coords
    except Exception as e:
        print(f"❌ GPSD Error on Actuator Pi: {e}")
        return None

def gps_polling_thread():
    global latest_gps_coords
    while True:
        latest_gps_coords = get_gps_coords()
        time.sleep(3)

def main():
    print(f"--- Starting Actuator Agent ({AGENT_ID}) ---")
    setup_dac()
    threading.Thread(target=gps_polling_thread, daemon=True).start()

    while True:
        try:
            print(f"Attempting to connect to Control Center at {LAPTOP_IP}:{LAPTOP_PORT}...")
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((LAPTOP_IP, LAPTOP_PORT))
                print("✅ Connected to Control Center.")
                
                intro_message = json.dumps({'id': AGENT_ID}).encode('utf-8')
                s.sendall(intro_message)
                
                while True:
                    command_data = s.recv(1024)
                    if not command_data: break
                    
                    command = json.loads(command_data.decode('utf-8'))
                    voltage_set = control_vehicle(command['voltage'], command['status'])
                    
                    report_back = {
                        'type': 'actuator_status',
                        'gps': latest_gps_coords,
                        'voltage_set': voltage_set,
                        'timestamp': time.time()
                    }
                    s.sendall(json.dumps(report_back).encode('utf-8'))

        except (ConnectionRefusedError, ConnectionResetError, BrokenPipeError) as e:
            print(f"❌ Connection lost: {e}. Retrying in 5 seconds...")
            if dac: dac.value = 0
        except Exception as e:
            print(f"❌ An unexpected error occurred: {e}. Retrying in 5 seconds...")
            if dac: dac.value = 0
        
        time.sleep(5)

if _name_ == "_main_":
    main()
