# sensor_agent_pi.py
import socket
import json
import time
import threading
from gpsdclient import GPSDClient
import busio
import digitalio
import board
from adafruit_mcp3xxx.mcp3008 import MCP3008
from adafruit_mcp3xxx.analog_in import AnalogIn

# --- Configuration ---
LAPTOP_IP = "YOUR_LAPTOP_IP_ADDRESS"  # <--- IMPORTANT: SET YOUR LAPTOP's IP
LAPTOP_PORT = 65430
AGENT_ID = "sensor_pi"

# --- Global State ---
latest_gps_coords = None
adc_channel = None

def setup_adc():
    global adc_channel
    try:
        spi = busio.SPI(clock=board.SCK, MISO=board.MISO, MOSI=board.MOSI)
        cs = digitalio.DigitalInOut(board.D5)
        mcp = MCP3008(spi, cs)
        adc_channel = AnalogIn(mcp, 0)
        print("✅ Adafruit MCP3008 ADC initialized.")
    except Exception as e:
        print(f"❌ ERROR: Could not initialize ADC: {e}")
        adc_channel = None

def get_sensor_reading():
    if not adc_channel:
        return 0.0, "Junk (No ADC)"
    try:
        voltage = adc_channel.voltage
        status = "Proper" if voltage > 0.1 else "Junk (Disconnected)"
        return voltage, status
    except Exception as e:
        print(f"ADC Read Error: {e}")
        return 0.0, "Junk (Read Error)"

def get_gps_coords():
    try:
        with GPSDClient() as client:
            for result_str in client.json_stream():
                result = json.loads(result_str)
                if result.get("class") == "TPV" and result.get("mode") == 3 and "lat" in result:
                    coords = (result['lat'], result['lon'])
                    print(f"🛰  Sensor Pi GPS Lock: {coords}")
                    return coords
    except Exception as e:
        print(f"❌ GPSD Error on Sensor Pi: {e}")
        return None

def gps_polling_thread():
    global latest_gps_coords
    while True:
        latest_gps_coords = get_gps_coords()
        time.sleep(3)

def main():
    print(f"--- Starting Sensor Agent ({AGENT_ID}) ---")
    setup_adc()
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
                    voltage, status = get_sensor_reading()
                    packet = {
                        'type': 'sensor_data',
                        'voltage': voltage,
                        'status': status,
                        'gps': latest_gps_coords,
                        'timestamp': time.time()
                    }
                    s.sendall(json.dumps(packet).encode('utf-8'))
                    print(f"-> Sent: Voltage={voltage:.2f}V, Status={status}, GPS={latest_gps_coords}")
                    time.sleep(0.5)

        except (ConnectionRefusedError, ConnectionResetError, BrokenPipeError) as e:
            print(f"❌ Connection lost to Control Center: {e}. Retrying in 5 seconds...")
        except Exception as e:
            print(f"❌ An unexpected error occurred: {e}. Retrying in 5 seconds...")
        
        time.sleep(5)

if _name_ == "_main_":
    main()
