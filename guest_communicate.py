# Guest.py (Revised to implement Task 3)
import socket, json, time
import board
import busio
import adafruit_mcp4725

# --- 1. CONFIGURATION (No changes needed here) ---
HOST = 'pihost.local'
PORT = 65434
V_REF = 3.3

# --- 2. SETUP FUNCTIONS (No changes needed here) ---
def setup_dac():
    """Initializes the MCP4725 DAC."""
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        for addr in [0x60, 0x62, 0x63]:
            try:
                dac = adafruit_mcp4725.MCP4725(i2c, address=addr)
                print(f"✅ Adafruit MCP4725 DAC initialized successfully at address 0x{addr:02X}.")
                return dac
            except (ValueError, OSError):
                continue
        print("❌ ERROR: Could not find MCP4725 at available addresses. Real output will fail.")
        return None
    except Exception as e:
        print(f"❌ ERROR: An unexpected error occurred during DAC setup: {e}")
        return None

# --- 3. CORE LOGIC ---
def control_guest_vehicle_action(dac, voltage, status):
    """Sets DAC voltage and returns the voltage that was set."""
    print(f"  -> Data received [Status: {status}, Voltage: {voltage:.2f}V]")
    
    if not dac:
        print("     ❗ Action: No action taken (DAC not initialized).")
        # --- TASK 3 CHANGE ---
        return 0.0 # Return 0.0 if DAC doesn't exist

    if status == "Proper":
        output_value = int((voltage / V_REF) * 65535)
        dac.value = max(0, min(65535, output_value))
        print(f"     ✅ Action: Setting DAC output to match received voltage ({voltage:.2f}V).")
        # --- TASK 3 CHANGE ---
        return voltage # Return the voltage we were asked to set
    else:
        dac.value = 0
        print("     ❗ Action: Setting DAC output to safe state (0V) due to junk data.")
        # --- TASK 3 CHANGE ---
        return 0.0 # Return 0.0 because we set the DAC to zero

# --- 4. MAIN EXECUTION BLOCK ---
def main():
    """Main function to connect to host and process data."""
    print("--- Pi2 Guest (Live Actuator) ---")
    dac = setup_dac()
    
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            print(f"Connecting to host at {HOST}:{PORT}...")
            s.connect((HOST, PORT))
            print("✅ Connection successful. Waiting for data...")
            
            while True:
                data = s.recv(1024)
                if not data:
                    print("\nHost has closed the connection.")
                    break
                
                t2 = time.time()
                received_packet = json.loads(data.decode('utf-8'))
                
                # --- TASK 3 CHANGE ---
                # Capture the voltage that was actually set on the DAC
                voltage_set_on_dac = control_guest_vehicle_action(
                    dac, received_packet['voltage'], received_packet['status']
                )
                
                t3 = time.time()
                
                # --- TASK 3 CHANGE ---
                # Add the actual DAC voltage to the report sent back to the host
                report_back = {
                    't2': t2, 
                    't3': t3, 
                    'dac_voltage_set': voltage_set_on_dac
                }
                
                s.sendall(json.dumps(report_back).encode('utf-8'))
                
        except ConnectionRefusedError:
            print(f"❌ ERROR: Connection refused. Is the Host script running on {HOST}?")
        except Exception as e:
            print(f"❌ An error occurred: {e}")
        finally:
            if dac:
                dac.value = 0
                print("\nProgram terminated. DAC output set to 0V.")

if __name__ == "__main__":
    main()
